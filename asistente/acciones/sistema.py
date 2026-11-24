"""Acciones de Windows: abrir apps y carpetas, teclas multimedia, bloquear, apagar,
capturas, portapapeles y dictado. En otro sistema operativo las acciones no hacen nada
(sirve para probar la lógica)."""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..config import DATOS
from ..texto import normalizar

log = logging.getLogger("sistema")
ES_WINDOWS = sys.platform == "win32"
SIN_VENTANA = 0x08000000 if ES_WINDOWS else 0  # CREATE_NO_WINDOW: sin parpadeo de consola

# ---------------------------------------------------------------- carpetas
_CARPETAS_GUID = {
    "descargas": ("374DE290-123F-4565-9164-39C4925E467B", "Downloads"),
    "documentos": ("FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents"),
    "escritorio": ("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop"),
    "imagenes": ("33E28130-4E1E-4676-835A-98395C3BC3BB", "Pictures"),
    "videos": ("18989B1D-99B5-455B-841C-AB7C74E4DDFC", "Videos"),
    "musica": ("4BD8D571-6D19-48D3-BE97-422220080E43", "Music"),
}
ALIAS_CARPETAS = {
    "descargas": "descargas", "downloads": "descargas", "documentos": "documentos",
    "mis documentos": "documentos", "escritorio": "escritorio", "imagenes": "imagenes",
    "fotos": "imagenes", "mis fotos": "imagenes", "videos": "videos", "mis videos": "videos",
    "musica": "musica", "mi musica": "musica", "jarvis": "jarvis", "plantillas": "plantillas",
    "capturas": "capturas",
}


def carpeta(nombre: str) -> Path:
    """Ruta real de una carpeta de usuario (respeta OneDrive y carpetas movidas)."""
    if nombre == "jarvis":
        return carpeta("documentos") / "Jarvis"
    if nombre == "plantillas":
        return carpeta("jarvis") / "Plantillas"
    if nombre == "capturas":
        return carpeta("imagenes") / "Capturas Jarvis"
    guid, respaldo = _CARPETAS_GUID[nombre]
    if ES_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            u = uuid.UUID(guid)
            g = GUID(u.time_low, u.time_mid, u.time_hi_version)
            for i, b in enumerate(u.bytes[8:]):
                g.Data4[i] = b
            ruta = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                                          ctypes.byref(ruta)) == 0:
                valor = ruta.value
                ctypes.windll.ole32.CoTaskMemFree(ruta)
                return Path(valor)
        except Exception as e:
            log.warning("No pude leer la carpeta %s: %s", nombre, e)
    return Path.home() / respaldo


def abrir_ruta(ruta: Path | str) -> None:
    if ES_WINDOWS:
        os.startfile(str(ruta))  # noqa: S606 — abre con el programa asociado
    else:
        log.info("[simulado] abrir %s", ruta)


# ---------------------------------------------------------------- apps
ALIAS_APPS = {
    "word": "Word", "microsoft word": "Word", "world": "Word", "worth": "Word", "guord": "Word",
    "guort": "Word", "excel": "Excel", "microsoft excel": "Excel",
    "powerpoint": "PowerPoint", "power point": "PowerPoint", "outlook": "Outlook",
    "bloc de notas": "Bloc de notas", "blog de notas": "Bloc de notas", "notepad": "Bloc de notas",
    "calculadora": "Calculadora", "paint": "Paint", "vs code": "Visual Studio Code",
    "visual studio code": "Visual Studio Code", "visual code": "Visual Studio Code",
    "code": "Visual Studio Code", "terminal": "Terminal", "spotify": "Spotify",
    "discord": "Discord", "zoom": "Zoom", "obs": "OBS Studio", "anydesk": "AnyDesk",
    "league of legends": "League of Legends", "league": "League of Legends", "lol": "League of Legends",
    "riot": "Riot Client", "teams": "Microsoft Teams", "whatsapp": "WhatsApp",
    "camara": "Cámara", "reloj": "Reloj", "fotos": "Fotos", "recortes": "Recortes",
    "herramienta de recortes": "Recortes", "android studio": "Android Studio",
    "postman": "Postman", "docker": "Docker Desktop", "steam": "Steam",
}
ESPECIALES = {
    "configuracion": "ms-settings:", "ajustes": "ms-settings:", "explorador": "explorer",
    "explorador de archivos": "explorer", "archivos": "explorer", "mis archivos": "explorer",
    "administrador de tareas": "taskmgr", "panel de control": "control",
}
ARCHIVO_APPS = DATOS / "apps.json"


