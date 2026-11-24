"""Motor de diálogo: decide qué responder a cada frase, recuerda preguntas pendientes
("¿sobre qué tema?") y maneja el modo silencio. Lo usan la voz (app.py) y la consola
de texto (para probar sin micrófono)."""
from __future__ import annotations

import difflib
import logging
import queue
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime

from .acciones.pantalla import Pantalla
from .actividad import Actividad
from .acciones import administracion
from .acciones.encargos import Encargos
from .comandos import Comandos, nombre_dictado, pide_nombre, preferencia_nombre
from .conocimiento import Conocimiento
from .correcciones import Correcciones
from .obsidian import MemoriaObsidian
from .rutinas import Rutinas
from .trabajo import TrabajoOffice
from .iniciativa import proponer
from .config import cargar
from .control_pantalla import es_dictado_literal, escritura_libre, procesar_pantalla
from .respuesta import Respuesta
from .router import Router, detectar
from .store import Store
from .sync import Sincronizador
from .texto import confirma_accion, es_no, normalizar

log = logging.getLogger("motor")
REPETIR = re.compile(r"^(?:repite|repitelo|repitemelo|repetir|que dijiste|como dijiste|otra vez|de nuevo|"
                     r"no te escuche|no te entendi|puedes repetir)$")
# "Ya no quiero que...", "no lo envíes", "olvídalo": se cancela lo que estaba en curso. Va ANTES que los
# comandos, para que "ya no quiero que abras YouTube" no termine abriendo YouTube.
CANCELAR = re.compile(
    r"^(?:(?:no|ya|bueno|oye|mejor|pero|ah|eh|ok|okay|jarvis)\s+)*"
    r"(?:ya no(?: quiero| hace falta| es necesario| importa| lo hagas| lo quiero| sigas)?(?: que .*)?"
    r"|no quiero que .*"
    r"|no (?:lo |la |los |las |me |le |te )?(?:hagas|abras|habras|busques|envies|mandes|pongas|cierres|apagues|"
    r"reproduzcas|escribas|crees|llames|agendes|guardes|borres|leas|sigas|respondas|contestes)\b.*"
    r"|no hagas nada|mejor no|olvidalo|olvida eso|dejalo(?: asi)?|deja(?:lo)? asi|deja eso"
    r"|cancela(?:lo)?(?: todo| eso)?|cancelar|no importa|para(?: ya)?|alto|basta(?: ya)?|detente|stop"
    r"|ya basta|ya esta(?: bien)?|suficiente|no sigas)"
    r"(?:\s+(?:por favor|porfa|jarvis|gracias))*$")
# Palabras cortas que Whisper suele escuchar mal ("apágate" → "apvate", "apártate").
CONTROL = {"apagate": "apágate", "callate": "cállate", "detente": "detente", "cancela": "cancela",
           "cancelalo": "cancélalo", "olvidalo": "olvídalo", "dejalo": "déjalo", "silencio": "silencio",
           "duermete": "duérmete", "basta": "basta", "repite": "repite"}


@dataclass
class Pendiente:
    manejar: Callable[[str], Respuesta | None]
    hasta: float
    al_expirar: Callable[[], Respuesta | None] | None = None
    al_cancelar: Callable[[], None] | None = None
    desde_audio: float = field(default_factory=time.perf_counter)
    origen: str = ""


_RELLENO = {"ya", "oye", "bueno", "ok", "okay", "ahora", "porfa", "por", "favor", "pues"}


# Palabras que el reconocimiento escucha mal una y otra vez → la que quisiste decir.
_ARREGLOS = {
    "naveador": "navegador", "nabegador": "navegador", "navegado": "navegador", "naveghador": "navegador",
    "novegador": "navegador", "nagevador": "navegador", "subpende": "suspende", "supende": "suspende",
    "suspénde": "suspende", "suspénda": "suspende", "watsap": "WhatsApp", "guasap": "WhatsApp",
    "wasap": "WhatsApp", "whatsap": "WhatsApp", "yutub": "YouTube", "chatgepete": "ChatGPT",
}
_ARREGLO_RE = re.compile(r"\b(" + "|".join(map(re.escape, _ARREGLOS)) + r")\b", re.IGNORECASE)
# ¿La frase pide hacer algo? (para gastar ~2 s en interpretarla con el modelo solo cuando vale la pena)
_PARECE_ORDEN = re.compile(
    r"\b(?:abr\w*|abiert\w*|pon|ponme|poner|pongas|cierr\w*|cerr\w*|busc\w*|suspend\w*|apag\w*|reinici\w*|"
    r"bloque\w*|minimiz\w*|maximiz\w*|escrib\w*|presion\w*|puls\w*|mand\w*|envi\w*|crea|creame|crear|"
    r"genera\w*|dibuj\w*|haz|hazme|muestr\w*|cambi\w*|reproduc\w*|naveg\w*|nave\w*|chrome|whatsapp|youtube|"
    r"word|excel|powerpoint|ventana|pantalla|computadora|laptop|pestanas?|imagen|documento|volumen|chatgpt|"
    r"gemini|correo|recuerd\w*|alarma|captura|carpeta|archivo|programa|aplicacion|app|web)\b")
