"""Sincronización en segundo plano de Gmail y Clementina hacia el SQLite local."""
from __future__ import annotations

import logging
import re
import threading
import time

from .collectors import clementina, gmail
from .store import Store

log = logging.getLogger("sync")


class Sincronizador:
    def __init__(self, cfg: dict, store: Store):
        self.cfg = cfg
        self.store = store
        self._lock = threading.Lock()
        self.errores: dict[str, str] = {}

    def sincronizar(self) -> dict[str, str]:
        """Corre ambos colectores. Devuelve errores por fuente (vacío si todo bien)."""
        if not self._lock.acquire(blocking=False):
            return self.errores  # ya hay una sincronización en curso
        try:
            self.errores = {}
            zona = self.cfg["usuario"]["zona_horaria"]
            if self.cfg["gmail"]["habilitado"]:
                self._uno("gmail", lambda: gmail.recolectar(self.cfg["gmail"]))
            cle = self.cfg["clementina"]
            if cle["habilitado"] and (cle["modo"] == "sesion" or cle["url_ics"]):
                self._uno("clementina", lambda: clementina.recolectar(self.cfg["clementina"], zona))
            return self.errores
        finally:
            self._lock.release()

    def _uno(self, fuente: str, fn) -> None:
        t = time.perf_counter()
        try:
            items = fn()
            self.store.reemplazar_fuente(fuente, items)
            log.info("%s: %d items en %.1fs", fuente, len(items), time.perf_counter() - t)
        except Exception as e:  # una fuente caída no debe tumbar al asistente
            self.errores[fuente] = str(e)
            # Los errores de red traen la URL completa: nunca escribir tokens en el registro.
            log.warning("%s falló: %s", fuente, re.sub(r"(authtoken|token|key|userid)=[^&\s'\"]+", r"\1=***", str(e)))

    def en_segundo_plano(self) -> threading.Thread:
        h = threading.Thread(target=self.sincronizar, daemon=True)
        h.start()
        return h

    def periodico(self) -> None:
        minutos = self.cfg["sync"]["minutos_entre_sync"]

        def bucle():
            while True:
                time.sleep(minutos * 60)
                self.sincronizar()

        threading.Thread(target=bucle, daemon=True).start()