class Apps:
    """Índice de las apps del menú Inicio (Get-StartApps): incluye las de la Tienda."""

    def __init__(self):
        self.apps: list[dict] = []
        if ARCHIVO_APPS.exists():
            try:
                self.apps = json.loads(ARCHIVO_APPS.read_text(encoding="utf-8"))
            except Exception:
                self.apps = []

    def actualizar_en_fondo(self) -> None:
        threading.Thread(target=self.actualizar, daemon=True).start()

    def actualizar(self) -> None:
        if not ES_WINDOWS:
            return
        cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                               text=True, encoding="utf-8", timeout=30, creationflags=SIN_VENTANA)
            datos = json.loads(r.stdout or "[]")
            self.apps = [d for d in (datos if isinstance(datos, list) else [datos])
                         if d.get("Name") and d.get("AppID")]
            ARCHIVO_APPS.write_text(json.dumps(self.apps, ensure_ascii=False), encoding="utf-8")
            log.info("Índice de apps: %d", len(self.apps))
        except Exception as e:
            log.warning("No pude listar las apps: %s", e)

    def buscar(self, consulta: str) -> dict | None:
        """Devuelve {'Name', 'AppID'} o {'especial': comando}."""
        q = normalizar(consulta)
        if q in ESPECIALES:
            return {"Name": consulta, "especial": ESPECIALES[q]}
        objetivo = normalizar(ALIAS_APPS.get(q, consulta))
        candidatos = [a for a in self.apps
                      if not any(x in normalizar(a["Name"]) for x in ("desinstal", "uninstall"))]
        nombres = {normalizar(a["Name"]): a for a in candidatos}
        if q in nombres:
            return nombres[q]
        if objetivo in nombres:
            return nombres[objetivo]
        # Algunos accesos incorporan la marca o la versión, p. ej. «Microsoft Word 2016».
        # Una palabra truncada o una semejanza fonética no identifica una aplicación.
        if q not in ALIAS_APPS:
            return None
        patron = rf"(?:microsoft |office |microsoft office )?{re.escape(objetivo)}(?: \d[\d ]*)?"
        coinciden = [a for n, a in nombres.items() if re.fullmatch(patron, n)]
        return coinciden[0] if len(coinciden) == 1 else None

    @staticmethod
    def abrir(app: dict) -> None:
        if not ES_WINDOWS:
            log.info("[simulado] abrir app %s", app)
            return
        if "especial" in app:
            esp = app["especial"]
            if esp.endswith(":"):
                os.startfile(esp)  # noqa: S606
            else:
                subprocess.Popen([esp], creationflags=SIN_VENTANA)
            return
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])


# ---------------------------------------------------------------- teclado
VK = {"vol_mute": 0xAD, "vol_down": 0xAE, "vol_up": 0xAF, "next": 0xB0, "prev": 0xB1,
      "play": 0xB3, "ctrl": 0x11, "v": 0x56, "z": 0x5A, "c": 0x43, "enter": 0x0D}


def tecla(nombre: str, veces: int = 1) -> None:
    if not ES_WINDOWS:
        log.info("[simulado] tecla %s x%d", nombre, veces)
        return
    import ctypes
    u = ctypes.windll.user32
    for _ in range(veces):
        u.keybd_event(VK[nombre], 0, 0, 0)
        u.keybd_event(VK[nombre], 0, 2, 0)
        time.sleep(0.01)


def combinacion(*nombres: str) -> None:
    if not ES_WINDOWS:
        log.info("[simulado] combinación %s", "+".join(nombres))
        return
    import ctypes
    u = ctypes.windll.user32
    for n in nombres:
        u.keybd_event(VK[n], 0, 0, 0)
    for n in reversed(nombres):
        u.keybd_event(VK[n], 0, 2, 0)