_QUEJA = re.compile(r"\b(?:no (?:veo|se (?:abrio|abre|ve)|abriste|hiciste|funciona|paso nada|lo hiciste|"
                    r"lo abriste|aparece)|otra vez|de nuevo|intentalo|vuelve a intentar)\b")


def arreglar_palabras(texto: str) -> str:
    return _ARREGLO_RE.sub(lambda m: _ARREGLOS[m.group(1).lower()], texto)


def parece_orden(texto: str) -> bool:
    n = normalizar(texto)
    return len(n.split()) <= 25 and bool(_PARECE_ORDEN.search(n) or _QUEJA.search(n))


_PREFACIO = re.compile(r"^(?:(?:a ver|mientras tanto|este|oye|eh|ya[ ,]+pero|ya|esto era|"
                       r"s[ií][ ,]+pero|no ver(?=[ ,]+quiero))\b[\s,.]+)+", re.I)
_VERBO = {"abre": "abre", "abra": "abre", "abras": "abre", "buscas": "busca", "busques": "busca",
          "busquen": "busca", "busque": "busca", "busca": "busca", "minimices": "minimiza",
          "minimiza": "minimiza", "maximices": "maximiza", "subas": "sube", "bajes": "baja",
          "sube": "sube", "baja": "baja"}
_PETICION = re.compile(r"^(?:(?:quiero|necesito|quisiera) (?:de )?qu[eé] (?:me )?)?("
                       + "|".join(_VERBO) + r")\b[\s,]*", re.I)
_INCIDENCIA = re.compile(r"^(?:no (?:veo|se abr(?:e|io)|hiciste|funciona|paso nada|te pedi|ha subido)|"
                         r"estoy revisando y no|(?:solo|solamente) ha subido|todavia no (?:lo |la )?"
                         r"(?:abr[ae]s|obr[ae]s))\b")
_BUSQUEDA_GENERAL = re.compile(r"^busca (?:toda (?:la )?informacion(?: relevante)?|"
                               r"mas (?:informacion|detalles)(?: (?:sobre|de) (?:eso|el tema))?|"
                               r"(?:informacion )?sobre (?:eso|el mismo tema))$")


def pedido_directo(texto: str) -> str:
    """Corrige el verbo y el destino local sin modificar contenido dictado ni consultas."""
    candidato = _PREFACIO.sub("", texto.strip())
    m = _PETICION.match(candidato)
    if not m:
        return re.sub(r"^(?:subpende|supende)\b", "suspende", texto, flags=re.I)
    verbo = _VERBO[m[1].lower()]
    resto = candidato[m.end():]
    if verbo == "abre":
        resto = re.sub(r"^de\s+(?=(?:word|world|excel|bloc|blog)\b)", "", resto, flags=re.I)
        resto = arreglar_palabras(resto)
    elif verbo == "busca":
        resto = re.sub(r"^no s[eé][ ,]+(?=c[oó]mo\b)", "", resto, flags=re.I)
    return f"{verbo} {resto}".strip()


def corregir_corto(texto: str) -> str:
    """Si dijiste una sola palabra (o la repetiste: «¡apávate, apávate!») parecida a una orden de
    control, la corrige."""
    palabras = [w for w in normalizar(texto).split() if w not in _RELLENO]
    unicas = list(dict.fromkeys(palabras))
    if len(unicas) != 1 or len(unicas[0]) < 5:
        return texto
    if unicas[0] in CONTROL:
        return CONTROL[unicas[0]] if len(palabras) > 1 else texto
    cerca = difflib.get_close_matches(unicas[0], list(CONTROL), n=1, cutoff=0.72)
    if not cerca:
        return texto
    log.info("Entendí «%s» como «%s»", texto, CONTROL[cerca[0]])
    return CONTROL[cerca[0]]


