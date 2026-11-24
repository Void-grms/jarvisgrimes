"""Prepara encargos en memoria para las aplicaciones de escritorio ChatGPT y Claude."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..respuesta import Respuesta
from ..texto import Frase
from . import pantalla, sistema, ventanas

log = logging.getLogger("encargos")
_IA = r"(?P<ia>chat ?gpt|chat ge pe te|claude)"
_APLICACION = rf"(?:(?:la )?(?:app|aplicacion)(?: de escritorio)? (?:de )?)?{_IA}(?: de escritorio)?"
_HACER = (r"(?:haz(?:me)?|hacer|haga(?:s)?|cre(?:a|ame|ar|es|e)|prepar(?:a|ame|ar|es|e)|genera(?:me|r)?|"
          r"gener(?:es|e)|redact(?:a|ame|ar|es|e)|escrib(?:e|eme|ir|as|a)|disen(?:a|ar|e)|"
          r"edit(?:a|ar|es|e)|modific(?:a|ar)|modifiqu(?:es|e)|corr(?:ige|igeme|egir|ijas|ija)|"
          r"revis(?:a|ar|es|e)|mejor(?:a|ar|es|e)|reescrib(?:e|ir|as|a)|resum(?:e|ir|as|a)|"
          r"actualiz(?:a|ar)|actualic(?:es|e)|conv(?:iert[ea]s?|ertir)|transform(?:a|ar|es|e))")
_CREACION = re.compile(rf"^{_HACER}\b")
_ARTEFACTO = re.compile(r"\b(?:informe|documento|presentacion|diapositivas|carta|ensayo|resumen|"
                        r"texto|propuesta|archivo|hoja de calculo|tabla|borrador|word|excel|powerpoint)\b")
_PEDIR = re.compile(rf"^(?:(?:usa|uses|usar|utiliza|utilices|utilizar|abre|abras) {_APLICACION})"
                    rf"(?: (?:para (?:que )?|y (?:pidele (?:que )?)?|que )(?P<tarea>.+))?$")
_DELEGAR = re.compile(rf"^(?:pide(?:le)?|encarga(?:le)?|solicita(?:le)?|dile) a {_APLICACION} "
                      r"que (?P<tarea>.+)$")
_CON = re.compile(rf"\b(?:con|usando|utilizando|mediante|en) {_APLICACION}\b")
_NOMBRE_APP = _APLICACION.replace("(?P<ia>", "(?:")
_ALTERNATIVAS = re.compile(rf"\b(?:con|usando|utilizando|mediante|en) {_NOMBRE_APP} "
                           rf"(?:o|y) (?:(?:con|en) )?{_NOMBRE_APP}\b")
_SOLO = re.compile(rf"^(?:mejor )?(?:(?:hazlo|hazla|preparalo|preparala) )?"
                   rf"(?:(?:con|en|usando|usa|utiliza) )?{_APLICACION}$")
_COPIAR = re.compile(r"^(?:copia(?:me)? (?:el |ese |este |tu )?(?:encargo|pedido|prompt|borrador)"
                     r"(?: al portapapeles)?|copia (?:al|en el) portapapeles el encargo)$")
_LEER = re.compile(r"^(?:lee(?:me)?|muestra(?:me)?|repite(?:me)?) (?:el |ese |este )?encargo$")
_PEGAR = re.compile(rf"^(?:pegalo|pega el encargo|pega el pedido) en {_APLICACION}$")
_PUENTE = Path(__file__).with_name("encargo_uia.ps1")
_ENVIO = threading.Lock()
_ERRORES = {
    "ventana": "Cambió la ventana de la aplicación.",
    "campo": "No identifiqué un único campo de mensaje editable.",
    "ocupado": "El campo de mensaje ya contiene un borrador.",
    "borrador": "El campo recibió texto. Revisa el borrador en la aplicación.",
    "incierto": "No pude verificar el estado del envío. Comprueba la conversación antes de repetirlo.",
    "sin_seleccion": "Selecciona el texto de la respuesta en la aplicación y vuelve a intentarlo.",
    "adjunto": "La aplicación ya tiene un archivo pendiente en ese mensaje. Revísalo antes de continuar.",
    "compositor": "No pude comprobar los archivos del cuadro de mensaje. Puedes copiar el encargo y revisarlo en la aplicación.",
}


def _nombre(clave: str) -> str:
    return "Claude" if clave == "claude" else "ChatGPT"


@dataclass(frozen=True)
class Encargo:
    aplicacion: str
    tarea: str

    def instrucciones(self) -> str:
        return (f"{self.tarea}\n\n"
                "Entrega el resultado en el formato solicitado. Usa los datos que te proporcione. "
                "Si necesitas un archivo o un dato que falta, pídemelo.")


class Encargos:
    def __init__(self, abrir_revision=None):
        self.abrir_revision = abrir_revision
        self.limpiar()

    def limpiar(self) -> None:
        self.borrador: Encargo | None = None
        self.tarea_reciente = ""
        self.hora_tarea = 0.0

    def procesar(self, texto: str, apps) -> Respuesta | None:
        """Reconoce la delegación expresa y los seguimientos sobre el último encargo."""
        f = Frase(texto).sin_cortesia()
        if re.match(r"^escribe (?:aqui|literalmente|tal cual|en (?:el|la|este|esta) "
                    r"(?:campo|casilla|cuadro|documento))\b", f.txt):
            self.tarea_reciente = ""
            return None
        if _COPIAR.fullmatch(f.txt):
            pedido = self.borrador
            if pedido is None:
                return Respuesta("Todavía no hay un encargo preparado.")
            return Respuesta(ejecutar=lambda: self._copiar(pedido))
        if _LEER.fullmatch(f.txt):
            return Respuesta(self.borrador.instrucciones() if self.borrador
                             else "Todavía no hay un encargo preparado.")
        pegar = _PEGAR.fullmatch(f.txt)
        if pegar:
            pedido = self.borrador
            destino = _nombre(pegar["ia"])
            if pedido is None or pedido.aplicacion != destino:
                return Respuesta(f"Todavía no hay un encargo preparado para {destino}.")
            if self.abrir_revision is not None:
                return self._preparar(destino, pedido.tarea, apps)
            return Respuesta(ejecutar=lambda: _operar(destino, pedido.instrucciones(), "pegar", apps)[1])

        m = _DELEGAR.fullmatch(f.txt) or _PEDIR.fullmatch(f.txt)
        if m and m["tarea"] and _CREACION.match(m["tarea"]):
            segundo = _CON.search(m["tarea"])
            if segundo and _nombre(segundo["ia"]) != _nombre(m["ia"]):
                return Respuesta("Mencionaste dos aplicaciones. Repite el encargo con el destino que prefieras.")
            return self._preparar(_nombre(m["ia"]), f.grupo(m, "tarea"), apps)
        solo = _SOLO.fullmatch(f.txt)
        if solo and self.tarea_reciente and time.monotonic() - self.hora_tarea <= 120:
            return self._preparar(_nombre(solo["ia"]), self.tarea_reciente, apps)
        if _CREACION.match(f.txt):
            proveedores = list(_CON.finditer(f.txt))
            alternativas = _ALTERNATIVAS.search(f.txt)
            if alternativas:
                self.tarea_reciente = f.sin_span(*alternativas.span()).texto.strip(" .,;:")
                self.hora_tarea = time.monotonic()
                return Respuesta("¿Con qué aplicación lo preparo, ChatGPT o Claude?",
                                 siguiente=lambda t: self._elegir(t, apps))
            if len(proveedores) == 1:
                proveedor = proveedores[0]
                antes = f.txt[:proveedor.start()].rstrip()
                if re.search(r"\b(?:no|nunca|evita|sin|trabajar|comparar|hablar|interactuar)$", antes):
                    return None
                if re.search(r"\b(?:comparacion|comparar?|comparando|comparativa|diferencias?)\b", antes):
                    return None
                pedido = f.sin_span(*proveedor.span()).texto.strip(" .,;:")
                return self._preparar(_nombre(proveedor["ia"]), pedido, apps)
            if len(proveedores) > 1:
                return Respuesta("¿Con qué aplicación quieres preparar el encargo?")
            if _ARTEFACTO.search(f.txt):
                self.tarea_reciente, self.hora_tarea = f.texto, time.monotonic()
                return None
        # Una referencia corta se refiere al pedido inmediatamente anterior.
        self.tarea_reciente = ""
        return None

    def _elegir(self, texto, apps):
        opcion = _SOLO.fullmatch(Frase(texto).sin_cortesia().txt)
        if opcion and self.tarea_reciente:
            return self._preparar(_nombre(opcion["ia"]), self.tarea_reciente, apps)
        return None

    def _preparar(self, nombre: str, tarea: str, apps) -> Respuesta:
        pedido = Encargo(nombre, tarea)

        def abrir():
            self.borrador = pedido
            self.tarea_reciente, self.hora_tarea = tarea, time.monotonic()
            if self.abrir_revision is not None:
                self.abrir_revision(nombre, tarea)
                return f"Revisa el encargo para {nombre} en el panel."
            app = apps.buscar(nombre)
            ayuda = "Di «copia el encargo» para llevarlo al portapapeles."
            if app is None:
                return f"Encargo preparado. No encontré {nombre} en el menú Inicio. {ayuda}"
            try:
                al_frente = ventanas.abrir_y_enfocar(lambda: apps.abrir(app), (nombre.lower(),), espera=5)
            except OSError:
                log.exception("Windows no pudo abrir %s", nombre)
                al_frente = False
            if al_frente:
                return f"Encargo preparado. {nombre} está al frente. Di «pégalo en {nombre}» para revisarlo."
            return f"Encargo preparado. No pude comprobar que {nombre} esté al frente. {ayuda}"

        confirmar = "" if self.abrir_revision is not None else f"¿Preparo el encargo y abro {nombre}?"
        return Respuesta(ejecutar=abrir, confirmar_voz=confirmar)

    @staticmethod
    def _copiar(pedido: Encargo) -> str:
        import pyperclip
        try:
            pyperclip.copy(pedido.instrucciones())
        except (OSError, RuntimeError, pyperclip.PyperclipException):
            return "No pude copiar el encargo. Sigue disponible para volver a intentarlo."
        return f"Encargo copiado. Puedes revisarlo y pegarlo en {pedido.aplicacion}."


def _operar(destino: str, texto: str, operacion: str, apps=None) -> tuple[bool, str]:
    if destino not in {"Claude", "ChatGPT"} or operacion not in {"enviar", "pegar", "seleccion"}:
        return False, "Elige la aplicación de escritorio ChatGPT o Claude."
    if not texto.strip() and operacion != "seleccion":
        return False, "El encargo está vacío."
    apps = apps if apps is not None else sistema.Apps()
    app = apps.buscar(destino)
    if not app:
        return False, f"No encontré {destino} en el menú Inicio."
    proceso = destino.lower()
    actual = ventanas.activa()
    if not actual or actual.proceso != proceso:
        visibles = ventanas.de_programa((proceso,))
        if len(visibles) > 1:
            return False, f"Hay varias ventanas de {destino}. Deja abierta la conversación que quieres usar."
        if not visibles and operacion == "seleccion":
            return False, f"Selecciona la respuesta en {destino} antes de importarla."
        try:
            enfocada = (ventanas.enfocar(visibles[0].hwnd) if visibles else
                        ventanas.abrir_y_enfocar(lambda: apps.abrir(app), (proceso,), espera=6))
        except OSError:
            enfocada = False
        actual = ventanas.activa() if enfocada else None
    identidad = pantalla._ventana()
    if not actual or actual.proceso != proceso or not identidad or identidad[0] != actual.hwnd:
        return False, f"No pude comprobar que {destino} quedara al frente."
    respuesta = pantalla._uia(identidad, operacion, puente=_PUENTE, proceso=proceso, texto=texto)
    if operacion == "seleccion" and respuesta.get("texto"):
        return True, respuesta["texto"]
    if operacion == "pegar" and respuesta.get("estado") == "pegado":
        return True, f"Dejé el encargo en el campo de mensaje de {destino}."
    if operacion == "enviar" and respuesta.get("estado") == "enviado":
        return True, f"Activé Enviar en {destino}."
    if operacion == "enviar" and respuesta.get("error") == "tiempo":
        return False, _ERRORES["incierto"]
    motivo = _ERRORES.get(respuesta.get("error"), "Windows no pudo verificar la operación.")
    return False, motivo + (" Puedes decir «copia el encargo»." if operacion == "pegar" else "")


def enviar_revisado(destino: str, prompt: str) -> tuple[bool, str]:
    """El panel lo llama exclusivamente al autorizar el texto exacto mostrado en su vista previa."""
    if not _ENVIO.acquire(blocking=False):
        return False, "Hay un envío en curso."
    try:
        return _operar(destino, prompt, "enviar")
    finally:
        _ENVIO.release()


def leer_seleccion(destino: str) -> tuple[bool, str]:
    """Lee una sola selección de la app indicada, por accesibilidad y sin usar el portapapeles."""
    return _operar(destino, "", "seleccion")
