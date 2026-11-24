"""Archivos: buscar por nombre en tus carpetas, saber qué documento tienes abierto y
sacar su texto (para resumirlo)."""
from __future__ import annotations

import difflib
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from ..texto import normalizar, numeros_a_digitos
from .sistema import ES_WINDOWS, carpeta

log = logging.getLogger("archivos")

IGNORAR = {"node_modules", ".git", ".venv", "venv", "__pycache__", "site-packages", "appdata",
           "$recycle.bin", ".cache", ".gradle", ".idea", ".vs", "env", "dist-packages", "bower_components"}
TIPOS = {
    "pdf": {".pdf"}, "word": {".docx", ".doc", ".odt", ".rtf"}, "documento": {".docx", ".doc", ".pdf", ".odt", ".rtf", ".txt"},
    "excel": {".xlsx", ".xls", ".csv", ".ods"}, "presentacion": {".pptx", ".ppt", ".odp"},
    "powerpoint": {".pptx", ".ppt"}, "foto": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"},
    "imagen": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp", ".svg"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm"}, "audio": {".mp3", ".wav", ".m4a", ".ogg"},
}
LEGIBLES = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".csv", ".rtf"}


def carpetas_usuario(extra: list[str] | None = None) -> list[Path]:
    rutas = [carpeta(n) for n in ("escritorio", "documentos", "descargas", "imagenes", "videos", "musica")]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            rutas.append(Path(os.environ[var]))
    rutas += [Path(os.path.expandvars(e)) for e in (extra or [])]
    vistas, unicas = set(), []
    for r in rutas:
        try:
            clave = str(r.resolve()).lower()
        except OSError:
            continue
        if r.exists() and clave not in vistas:
            vistas.add(clave)
            unicas.append(r)
    return unicas


class IndiceArchivos:
    def __init__(self, carpetas: list[Path]):
        self.carpetas = carpetas
        self.items: list[tuple[str, str, float, bool]] = []  # (ruta, nombre normalizado, mtime, es_carpeta)
        self.listo = False
        self._lock = threading.Lock()

    def actualizar_en_fondo(self) -> None:
        threading.Thread(target=self.actualizar, daemon=True).start()

    def actualizar(self, limite: int = 400_000, max_seg: float = 90) -> None:
        nuevos, inicio = [], time.time()
        pila = list(self.carpetas)
        while pila and len(nuevos) < limite and time.time() - inicio < max_seg:
            d = pila.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        nombre = e.name
                        if nombre.startswith((".", "~$")):
                            continue
                        try:
                            es_dir = e.is_dir(follow_symlinks=False)
                        except OSError:
                            continue
                        if es_dir:
                            if nombre.lower() in IGNORAR:
                                continue
                            pila.append(e.path)
                        try:
                            mtime = e.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            mtime = 0
                        base = nombre if es_dir else os.path.splitext(nombre)[0]
                        nuevos.append((e.path, numeros_a_digitos(normalizar(base.replace("_", " ").replace("-", " "))),
                                       mtime, es_dir))
            except OSError:
                continue
        with self._lock:
            self.items = nuevos
            self.listo = True
        log.info("Índice de archivos: %d elementos en %.1fs", len(nuevos), time.time() - inicio)

    def buscar(self, consulta: str, tipo: str | None = None, n: int = 5) -> list[Path]:
        q = numeros_a_digitos(normalizar(consulta))
        palabras = [w for w in q.split() if w not in ("el", "la", "los", "las", "de", "del", "mi", "mis", "un", "una")]
        if not palabras:
            return []
        exts = TIPOS.get(tipo or "")
        solo_carpetas = tipo == "carpeta"
        with self._lock:
            items = list(self.items)
        puntuados = []
        for ruta, nombre, mtime, es_dir in items:
            if solo_carpetas != es_dir and (solo_carpetas or tipo):
                continue
            if exts and os.path.splitext(ruta)[1].lower() not in exts:
                continue
            tokens = nombre.split()
            if all(any(t.startswith(w) for t in tokens) for w in palabras):
                # exacto > casi exacto (menos palabras de sobra) > el resto
                p = 2.0 + (1.0 if nombre == " ".join(palabras) else 0.0) + 0.3 * len(palabras) / max(1, len(tokens))
            elif sum(any(t.startswith(w) for t in tokens) for w in palabras) >= max(1, len(palabras) - 1) and len(palabras) > 1:
                p = 1.2
            else:
                r = difflib.SequenceMatcher(None, " ".join(palabras), nombre).ratio()
                if r < 0.72:
                    continue
                p = r
            puntuados.append((p, mtime, ruta))
        puntuados.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [Path(r) for _, _, r in puntuados[:n]]


def lugar_hablado(ruta: Path) -> str:
    """'C:\\Users\\x\\Downloads\\a.pdf' → 'en Descargas'."""
    nombres = {"descargas": "Descargas", "documentos": "Documentos", "escritorio": "el Escritorio",
               "imagenes": "Imágenes", "videos": "Videos", "musica": "Música"}
    for clave, hablado in nombres.items():
        try:
            base = carpeta(clave)
            rel = ruta.parent.relative_to(base)
            return f"en {hablado}" + (f", carpeta {rel.parts[-1]}" if rel.parts else "")
        except (ValueError, KeyError):
            continue
    return f"en la carpeta {ruta.parent.name}"


