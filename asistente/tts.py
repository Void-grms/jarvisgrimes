"""Voz local con Piper: sintetiza la frase siguiente mientras suena la actual.

Toda una respuesta sale por UN solo flujo de audio (sin abrir y cerrar el parlante en cada frase),
así no se traba entre frases y se puede cortar al instante si lo interrumpes.
"""
from __future__ import annotations

import collections
import json
import logging
import queue
import random
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import numpy as np
import sounddevice as sd
from piper import PiperVoice

from .config import ruta_proyecto
from .pronunciacion import aplicar_pronunciaciones, validar_pronunciaciones

log = logging.getLogger("voz")
_LIMPIAR = re.compile(r"[*#`>\[\]]|[\U0001F300-\U0001FAFF☀-➿]\ufe0f?")
_MARCA_LISTA = re.compile(r"^\s*[-+*•]\s+")
_TRATAMIENTOS = {"sr": "señor", "sra": "señora", "dr": "doctor", "dra": "doctora"}
_UNIDADES = {"KB": "kilobyte", "MB": "megabyte", "GB": "gigabyte", "TB": "terabyte"}
_CANTIDAD = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(KB|MB|GB|TB)\b")
_HORA = re.compile(r"\b(la|las)\s+([01]?\d|2[0-3]):([0-5]\d)(?![\d:])", re.I)


def _leer_cantidad(m: re.Match) -> str:
    numero, unidad = m.groups()
    if re.fullmatch(r"0*1(?:,0+)?", numero):
        return f"un {_UNIDADES[unidad]}"
    return f"{numero} {_UNIDADES[unidad]}s"


def _leer_hora(m: re.Match) -> str:
    hora, minuto = int(m[2]), int(m[3])
    sufijo = {0: "en punto", 15: "y cuarto", 30: "y media"}.get(minuto, f"y {minuto}")
    horas = "la una" if hora == 1 else f"las {hora}"
    return f"{horas} {sufijo}"


def preparar_lectura(texto: str, pronunciaciones=None) -> str:
    """Adapta el texto hablado. El texto original se conserva en los subtítulos."""
    # eSpeak es-419 ignora la tilde combinante de un texto pegado o dictado en NFD.
    texto = unicodedata.normalize("NFC", texto)
    texto = re.sub(r"\[([^\]\n]+)\]\(https?://[^\s)]+\)", r"\1", texto)
    texto = aplicar_pronunciaciones(texto, pronunciaciones)
    lineas = []
    for linea in texto.splitlines():
        if _MARCA_LISTA.match(linea):
            if lineas and lineas[-1][-1] not in ".!?,:;…":
                lineas[-1] += "."
            linea = _MARCA_LISTA.sub("", linea)
        linea = _LIMPIAR.sub("", linea).strip()
        if linea:
            lineas.append(linea)
    texto = " ".join(lineas)
    # Con puntos suspensivos eSpeak llega a unir las palabras a ambos lados.
    texto = re.sub(r"(?:\.{3,}|…+)\s*(?=$|[¿¡])", ". ", texto)
    texto = re.sub(r"(?:\.{3,}|…+)\s*(?=\w)", ", ", texto)
    texto = re.sub(r"(?<!\w)_+|_+(?!\w)", "", texto).replace("_", " ")
    texto = re.sub(r"\b(sr|sra|dr|dra)\.(?=\s+[^\W\d_])",
                   lambda m: _TRATAMIENTOS[m[1].lower()], texto, flags=re.I)
    texto = _HORA.sub(_leer_hora, texto)
    texto = _CANTIDAD.sub(_leer_cantidad, texto)
    return texto.strip()


