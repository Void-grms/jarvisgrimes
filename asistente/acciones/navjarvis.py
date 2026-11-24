"""Navegador propio de Jarvis: un Chrome con perfil aparte que Jarvis sí puede controlar
(tu Chrome normal no se deja controlar desde fuera). Se usa para WhatsApp Web y para
entrar a tus plataformas. La primera vez inicias sesión tú en cada sitio; Chrome guarda
las sesiones y, si aceptas, las contraseñas en ESTE perfil (datos/navegador-jarvis).

Playwright no admite varios hilos, así que todo pasa por un único hilo trabajador.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import queue
import threading
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse

from ..config import DATOS

log = logging.getLogger("navjarvis")
PERFIL = DATOS / "navegador-jarvis"
DEBUG = DATOS / "debug"


class NavegadorJarvis:
    def __init__(self, canal: str = "chrome", perfil=None):
        self.canal = canal
        self.perfil = perfil or PERFIL
        self._tareas: queue.Queue = queue.Queue()
        self._hilo: threading.Thread | None = None
        self._p = None
        self.ctx = None

    # ---------- hilo trabajador ----------
    def _asegurar_hilo(self) -> None:
        if self._hilo and self._hilo.is_alive():
            return
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="navegador-jarvis")
        self._hilo.start()

    def _bucle(self) -> None:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            self._p = p
            while True:
                fn, fut = self._tareas.get()
                if not fut.set_running_or_notify_cancel():
                    continue
                try:
                    fut.set_result(fn(self._contexto()))
                except Exception as e:  # noqa: BLE001 — se reenvía al que pidió la tarea
                    fut.set_exception(e)

    def _contexto(self):
        if self.ctx is not None:
            try:
                self.ctx.pages  # noqa: B018 — falla si el usuario cerró la ventana
                return self.ctx
            except Exception:
                self.ctx = None
        self.perfil.mkdir(parents=True, exist_ok=True)
        oculto = os.environ.get("JARVIS_HEADLESS") == "1"  # solo para pruebas automáticas
        opciones = dict(user_data_dir=str(self.perfil), headless=oculto, no_viewport=True, locale="es-PE",
                        ignore_default_args=["--enable-automation"],
                        args=["--disable-blink-features=AutomationControlled", "--start-maximized"])
        try:
            self.ctx = self._p.chromium.launch_persistent_context(channel=self.canal, **opciones)
        except Exception as e:
            log.warning("No pude abrir Chrome (%s); uso el Chromium de Playwright", e)
            self.ctx = self._p.chromium.launch_persistent_context(**opciones)
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        return self.ctx

    def ejecutar(self, fn: Callable, timeout: float = 180):
        """Corre fn(contexto) en el hilo del navegador y devuelve su resultado."""
        self._asegurar_hilo()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._tareas.put((fn, fut))
        return fut.result(timeout)

    # ---------- utilidades (llamar solo dentro de una tarea) ----------
    @staticmethod
    def pestana(ctx, url: str, recargar: bool = False):
        """Reutiliza la pestaña del mismo sitio o abre una nueva."""
        host = urlparse(url).netloc
        for pg in ctx.pages:
            try:
                if host and urlparse(pg.url).netloc == host:
                    if recargar:
                        pg.goto(url, wait_until="domcontentloaded")
                    pg.bring_to_front()
                    return pg
            except Exception:
                continue
        vacias = [pg for pg in ctx.pages if pg.url in ("about:blank", "chrome://newtab/")]
        pg = vacias[0] if vacias else ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.bring_to_front()
        return pg

    @staticmethod
    def captura_debug(pg, nombre: str) -> None:
        try:
            DEBUG.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(DEBUG / f"{datetime.now():%H%M%S} {nombre}.png"))
        except Exception:
            pass
