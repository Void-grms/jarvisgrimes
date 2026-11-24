"""Control de la computadora como lo harías tú: ventanas, teclado, mouse y energía.

Todo con la API de Windows (ctypes), sin instalar nada. Fuera de Windows las funciones solo
registran lo que harían (así se pueden probar en otro equipo).
"""
from __future__ import annotations

import ctypes
import difflib
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..texto import normalizar

log = logging.getLogger("ventanas")
ES_WINDOWS = os.name == "nt"
SIN_VENTANA = 0x08000000

# Cómo llamas a un programa → nombres de su proceso (sin .exe).
PROGRAMAS: dict[str, tuple[str, ...]] = {
    "navegador": ("chrome", "msedge", "firefox", "brave", "opera"),
    "internet": ("chrome", "msedge", "firefox", "brave", "opera"),
    "chrome": ("chrome",), "google chrome": ("chrome",), "edge": ("msedge",), "microsoft edge": ("msedge",),
    "firefox": ("firefox",), "brave": ("brave",), "opera": ("opera",),
    "word": ("winword",), "excel": ("excel",), "powerpoint": ("powerpnt",), "power point": ("powerpnt",),
    "outlook": ("outlook", "olk"), "onenote": ("onenote",), "teams": ("ms-teams", "teams"),
    "whatsapp": ("whatsapp", "whatsapp.root"), "telegram": ("telegram",), "discord": ("discord",),
    "spotify": ("spotify",), "zoom": ("zoom",), "claude": ("claude",), "anydesk": ("anydesk",),
    "vs code": ("code",), "visual studio code": ("code",), "visual studio": ("devenv",),
    "explorador": ("explorer",), "explorador de archivos": ("explorer",), "mis archivos": ("explorer",),
    "bloc de notas": ("notepad",), "notepad": ("notepad",), "terminal": ("windowsterminal",),
    "calculadora": ("calculatorapp",), "configuracion": ("systemsettings",),
    "league of legends": ("leagueclientux",), "league": ("leagueclientux",), "riot": ("riotclientux",),
    "steam": ("steam",), "obs": ("obs64",), "acrobat": ("acrobat", "acrord32"),
    "administrador de tareas": ("taskmgr",), "paint": ("mspaint",), "fotos": ("photos",),
}
NAVEGADORES = PROGRAMAS["navegador"]

# Teclas por su nombre hablado → código virtual de Windows.
TECLAS = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "mayus": 0x10, "mayuscula": 0x10, "mayusculas": 0x10,
    "alt": 0x12, "win": 0x5B, "windows": 0x5B, "inicio de windows": 0x5B,
    "enter": 0x0D, "intro": 0x0D, "entrar": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "tabulador": 0x09, "espacio": 0x20, "barra espaciadora": 0x20, "borrar": 0x08, "retroceso": 0x08,
    "backspace": 0x08, "suprimir": 0x2E, "supr": 0x2E, "delete": 0x2E, "arriba": 0x26, "abajo": 0x28,
    "izquierda": 0x25, "derecha": 0x27, "inicio": 0x24, "home": 0x24, "fin": 0x23, "end": 0x23,
    "avance de pagina": 0x22, "avpag": 0x22, "retroceso de pagina": 0x21, "repag": 0x21,
    "insertar": 0x2D, "imprimir pantalla": 0x2C, "oemmas": 0xBB, "oemmenos": 0xBD,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
_EXTENDIDAS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B}
_MODIFICADORES = {0x11, 0x10, 0x12, 0x5B}


@dataclass
class Ventana:
    hwnd: int
    titulo: str
    proceso: str
    minimizada: bool = False


# ---------------------------------------------------------------- API de Windows
_api = None


def _windows():
    """user32/kernel32 con tipos correctos (en 64 bits los handles no caben en un int)."""
    global _api
    if _api is None:
        from ctypes import wintypes
        u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        for nombre in ("SetForegroundWindow", "BringWindowToTop", "IsIconic", "IsWindowVisible",
                       "GetWindowTextLengthW", "SetFocus", "IsWindow"):
            getattr(u, nombre).argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        k.GetConsoleWindow.restype = wintypes.HWND
        u.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _api = (u, k)
    return _api


def _proceso(pid: int) -> str:
    from ctypes import wintypes
    _, k = _windows()
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower().removesuffix(".exe")
    finally:
        k.CloseHandle(h)
    return ""


