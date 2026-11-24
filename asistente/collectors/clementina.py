"""Clementina (UCV Virtual, uvcv.edu.pe) — está hecha sobre Moodle.

Dos modos (config.toml → [clementina] modo):

* "ics" (recomendado): lee el enlace de calendario que Moodle genera en
  Calendario → Exportar → "Obtener URL del calendario". Es una simple descarga,
  no abre navegador, tarda < 1 s y no caduca. Trae clases, evaluaciones y entregas,
  aunque no sabe si ya entregaste algo.
* "sesion": abre tu sesión guardada (navegador en segundo plano) y le pide a
  Moodle la "línea de tiempo", que solo incluye lo que te falta entregar.
  Más preciso, pero la sesión caduca cada cierto tiempo.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from ..config import DATOS
from ..store import Item

PERFIL = DATOS / "navegador-clementina"
BASE = "https://uvcv.edu.pe"
DIAS_ADELANTE = 21


# ---------- utilidades ----------
def curso_corto(nombre: str) -> str:
    """'202602 - DISTANCIA - A - LIMA NORTE - MAESTRÍA EN ... - HERRAMIENTAS DIGITALES ... - A1'
    → 'Herramientas Digitales Para La Práctica Docente'."""
    if not nombre:
        return ""
    partes = [p.strip() for p in nombre.split(" - ") if p.strip()]
    if len(partes) >= 2 and re.fullmatch(r"[A-Z]?\d{0,2}", partes[-1]):
        partes = partes[:-1]  # quita la sección (A1, A2...)
    corto = partes[-1] if partes else nombre
    # Para hablar basta el núcleo: "Herramientas Digitales PARA LA PRÁCTICA DOCENTE" → "Herramientas Digitales"
    corto = re.split(r"\s+(?:PARA|EN LA|EN EL|EN|DE LA|DEL)\s+", corto, maxsplit=1, flags=re.I)[0]
    if corto.isupper():
        menores = {"de", "la", "el", "en", "para", "y", "a", "los", "las", "del", "con", "por"}
        corto = " ".join(w.lower() if i and w.lower() in menores else w.capitalize()
                         for i, w in enumerate(corto.lower().split()))
    return corto[:70]


def clasificar(titulo: str) -> str:
    t = titulo.lower()
    if re.search(r"videoconferencia|sesi[oó]n|clase|tutor[ií]a|reuni[oó]n|webinar", t):
        return "clase"
    if re.search(r"evaluaci[oó]n|examen|sustentaci|prueba|cuestionario|quiz", t) and "turnitin" not in t:
        return "examen"
    return "tarea"


def limpiar_titulo(titulo: str) -> str:
    t = re.sub(r"^(Vencimiento de|Vence|Se cierra|Cierre de|Fecha límite de)\s+", "", titulo.strip(), flags=re.I)
    t = re.sub(r"\s*\([A-Z0-9_\-\s]{2,}\)", "", t)  # quita códigos tipo (IAGPD_A2) o (CPI)
    return re.sub(r"\s+", " ", t).strip()[:110]


# ---------- modo ICS ----------
def _desplegar(texto: str) -> list[str]:
    """RFC 5545: las líneas largas continúan en la siguiente si empiezan con espacio."""
    lineas: list[str] = []
    for l in texto.replace("\r\n", "\n").split("\n"):
        if l.startswith((" ", "\t")) and lineas:
            lineas[-1] += l[1:]
        else:
            lineas.append(l)
    return lineas


def _fecha_ics(valor: str, params: str, tz: ZoneInfo) -> float | None:
    valor = valor.strip()
    try:
        if len(valor) == 8:  # todo el día
            d = datetime.strptime(valor, "%Y%m%d").replace(hour=23, minute=59, tzinfo=tz)
        elif valor.endswith("Z"):
            d = datetime.strptime(valor, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        else:
            m = re.search(r"TZID=([^;:]+)", params)
            zona = ZoneInfo(m.group(1)) if m else tz
            d = datetime.strptime(valor, "%Y%m%dT%H%M%S").replace(tzinfo=zona)
        return d.timestamp()
    except Exception:
        return None


def _desescapar(v: str) -> str:
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parsear_ics(texto: str, tz: ZoneInfo, ahora: float | None = None) -> list[Item]:
    ahora = ahora or time.time()
    hasta = ahora + DIAS_ADELANTE * 86400
    items, ev = [], None
    for linea in _desplegar(texto):
        if linea == "BEGIN:VEVENT":
            ev = {}
            continue
        if linea == "END:VEVENT" and ev is not None:
            inicio = ev.get("DTSTART")
            fin = ev.get("DTEND") or inicio
            titulo = ev.get("SUMMARY", "")
            if inicio and titulo and fin and fin >= ahora - 3600 and inicio <= hasta:
                tipo = clasificar(titulo)
                # Para clases importa cuándo empiezan; para entregas, cuándo vencen.
                cuando = inicio if tipo in ("clase", "examen") else (fin if fin > inicio else inicio)
                items.append(Item(
                    fuente="clementina", id=ev.get("UID", f"{titulo}{inicio}"), tipo=tipo,
                    titulo=limpiar_titulo(titulo), curso=curso_corto(ev.get("CATEGORIES", "")),
                    vence_ts=cuando, detalle=ev.get("DESCRIPTION", "")[:200]))
            ev = None
            continue
        if ev is None or ":" not in linea:
            continue
        clave, valor = linea.split(":", 1)
        nombre, _, params = clave.partition(";")
        if nombre in ("DTSTART", "DTEND"):
            ev[nombre] = _fecha_ics(valor, params, tz)
        elif nombre in ("SUMMARY", "CATEGORIES", "UID", "DESCRIPTION"):
            ev[nombre] = _desescapar(valor)
    return sorted(items, key=lambda i: i.vence_ts or 9e12)


def recolectar_ics(cfg: dict, tz: ZoneInfo) -> list[Item]:
    url = cfg["url_ics"].strip()
    if not url:
        raise ValueError("Falta [clementina] url_ics en config.toml (ver README)")
    r = requests.get(url, timeout=20, headers={"User-Agent": "JarvisLocal/1.0"})
    r.raise_for_status()
    if "BEGIN:VCALENDAR" not in r.text[:500]:
        raise PermissionError("El enlace de calendario ya no es válido. Genera uno nuevo en Clementina.")
    return parsear_ics(r.text, tz)


# ---------- modo sesión (línea de tiempo de Moodle) ----------
def _abrir(p, headless: bool, navegador: str):
    PERFIL.mkdir(parents=True, exist_ok=True)
    kwargs = dict(user_data_dir=str(PERFIL), headless=headless, locale="es-PE")
    try:
        return p.chromium.launch_persistent_context(channel=navegador, **kwargs)
    except Exception:
        return p.chromium.launch_persistent_context(**kwargs)


def conectar_sesion(cfg: dict) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _abrir(p, headless=False, navegador=cfg["navegador"])
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(f"{BASE}/my/")
        input("\nInicia sesión en Clementina en la ventana que se abrió y presiona Enter aquí...")
        ctx.close()
    print("Sesión guardada.")


JS_TIMELINE = """async ([desde, hasta]) => {
  const r = await fetch(M.cfg.wwwroot + '/lib/ajax/service.php?sesskey=' + M.cfg.sesskey +
    '&info=core_calendar_get_action_events_by_timesort', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify([{index: 0, methodname: 'core_calendar_get_action_events_by_timesort',
      args: {limitnum: 50, timesortfrom: desde, timesortto: hasta, limittononsuspendedevents: true}}])
  });
  return await r.json();
}"""


def recolectar_sesion(cfg: dict, tz: ZoneInfo) -> list[Item]:
    from playwright.sync_api import sync_playwright
    ahora = int(time.time())
    with sync_playwright() as p:
        ctx = _abrir(p, headless=True, navegador=cfg["navegador"])
        pagina = ctx.new_page()
        pagina.goto(f"{BASE}/my/", wait_until="domcontentloaded", timeout=45000)
        if "/login" in pagina.url:
            ctx.close()
            raise PermissionError("La sesión de Clementina caducó. Ejecuta: conectar-clementina.bat")
        res = pagina.evaluate(JS_TIMELINE, [ahora - 86400, ahora + DIAS_ADELANTE * 86400])
        ctx.close()
    if not res or res[0].get("error"):
        raise RuntimeError(f"Moodle respondió con error: {res[0].get('exception') if res else res}")
    items = []
    for e in res[0]["data"]["events"]:
        titulo = e.get("activityname") or e.get("name", "")
        items.append(Item(
            fuente="clementina", id=str(e["id"]), tipo=clasificar(e.get("name", "")),
            titulo=limpiar_titulo(titulo), curso=curso_corto((e.get("course") or {}).get("fullname", "")),
            vence_ts=float(e.get("timesort") or e.get("timestart")), url=e.get("url", "")))
    return items


def recolectar(cfg: dict, zona: str = "America/Lima") -> list[Item]:
    tz = ZoneInfo(zona)
    return recolectar_sesion(cfg, tz) if cfg.get("modo") == "sesion" else recolectar_ics(cfg, tz)
