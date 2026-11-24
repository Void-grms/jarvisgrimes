"""Bucle de voz: escuchar → entender (Motor) → responder en voz, con medición de latencia.
Si hay widget, le va contando qué hace (escuchando, oyendo, pensando, hablando)."""
from __future__ import annotations

import difflib
import logging
import queue
import re
import socket
import threading
import time
from dataclasses import replace

from .brain import Cerebro
from .ensayo_escucha import EnsayoEscucha
from .motor import Motor
from .respuesta import Respuesta
from .texto import normalizar

log = logging.getLogger("asistente")
PUERTO = 47321  # para instancia única y para el aviso "despierta" al reanudar
# Lo que dices al interrumpir para que solo se calle ("ya", "para", "basta"...).
PARAR = re.compile(r"^(?:(?:jarvis|oye|ok|okay|ya|no|espera)\s+)*(?:ya(?: ya)*|para(?: ya)?|basta(?: ya)?|alto|stop|"
                   r"detente|callate|silencio|ya esta(?: bien)?|suficiente|gracias|espera|no|no no|ok)"
                   r"(?:\s+(?:jarvis|gracias|por favor))*$")


def avisar_instancia_existente(orden: bytes = b"despierta") -> bool:
    """Pide despertar o mostrar el panel a la instancia que ya escucha en este equipo."""
    try:
        with socket.create_connection(("127.0.0.1", PUERTO), timeout=0.5) as s:
            s.sendall(orden)
            if orden == b"panel":
                return s.recv(8) == b"ok"
        return True
    except OSError:
        return False


def seguro(fn):
    def envoltura():
        try:
            fn()
        except Exception:
            log.exception("Falló una acción")
    return envoltura


class _SinWidget:
    """Estado vacío cuando no hay interfaz."""
    modo = ""
    nivel = 0.0
    mic_activo = True

    def decir_usuario(self, *_):
        pass

    def decir_jarvis(self, *_, **__):
        pass