def fecha_hablada(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    dias = (datetime.now().date() - d.date()).days
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "ayer"
    if dias < 7:
        return f"hace {dias} días"
    return f"el {d.day}/{d.month}/{d.year}"


# ---------------------------------------------------------------- ventana y documento activos
def ventana_activa() -> tuple[str, str]:
    """(título, ruta del programa) de la ventana que tienes al frente."""
    if not ES_WINDOWS:
        return "", ""
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = u.GetForegroundWindow()
    largo = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(largo + 1)
    u.GetWindowTextW(hwnd, buf, largo + 1)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        tam = wintypes.DWORD(1024)
        ruta = ctypes.create_unicode_buffer(1024)
        if k.QueryFullProcessImageNameW(h, 0, ruta, ctypes.byref(tam)):
            exe = ruta.value
        k.CloseHandle(h)
    return buf.value, exe


_OFFICE = {"winword.exe": "Word.Application", "powerpnt.exe": "PowerPoint.Application",
           "excel.exe": "Excel.Application"}


def texto_office_activo(exe: str) -> tuple[str, str] | None:
    """Texto del documento activo en Word/PowerPoint/Excel vía COM (sirve aunque no esté guardado)."""
    prog = _OFFICE.get(Path(exe).name.lower())
    if not prog or not ES_WINDOWS:
        return None
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        app = win32com.client.GetActiveObject(prog)
        if prog.startswith("Word"):
            d = app.ActiveDocument
            return d.Name, d.Content.Text
        if prog.startswith("PowerPoint"):
            p = app.ActivePresentation
            partes = []
            for s in p.Slides:
                for sh in s.Shapes:
                    if sh.HasTextFrame and sh.TextFrame.HasText:
                        partes.append(sh.TextFrame.TextRange.Text)
            return p.Name, "\n".join(partes)
        w = app.ActiveWorkbook
        hoja = w.ActiveSheet
        valores = hoja.UsedRange.Value or ()
        filas = [" | ".join("" if c is None else str(c) for c in (f if isinstance(f, tuple) else (f,)))
                 for f in (valores if isinstance(valores, tuple) else ((valores,),))[:300]]
        return w.Name, "\n".join(filas)
    except Exception as e:
        log.warning("No pude leer el documento de Office: %s", e)
        return None


_NOMBRE_EN_TITULO = re.compile(r"([^\\/:*?\"<>|]+?\.(?:pdf|docx?|pptx?|xlsx?|txt|md|csv|rtf))\b", re.I)


def archivo_desde_titulo(titulo: str, indice: IndiceArchivos | None) -> Path | None:
    m = _NOMBRE_EN_TITULO.search(titulo)
    if not m:
        return None
    nombre = m.group(1).strip()
    # 1) Archivos recientes de Windows (accesos directos en %APPDATA%\Microsoft\Windows\Recent)
    if ES_WINDOWS:
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            recientes = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Recent"
            for lnk in sorted(recientes.glob(f"{nombre}*.lnk"), key=lambda p: p.stat().st_mtime, reverse=True):
                destino = Path(shell.CreateShortCut(str(lnk)).Targetpath)
                if destino.exists():
                    return destino
        except Exception as e:
            log.info("Recientes no disponibles: %s", e)
    # 2) El índice de archivos (coincidencia exacta de nombre, el más reciente)
    if indice:
        exactos = [Path(r) for r, _, _, d in indice.items if not d and Path(r).name.lower() == nombre.lower()]
        if exactos:
            return max(exactos, key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return None


def extraer_texto(ruta: Path, max_chars: int = 40000) -> str:
    ext = ruta.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        partes, total = [], 0
        for pagina in PdfReader(str(ruta)).pages:
            t = pagina.extract_text() or ""
            partes.append(t)
            total += len(t)
            if total > max_chars:
                break
        texto = "\n".join(partes)
    elif ext == ".docx":
        from docx import Document
        d = Document(str(ruta))
        texto = "\n".join(p.text for p in d.paragraphs if p.text.strip())
        for t in d.tables:
            for fila in t.rows:
                texto += "\n" + " | ".join(c.text for c in fila.cells)
    elif ext == ".pptx":
        from pptx import Presentation
        texto = "\n".join(sh.text_frame.text for s in Presentation(str(ruta)).slides
                          for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip())
    elif ext == ".xlsx":
        from openpyxl import load_workbook
        ws = load_workbook(str(ruta), read_only=True, data_only=True).active
        texto = "\n".join(" | ".join("" if c is None else str(c) for c in fila)
                          for fila in ws.iter_rows(max_row=300, values_only=True))
    else:
        texto = ruta.read_text(encoding="utf-8", errors="ignore")
    return re.sub(r"[ \t]+", " ", texto)[:max_chars]