def volumen(accion: str, nivel: int | None = None) -> None:
    if accion == "subir":
        tecla("vol_up", 5)        # cada pulsación = 2 %
    elif accion == "bajar":
        tecla("vol_down", 5)
    elif accion == "silenciar":
        tecla("vol_mute")
    elif accion == "nivel" and nivel is not None:
        tecla("vol_down", 50)
        tecla("vol_up", max(0, min(100, nivel)) // 2)


def multimedia(accion: str) -> None:
    tecla({"pausa": "play", "siguiente": "next", "anterior": "prev"}[accion])


# ---------------------------------------------------------------- energía
def _cmd(args: list[str]) -> None:
    if ES_WINDOWS:
        subprocess.Popen(args, creationflags=SIN_VENTANA)
    else:
        log.info("[simulado] %s", " ".join(args))


def bloquear() -> None:
    if ES_WINDOWS:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
    else:
        log.info("[simulado] bloquear")


def apagar(reiniciar: bool = False, segundos: int = 60) -> None:
    _cmd(["shutdown", "/r" if reiniciar else "/s", "/t", str(segundos)])


def cancelar_apagado() -> None:
    _cmd(["shutdown", "/a"])


def suspender() -> None:
    from .ventanas import suspender as suspender_de_verdad  # rundll32 hiberna si la hibernación está activa
    suspender_de_verdad()


# ---------------------------------------------------------------- captura y portapapeles
def captura() -> Path:
    destino = carpeta("capturas")
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / f"captura {datetime.now():%Y-%m-%d %H.%M.%S}.png"
    if ES_WINDOWS:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(ruta)
    else:
        log.info("[simulado] captura %s", ruta)
    return ruta


def copiar(texto: str) -> None:
    try:
        import pyperclip
        pyperclip.copy(texto)
    except Exception as e:
        log.warning("No pude copiar al portapapeles: %s", e)


def escribir(texto: str) -> None:
    """Pega texto donde esté el cursor (respeta tildes y ñ) y restaura el portapapeles."""
    try:
        import pyperclip
        anterior = pyperclip.paste()
    except Exception:
        pyperclip, anterior = None, None
    copiar(texto)
    time.sleep(0.05)
    combinacion("ctrl", "v")
    if pyperclip is not None and anterior is not None:
        time.sleep(0.4)
        try:
            pyperclip.copy(anterior)
        except Exception:
            pass


def copiar_seleccion() -> str:
    """Copia lo que tengas seleccionado en la ventana del frente y lo devuelve."""
    try:
        import pyperclip
    except ImportError:
        return ""
    try:
        anterior = pyperclip.paste()
    except Exception:
        anterior = ""
    marca = "\u2063jarvis\u2063"
    try:
        pyperclip.copy(marca)
        combinacion("ctrl", "c")
        time.sleep(0.35)
        texto = pyperclip.paste()
    except Exception:
        return ""
    finally:
        try:
            pyperclip.copy(anterior)
        except Exception:
            pass
    return "" if texto == marca else texto


def mostrar_en_carpeta(ruta: Path) -> None:
    if ES_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(ruta)])
    else:
        log.info("[simulado] mostrar en carpeta %s", ruta)


# ---------------------------------------------------------------- estado del equipo
NOMBRES_PROCESO = {
    "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave", "opera": "Opera",
    "claude": "Claude", "code": "VS Code", "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint",
    "outlook": "Outlook", "olk": "Outlook", "onenote": "OneNote", "ms-teams": "Teams", "teams": "Teams",
    "discord": "Discord", "spotify": "Spotify", "whatsapp": "WhatsApp", "telegram": "Telegram",
    "zoom": "Zoom", "anydesk": "AnyDesk", "steam": "Steam", "steamwebhelper": "Steam", "obs64": "OBS",
    "leagueclient": "League of Legends", "leagueclientux": "League of Legends",
    "leagueclientuxrender": "League of Legends", "league of legends": "League of Legends",
    "riotclientservices": "Riot Client", "riotclientux": "Riot Client", "riotclientuxrender": "Riot Client",
    "riotclientcrashhandler": "Riot Client", "vgtray": "Riot Vanguard", "vgc": "Riot Vanguard",
    "lghub": "Logitech G HUB", "lghub_agent": "Logitech G HUB", "lghub_system_tray": "Logitech G HUB",
    "lghub_updater": "Logitech G HUB", "ollama": "Ollama (mi cerebro)", "ollama app": "Ollama (mi cerebro)",
    "ollama_llama_server": "Ollama (mi cerebro)", "llama-server": "Ollama (mi cerebro)",
    "python": "Jarvis", "pythonw": "Jarvis", "msmpeng": "el antivirus de Windows",
    "memory compression": "la compresión de memoria de Windows", "explorer": "el Explorador",
    "msedgewebview2": "WebView2 (apps de Windows)", "searchhost": "la búsqueda de Windows",
    "notepad": "el Bloc de notas", "photos": "Fotos", "acrobat": "Acrobat", "acrord32": "Acrobat",
}