class Asistente:
    def __init__(self, estado=None, cfg=None):
        self.motor = Motor(cfg)
        self.cfg = self.motor.cfg
        self.estado = estado or _SinWidget()
        self.estado.actividad = self.motor.actividad
        self.motor.encargos.abrir_revision = getattr(self.estado, "solicitar_encargo", None)
        self.motor.trabajo.mostrar = getattr(self.estado, "mostrar_proyecto", None)
        self.motor.trabajo.avisar = self._avisar_trabajo
        self.estado.trabajo_office = self.motor.trabajo
        self._despertar = threading.Event()
        self._t_oyendo = 0.0
        self._interrumpido = 0.0      # cuándo lo interrumpieron (voz o clic)
        self._dicho: list[str] = []   # últimas frases que dijo (para no confundir su eco con tu voz)
        self._inicio_voz = self._fin_voz = 0.0
        self._ultima_aclaracion = float("-inf")
        self._aviso_memoria = 0.0
        self._ignorar_audio_hasta = 0.0

    def _avisar_trabajo(self, mensaje):
        self.motor.avisar(mensaje)
        if actualizar := getattr(self.estado, "noticia_trabajo", None):
            actualizar(mensaje)

    # ---------- arranque ----------
    def cargar_modelos(self) -> None:
        from .stt import Oido, Transcriptor
        from .tts import Voz
        t = time.perf_counter()
        cerebro: dict = {}

        def iniciar_cerebro():
            cerebro["c"] = Cerebro(self.cfg["llm"], self.motor.nombre)
            cerebro["c"].precalentar()

        hilo = threading.Thread(target=iniciar_cerebro, daemon=True)
        hilo.start()  # el LLM carga en paralelo con la voz y el oído
        cv = self.cfg["voz"]
        self.voz = Voz.desde_config(cv, al_nivel=self._nivel)
        if cv.get("relleno", False):
            self.voz.preparar_rellenos(cv.get("frases_relleno", []))
        self.stt = Transcriptor(self.cfg["stt"])
        self.oido = Oido(self.cfg["escucha"], al_nivel=self._nivel_mic, al_empezar=self._empezo_a_hablar)
        self.oido.nivel_salida = self.voz.nivel_reciente
        self.oido.al_interrumpir = self._interrumpir
        hilo.join()
        self.motor.preparar(cerebro["c"])
        self.motor.comandos.conectar_voz(self.voz)
        log.info("Modelos listos en %.1fs", time.perf_counter() - t)

    def servidor_avisos(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", PUERTO))
        srv.listen(2)

        def bucle():
            while True:
                con, _ = srv.accept()
                with con:
                    con.settimeout(1)
                    try:
                        orden = con.recv(32)
                        if orden == b"despierta":
                            self._despertar.set()
                        elif orden == b"panel" and getattr(self.estado, "solicitar_panel", None):
                            self.estado.solicitar_panel()
                            con.sendall(b"ok")
                    except OSError:
                        continue

        threading.Thread(target=bucle, daemon=True).start()

    # ---------- widget ----------
    def _nivel(self, v: float) -> None:
        self.estado.nivel = v

    def _nivel_mic(self, v: float) -> None:
        if self.estado.modo in ("oyendo", "escuchando"):
            self.estado.nivel = v

    def _empezo_a_hablar(self) -> None:
        self._t_oyendo = time.time()
        self.estado.modo = "oyendo"

    def _reposo(self) -> None:
        self.estado.modo = "escuchando" if self.motor.despierto() else "dormido"
        self.estado.nivel = 0.0

    def clic(self) -> None:
        """Clic en el widget: si está hablando, se calla; si no, despierta y escucha."""
        self.motor.activo_hasta = time.time() + self.cfg["escucha"]["minutos_conversacion"] * 60
        if self.estado.modo == "hablando":
            self._interrumpido = time.perf_counter()
            self.voz.detener()
            return
        self.motor.avisar("¿Sí? Te escucho.")

    def _interrumpir(self) -> bool:
        """El micrófono detectó que le hablas encima: se calla y te escucha."""
        if self.estado.modo != "hablando":
            return False
        log.info("⏹ Me hablaste encima: me callo y te escucho.")
        self._interrumpido = time.perf_counter()
        self.voz.detener()
        return True

    def _era_mi_eco(self, texto: str) -> bool:
        """¿Lo que 'oyó' es su propia voz saliendo por el parlante?"""
        mio = normalizar(" ".join(self._dicho[-3:])).split()
        oido = normalizar(texto).split()
        if not mio or not oido:
            return False
        parecido = difflib.SequenceMatcher(None, oido, mio).find_longest_match(0, len(oido), 0, len(mio)).size
        return parecido >= 2 and parecido / len(oido) >= 0.8

    # ---------- hablar ----------
    def decir(self, frases, t0: float | None = None, relleno: bool = False) -> bool:
        """Habla. Devuelve False si lo interrumpieron."""
        def primera_voz():
            self._inicio_voz = time.perf_counter()
            self._dicho.clear()
            self.estado.modo = "hablando"
            if t0:
                log.info("⏱ fin de tu voz → primera palabra: %.2fs", time.perf_counter() - t0)

        def al_frase(f: str) -> None:
            self._dicho = (self._dicho + [f])[-6:]
            if not texto_fijo:
                self.estado.decir_jarvis(f, agregar=True)

        texto_fijo = isinstance(frases, str)
        self.estado.decir_jarvis(frases if texto_fijo else "")
        self.oido.silenciado.set()
        try:
            return self.voz.decir([frases] if texto_fijo else frases, primera_voz,
                                  relleno=self.voz.relleno() if relleno else None, al_frase=al_frase)
        finally:
            self._fin_voz = time.perf_counter()
            time.sleep(0.15)  # deja que se apague el eco del parlante
            self.oido.silenciado.clear()

    def responder(self, r: Respuesta, t0: float | None = None, *, activar: bool = True) -> None:
        self.motor.ejecutar(r)
        if r.accion:
            threading.Thread(target=seguro(r.accion), daemon=True).start()
        completo = self.decir(r.texto, t0, relleno=r.relleno) if r.texto else True
        if r.despues and completo:  # si lo interrumpiste, no sigue con lo que iba a hacer después
            seguro(r.despues)()
        if not completo and r.siguiente:
            if r.al_cancelar:
                seguro(r.al_cancelar)()
            r = replace(r, siguiente=None)
        self.motor.aplicar(r, activar=activar)
        self._reposo()

    def _revisar_memoria(self, t_stt: float) -> None:
        """Si va lento por falta de RAM, lo dice (como mucho cada 30 minutos)."""
        if t_stt < 3.5 or time.time() - self._aviso_memoria < 1800:
            return
        try:
            from .acciones.sistema import memoria
            _, _, pct = memoria()
        except Exception:
            return
        if pct >= 90:
            self._aviso_memoria = time.time()
            self.motor.avisar(f"Ojo: voy lento porque la memoria está al {pct} %. Si cierras algunos programas "
                              "respondo más rápido. Pregúntame qué está consumiendo memoria.")

    # ---------- bucle ----------
    def _atender_panel(self) -> None:
        eventos = getattr(self.estado, "eventos_panel", None)
        if eventos is None:
            return
        while True:
            try:
                tipo, valores, terminado = eventos.get_nowait()
            except queue.Empty:
                return
            if tipo == "voz":
                self.cfg["voz"].update(valores)
                if "velocidad" in valores:
                    self.voz.velocidad = valores["velocidad"]
                if "tono_semitonos" in valores:
                    self.voz.tono_semitonos = valores["tono_semitonos"]
                if "pausa_ms" in valores:
                    self.voz.pausa = valores["pausa_ms"] / 1000
                if "pronunciaciones" in valores:
                    self.voz.pronunciaciones = dict(valores["pronunciaciones"])
            elif tipo == "muestra":
                from .muestra import reproducir_muestra
                self.oido.pausado.set()
                try:
                    actual = self.voz if valores["modelo_piper"] == self.cfg["voz"]["modelo_piper"] else None
                    reproducir_muestra(valores, actual)
                except Exception as e:
                    terminado(f"No se pudo reproducir la muestra. {e}", True)
                else:
                    terminado("Muestra reproducida.", False)
                finally:
                    time.sleep(0.2)
                    self._ignorar_audio_hasta = time.perf_counter()
                    if self.estado.mic_activo:
                        self.oido.pausado.clear()

    @property
    def _ensayo(self):
        ensayo = getattr(self.estado, "ensayo_escucha", None)
        return ensayo if isinstance(ensayo, EnsayoEscucha) else None

    def _procesar_audio(self, audio, fin: float) -> None:
        """Solo el texto aceptado y posterior a la pregunta puede autorizar una acción."""
        from .stt import TASA
        espera = time.perf_counter() - fin
        if espera > 15:
            log.info("Descarté una frase que llevaba %.1f s esperando.", espera)
            self._reposo()
            return
        duracion = len(audio) / TASA
        inicio = fin - duracion
        ensayo = self._ensayo
        turno = ensayo.turno(inicio) if ensayo else (0, False)
        if turno is None:
            self._reposo()
            return
        en_ensayo = turno[1]
        pendiente = None if en_ensayo else self.motor.pendiente
        # La captura incluye hasta 300 ms de prebúfer antes del comienzo de la voz.
        if (not self.estado.mic_activo or inicio < getattr(self, "_ignorar_audio_hasta", 0)
                or (pendiente and min(fin, inicio + 0.30) < pendiente.desde_audio)):
            self._reposo()
            return
        self.estado.modo = "pensando"
        t = time.perf_counter()
        try:
            resultado = self.stt.reconocer(audio)
        except Exception:
            if not en_ensayo:
                raise
            log.exception("Falló el reconocimiento durante el ensayo de escucha")
            ensayo.registrar(turno, "", "error", duracion, time.perf_counter() - t)
            self._reposo()
            return
        demora = time.perf_counter() - t
        texto = resultado.texto
        # El panel puede cambiar de modo mientras Whisper trabaja. El audio del
        # ensayo nunca llega a la memoria, al historial ni al intérprete de órdenes.
        if en_ensayo:
            if self.estado.mic_activo:
                ensayo.registrar(turno, texto, resultado.estado, duracion, demora)
            else:
                ensayo.detener("El ensayo se detuvo al silenciar el micrófono.")
            self._reposo()
            return
        if ensayo and turno != ensayo.turno(inicio):
            self._reposo()
            return
        log.info("⏱ transcripción %s: %.2fs (%.1fs de audio) «%s»",
                 resultado.estado, demora, duracion, texto)
        self._revisar_memoria(demora)
        if not self.estado.mic_activo or time.perf_counter() - fin > 15:
            if self.estado.mic_activo:
                log.info("La frase caducó durante el reconocimiento.")
            self._reposo()
            return
        interrupcion = inicio <= self._interrumpido <= fin and self._interrumpido > 0
        cerca_de_voz = self._inicio_voz <= fin and inicio <= self._fin_voz + 0.35
        if texto and cerca_de_voz and self._era_mi_eco(texto):
            if interrupcion:
                self.oido.sensibilidad = min(8.0, self.oido.sensibilidad * 1.3)
            log.info("Descarté el eco de mi última respuesta.")
        elif resultado.estado == "dudosa":
            if self.motor.despierto() and t - self._ultima_aclaracion >= 12 and not cerca_de_voz:
                self._ultima_aclaracion = t
                self.decir("No te oí bien. ¿Puedes repetirlo?")
        elif resultado.estado == "aceptada" and texto:
            orden = normalizar(self.motor.quitar_clave(texto)[1])
            if interrupcion and PARAR.fullmatch(orden):
                self.estado.decir_usuario(texto)
                if orden == "espera" and self.motor.trabajo.cancelar(pausar=True):
                    self.estado.decir_jarvis("Pausé el trabajo.")
                    if actualizar := getattr(self.estado, "noticia_trabajo", None):
                        actualizar("Trabajo en pausa.")
            else:
                r = self.motor.entender(texto, por_voz=True)
                if r:
                    self.estado.decir_usuario(texto)
                    self.responder(r, fin)
                    return
                log.info("En reposo. La frase no incluía la palabra de activación.")
        self._reposo()

    def _atender_avisos(self):
        if self._despertar.is_set():
            self._despertar.clear()
            self.oido.reiniciar()  # tras suspender, Windows suele soltar el micrófono
            r = self.motor.saludo()
            if r:
                self.responder(r)
        r = self.motor.expirado()
        if r:
            self.responder(r)
        while not self.motor.avisos.empty():
            self.responder(self.motor.avisos.get(), activar=False)

    def correr(self) -> None:
        self.estado.modo = "cargando"
        self.servidor_avisos()
        self.cargar_modelos()
        self._atender_panel()
        self.oido.iniciar()
        self.motor.sync.periodico()
        if self.cfg["escucha"]["saludar_al_iniciar"]:
            r = self.motor.saludo(forzar=True)
            if r:
                self.responder(r)
        else:
            self.motor.sync.en_segundo_plano()
        self._reposo()
        log.info("Escuchando. Palabra clave: %s", self.cfg["escucha"]["palabra_clave"])
        ensayo = self._ensayo
        while True:
            self._atender_panel()
            if self.estado.mic_activo == self.oido.pausado.is_set():  # cambió desde el widget
                (self.oido.pausado.clear if self.estado.mic_activo else self.oido.pausado.set)()
            if ensayo and not self.estado.mic_activo:
                ensayo.detener("El ensayo se detuvo al silenciar el micrófono.")
            if not ensayo or not ensayo.activo:
                self._atender_avisos()
            if self.estado.modo not in ("oyendo", "pensando", "hablando") or (
                    self.estado.modo == "oyendo" and time.time() - self._t_oyendo > 12):
                self._reposo()
            try:
                audio, t0 = self.oido.frases.get(timeout=0.3)
            except queue.Empty:
                continue
            self._procesar_audio(audio, t0)