def listar() -> list[Ventana]:
    """Ventanas visibles de aplicaciones, de la más reciente a la más antigua."""
    if not ES_WINDOWS:
        return []
    from ctypes import wintypes
    u, k = _windows()
    dwm = ctypes.WinDLL("dwmapi")
    propio = os.getpid()
    consola = k.GetConsoleWindow()
    ventanas: list[Ventana] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cada(hwnd, _):
        if not hwnd or hwnd == consola or not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        largo = u.GetWindowTextLengthW(hwnd)
        if not largo or u.GetWindowLongW(hwnd, -20) & 0x80:  # sin título o WS_EX_TOOLWINDOW
            return True
        oculta = wintypes.DWORD()
        if dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(oculta), 4) == 0 and oculta.value:
            return True  # apps de la tienda "dormidas" en segundo plano
        buf = ctypes.create_unicode_buffer(largo + 1)
        u.GetWindowTextW(hwnd, buf, largo + 1)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != propio and buf.value != "Program Manager":
            ventanas.append(Ventana(int(hwnd), buf.value, _proceso(pid.value), bool(u.IsIconic(hwnd))))
        return True

    u.EnumWindows(cada, 0)
    return ventanas


def activa() -> Ventana | None:
    if not ES_WINDOWS:
        return None
    u, _ = _windows()
    hwnd = u.GetForegroundWindow()
    return next((v for v in listar() if v.hwnd == hwnd), None)


def buscar(nombre: str, ventanas: list[Ventana] | None = None) -> Ventana | None:
    """La ventana abierta más reciente que corresponde a un programa o a un título."""
    n = normalizar(nombre)
    n = re.sub(r"^(?:el|la|los|las|mi|un|una) ", "", n)
    n = re.sub(r" (?:abierto|abierta|que tengo abierto|que tengo abierta)$", "", n)
    if not n:
        return None
    vs = listar() if ventanas is None else ventanas
    procesos = PROGRAMAS.get(n)
    if procesos:
        return next((v for v in vs if v.proceso in procesos), None)
    for v in vs:  # por título: "la ventana de WhatsApp", "el documento tesis", "YouTube"
        t = normalizar(v.titulo)
        if f" {n} " in f" {t} ":
            return v
    for v in vs:  # por nombre de proceso parecido ("winword" ≈ "word" no; "spotify" ≈ "spotifi" sí)
        if v.proceso and difflib.SequenceMatcher(None, n.replace(" ", ""), v.proceso).ratio() >= 0.8:
            return v
    return None


def de_programa(procesos: tuple[str, ...], ventanas: list[Ventana] | None = None) -> list[Ventana]:
    return [v for v in (listar() if ventanas is None else ventanas) if v.proceso in procesos]


def enfocar(hwnd: int) -> bool:
    """La trae al frente aunque esté minimizada (Windows a veces lo impide: hay que insistir)."""
    if not ES_WINDOWS:
        log.info("[simulado] enfocar %s", hwnd)
        return True
    u, k = _windows()
    u.ShowWindow(hwnd, 9 if u.IsIconic(hwnd) else 5)  # SW_RESTORE / SW_SHOW
    u.keybd_event(0x12, 0, 0, 0)  # Alt: Windows permite cambiar el foco tras "una tecla"
    u.keybd_event(0x12, 0, 2, 0)
    u.BringWindowToTop(hwnd)
    u.SetForegroundWindow(hwnd)
    if u.GetForegroundWindow() != hwnd:
        actual = u.GetForegroundWindow()
        hilo_actual = u.GetWindowThreadProcessId(actual, None)
        propio = k.GetCurrentThreadId()
        u.AttachThreadInput(propio, hilo_actual, True)
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            u.SetFocus(hwnd)
        finally:
            u.AttachThreadInput(propio, hilo_actual, False)
    time.sleep(0.05)
    return u.GetForegroundWindow() == hwnd


def minimizar(hwnd: int) -> None:
    if ES_WINDOWS:
        _windows()[0].ShowWindow(hwnd, 6)
    else:
        log.info("[simulado] minimizar %s", hwnd)


