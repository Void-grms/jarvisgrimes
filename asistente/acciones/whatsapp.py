"""WhatsApp Web dentro del navegador de Jarvis.

La primera vez hay que vincularlo: Jarvis abre WhatsApp Web y escaneas el código QR con
tu celular (WhatsApp → Dispositivos vinculados). Después la sesión se queda guardada.

Enviar NUNCA es automático: Comandos deja el mensaje escrito como borrador, te pregunta
"¿Lo envío?" y solo con un "sí" presiona Enter.
"""
from __future__ import annotations

import difflib
import logging
import re

from ..texto import normalizar
from .navjarvis import NavegadorJarvis

log = logging.getLogger("whatsapp")
URL = "https://web.whatsapp.com/"
LISTO = "#side, #pane-side"
BUSCADOR = ["#side [contenteditable='true']", "#side input[type='text']", "#side input",
            "[contenteditable='true'][aria-label*='usca' i]", "input[aria-label*='usca' i]",
            "[contenteditable='true'][data-tab='3']"]
REDACTOR = ["#main footer [contenteditable='true']", "footer [contenteditable='true'][role='textbox']",
            "[contenteditable='true'][aria-label*='mensaje' i]", "[contenteditable='true'][aria-label*='message' i]"]

JS_TITULOS = """() => [...document.querySelectorAll('#pane-side span[title]')]
  .map((e, i) => ({i, titulo: (e.getAttribute('title') || '').trim()})).filter(x => x.titulo)"""
JS_TITULO_CHAT = """() => { const h = document.querySelector('#main header'); if (!h) return '';
  const s = h.querySelector('span[title], span[dir="auto"]');
  return ((s && (s.getAttribute('title') || s.innerText)) || h.innerText.split('\\n')[0] || '').trim(); }"""
JS_MENSAJES = """(n) => [...document.querySelectorAll('#main div.copyable-text[data-pre-plain-text]')]
  .slice(-n).map(e => { const t = e.querySelector('span.selectable-text');
    return {pre: e.getAttribute('data-pre-plain-text') || '', out: !!e.closest('.message-out'),
            texto: ((t ? t.innerText : e.innerText) || '').trim()}; })"""
JS_NO_LEIDOS = """() => { const res = [];
  for (const f of document.querySelectorAll('#pane-side [role="listitem"], #pane-side [role="row"]')) {
    const b = [...f.querySelectorAll('[aria-label]')].find(e => /no le[ií]d|unread/i.test(e.getAttribute('aria-label')));
    if (!b) continue;
    const t = f.querySelector('span[title]');
    const n = parseInt((b.innerText || '').trim()) || parseInt(((b.getAttribute('aria-label') || '').match(/\\d+/) || ['1'])[0]);
    res.push({chat: t ? t.getAttribute('title') : '', n});
  } return res; }"""
JS_RESULTADOS = """() => [...document.querySelectorAll('#pane-side [role="listitem"], #pane-side [role="row"]')]
  .map(f => (f.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean)).filter(l => l.length)"""


class NecesitaQR(RuntimeError):
    pass


class NoEncontrado(RuntimeError):
    pass


def mejor_coincidencia(nombre: str, titulos: list[str]) -> int | None:
    q = normalizar(nombre)
    mejor, puntaje = None, 0.0
    for i, t in enumerate(titulos):
        n = normalizar(t)
        if not n:
            continue
        if n == q:
            p = 1.0
        elif all(w in n.split() for w in q.split()):
            p = 0.92
        elif q in n:
            p = 0.85
        else:
            p = difflib.SequenceMatcher(None, q, n).ratio()
        if p > puntaje + 1e-9:
            mejor, puntaje = i, p
    return mejor if puntaje >= 0.55 else None


def _hora_y_quien(pre: str) -> tuple[str, str]:
    m = re.match(r"\[(?P<h>[^,\]]+)[^\]]*\]\s*(?P<q>[^:]+):", pre)
    return (m.group("h").strip(), m.group("q").strip()) if m else ("", "")


