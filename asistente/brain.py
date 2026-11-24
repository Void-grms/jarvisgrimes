"""Cliente de Ollama con streaming por frases (para hablar mientras piensa)."""
from __future__ import annotations

import json
import copy
import logging
import re
import time
from collections.abc import Iterator

import requests

log = logging.getLogger("cerebro")

SISTEMA = (
    "El usuario se llama {nombre} y habla con Jarvis, su asistente local de voz. Responde en "
    "español peruano en una o dos frases breves. Termina cuando hayas dado la respuesta. "
    "Si el pedido es confuso, pregunta por el dato que falta. En este turno solo conversas. "
    "No prometas ni des por hecha ninguna acción en el equipo."
)

# El motor contrasta la propuesta con el pedido y espera confirmación.
INTERPRETE = (
    "Simplifica la petición de voz quitando muletillas. Conserva el verbo y el destino que "
    "dijo la persona. Los verbos admitidos son abre, busca, minimiza y maximiza. Deja orden "
    "vacía si hay negación, queja o duda sobre el destino. Devuelve solo un objeto JSON con la clave orden."
)
_VACIA = {"", "vacia", "vacio", "(vacia)", "ninguna", "none", "null", "nada"}

FIN_FRASE = re.compile(r"""[.!?…]+[»”"')\]]*\s+""")
ABREVIATURA = re.compile(r"\b(?:sr|sra|srta|dr|dra|ud|uds|pág|núm|p|ej|ee|uu)\.$", re.I)
PENSAMIENTO = re.compile(r"<think>.*?(?:</think>|\Z)|</think>", re.S | re.I)
OFRECIMIENTO = re.compile(
    r"(?:en qué (?:más )?(?:puedo ayudarte|te puedo ayudar)|"
    r"hay algo más (?:en (?:lo )?que (?:pueda ayudarte|te pueda ayudar)|que (?:necesites|quieras saber))|"
    r"(?:necesitas|quieres saber) algo más|qué más necesitas saber)(?: hoy| por ahora)?", re.I)


def _oraciones(trozos: Iterator[str]) -> Iterator[str]:
    """Separa frases completas aunque una palabra o etiqueta llegue en varios trozos."""
    generado = visible = ""
    inicio = 0
    for trozo in trozos:
        generado += trozo
        visible = PENSAMIENTO.sub("", generado)
        for fin in FIN_FRASE.finditer(visible, inicio):
            frase = visible[inicio:fin.end()].strip()
            if ABREVIATURA.search(frase):
                continue
            inicio = fin.end()
            if frase:
                yield frase
    if resto := visible[inicio:].strip():
        yield resto


