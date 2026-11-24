"""Lee y opera controles de la ventana activa mediante accesibilidad de Windows."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..texto import normalizar

_PUENTE = Path(__file__).with_name("pantalla_uia.ps1")
_ERROR = {
    "ventana": "Cambió la ventana activa. Vuelve a pedirme la acción en la ventana que quieres usar.",
    "foco": "Cambió el campo activo. Selecciona el campo y vuelve a pedírmelo.",
    "control": "El control cambió o dejó de estar disponible.",
    "inaccesible": "Windows no me dio acceso a ese control.",
    "tiempo": "La aplicación tardó demasiado en responder. Comprueba su estado.",
}
_TIPOS = {"Button": "botón", "Edit": "campo", "Hyperlink": "enlace", "MenuItem": "menú",
          "TabItem": "pestaña", "CheckBox": "casilla", "RadioButton": "opción",
          "ComboBox": "lista", "ListItem": "elemento", "Document": "documento"}


def _ventana():
    if os.name == "nt":
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            return hwnd, win32process.GetWindowThreadProcessId(hwnd)[1]
    return None


def _uia(ventana, operacion, *, puente=_PUENTE, **datos):
    """Solo el código fijo viaja como comando. Los textos se leen como JSON por stdin."""
    hwnd, pid = ventana
    peticion = dict(hwnd=hwnd, pid=pid, operacion=operacion, **datos)
    try:
        proceso = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", puente.read_text(encoding="utf-8")],
            input=json.dumps(peticion, ensure_ascii=True).encode("ascii"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if proceso.returncode == 0:
            return json.loads(proceso.stdout.decode("utf-8-sig"))
    except subprocess.TimeoutExpired:
        return {"error": "tiempo"}
    except (OSError, ValueError):
        pass
    return {"error": "inaccesible"}


def _word(ventana, original=None, texto=""):
    """Solo una selección del documento que ya estaba abierto en ese HWND de Word."""
    import pythoncom
    from win32com.client import GetActiveObject
    pythoncom.CoInitialize()
    try:
        app = GetActiveObject("Word.Application")
        w = app.ActiveWindow
        if _ventana() != ventana or int(w.Hwnd) != ventana[0]:
            return None
        rango = w.Selection.Range.Duplicate
        destino = (str(rango.Document.FullName), rango.StoryType, rango.Start, rango.End)
        if original is not None:
            if original != destino or _ventana() != ventana or int(app.ActiveWindow.Hwnd) != ventana[0]:
                return None
            rango.Collapse(0)  # wdCollapseEnd: conserva el texto que estaba seleccionado
            rango.InsertAfter(texto)
            # Actualizar el cursor es secundario; el texto ya fue insertado.
            try:
                if _ventana() == ventana:
                    rango.Collapse(0)
                    rango.Select()
            except Exception:
                pass
        return destino
    except Exception:
        return None
    finally:
        pythoncom.CoUninitialize()


def _elegir(controles, etiqueta, *, clic=False):
    objetivo = normalizar(etiqueta)
    if not objetivo:
        return None, "Dime la etiqueta del control."
    exactos = [c for c in controles if normalizar(c["nombre"]) == objetivo]
    posibles = exactos or [c for c in controles if f" {objetivo} " in f" {normalizar(c['nombre'])} "]
    if len(posibles) == 1:
        if clic and not exactos:
            return None, f"El control se llama «{posibles[0]['nombre']}». Indícame su nombre completo."
        return posibles[0], ""
    if posibles:
        return None, "Hay varios controles con esa etiqueta. Dime la etiqueta completa o enfoca el campo que necesitas."
    return None, "No encontré un control disponible con esa etiqueta."


class Pantalla:
    def __init__(self):
        self._descrita = None

    def describir(self) -> str:
        ventana = _ventana()
        if not ventana:
            return "No encuentro una ventana activa."
        vista = _uia(ventana, "leer")
        if "error" in vista:
            return _ERROR[vista["error"]]
        self._descrita = (ventana, vista["titulo"], time.monotonic())
        titulo = vista["titulo"][:100] or "Ventana sin título"
        resumen = [titulo]
        foco = vista["foco"]
        if foco and foco["edita"]:
            resumen.append(f"El cursor está en {foco['nombre'][:70] or 'un campo de texto'}")
        etiquetas = {normalizar(c["nombre"]) for c in vista["controles"] if c["click"] or c["edita"]}
        for c in vista["controles"]:
            if c["tipo"] in {"Text", "Document"} and c["nombre"] and c["nombre"] != titulo:
                if normalizar(c["nombre"]) not in etiquetas:
                    resumen.append(c["nombre"][:120])
                    break
        partes = []
        for c in vista["controles"]:
            if c["nombre"] and (c["click"] or c["edita"]):
                entrada = f"{_TIPOS.get(c['tipo'], 'control')} {c['nombre'][:70]}"
                if entrada not in partes:
                    partes.append(entrada)
            if len(partes) == 7:
                break
        if partes:
            resumen.append("Veo " + ", ".join(partes))
        elif len(resumen) == 1:
            resumen.append("No expone controles con etiqueta")
        return ". ".join(resumen) + "."

    def preparar(self, operacion: str, *args: str) -> Callable[[], str] | str:
        """Devuelve una aclaración o una acción sobre el destino capturado y revalidado."""
        ventana = _ventana()
        if not ventana:
            return "No encuentro una ventana activa."
        etiqueta = args[0] if operacion == "clic" and args else ""
        if operacion == "escribir" and len(args) == 2:
            etiqueta = args[1]
        vista = _uia(ventana, "leer", etiqueta=etiqueta, soloFoco=operacion == "escribir" and not etiqueta)
        if "error" in vista:
            return _ERROR[vista["error"]]
        if self._descrita:
            previa, titulo, instante = self._descrita
            self._descrita = None
            if time.monotonic() - instante <= 60 and (previa != ventana or titulo != vista["titulo"]):
                return _ERROR["ventana"]
        controles = vista["controles"]
        boton = None
        foco = False
        texto = ""
        error = ""
        if operacion == "clic" and len(args) == 1:
            control, error = _elegir([c for c in controles if c["click"]], args[0], clic=True)
        elif operacion == "escribir" and len(args) in (1, 2):
            texto = args[0]
            if len(args) == 2 and args[1]:
                control, error = _elegir([c for c in controles if c["edita"]], args[1])
            else:
                foco = True
                control = vista["foco"]
                if not control or not (control["edita"] or control["word"]):
                    error = "Selecciona un campo de texto editable."
        elif operacion == "buscar" and len(args) == 1:
            texto = args[0]
            campos = [c for c in controles if c["edita"] and
                      re.search(r"\b(busca\w*|busqueda|search|find)\b", normalizar(c["nombre"]))]
            if len(campos) != 1:
                return "Indica el campo de búsqueda por su etiqueta con «escribe en»."
            control = campos[0]
            botones = [c for c in controles if c["click"] == "Invoke" and c["padre"] == control["padre"]
                       and normalizar(c["nombre"]) in {"buscar", "search", "iniciar busqueda", "realizar busqueda",
                                                       "buscar con google", "google search"}]
            if len(botones) == 1:
                boton = botones[0]
        else:
            return "Dime qué control quieres usar y qué acción hacer."
        if error:
            return error
        if vista["incompleto"] and not foco:
            return "La ventana contiene demasiados controles. Enfoca el campo o acota la vista."
        if operacion != "clic" and not texto.strip():
            return "Dime el texto que quieres introducir."
        word = operacion == "escribir" and foco and control["word"] and not control["edita"]
        seleccion = _word(ventana) if word else None
        if word and seleccion is None:
            return "No pude identificar la selección en el documento activo de Word."
        titulo = vista["titulo"]

        def ejecutar():
            if _ventana() != ventana:
                return _ERROR["ventana"]
            r = _uia(ventana, "word" if word else operacion, titulo=titulo, control=control,
                     texto=texto, foco=foco, boton=boton)
            if "error" in r:
                return _ERROR[r["error"]]
            if word:
                if _word(ventana, seleccion, texto) is None:
                    return "Cambió la selección de Word o el documento no admite escritura."
                return "Escribí el texto en Word."
            return {"clic": "Activé el control.", "escrito": "Añadí el texto al campo.",
                    "consulta": "Escribí la consulta en el campo de búsqueda.",
                    "buscado": "Envié la búsqueda."}[r["estado"]]

        return ejecutar

    def _ejecutar(self, operacion: str, *args: str) -> str:
        accion = self.preparar(operacion, *args)
        return accion() if callable(accion) else accion

    def clic(self, etiqueta: str) -> str:
        return self._ejecutar("clic", etiqueta)

    def escribir(self, texto: str, etiqueta: str = "") -> str:
        return self._ejecutar("escribir", texto, etiqueta)

    def buscar(self, consulta: str) -> str:
        return self._ejecutar("buscar", consulta)
