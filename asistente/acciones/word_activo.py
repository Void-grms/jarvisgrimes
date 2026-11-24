"""Identidad y contexto de un documento de Office en la ventana de Windows."""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import pantalla, ventanas

log = logging.getLogger("office")
_PROCESOS = {"winword": "word", "powerpnt": "powerpoint"}
_PROGID = {"word": "Word.Application", "powerpoint": "PowerPoint.Application"}
_EXTENSION = {"word": ".docx", "powerpoint": ".pptx"}


class ErrorDocumento(ValueError):
    """La persona puede corregir la situación siguiendo el mensaje."""


class SinOfficeActivo(ErrorDocumento):
    """La ventana al frente pertenece a otra aplicación."""


@dataclass(frozen=True)
class DocumentoActivo:
    ruta: Path
    tipo: str
    hwnd: int
    seleccion: str = ""
    parrafo: str = ""
    diapositiva: int | None = None
    parrafo_anterior: str = ""


@contextmanager
def _com():
    import ctypes
    # Mantener nuestro propio contador. En el hilo que importó pythoncom su CoInitialize
    # puede devolver éxito sin sumar una referencia, incluso dentro de otro bloque COM.
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    ole.CoInitializeEx.restype = ctypes.c_long
    resultado = ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    if resultado < 0 and resultado != -2147417850:  # RPC_E_CHANGED_MODE: COM ya estaba activo
        raise OSError(f"Office no pudo iniciar COM (0x{resultado & 0xffffffff:08x}).")
    try:
        yield
    finally:
        if resultado >= 0:
            ole.CoUninitialize()


def _frente():
    # Incluye diálogos y ventanas propias que la lista de ventanas conmutables excluye.
    identidad = pantalla._ventana()
    if identidad:
        hwnd, pid = identidad
        return hwnd, ventanas._proceso(pid)
    return None


def _hwnd(ventana, tipo):
    if tipo == "word":
        return int(ventana.Hwnd)
    # PowerPoint entrega el HWND de su vista MDI. Windows identifica el marco exterior.
    import win32gui
    hoja = int(ventana.HWND)
    return int(win32gui.GetAncestor(hoja, 2)) if win32gui.IsWindow(hoja) else hoja


def _powerpoint(abrir=False):
    # DocumentWindow.HWND está restringida en la tabla de tipos de PowerPoint y no
    # está disponible por IDispatch. Usar la interfaz tipada que expone Office.
    from comtypes.client import CreateObject, GetActiveObject
    try:
        return GetActiveObject("PowerPoint.Application")
    except OSError:
        if abrir:
            return CreateObject("PowerPoint.Application")
        raise


def _ventana_office(hwnd: int, tipo: str):
    """La tabla COM global puede devolver otra instancia de Word o PowerPoint."""
    import pythoncom
    import pywintypes
    import win32con
    import win32gui
    from win32com.client import Dispatch, GetActiveObject

    if tipo == "powerpoint":
        app = _powerpoint()
        ventana = app.ActiveWindow
        if _hwnd(ventana, tipo) == hwnd:
            return ventana
        candidatas = [v for v in app.Windows if _hwnd(v, tipo) == hwnd]
        if len(candidatas) == 1:
            return candidatas[0]
        raise ErrorDocumento("No pude identificar la presentación que tienes al frente.")

    try:
        ventana = GetActiveObject(_PROGID[tipo]).ActiveWindow
        if _hwnd(ventana, tipo) == hwnd:
            return ventana
    except (pywintypes.com_error, AttributeError):
        pass
    # OBJID_NATIVEOM ofrece el objeto Window/DocumentWindow desde el panel de Office.
    clase = "_WwG"
    hijos = []
    def recoger(hijo, _):
        if win32gui.GetClassName(hijo) == clase and win32gui.IsWindowVisible(hijo):
            hijos.append(hijo)
        return True
    win32gui.EnumChildWindows(hwnd, recoger, None)
    for hijo in hijos:
        try:
            _, resultado = win32gui.SendMessageTimeout(hijo, win32con.WM_GETOBJECT, 0, -16,
                                                       win32con.SMTO_ABORTIFHUNG, 500)
            if resultado:
                objeto = pythoncom.ObjectFromLresult(resultado, pythoncom.IID_IDispatch, 0)
                ventana = Dispatch(objeto)
                if _hwnd(ventana, tipo) == hwnd:
                    return ventana
        except (pywintypes.com_error, pywintypes.error, AttributeError):
            continue
    raise ErrorDocumento("No pude vincular la ventana al documento. Deja el documento al frente y vuelve a pedírmelo.")


def _documento(ventana, tipo):
    return ventana.Document if tipo == "word" else ventana.Presentation


def _ruta_guardada(doc, tipo):
    if not doc.Path or doc.Saved not in (True, -1):
        raise ErrorDocumento("Guarda los cambios del documento y vuelve a pedírmelo.")
    ruta = Path(str(doc.FullName))
    if not ruta.is_absolute() or not ruta.is_file():
        raise ErrorDocumento("Guarda una copia accesible en este equipo, ábrela y vuelve a pedírmelo.")
    if ruta.suffix.lower() != _EXTENSION[tipo]:
        raise ErrorDocumento(f"Guarda una copia en formato {_EXTENSION[tipo]} y déjala al frente.")
    return ruta.resolve(strict=True)