def memoria() -> tuple[float, float, int]:
    """(GB en uso, GB totales, % en uso), como lo muestra el Administrador de tareas."""
    gib = 1024 ** 3
    if ES_WINDOWS:
        import ctypes

        class _Estado(ctypes.Structure):
            _fields_ = [("largo", ctypes.c_ulong), ("carga", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("libre", ctypes.c_ulonglong),
                        ("pf_total", ctypes.c_ulonglong), ("pf_libre", ctypes.c_ulonglong),
                        ("v_total", ctypes.c_ulonglong), ("v_libre", ctypes.c_ulonglong),
                        ("v_ext", ctypes.c_ulonglong)]

        e = _Estado()
        e.largo = ctypes.sizeof(_Estado)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(e))
        return (e.total - e.libre) / gib, e.total / gib, int(e.carga)
    info = {}
    with open("/proc/meminfo") as f:
        for linea in f:
            clave, valor = linea.split(":", 1)
            info[clave] = int(valor.split()[0]) * 1024
    total, libre = info["MemTotal"], info.get("MemAvailable", info["MemFree"])
    return (total - libre) / gib, total / gib, round(100 * (total - libre) / total)


def procesos_memoria(n: int = 4, salida: str | None = None) -> list[tuple[str, float]]:
    """Programas que más memoria usan, agrupados por nombre: [(nombre amigable, MB)]."""
    if salida is None:
        if not ES_WINDOWS:
            return []
        salida = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                creationflags=SIN_VENTANA, timeout=10, encoding="mbcs", errors="replace").stdout
    totales: dict[str, float] = {}
    for fila in csv.reader(salida.splitlines()):
        if len(fila) < 5:
            continue
        clave = fila[0].lower().removesuffix(".exe")
        kb = int(re.sub(r"\D", "", fila[-1]) or 0)
        nombre = NOMBRES_PROCESO.get(clave, fila[0].removesuffix(".exe").removesuffix(".EXE"))
        totales[nombre] = totales.get(nombre, 0.0) + kb / 1024
    return sorted(totales.items(), key=lambda x: -x[1])[:n]


def cpu_pct(intervalo: float = 0.5) -> int:
    """Uso del procesador (%) medido durante `intervalo` segundos."""
    if ES_WINDOWS:
        import ctypes

        def leer():
            ocio, nucleo, usuario = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(ocio), ctypes.byref(nucleo), ctypes.byref(usuario))
            return ocio.value, nucleo.value + usuario.value
    else:
        def leer():
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
            return v[3] + v[4], sum(v)
    o1, t1 = leer()
    time.sleep(intervalo)
    o2, t2 = leer()
    return max(0, min(100, round(100 * (1 - (o2 - o1) / max(1, t2 - t1)))))


def bateria() -> tuple[int | None, bool]:
    """(% de batería o None si no hay, ¿está enchufada?)."""
    if not ES_WINDOWS:
        return None, True
    import ctypes

    class _Energia(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("bandera", ctypes.c_ubyte), ("porcentaje", ctypes.c_ubyte),
                    ("ahorro", ctypes.c_ubyte), ("restante", ctypes.c_ulong), ("total", ctypes.c_ulong)]

    e = _Energia()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(e)):
        return None, True
    return (e.porcentaje if e.porcentaje <= 100 else None), e.ac == 1


def gb_hablado(gb: float) -> str:
    if gb < 1:
        return f"{round(gb * 1024 / 10) * 10:.0f} megas"
    return f"{gb:.1f}".replace(".", ",").replace(",0", "") + " gigas"