def minimizar_grupo(navegadores: bool = False) -> str:
    """Minimiza una sola vez cada ventana elegible y verifica el estado de los HWND afectados."""
    ventanas = de_programa(NAVEGADORES) if navegadores else listar()
    if not ventanas:
        return "No encontré ventanas del navegador." if navegadores else "No encontré ventanas de aplicaciones."
    pendientes = {v.hwnd for v in ventanas if not v.minimizada}
    if not pendientes:
        return "Las ventanas ya estaban minimizadas."
    objetivo = len(pendientes)
    u, _ = _windows()
    for hwnd in pendientes:
        u.ShowWindowAsync(hwnd, 6)  # SW_MINIMIZE; una app bloqueada no detiene a las demás.
    hasta = time.monotonic() + 1.5
    while pendientes:
        pendientes = {hwnd for hwnd in pendientes if not u.IsIconic(hwnd)}
        if not pendientes or time.monotonic() >= hasta:
            break
        time.sleep(0.05)
    hechas = objetivo - len(pendientes)
    if pendientes:
        return f"Windows confirmó {hechas} de {objetivo} ventanas minimizadas."
    return "Minimicé una ventana." if hechas == 1 else f"Minimicé {hechas} ventanas."


def maximizar(hwnd: int) -> None:
    if ES_WINDOWS:
        _windows()[0].ShowWindow(hwnd, 3)
        enfocar(hwnd)
    else:
        log.info("[simulado] maximizar %s", hwnd)


def cerrar(hwnd: int) -> None:
    """Como la X de la ventana: si hay algo sin guardar, el programa te lo pregunta."""
    if ES_WINDOWS:
        _windows()[0].PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    else:
        log.info("[simulado] cerrar %s", hwnd)


def _coincide(v: Ventana, procesos: tuple[str, ...], t: str) -> bool:
    return bool(procesos and v.proceso in procesos) or bool(t and t in normalizar(v.titulo))


def foto(procesos: tuple[str, ...], titulo: str = "") -> dict[int, str]:
    """{hwnd: título} de las ventanas de esos programas (para saber luego qué cambió)."""
    t = normalizar(titulo)
    return {v.hwnd: v.titulo for v in listar() if _coincide(v, procesos, t)}


def esperar_y_enfocar(procesos: tuple[str, ...], antes: dict[int, str] | None = None,
                      espera: float = 8.0, titulo: str = "", *, exigir_nueva: bool = False) -> bool:
    """Después de abrir algo, espera su ventana (o el cambio de pestaña) y la trae al frente.

    `antes` es {hwnd: título} de esas ventanas antes de abrir. Si nada cambia, al final enfoca la
    más reciente de ese programa."""
    if not ES_WINDOWS:
        return True
    t = normalizar(titulo)
    fin = time.monotonic() + espera
    while time.monotonic() < fin:
        vs = [v for v in listar() if _coincide(v, procesos, t)]
        if antes is None:
            if vs:
                return enfocar(vs[0].hwnd)
        else:
            cambio = [v for v in vs if v.hwnd not in antes or (not exigir_nueva and antes[v.hwnd] != v.titulo)]
            if t:
                cambio = [v for v in cambio if t in normalizar(v.titulo)] or cambio
            if cambio:
                return enfocar(cambio[0].hwnd)
        time.sleep(0.25)
    if exigir_nueva:
        return False
    vs = [v for v in listar() if _coincide(v, procesos, t)]
    return enfocar(vs[0].hwnd) if vs else False


def abrir_y_enfocar(lanzar: Callable[[], None], procesos: tuple[str, ...], titulo: str = "",
                    espera: float = 8.0) -> bool:
    antes = foto(procesos, titulo)
    lanzar()
    return esperar_y_enfocar(procesos, antes, espera, titulo)


def abrir_bloc(nuevo: bool = False) -> str:
    """Activa el Bloc de notas o solicita un documento en blanco en esa aplicación."""
    from pathlib import Path
    procesos = ("notepad",)
    antes = foto(procesos)
    if antes:
        hwnd = next(iter(antes))
        if not enfocar(hwnd):
            return "No pude traer el Bloc de notas al frente."
        if not nuevo:
            return "El Bloc de notas está al frente."
        if _windows()[0].GetForegroundWindow() != hwnd or not teclas("ctrl+n"):
            return "Windows no recibió la orden para abrir un Bloc de notas nuevo."
    else:
        try:
            subprocess.Popen([str(Path(os.environ["SystemRoot"], "System32", "notepad.exe"))])
        except (OSError, KeyError):
            return "No pude iniciar el Bloc de notas de Windows."
    if esperar_y_enfocar(procesos, antes, espera=4, exigir_nueva=True):
        return "Abrí una ventana nueva del Bloc de notas."
    return "Pedí abrir el Bloc de notas. No pude confirmar una ventana nueva."