def _texto(rango):
    return str(rango.Text or "").rstrip("\r\x07")


def _contexto_word(ventana):
    """La selección puede estar vacía y el cursor puede estar en el primer párrafo."""
    from pywintypes import com_error
    seleccion = parrafo = anterior = ""
    try:
        rango = ventana.Selection.Range.Duplicate
        if int(rango.Start) != int(rango.End):
            seleccion = _texto(rango)
        actual = rango.Paragraphs.Item(1)
        parrafo = _texto(actual.Range)
        previo = actual.Previous(1)
        if previo is not None:
            anterior = _texto(previo.Range)
    except (AttributeError, com_error, OSError):
        pass
    return seleccion, parrafo, anterior


def _contexto_presentacion(ventana, ruta):
    from pywintypes import com_error
    seleccion, indice = "", None
    try:
        hoja = ventana.View.Slide
        # Una presentación incrustada puede mostrar una diapositiva de otro archivo.
        if Path(str(hoja.Parent.FullName)).resolve() == ruta:
            indice = int(hoja.SlideIndex)
        sel = ventana.Selection
        if sel.Type == 1 and sel.SlideRange.Count != 1:  # ppSelectionSlides
            indice = None
        elif sel.Type == 3 and indice is not None:  # ppSelectionText
            seleccion = _texto(sel.TextRange)
    except (AttributeError, com_error, OSError):
        pass
    return seleccion, indice


def capturar_documento(tipo: str | None = None) -> DocumentoActivo:
    """Lee ruta guardada y selección del HWND en primer plano y revalida ambos extremos."""
    frente = _frente()
    if not frente or not frente[1]:
        raise ErrorDocumento("No pude identificar la ventana activa. Deja el documento al frente y repite el pedido.")
    hwnd, proceso = frente
    programa = _PROCESOS.get(proceso)
    if not programa or (tipo and tipo != programa):
        esperado = {"word": "el documento en Word", "powerpoint": "la presentación en PowerPoint"}
        error = ErrorDocumento if programa else SinOfficeActivo
        raise error(f"Deja {esperado.get(tipo, 'Word o PowerPoint')} al frente y repite el pedido.")
    try:
        with _com():
            ventana = _ventana_office(hwnd, programa)
            ruta = _ruta_guardada(_documento(ventana, programa), programa)
            seleccion = parrafo = anterior = ""
            indice = None
            if programa == "word":
                seleccion, parrafo, anterior = _contexto_word(ventana)
            else:
                seleccion, indice = _contexto_presentacion(ventana, ruta)
            de_nuevo = _ruta_guardada(_documento(ventana, programa), programa)
            if _frente() != frente or _hwnd(ventana, programa) != hwnd or de_nuevo != ruta:
                raise ErrorDocumento("El documento activo cambió. Déjalo al frente y repite el pedido.")
            return DocumentoActivo(ruta, programa, hwnd, seleccion, parrafo, indice, anterior)
    except ErrorDocumento:
        raise
    except Exception as exc:
        raise ErrorDocumento("Office no pudo identificar el documento. Comprueba que se puede editar y vuelve a intentarlo.") from exc


def _abrir_office(ruta, tipo):
    from win32com.client import Dispatch
    app = _powerpoint(abrir=True) if tipo == "powerpoint" else Dispatch(_PROGID[tipo])
    coleccion = app.Documents if tipo == "word" else app.Presentations
    doc = None
    for abierta in coleccion:
        nombre = Path(str(abierta.FullName))
        if nombre.is_absolute() and nombre.resolve() == ruta:
            doc = abierta
            break
    if doc is None and tipo == "word":
        # FileName, ConfirmConversions, ReadOnly, AddToRecentFiles
        doc = coleccion.Open(str(ruta), False, False, False)
    elif doc is None:
        # FileName, ReadOnly, Untitled, WithWindow
        doc = coleccion.Open(str(ruta), False, False, True)
    if Path(str(doc.FullName)).resolve() != ruta:
        return None
    app.Visible = True
    ventana = doc.Windows.Item(1)
    if Path(str(_documento(ventana, tipo).FullName)).resolve() != ruta:
        return None
    ventana.Activate()
    return ventana


def abrir_verificado(ruta: Path, espera: float = 6.0) -> bool:
    """Abre el archivo exacto con Office y confirma su ruta y su ventana en primer plano."""
    ruta = Path(ruta).resolve()
    tipo = next((t for t, ext in _EXTENSION.items() if ext == ruta.suffix.lower()), None)
    if os.name != "nt" or tipo is None or not ruta.is_file():
        return False
    try:
        with _com():
            ventana = _abrir_office(ruta, tipo)
            if ventana is None:
                return False
            hwnd = _hwnd(ventana, tipo)
            ventanas.enfocar(hwnd)
            limite = time.monotonic() + espera
            while True:
                frente = _frente()
                if frente and frente[0] == hwnd and _PROCESOS.get(frente[1]) == tipo:
                    actual = _ventana_office(hwnd, tipo)
                    correcto = Path(str(_documento(actual, tipo).FullName)).resolve() == ruta
                    return correcto and _frente() == frente
                if time.monotonic() >= limite:
                    return False
                time.sleep(0.15)
    except Exception as exc:
        log.info("Office no pudo confirmar la apertura de la copia: %s", type(exc).__name__)
        return False
