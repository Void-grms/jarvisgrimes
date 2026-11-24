"""Operaciones administrativas explícitas, con los binarios del sistema y UAC."""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from ..respuesta import Respuesta
from ..texto import normalizar

log = logging.getLogger("administracion")
_CONSOLA = {
    "administrador de dispositivos": "devmgmt.msc",
    "administracion de equipos": "compmgmt.msc",
    "visor de eventos": "eventvwr.msc",
    "servicios": "services.msc",
}
_PERMISOS = {"que permisos tienes", "tienes permisos de administrador", "eres administrador",
             "estas ejecutandote como administrador"}
_AYUDA = {"modo administrador", "activa el modo administrador", "trabaja como administrador",
          "que puedes hacer como administrador", "que tareas puedes hacer como administrador"}
_ARCHIVOS = {"verifica los archivos del sistema", "comprueba los archivos del sistema",
             "verifica la integridad de los archivos de windows"}
_DNS = {"vacia la cache dns", "limpia la cache dns"}


def _sistema() -> Path:
    import win32api
    return Path(win32api.GetSystemDirectory())


def _elevado() -> bool:
    from win32com.shell import shell
    return bool(shell.IsUserAnAdmin())


def _iniciar(nombre: str, ejecutable: Path, argumentos: list[str], esperar: bool) -> str:
    import pythoncom
    import pywintypes
    import win32event
    import win32process
    from win32com.shell import shell, shellcon

    pythoncom.CoInitialize()
    try:
        proceso = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_FLAG_DDEWAIT,
            lpVerb="runas", lpFile=str(ejecutable),
            lpParameters=subprocess.list2cmdline(argumentos), lpDirectory=str(ejecutable.parent),
            nShow=0 if esperar else 1)
    except pywintypes.error as exc:
        if exc.winerror == 1223:
            return "Windows canceló la solicitud de administrador."
        log.warning("Windows denegó %s (código %s)", nombre, exc.winerror)
        return f"Windows no pudo iniciar {nombre}. Código {exc.winerror}."
    finally:
        pythoncom.CoUninitialize()
    handle = proceso.get("hProcess")
    try:
        if not esperar:
            return f"Windows inició {nombre}."
        if not handle:
            return f"Windows recibió la solicitud para {nombre}. No pude verificar el resultado."
        win32event.WaitForSingleObject(handle, win32event.INFINITE)
        codigo = win32process.GetExitCodeProcess(handle)
        if codigo:
            return f"{nombre.capitalize()} terminó con el código {codigo} de Windows."
        if ejecutable.name.lower() == "sfc.exe":
            return "Terminó la verificación de los archivos del sistema. El detalle está en el registro CBS de Windows."
        return "Windows vació la caché DNS."
    finally:
        if handle:
            handle.Close()


def procesar(texto: str, avisar) -> Respuesta | None:
    n = normalizar(texto)
    if n in _AYUDA:
        return Respuesta("Puedo verificar los archivos de Windows, vaciar la caché DNS o abrir una herramienta "
                         "administrativa. Por ejemplo, dime «abre Servicios como administrador».")
    if n in _PERMISOS:
        if os.name != "nt":
            return Respuesta("La administración integrada requiere Windows.")
        return Respuesta("Jarvis se está ejecutando como administrador." if _elevado() else
                         "Trabajo con tu sesión de usuario. Puedo pedir permisos de administrador para tareas concretas.")
    m = re.fullmatch(r"(?:abre|inicia|ejecuta) (?:como administrador )?(?:el |la |los )?(.+?)"
                     r"(?: como administrador)?", n)
    herramienta = m[1] if m and "como administrador" in n else ""
    tarea = re.sub(r" como administrador$", "", n)
    if not (herramienta or tarea in _DNS or tarea in _ARCHIVOS):
        return None
    if herramienta and herramienta not in {*_CONSOLA, "administrador de tareas"}:
        return Respuesta("Puedo abrir Servicios, el Visor de eventos, Administración de equipos y los "
                         "administradores de tareas y dispositivos con permisos de administrador.")
    if os.name != "nt":
        return Respuesta("Esa operación requiere Windows.")
    carpeta = _sistema()
    if herramienta:
        nombre = herramienta
        exe = carpeta / ("mmc.exe" if herramienta in _CONSOLA else "Taskmgr.exe")
        args = [str(carpeta / _CONSOLA[herramienta])] if herramienta in _CONSOLA else []
    elif tarea in _ARCHIVOS:
        nombre, exe, args = "la verificación de archivos", carpeta / "sfc.exe", ["/verifyonly"]
    else:
        nombre, exe, args = "la limpieza de la caché DNS", carpeta / "ipconfig.exe", ["/flushdns"]

    def ejecutar():
        try:
            resultado = _iniciar(nombre, exe, args, esperar=not herramienta)
        except Exception:
            log.exception("Error en la operación administrativa %s", nombre)
            resultado = f"No pude verificar el resultado de {nombre}."
        avisar(resultado)

    return Respuesta(f"Solicito autorización a Windows para {nombre}.", accion=ejecutar)