class WhatsApp:
    def __init__(self, nav: NavegadorJarvis):
        self.nav = nav

    # ---------- utilidades internas (hilo del navegador) ----------
    def _pagina(self, ctx, esperar_s: int = 0):
        pg = self.nav.pestana(ctx, URL)
        try:
            pg.wait_for_selector(f"{LISTO}, canvas", timeout=60000)
        except Exception:
            pass
        if pg.locator(LISTO).count() == 0:
            if not esperar_s:
                raise NecesitaQR("WhatsApp necesita vincularse con el código QR.")
            pg.bring_to_front()
            pg.wait_for_selector(LISTO, timeout=esperar_s * 1000)
        return pg

    @staticmethod
    def _visible(pg, selectores: list[str]):
        for s in selectores:
            loc = pg.locator(s)
            for i in range(min(loc.count(), 6)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        return None

    def _escribir_busqueda(self, pg, texto: str) -> None:
        caja = self._visible(pg, BUSCADOR)
        if caja is None:
            self.nav.captura_debug(pg, "whatsapp sin buscador")
            raise RuntimeError("No encontré el buscador de WhatsApp.")
        caja.click()
        pg.keyboard.press("Control+A")
        pg.keyboard.press("Backspace")
        pg.keyboard.insert_text(texto)
        pg.wait_for_timeout(2200)

    def _redactor(self, pg):
        r = self._visible(pg, REDACTOR)
        if r is None:
            pg.wait_for_selector(REDACTOR[0], timeout=10000)
            r = self._visible(pg, REDACTOR)
        if r is None:
            self.nav.captura_debug(pg, "whatsapp sin caja de mensaje")
            raise RuntimeError("No encontré la caja para escribir el mensaje.")
        return r

    def _abrir_chat(self, pg, nombre: str) -> str:
        # Si el nombre completo no aparece (tildes, apellidos), se reintenta con la primera palabra.
        # Al final, sin búsqueda: entre tus chats recientes (útil si WhatsApp distingue tildes).
        intentos = list(dict.fromkeys([nombre, nombre.split()[0] if nombre.split() else nombre])) + [""]
        candidatos, idx = [], None
        for consulta in intentos:
            self._escribir_busqueda(pg, consulta)
            candidatos = pg.evaluate(JS_TITULOS)
            idx = mejor_coincidencia(nombre, [c["titulo"] for c in candidatos])
            if idx is not None:
                break
        if idx is None:
            self.nav.captura_debug(pg, f"whatsapp no encontrado {nombre}")
            pg.keyboard.press("Escape")
            raise NoEncontrado(nombre)
        pg.locator("#pane-side span[title]").nth(candidatos[idx]["i"]).click()
        pg.wait_for_timeout(1200)
        self._redactor(pg)
        return pg.evaluate(JS_TITULO_CHAT) or candidatos[idx]["titulo"]

    # ---------- API pública ----------
    def vincular(self, esperar_s: int = 150) -> bool:
        def tarea(ctx):
            self._pagina(ctx, esperar_s=esperar_s)
            return True
        return self.nav.ejecutar(tarea, timeout=esperar_s + 90)

    def preparar_mensaje(self, nombre: str, texto: str) -> str:
        """Abre el chat y deja el mensaje escrito SIN enviarlo. Devuelve el nombre del chat."""
        def tarea(ctx):
            pg = self._pagina(ctx)
            titulo = self._abrir_chat(pg, nombre)
            r = self._redactor(pg)
            r.click()
            pg.keyboard.press("Control+A")
            pg.keyboard.press("Backspace")
            pg.keyboard.insert_text(texto.replace("\n", " "))
            return titulo
        return self.nav.ejecutar(tarea)

    def enviar(self) -> bool:
        def tarea(ctx):
            pg = self._pagina(ctx)
            r = self._redactor(pg)
            if not r.inner_text().strip():
                return False
            r.click()
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1500)
            return not r.inner_text().strip()
        return self.nav.ejecutar(tarea)

    def borrar_borrador(self) -> None:
        def tarea(ctx):
            pg = self._pagina(ctx)
            r = self._redactor(pg)
            r.click()
            pg.keyboard.press("Control+A")
            pg.keyboard.press("Backspace")
        try:
            self.nav.ejecutar(tarea)
        except Exception as e:
            log.warning("No pude borrar el borrador: %s", e)

    def ultimos_mensajes(self, nombre: str, n: int = 5) -> tuple[str, list[dict]]:
        def tarea(ctx):
            pg = self._pagina(ctx)
            titulo = self._abrir_chat(pg, nombre)
            pg.wait_for_timeout(800)
            mensajes = []
            for m in pg.evaluate(JS_MENSAJES, n):
                hora, quien = _hora_y_quien(m["pre"])
                mensajes.append({"quien": "Tú" if m["out"] else (quien or titulo), "hora": hora,
                                 "texto": m["texto"]})
            return titulo, mensajes
        return self.nav.ejecutar(tarea)

    def no_leidos(self) -> list[dict]:
        def tarea(ctx):
            pg = self._pagina(ctx)
            pg.wait_for_timeout(1500)
            return [x for x in pg.evaluate(JS_NO_LEIDOS) if x.get("chat")]
        return self.nav.ejecutar(tarea)

    def buscar(self, consulta: str) -> list[list[str]]:
        def tarea(ctx):
            pg = self._pagina(ctx)
            self._escribir_busqueda(pg, consulta)
            filas = pg.evaluate(JS_RESULTADOS)
            pg.keyboard.press("Escape")
            return filas[:6]
        return self.nav.ejecutar(tarea)
