"""Entrar a tus plataformas en el navegador de Jarvis.

Jarvis abre la página, espera a que Chrome autocomplete tu usuario y contraseña
(guardados en el perfil de Jarvis) y presiona "Entrar". Si falta el usuario, te lo pide
por voz y lo escribe; la contraseña NUNCA la escucha ni la escribe: si falta, te pide que
la escribas tú en la ventana y él presiona Entrar en cuanto la detecta.
"""
from __future__ import annotations

import logging
import re
import time

from .navjarvis import NavegadorJarvis

log = logging.getLogger("sesiones")

JS_ESTADO = r"""() => {
  const vis = e => { if (!e) return false; const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const auto = e => { for (const p of [':autofill', ':-webkit-autofill']) { try { if (e.matches(p)) return true; } catch (x) {} } return false; };
  const lleno = e => !!e && ((e.value || '').length > 0 || auto(e));
  document.querySelectorAll('[data-jarvis]').forEach(b => b.removeAttribute('data-jarvis'));
  const texto = (document.body && document.body.innerText || '').toLowerCase();
  const botones = sel => [...document.querySelectorAll(sel)].filter(vis);
  const marcar = (b, v) => { if (b) b.setAttribute('data-jarvis', v); return !!b; };
  // "¿Quiere mantener la sesión iniciada?" (Microsoft) → Sí
  if (/mantener la sesi[oó]n iniciada|stay signed in/.test(texto)) {
    const si = botones('button, input[type=submit], input[type=button]').find(b => /^(s[ií]|yes)$/i.test((b.innerText || b.value || '').trim()));
    if (marcar(si, 'boton')) return {kmsi: true, login: true, usuario_ok: true, clave_ok: true, boton: true};
  }
  const pass = [...document.querySelectorAll('input[type=password]')].filter(vis)[0] || null;
  const todos = [...document.querySelectorAll('input')];
  const candidatos = todos.filter(vis).filter(e => /^(email|text|tel|)$/.test((e.getAttribute('type') || '').toLowerCase()))
                         .filter(e => !/search|buscar|captcha|query/i.test((e.name || '') + (e.id || '') + (e.placeholder || '')));
  let user = null;
  if (pass) user = candidatos.filter(e => todos.indexOf(e) < todos.indexOf(pass)).pop() || null;
  else user = candidatos.find(e => /user|usuario|email|correo|login|identifier|loginfmt|username|dni|codigo/i.test(
                 (e.name || '') + (e.id || '') + (e.getAttribute('type') || '') + (e.autocomplete || '') + (e.placeholder || ''))) || null;
  if (user) marcar(user, 'usuario');
  if (pass) marcar(pass, 'clave');
  const raiz = (pass || user) ? ((pass || user).form || document) : document;
  const lista = [...raiz.querySelectorAll('button, input[type=submit], input[type=button], a[role=button], div[role=button]')].filter(vis);
  const re = /^(entrar|ingresar|iniciar sesi[oó]n|acceder|log ?in|sign ?in|siguiente|next|continuar|continue|aceptar|enviar|ingresa|acceso)$/i;
  const boton = lista.find(b => re.test((b.innerText || b.value || '').trim())) ||
                lista.find(b => (b.getAttribute('type') || '').toLowerCase() === 'submit');
  marcar(boton, 'boton');
  return {kmsi: false, login: !!(pass || user), hay_usuario: !!user, hay_clave: !!pass,
          usuario_ok: !user || lleno(user), clave_ok: !pass || lleno(pass), boton: !!boton};
}"""


class Sesiones:
    def __init__(self, nav: NavegadorJarvis):
        self.nav = nav

    def _paso(self, pg, max_pasos: int = 6) -> str:
        """Avanza el login todo lo posible. Devuelve listo | falta_usuario | falta_clave | sin_boton."""
        for _ in range(max_pasos):
            e = pg.evaluate(JS_ESTADO)
            if not e["login"] and not e["kmsi"]:
                return "listo"
            if not e["usuario_ok"]:
                return "falta_usuario"
            if not e["clave_ok"]:
                return "falta_clave"
            if not e["boton"]:
                pg.keyboard.press("Enter")
            else:
                pg.locator("[data-jarvis='boton']").first.click()
            try:
                pg.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            pg.wait_for_timeout(2500)
        return "listo" if not pg.evaluate(JS_ESTADO)["login"] else "sin_boton"

    def entrar(self, url: str) -> str:
        def tarea(ctx):
            pg = self.nav.pestana(ctx, url, recargar=True)
            pg.wait_for_timeout(2500)  # da tiempo a que Chrome autocomplete
            try:
                return self._paso(pg)
            except Exception:
                self.nav.captura_debug(pg, "login")
                raise
        return self.nav.ejecutar(tarea)

    def escribir_usuario(self, url: str, usuario: str) -> str:
        def tarea(ctx):
            pg = self.nav.pestana(ctx, url)
            pg.evaluate(JS_ESTADO)
            campo = pg.locator("[data-jarvis='usuario']").first
            if campo.count() == 0:
                return self._paso(pg)
            campo.fill(usuario)
            return self._paso(pg)
        return self.nav.ejecutar(tarea)

    def esperar_clave(self, url: str, segundos: int = 120) -> str:
        """Espera a que escribas la contraseña tú mismo y luego presiona Entrar."""
        def tarea(ctx):
            pg = self.nav.pestana(ctx, url)
            pg.bring_to_front()
            fin = time.time() + segundos
            while time.time() < fin:
                e = pg.evaluate(JS_ESTADO)
                if not e["login"]:
                    return "listo"  # entraste tú
                if e["clave_ok"] and e["usuario_ok"]:
                    pg.wait_for_timeout(700)
                    return self._paso(pg)
                pg.wait_for_timeout(1000)
            return "falta_clave"
        return self.nav.ejecutar(tarea, timeout=segundos + 60)


def usuario_dictado(texto: str) -> str:
    """Convierte un correo dictado, como «ana arroba ejemplo punto com», al formato escrito."""
    t = texto.strip().rstrip(".").lower()
    for hablado, simbolo in ((" arroba ", "@"), (" punto ", "."), (" guion bajo ", "_"),
                             (" guion ", "-"), (" barra baja ", "_")):
        t = t.replace(hablado, simbolo)
    if "@" in t or re.fullmatch(r"[\d\s]+", t) or "." in t:
        t = t.replace(" ", "")
    return t