class Voz:
    def __init__(self, ruta_modelo: str, velocidad: float = 1.18, pausa_ms: int = 95, al_nivel=None,
                 hilos: int = 2, tono_semitonos: float = 0.7, pronunciaciones=None):
        self.voz = self._cargar_modelo(ruta_modelo, hilos)
        self.tasa = self.voz.config.sample_rate
        self.velocidad = velocidad
        self.tono_semitonos = tono_semitonos
        self.pronunciaciones = validar_pronunciaciones(pronunciaciones or {})
        self.pausa = pausa_ms / 1000
        self.al_nivel = al_nivel      # volumen de la voz 0..1 (para el widget)
        self.rellenos: list[np.ndarray] = []
        self._ultimo_relleno = -1
        # Salida de audio: cola de trozos que el parlante va consumiendo.
        self._lock = threading.Lock()
        self._trozos: collections.deque[np.ndarray] = collections.deque()
        self._offset = 0
        self._pendientes = 0
        self._silencio_salida = 0
        self._salida: collections.deque[tuple[float, float]] = collections.deque(maxlen=96)
        self._detener = threading.Event()
        self.interrumpida = False
        self.sintetizar("Listo.")  # precalienta onnxruntime

    @classmethod
    def desde_config(cls, cfg: dict, al_nivel=None):
        return cls(str(ruta_proyecto(cfg["modelo_piper"])), cfg["velocidad"], cfg["pausa_ms"],
                   al_nivel, cfg["hilos"], cfg["tono_semitonos"], cfg.get("pronunciaciones"))

    @staticmethod
    def _cargar_modelo(ruta: str, hilos: int) -> PiperVoice:
        """Crea una única sesión de inferencia, también al abrir la escucha de muestra."""
        if not hilos:
            return PiperVoice.load(ruta)
        try:
            import onnxruntime as ort
            from piper.config import PiperConfig

            opciones = ort.SessionOptions()
            opciones.intra_op_num_threads = hilos
            opciones.inter_op_num_threads = 1
            opciones.add_session_config_entry("session.intra_op.allow_spinning", "0")
            with Path(f"{ruta}.json").open(encoding="utf-8") as f:
                config = PiperConfig.from_dict(json.load(f))
            sesion = ort.InferenceSession(str(ruta), sess_options=opciones, providers=["CPUExecutionProvider"])
            return PiperVoice(config=config, session=sesion)
        except (AttributeError, ImportError, TypeError) as e:
            log.debug("Uso el cargador estándar de esta versión de Piper: %s", e)
            return PiperVoice.load(ruta)

    # ---------------------------------------------------------------- frases
    def preparar_rellenos(self, frases: list[str]) -> None:
        """Frases cortas que dice mientras el modelo piensa (ya sintetizadas: suenan al instante)."""
        self.rellenos = [a for a in (self.sintetizar(f) for f in frases) if a.size]

    def relleno(self) -> np.ndarray | None:
        if not self.rellenos:
            return None
        opciones = [i for i in range(len(self.rellenos)) if i != self._ultimo_relleno] or [0]
        self._ultimo_relleno = random.choice(opciones)
        return self.rellenos[self._ultimo_relleno]

    def sintetizar(self, texto: str) -> np.ndarray:
        partes = []
        for pcm in self._oraciones_audio(texto):
            if partes:
                partes.append(np.zeros(int(self.tasa * self.pausa), dtype=np.int16))
            partes.append(pcm)
        return np.concatenate(partes) if partes else np.zeros(0, dtype=np.int16)

    def _oraciones_audio(self, texto: str) -> Iterator[np.ndarray]:
        texto = preparar_lectura(texto, getattr(self, "pronunciaciones", None))
        if not texto:
            return
        # Compensa la duración en Piper antes de elevar ligeramente la entonación.
        # Así el tono y la velocidad se pueden ajustar por separado.
        tono = 2 ** (max(-3.0, min(3.0, self.tono_semitonos)) / 12)
        duracion = tono / max(0.5, min(2.0, self.velocidad))
        try:  # piper-tts >= 1.3
            from piper import SynthesisConfig
            cfg = SynthesisConfig(length_scale=duracion)
            oraciones = (chunk.audio_int16_bytes for chunk in self.voz.synthesize(texto, syn_config=cfg))
        except ImportError:  # piper-tts 1.2.x
            oraciones = self.voz.synthesize_stream_raw(texto, length_scale=duracion)
        for datos in oraciones:
            pcm = np.frombuffer(datos, dtype=np.int16)
            if not pcm.size:
                continue
            # Algunos finales de Piper ya incluyen más de 150 ms de silencio.
            audible = np.flatnonzero(np.abs(pcm.astype(np.int32)) > 96)
            if audible.size and pcm.size - audible[-1] > 0.15 * self.tasa:
                pcm = pcm[:audible[-1] + int(0.04 * self.tasa) + 1]
            if tono != 1.0:
                pcm = np.interp(np.arange(0, pcm.size, tono), np.arange(pcm.size), pcm).astype(np.int16)
            yield pcm

    # ---------------------------------------------------------------- salida de audio
    def _callback(self, outdata, frames, info, status) -> None:  # hilo de audio: hacer lo mínimo
        out = outdata[:, 0]
        hecho = 0
        with self._lock:
            while hecho < frames and self._trozos:
                trozo = self._trozos[0]
                n = min(frames - hecho, trozo.size - self._offset)
                out[hecho:hecho + n] = trozo[self._offset:self._offset + n]
                hecho += n
                self._offset += n
                if self._offset >= trozo.size:
                    self._trozos.popleft()
                    self._offset = 0
            self._pendientes = max(0, self._pendientes - hecho)
            if hecho:
                self._silencio_salida = 0
            self._silencio_salida += frames - hecho
        if hecho < frames:
            out[hecho:] = 0
        rms = float(np.sqrt(np.mean((out.astype(np.float32) / 32768.0) ** 2)))
        try:  # cuándo sonará de verdad este trozo (para comparar con lo que oye el micrófono)
            retraso = float(info.outputBufferDacTime - info.currentTime)
            retraso = retraso if 0.0 < retraso < 1.0 else 0.15
        except Exception:
            retraso = 0.15
        self._salida.append((time.monotonic() + retraso, rms))
        if self.al_nivel:
            self.al_nivel(min(1.0, rms * 4))

    def nivel_reciente(self) -> float:
        """Volumen que está saliendo por el parlante ahora (para no confundir mi voz con la tuya)."""
        ahora = time.monotonic()
        return max((r for t, r in list(self._salida) if ahora - 0.35 <= t <= ahora + 0.05), default=0.0)

    def _encolar(self, audio: np.ndarray, pausa: np.ndarray | None = None) -> None:
        with self._lock:
            if pausa is not None:
                # El parlante puede haber consumido el trozo anterior mientras Piper
                # preparaba este. Cuenta ese silencio como parte de la pausa elegida.
                restante = pausa if self._pendientes else pausa[self._silencio_salida:]
                if restante.size:
                    self._trozos.append(restante)
                    self._pendientes += restante.size
            self._trozos.append(audio)
            self._pendientes += audio.size

    def _esperar(self, quedan_s: float) -> None:
        """Espera hasta que en la cola quede menos de `quedan_s` segundos (o lo interrumpan)."""
        while not self._detener.is_set():
            with self._lock:
                pendientes = self._pendientes
            if pendientes <= quedan_s * self.tasa:
                return
            time.sleep(0.01)

    def detener(self) -> None:
        """Se calla al instante (lo llaman la interrupción por voz o el clic en la esfera)."""
        self._detener.set()
        with self._lock:
            self._trozos.clear()
            self._offset = 0
            self._pendientes = 0

    # ---------------------------------------------------------------- hablar
    def decir(self, frases: Iterable[str], al_empezar: Callable[[], None] | None = None,
              relleno: np.ndarray | None = None, al_frase: Callable[[str], None] | None = None) -> bool:
        """Reproduce frases en cuanto llegan. Bloquea hasta terminar de hablar.

        Si hay `relleno` ("Mmm, déjame pensar"), suena mientras el modelo sigue generando.
        Devuelve False si lo interrumpieron."""
        # Un productor de un turno interrumpido puede seguir esperando a Ollama.
        # Su señal debe seguir puesta aunque ya haya empezado la respuesta siguiente.
        detener_turno = self._detener = threading.Event()
        cola: queue.Queue = queue.Queue(maxsize=4)

        def poner(item) -> bool:
            while not detener_turno.is_set():
                try:
                    cola.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    pass
            return False

        def productor():
            try:
                for f in frases:
                    if detener_turno.is_set():
                        break
                    for i, audio in enumerate(self._oraciones_audio(f)):
                        if not poner((f if i == 0 else "", audio)):
                            return
            except Exception as e:  # p. ej. Ollama cerrado a mitad de respuesta
                log.warning("No pude generar la respuesta: %s", e)
                texto = "Perdón, se me cortó la idea. Revisa que Ollama esté abierto."
                poner((texto, self.sintetizar(texto)))
            finally:
                poner(None)
                if detener_turno.is_set() and hasattr(frases, "close"):
                    try:
                        frases.close()  # corta la generación del modelo (deja de escribir)
                    except Exception:
                        pass

        threading.Thread(target=productor, daemon=True).start()
        pausa = np.zeros(int(self.tasa * self.pausa), dtype=np.int16)
        empezo = False

        def empezar():
            nonlocal empezo
            if not empezo and al_empezar:
                al_empezar()
            empezo = True

        try:
            flujo = sd.OutputStream(samplerate=self.tasa, channels=1, dtype="int16", latency="high",
                                    callback=self._callback)
            flujo.start()
        except Exception as e:
            log.warning("No pude abrir el parlante como flujo (%s); uso el modo simple.", e)
            return self._decir_simple(cola, empezar, relleno, al_frase)
        try:
            if relleno is not None:
                empezar()
                self._encolar(relleno)
            while not self._detener.is_set():
                try:
                    item = cola.get(timeout=0.05)
                except queue.Empty:
                    continue
                if item is None:
                    break
                texto, audio = item
                self._esperar(0.12)  # que termine (casi) lo anterior: subtítulos al ritmo de la voz
                if self._detener.is_set():
                    break
                intervalo = pausa if empezo else None
                empezar()
                if al_frase and texto:
                    al_frase(texto)
                self._encolar(audio, intervalo)
            self._esperar(0.0)
            if not self._detener.is_set():
                time.sleep(0.2)  # deja salir lo que quedó en el buffer del parlante
        finally:
            try:
                (flujo.abort if self._detener.is_set() else flujo.stop)()
                flujo.close()
            except Exception:
                pass
            if self.al_nivel:
                self.al_nivel(0.0)
        self.interrumpida = self._detener.is_set()
        return not self.interrumpida

    def _decir_simple(self, cola: queue.Queue, empezar, relleno, al_frase) -> bool:
        """Plan B: una reproducción por frase (como antes)."""
        if relleno is not None:
            empezar()
            sd.play(relleno, self.tasa)
            sd.wait()
        while not self._detener.is_set():
            try:
                item = cola.get(timeout=0.05)
            except queue.Empty:
                continue
            if item is None:
                break
            texto, audio = item
            empezar()
            if al_frase and texto:
                al_frase(texto)
            sd.play(audio, self.tasa)
            while sd.get_stream().active and not self._detener.is_set():
                time.sleep(0.02)
            if self._detener.is_set():
                sd.stop()
                break
            time.sleep(self.pausa)
        self.interrumpida = self._detener.is_set()
        return not self.interrumpida