class Cerebro:
    def __init__(self, cfg: dict, nombre: str):
        self.url = cfg["url"].rstrip("/")
        self.modelo = cfg["modelo"]
        self.opciones = {
            "num_ctx": cfg["num_ctx"],
            "num_predict": cfg["max_tokens"],
            "temperature": cfg["temperatura"],
        }
        if cfg.get("hilos"):  # 0 = que Ollama decida
            self.opciones["num_thread"] = cfg["hilos"]
        self.turnos = cfg["turnos_memoria"]
        self.medicion: dict = {}  # tiempos de la última respuesta (para optimizar)
        self.nombre = nombre
        self.sistema = SISTEMA.format(nombre=nombre)
        self.historial: list[dict] = []
        self._usar_think = True
        self._sin_pensar_texto = False

    def precalentar(self) -> None:
        """Carga el modelo en RAM y lo deja residente (evita 5-10 s en la 1.ª respuesta)."""
        for _ in range(60):  # al iniciar Windows, Ollama puede tardar en levantar
            try:
                requests.get(f"{self.url}/api/version", timeout=2)
                break
            except requests.RequestException:
                time.sleep(2)
        try:
            requests.post(f"{self.url}/api/generate",
                          json={"model": self.modelo, "prompt": "", "keep_alive": -1,
                                "options": self._opciones_carga()},
                          timeout=120)
            # Calienta también la caché del prompt de sistema.
            for _ in self.frases("hola", contexto="", recordar=False):
                pass
        except requests.RequestException:
            pass

    def _opciones_carga(self) -> dict:
        """Opciones que obligan a Ollama a recargar el modelo si cambian: siempre las mismas."""
        return {k: v for k, v in self.opciones.items() if k in ("num_ctx", "num_thread")}

    def _payload(self, mensajes: list[dict]) -> dict:
        p = {"model": self.modelo, "messages": mensajes, "stream": True,
             "keep_alive": -1, "options": self.opciones}
        if self._usar_think:
            p["think"] = False  # los Qwen3.x piensan por defecto: eso mata la latencia
        return p

    def frases(self, texto: str, contexto: str, *, recordar: bool = True) -> Iterator[str]:
        """Devuelve la respuesta en trozos hablables, apenas cada trozo está listo."""
        historial = self.historial
        mensajes = [{"role": "system", "content": self.sistema}]
        if recordar and self.turnos > 0:
            mensajes += historial[-2 * self.turnos:]
        contenido = f"[{contexto}]\n{texto}" if contexto else texto
        if self._sin_pensar_texto:
            contenido += " /no_think"  # algunos modelos Qwen ignoran think=false
        mensajes.append({"role": "user", "content": contenido})

        t0 = time.perf_counter()
        r = requests.post(f"{self.url}/api/chat", json=self._payload(mensajes), stream=True, timeout=90)
        final = None
        penso = False

        def trozos() -> Iterator[str]:
            nonlocal final, penso
            for linea in r.iter_lines():
                if not linea:
                    continue
                dato = json.loads(linea)
                msg = dato.get("message", {})
                if msg.get("thinking"):
                    penso = True
                if dato.get("done"):
                    final = dato
                if contenido_modelo := msg.get("content"):
                    yield contenido_modelo
                if final is not None:
                    break

        dichas = []
        vistas = set()
        primera = None
        try:
            if r.status_code == 400 and "think" in r.text.lower():
                r.close()
                self._usar_think = False
                r = requests.post(f"{self.url}/api/chat", json=self._payload(mensajes), stream=True, timeout=90)
            r.raise_for_status()
            for frase in _oraciones(trozos()):
                if dichas and OFRECIMIENTO.fullmatch(frase.strip(" ¿?¡!.…")):
                    break
                clave = tuple(re.findall(r"\w+", frase.casefold()))
                if not clave or clave in vistas:
                    continue
                vistas.add(clave)
                dichas.append(frase)
                if primera is None:
                    primera = time.perf_counter() - t0
                yield frase
                if len(dichas) == 2:
                    break
        finally:
            r.close()
        if final is not None:
            self._registrar(final, primera, penso)
        else:  # Ollama solo envía sus contadores al final, incluso si paramos tras dos frases.
            self.medicion = {"primera": primera}
        if not dichas:
            yield "El modelo local no devolvió una respuesta."
            return
        if recordar and self.turnos > 0:
            # El contexto del equipo solo vale para el turno actual.
            historial.extend([{"role": "user", "content": texto},
                              {"role": "assistant", "content": " ".join(dichas)}])
            if len(historial) > 2 * self.turnos:
                del historial[:-2 * max(1, self.turnos // 2)]

    def _registrar(self, d: dict, primera: float | None, penso: bool) -> None:
        ns = 1e9
        evals, dur = d.get("eval_count", 0), d.get("eval_duration", 0) / ns
        self.medicion = {"primera": primera, "carga": d.get("load_duration", 0) / ns,
                         "prompt_tokens": d.get("prompt_eval_count", 0),
                         "prompt_s": d.get("prompt_eval_duration", 0) / ns,
                         "tokens": evals, "tok_s": evals / dur if dur else 0.0}
        log.info("⏱ modelo: primera frase %.2fs · carga %.2fs · prompt %d tok en %.2fs · %d tok a %.1f tok/s",
                 primera if primera is not None else -1, d.get("load_duration", 0) / ns, d.get("prompt_eval_count", 0),
                 d.get("prompt_eval_duration", 0) / ns, evals, evals / dur if dur else 0)
        if penso and not self._sin_pensar_texto:
            log.warning("El modelo está 'pensando' antes de responder (más lento); activo /no_think")
            self._sin_pensar_texto = True

    def olvidar(self) -> None:
        self.historial = []

    def cambiar_nombre(self, nombre: str) -> None:
        self.nombre = nombre
        self.sistema = SISTEMA.format(nombre=nombre)
        self.olvidar()

    def _corto(self, sistema: str, usuario: str, max_tokens: int, temperatura: float,
               esquema: dict | None = None, timeout: int = 40) -> str:
        """Pedido breve con las mismas opciones de carga que la charla (cambiarlas recargaría el modelo)."""
        payload = {"model": self.modelo, "stream": False, "keep_alive": -1,
                   "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                   "options": {**self._opciones_carga(), "num_predict": max_tokens, "temperature": temperatura}}
        if esquema:
            payload["format"] = esquema
        if self._usar_think:
            payload["think"] = False
        r = requests.post(f"{self.url}/api/chat", json=payload, timeout=timeout)
        if r.status_code == 400 and "think" in r.text.lower():
            self._usar_think = False
            payload.pop("think", None)
            r = requests.post(f"{self.url}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return PENSAMIENTO.sub("", r.json()["message"]["content"]).strip()

    def interpretar(self, texto: str, contexto: str = "") -> str:
        """Frase mal escuchada o indirecta → orden ejecutable ("" si es charla)."""
        t = time.perf_counter()
        usuario = (f"Antes: {contexto}\n" if contexto else "") + f"Frase: «{texto}»"
        esquema = {"type": "object", "properties": {"orden": {"type": "string"}}, "required": ["orden"]}
        try:
            crudo = self._corto(INTERPRETE, usuario, 48, 0.0, esquema, timeout=5)
            orden = str(json.loads(crudo).get("orden", ""))
        except Exception as e:
            log.warning("No pude interpretar «%s»: %s", texto, e)
            return ""
        orden = orden.strip().strip("«»\"'.").strip()
        log.info("⏱ intérprete: %.2fs «%s» → «%s»", time.perf_counter() - t, texto, orden)
        return "" if orden.casefold().strip("() ") in _VACIA else orden

    def redactar(self, pedido: str) -> str:
        """Texto listo para pegar (un correo, un mensaje, un párrafo)."""
        sistema = (f"Redacta en español el texto que pide {self.nombre}, listo para pegar tal cual: sin "
                   "comentarios, sin comillas, sin títulos, con buena ortografía y en menos de 130 palabras.")
        try:
            texto = self._corto(sistema, pedido, 320, 0.6, timeout=120)
        except Exception as e:
            log.warning("No pude redactar: %s", e)
            return ""
        texto = re.sub(r"^(?:aqu[ií] (?:tienes|est[aá])[^:\n]*:|claro[^:\n]*:)\s*", "", texto, flags=re.I)
        return texto.strip().strip('"«»').strip()

    def para_documento(self, cancelacion):
        """Una generación interrumpible con sus propios parámetros de sesión."""
        cerebro = copy.copy(self)
        cerebro._cancelacion = cancelacion
        return cerebro

    def generar_json(self, prompt: str, esquema: dict, sistema: str = "", modelo: str | None = None,
                     max_tokens: int = 1500, timeout: int = 900) -> dict:
        """Respuesta estructurada (Ollama valida el JSON contra el esquema)."""
        mensajes = ([{"role": "system", "content": sistema}] if sistema else []) + \
                   [{"role": "user", "content": prompt}]
        payload = {"model": modelo or self.modelo, "messages": mensajes, "stream": False,
                   "format": esquema, "keep_alive": -1,
                   "options": {**self._opciones_carga(), "num_ctx": max(self.opciones["num_ctx"], 4096),
                               "num_predict": max_tokens, "temperature": 0.7}}
        if self._usar_think:
            payload["think"] = False
        cancelacion = getattr(self, "_cancelacion", None)
        flujo = cancelacion is not None
        payload["stream"] = flujo
        with requests.Session() as sesion:
            if flujo:
                sesion.trust_env = False
            for intento in range(2):
                if flujo and cancelacion.is_set():
                    raise InterruptedError
                with sesion.post(f"{self.url}/api/chat", json=payload, timeout=timeout,
                                 stream=flujo, allow_redirects=not flujo) as r:
                    if intento == 0 and r.status_code == 400 and "think" in payload and "think" in r.text.lower():
                        self._usar_think = False
                        payload.pop("think")
                        continue
                    r.raise_for_status()
                    if not flujo:
                        contenido = r.json()["message"]["content"]
                        break
                    trozos = []
                    for linea in r.iter_lines():
                        if cancelacion.is_set():
                            raise InterruptedError
                        if linea:
                            dato = json.loads(linea)
                            if dato.get("error"):
                                raise ValueError("Ollama no pudo completar la generación.")
                            trozos.append(dato.get("message", {}).get("content", ""))
                    contenido = "".join(trozos)
                    break
        try:
            return json.loads(contenido)
        except json.JSONDecodeError:
            # Si se cortó por max_tokens, intenta rescatar lo que haya.
            m = re.search(r"\{.*\}", contenido, re.S)
            if m:
                return json.loads(m.group(0))
            raise