# ---------------------------------------------------------------- teclado y mouse
def _enviar(eventos: list[tuple[int, int, int, int]]) -> bool:
    """eventos: (tipo 1=teclado 0=mouse, código, banderas[, dato])."""
    from ctypes import wintypes

    class TECLADO(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSE(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", TECLADO), ("mi", MOUSE)]

    class ENTRADA(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    lista = (ENTRADA * len(eventos))()
    for i, ev in enumerate(eventos):
        tipo = ev[0]
        lista[i].type = tipo
        if tipo == 1:
            _, vk, banderas, scan = ev
            lista[i].u.ki = TECLADO(vk, scan, banderas, 0, 0)
        else:
            _, dato, banderas, _ = ev
            lista[i].u.mi = MOUSE(0, 0, dato & 0xFFFFFFFF, banderas, 0, 0)
    return ctypes.WinDLL("user32").SendInput(len(eventos), lista, ctypes.sizeof(ENTRADA)) == len(eventos)


def interpretar_teclas(texto: str) -> list[int] | None:
    """'control más t', 'alt f4', 'flecha abajo', 'enter' → códigos. None si no son teclas."""
    # normalizar elimina '+'. Hay que separar la notación de las tablas antes de aplicarla.
    if "+" in texto:
        partes = [normalizar(p) for p in texto.split("+")]
        partes = [p if p not in ("mas", "menos") else "oem" + p for p in partes]
        return interpretar_teclas(" mas ".join(partes)) if all(partes) else None
    t = normalizar(texto)
    t = re.sub(r"\b(?:la tecla|las teclas|tecla|teclas|la combinacion|el boton de|boton|flecha|la flecha)\b",
               " ", t)
    t = re.sub(r"\s*(?:\+|\bmas\b|\by\b|\bcon\b)\s*", " + ", t).strip(" +")
    partes = [p.strip() for p in t.split(" + ") if p.strip()]
    if len(partes) == 1 and " " in partes[0] and partes[0] not in TECLAS:
        partes = partes[0].split()  # "control c", "alt f4"
    codigos = []
    for p in partes:
        if p in TECLAS:
            codigos.append(TECLAS[p])
        elif re.fullmatch(r"[a-z0-9]", p):
            codigos.append(ord(p.upper()))
        else:
            return None
    return codigos or None


def teclas(combo: str | list[int]) -> bool:
    """Presiona una combinación: teclas('ctrl+t'), teclas('alt f4'), teclas([0x0D])."""
    codigos = interpretar_teclas(combo) if isinstance(combo, str) else combo
    if not codigos:
        return False
    if not ES_WINDOWS:
        log.info("[simulado] teclas %s", combo)
        return True
    mods = [c for c in codigos if c in _MODIFICADORES]
    resto = [c for c in codigos if c not in _MODIFICADORES] or mods[-1:]
    if resto == mods[-1:]:
        mods = mods[:-1]
    ext = lambda c: 1 if c in _EXTENDIDAS else 0  # noqa: E731
    eventos = [(1, c, ext(c), 0) for c in mods]
    for c in resto:
        eventos += [(1, c, ext(c), 0), (1, c, ext(c) | 2, 0)]
    eventos += [(1, c, ext(c) | 2, 0) for c in reversed(mods)]
    return _enviar(eventos)


def rueda(pasos: int) -> None:
    """Desplaza como la rueda del mouse (positivo = arriba) en la ventana bajo el cursor."""
    if not ES_WINDOWS:
        log.info("[simulado] rueda %d", pasos)
        return
    _enviar([(0, 120 * pasos, 0x0800, 0)])


# ---------------------------------------------------------------- energía
def _powershell(comando: str) -> None:
    if ES_WINDOWS:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", comando],
                         creationflags=SIN_VENTANA)
    else:
        log.info("[simulado] powershell %s", comando)


def suspender(hibernar: bool = False) -> None:
    """Suspende de verdad (no hiberna por error como rundll32 cuando la hibernación está activa)."""
    modo = "Hibernate" if hibernar else "Suspend"
    _powershell("Add-Type -AssemblyName System.Windows.Forms; "
                f"[void][System.Windows.Forms.Application]::SetSuspendState('{modo}', $false, $false)")


def apagar_pantalla() -> None:
    if ES_WINDOWS:
        _windows()[0].PostMessageW(0xFFFF, 0x0112, 0xF170, 2)  # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER
    else:
        log.info("[simulado] apagar pantalla")


def cerrar_sesion() -> None:
    if ES_WINDOWS:
        subprocess.Popen(["shutdown", "/l"], creationflags=SIN_VENTANA)
    else:
        log.info("[simulado] cerrar sesión")