class Motor:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or cargar()
        self.esc = self.cfg["escucha"]
        self.store = Store()
        self.correcciones = Correcciones(self.store)
        self.nombre = self.store.get_meta("nombre_preferido") or self.cfg["usuario"]["nombre"]
        self.router = Router(self.store, self.cfg["usuario"]["zona_horaria"])
        self.sync = Sincronizador(self.cfg, self.store)
        self.pantalla = Pantalla()
        self.actividad = Actividad()
        self.conocimiento = Conocimiento.desde_config(self.cfg)
        self.encargos = Encargos()
        self.avisos: queue.Queue = queue.Queue()  # str o Respuesta (puede traer una pregunta)
        self.trabajo = TrabajoOffice(self.store, self.avisar, self.conocimiento)
        self.boveda = MemoriaObsidian(self.conocimiento, self.trabajo.proyectos)
        self.rutinas = Rutinas(self.store, self.trabajo.proyectos, office_ocupado=self.trabajo.ocupado)
        self.trabajo.sugerir = self.esc.get("sugerir_pendientes", True)
        self.ultimo_texto = ""
        variantes = [self.esc["palabra_clave"], *self.esc.get("variantes_clave", [])]
        claves = "|".join(re.escape(v) for v in variantes if v)
        self.patron_clave = re.compile(
            rf"^\s*[¡¿]?(?:(?:oye|hola|hey|ey|ok|bueno)[\s,¡!¿?]+)*(?:{claves})\b|"
            rf",\s*(?:{claves})[.!?]*\s*$", re.IGNORECASE)
        self.activo_hasta = 0.0
        self.ultimo_saludo = 0.0
        self.pendiente: Pendiente | None = None
        self.cerebro = None
        self.comandos: Comandos | None = None

    def avisar(self, aviso) -> None:
        """Mensajes que llegan solos (recordatorios, documento listo, '¿lo envío?')."""
        self.avisos.put(self._grabar(aviso if isinstance(aviso, Respuesta) else Respuesta(aviso)))

    def _grabar(self, r: Respuesta) -> Respuesta:
        """Recuerda lo que se dijo, para 'repite'."""
        if isinstance(r.texto, str):
            if r.texto:
                self.ultimo_texto = r.texto
        elif r.texto:
            original = r.texto

            def grabando():
                dicho = []
                try:
                    for frase in original:
                        dicho.append(frase)
                        yield frase
                finally:  # también si lo interrumpen a mitad de frase
                    if dicho:
                        self.ultimo_texto = " ".join(dicho)
                    if hasattr(original, "close"):
                        original.close()
            r.texto = grabando()
        return r

    def preparar(self, cerebro) -> None:
        self.cerebro = cerebro
        self.trabajo.cerebro = cerebro
        self.comandos = Comandos(self.cfg, self.store, self.router, cerebro, self.avisar)
        self.comandos.revisar_encargo = self.encargos.abrir_revision
        self.comandos.iniciar()

    def ejecutar(self, r: Respuesta) -> None:
        """Al responder, ejecuta una sola vez la operación que produce el mensaje hablado."""
        if r.ejecutar:
            operacion, r.ejecutar = r.ejecutar, None
            try:
                r.texto = operacion()
            except Exception:
                log.exception("No se completó la operación en la ventana activa")
                r.texto = "No pude completar la acción en esa ventana."
            self._grabar(r)

    # ---------------------------------------------------------- estado
    def despierto(self) -> bool:
        return time.time() < self.activo_hasta or self.pendiente is not None

    def quitar_clave(self, texto: str) -> tuple[bool, str]:
        m = self.patron_clave.search(texto)
        if not m:
            return False, texto
        if m.start() == 0:
            return True, texto[m.end():].lstrip(" ,.:;!-")
        return True, texto[:m.start()].strip()

    def aplicar(self, r: Respuesta, *, activar: bool = True) -> None:
        """Se llama cuando ya terminó de hablar."""
        if r.dormir:
            self.encargos.limpiar()
            self.activo_hasta = 0
            if self.comandos:
                self.comandos.ultima_busqueda_google = None
            if self.cerebro:
                self.cerebro.olvidar()
        elif activar:
            self.activo_hasta = time.time() + self.esc["minutos_conversacion"] * 60
        if r.siguiente:
            self.pendiente = Pendiente(r.siguiente, time.time() + r.espera_s, r.al_expirar, r.al_cancelar,
                                       origen=r.origen)

    def expirado(self) -> Respuesta | None:
        if self.pendiente and time.time() > self.pendiente.hasta:
            p, self.pendiente = self.pendiente, None
            return p.al_expirar() if p.al_expirar else None
        return None

    # ---------------------------------------------------------- saludo
    def saludo(self, forzar: bool = False) -> Respuesta | None:
        if not forzar and time.time() - self.ultimo_saludo < self.esc["enfriamiento_saludo_min"] * 60:
            self.activo_hasta = time.time() + self.esc["minutos_conversacion"] * 60
            return None
        self.ultimo_saludo = time.time()
        ahora = datetime.now(self.router.tz)
        h = ahora.hour
        saludo = "Buenos días" if 5 <= h < 12 else ("Buenas tardes" if 12 <= h < 19 else "Buenas noches")
        texto = f"{saludo}, {self.nombre}. ¿Qué haremos hoy?"
        hoy = ahora.date().isoformat()
        if (self.esc["resumen_matutino"] and 5 <= h < 12 and self.comandos
                and self.store.get_meta("resumen_dia") != hoy):
            # Primer saludo de la mañana: resumen del día (clima, clases de hoy, correos importantes).
            self.store.set_meta("resumen_dia", hoy)
            hilo = threading.Thread(target=self.sync.sincronizar, daemon=True)
            hilo.start()
            hilo.join(timeout=10)
            texto = f"{self.comandos.resumen_dia(f'{saludo}, {self.nombre}.')} ¿Qué haremos hoy?"
        else:
            self.sync.en_segundo_plano()
            if self.esc["anunciar_resumen"]:
                texto = (f"{saludo}, {self.nombre}. {self.router.resumen_tareas(False)} "
                         f"{self.router.resumen_correos(False)} ¿Qué haremos hoy?")
        return self._grabar(Respuesta(texto, siguiente=lambda t: None,
                                      espera_s=self.esc["espera_respuesta_saludo_s"],
                                      al_expirar=lambda: Respuesta(dormir=True)))

    # ---------------------------------------------------------- entender
    def entender(self, texto: str, forzar: bool = False, *, por_voz: bool = False) -> Respuesta | None:
        """Devuelve qué hacer con la frase, o None si estaba dormido y no lo llamaron."""
        caducada = self.expirado()
        if caducada:
            if caducada.dormir:
                self.aplicar(caducada)
            else:
                self.avisar(caducada)
        original = texto
        llamado, resto = self.quitar_clave(texto)
        if self.comandos and self.comandos.dictando:
            orden = resto if llamado else texto
            r = self.comandos.dictar(orden)
            self.actividad.dictado(original, r.texto)
            return r
        despierto = self.despierto()
        if not (forzar or llamado or despierto):
            return None
        if not despierto and self.cerebro:
            self.cerebro.olvidar()
            if self.comandos:
                self.comandos.ultima_busqueda_google = None
        if llamado:
            texto = resto
        if len(normalizar(texto)) < 2:
            return Respuesta("Dime.")
        literal = es_dictado_literal(texto)
        nombre = None if literal else preferencia_nombre(texto)
        if nombre or (not literal and pide_nombre(texto)):
            if self.pendiente and self.pendiente.al_cancelar:
                self.pendiente.al_cancelar()
            self.pendiente = None
            if nombre:
                return self._grabar(self._guardar_nombre(nombre))
            return self._grabar(Respuesta("¿Cómo quieres que te llame?", siguiente=self._recibir_nombre))
        texto = corregir_corto(texto)
        n = normalizar(texto)
        if n in {"dime que haras", "que vas a hacer"} and self.pendiente:
            return Respuesta(self.ultimo_texto)
        if REPETIR.match(n):
            return Respuesta(self.ultimo_texto or "Todavía no he dicho nada.")
        if CANCELAR.match(n):
            return self._grabar(self.cancelar())
        consulta = self.actividad.consultar(n)
        if consulta is not None:
            return self._grabar(consulta)
        if self.pendiente and self.pendiente.origen and confirma_accion(texto):
            original = self.pendiente.origen
        if literal:
            if self.pendiente and self.pendiente.al_cancelar:
                self.pendiente.al_cancelar()
            self.pendiente = None
            r = self._seguro(lambda: escritura_libre(texto, self.pantalla))
        else:
            r = self._resolver(texto)
        if por_voz and not llamado and r.confirmar_voz and self.cfg["escucha"].get("confirmar_aperturas", False):
            orden = replace(r, confirmar_voz="", interpretacion=r.interpretacion or r.confirmar_voz.strip("¿?"))

            def confirmar(t: str) -> Respuesta | None:
                if confirma_accion(t):
                    return orden
                return Respuesta("De acuerdo.") if es_no(t) else None

            r = Respuesta(r.confirmar_voz, siguiente=confirmar, espera_s=15)
        r.origen = original
        self.actividad.preparar(original, r, orden=texto)
        return self._grabar(r)

    def _recibir_nombre(self, texto: str) -> Respuesta | None:
        if es_no(texto):
            return Respuesta("De acuerdo.")
        nombre = nombre_dictado(texto)
        return self._guardar_nombre(nombre) if nombre else None

    def _guardar_nombre(self, nombre: str) -> Respuesta:
        self.store.set_meta("nombre_preferido", nombre)
        self.nombre = nombre
        if self.cerebro:
            self.cerebro.cambiar_nombre(nombre)
        return Respuesta(f"Te llamaré {nombre}.")

    def cancelar(self) -> Respuesta:
        """Suelta la pregunta pendiente, avisa a las tareas en curso y descarta preguntas por llegar."""
        self.encargos.limpiar()
        detenido = self.trabajo.cancelar()
        p, self.pendiente = self.pendiente, None
        if self.comandos:
            self.comandos.cancelado_ts = time.time()
        quedan = []
        while not self.avisos.empty():
            a = self.avisos.get()
            if not a.siguiente:  # recordatorios y avisos sí se quedan; preguntas ("¿lo envío?") no
                quedan.append(a)
        for a in quedan:
            self.avisos.put(a)
        return Respuesta("Interrumpí el trabajo." if detenido else "Listo, lo dejo." if p else "Listo.",
                         accion=p.al_cancelar if p else None)

    def _resolver(self, texto: str) -> Respuesta:
        r = None
        if self.comandos:
            encargo = self._seguro(lambda: self.encargos.procesar(texto, self.comandos.apps))
            if encargo is not None:
                if self.pendiente and self.pendiente.al_cancelar:
                    self.pendiente.al_cancelar()
                self.pendiente = None
                return encargo
        if self.pendiente:
            p, self.pendiente = self.pendiente, None
            r = self._seguro(lambda: p.manejar(texto))
        if r is not None:
            return r
        if self.comandos:
            r = self._seguro(lambda: self.correcciones.procesar(
                texto, self.comandos.apps, self.comandos.abrir_aplicacion))
            if r is not None:
                return r
        try:
            r = self.rutinas.procesar(texto)
            if r is None:
                r = self.trabajo.procesar(texto)
        except (ValueError, OSError) as e:
            return Respuesta(str(e))
        if r is not None:
            return r
        try:
            r = self.boveda.procesar(texto, self.cerebro)
        except (ValueError, OSError) as e:
            return Respuesta(str(e))
        if r is not None:
            return r
        r = self._seguro(lambda: self.conocimiento.procesar(texto, self.ultimo_texto,
                                                           proyecto=self.boveda.referencia_proyecto))
        if r is not None:
            return r
        texto = pedido_directo(texto)
        n = normalizar(texto)
        r = self._seguro(lambda: administracion.procesar(texto, self.avisar))
        if r is not None:
            return r
        if _BUSQUEDA_GENERAL.fullmatch(n):
            reciente = self.comandos.ultima_busqueda_google if self.comandos else None
            if reciente and time.monotonic() - reciente[1] < 180:
                texto = f"busca en Google {reciente[0]}"
            else:
                return Respuesta("¿Sobre qué tema quieres buscar?", siguiente=self._recibir_tema, espera_s=30)
        incidencia = bool(_INCIDENCIA.match(n))
        if incidencia:
            nivel = self._seguro(lambda: self.comandos.c_nivel(texto)) if self.comandos else None
            return nivel or Respuesta("Entendido. ¿Qué quieres que haga ahora?")
        if r is None and n in {"como me llamo", "cual es mi nombre", "recuerdas mi nombre"}:
            return Respuesta(f"Te llamas {self.nombre}.")
        if r is None and n in {"no", "si", "si si", "gracias"}:
            return Respuesta("De nada." if n == "gracias" else "De acuerdo.")
        if n in {"dime que haras", "que vas a hacer"}:
            return Respuesta("Estoy esperando tu siguiente pedido.")
        if r is None:
            r = self._seguro(lambda: procesar_pantalla(texto, self.pantalla))
        if r is None and self.comandos:
            r = self._seguro(lambda: self.comandos.procesar(texto))
        if r is None:
            r = self._seguro(lambda: escritura_libre(texto, self.pantalla))
        if r is None:
            intencion = detectar(texto)
            if intencion:
                r = self._intencion(intencion, texto)
        if r is None and self.cerebro and re.match(r"^(?:abre|minimiza|maximiza|busca)\b", n):
            r = self._interpretar(texto)
        if r is None:
            r = Respuesta(texto=self._charla(texto), relleno=True)
        return r

    def _recibir_tema(self, texto: str) -> Respuesta | None:
        if es_no(texto):
            return Respuesta("De acuerdo.")
        if not texto.strip() or re.match(r"^(?:abre|busca|minimiza|escribe|guarda|cierra)\b", normalizar(texto)):
            return None
        return self._resolver(f"busca en Google {texto}")

    def _interpretar(self, texto: str) -> Respuesta:
        """Solo propone una reformulación del mismo verbo y con palabras del pedido."""
        aclarar = Respuesta("No entendí el destino. ¿Puedes repetir el pedido?")
        orden = self.cerebro.interpretar(texto)
        if not isinstance(orden, str) or not orden or re.search(r"[<>{}\n]", orden):
            return aclarar
        original, sugerida = normalizar(texto).split(), normalizar(orden).split()
        if (len(sugerida) < 2 or sugerida == original or sugerida[0] != original[0]
                or set(sugerida[1:]) - set(original) - {"el", "la", "las", "los", "en", "de"}):
            return aclarar

        def decidir(t):
            if confirma_accion(t) and self.comandos:
                return self.comandos.procesar(orden) or aclarar
            return Respuesta("De acuerdo.") if es_no(t) else None

        return Respuesta(f"¿Quieres decir «{orden}»?", siguiente=decidir, espera_s=15)

    @staticmethod
    def _seguro(fn) -> Respuesta | None:
        try:
            return fn()
        except Exception as e:
            log.exception("Error atendiendo")
            return Respuesta(f"Algo falló con eso: {str(e)[:100]}")

    def _intencion(self, intencion: str, texto: str) -> Respuesta:
        if intencion == "dormir":
            return Respuesta("Listo, aquí estaré si me llamas.", dormir=True)
        if intencion == "actualizar":
            return Respuesta("Dame unos segundos, reviso todo.", despues=self._actualizar)
        if intencion in {"avance", "pendientes", "tareas"}:
            resumen = self.router.responder(intencion, texto)
            if self.esc.get("sugerir_pendientes", True) and self.cfg["clementina"]["habilitado"]:
                propuesta = proponer(self.router, self.conocimiento.notas_rapidas.parent,
                                     intencion, texto, resumen)
                if propuesta:
                    return propuesta
            if intencion == "avance":
                return Respuesta("¿Sobre qué tarea quieres avanzar?")
            return Respuesta(resumen)
        return Respuesta(self.router.responder(intencion, texto))

    def _actualizar(self) -> None:
        errores = self.sync.sincronizar()
        self.avisar("Listo, actualizado." if not errores else
                    "Actualicé, pero falló " + " y ".join(errores) + ".")

    def _charla(self, texto: str) -> Iterator[str]:
        try:
            n = normalizar(texto)
            contexto = ""
            if self.ultimo_texto and re.search(
                    r"\b(?:eso|esa (?:app|ventana|opcion)|lo anterior|que (?:dijiste|hiciste)|"
                    r"a que te refieres|por que (?:lo|la)|que quisiste decir)\b", n):
                contexto = f"Última respuesta de Jarvis en esta conversación: {self.ultimo_texto[:360]}"
            elif re.search(r"\b(?:hoy|manana|semana|ano|fecha)\b", n):
                contexto = self.router.contexto_breve()
            yield from self.cerebro.frases(texto, contexto)
        except Exception as e:
            log.warning("LLM falló: %s", e)
            yield "No pude consultar el modelo local. Revisa si Ollama está abierto."
