"""Recordatorios, alarmas, temporizadores y pomodoros. Se guardan en SQLite, así que
sobreviven a reinicios; si la laptop estaba suspendida, avisan al despertar."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..config import DATOS

log = logging.getLogger("recordatorios")


@dataclass
class Recordatorio:
    id: int
    ts: float
    texto: str
    tipo: str  # recordatorio | alarma | temporizador | pomodoro | descanso


class Recordatorios:
    def __init__(self, avisar: Callable[[str], None], ruta=None):
        self.avisar = avisar
        self._lock = threading.Lock()
        self._con = sqlite3.connect(ruta or DATOS / "pendientes.db", check_same_thread=False)
        self._con.execute("""CREATE TABLE IF NOT EXISTS recordatorios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, texto TEXT NOT NULL,
            tipo TEXT NOT NULL, hecho INTEGER NOT NULL DEFAULT 0)""")
        self._con.commit()
        self._activo = False

    def iniciar(self) -> None:
        if not self._activo:
            self._activo = True
            threading.Thread(target=self._bucle, daemon=True).start()

    def agregar(self, ts: float, texto: str, tipo: str = "recordatorio") -> int:
        with self._lock, self._con:
            cur = self._con.execute("INSERT INTO recordatorios (ts, texto, tipo) VALUES (?,?,?)",
                                    (ts, texto, tipo))
            return cur.lastrowid

    def pendientes(self) -> list[Recordatorio]:
        with self._lock:
            filas = self._con.execute(
                "SELECT id, ts, texto, tipo FROM recordatorios WHERE hecho=0 ORDER BY ts").fetchall()
        return [Recordatorio(*f) for f in filas]

    def cancelar_todos(self) -> int:
        with self._lock, self._con:
            return self._con.execute("UPDATE recordatorios SET hecho=1 WHERE hecho=0").rowcount

    def revisar(self, ahora: float | None = None) -> list[Recordatorio]:
        """Dispara los vencidos. Devuelve los que avisó (útil para pruebas)."""
        ahora = ahora or time.time()
        with self._lock:
            filas = self._con.execute(
                "SELECT id, ts, texto, tipo FROM recordatorios WHERE hecho=0 AND ts<=? ORDER BY ts",
                (ahora,)).fetchall()
            if filas:
                with self._con:
                    self._con.executemany("UPDATE recordatorios SET hecho=1 WHERE id=?",
                                          [(f[0],) for f in filas])
        disparados = [Recordatorio(*f) for f in filas]
        for r in disparados:
            self._disparar(r, tarde=ahora - r.ts > 120)
        return disparados

    def _disparar(self, r: Recordatorio, tarde: bool) -> None:
        prefijo = "Mientras la laptop estaba suspendida se pasó un aviso. " if tarde else ""
        if r.tipo == "temporizador":
            self.avisar(f"{prefijo}Se acabó el temporizador{': ' + r.texto if r.texto else ''}.")
        elif r.tipo == "alarma":
            self.avisar(f"{prefijo}Es la hora de tu alarma{': ' + r.texto if r.texto else ''}.")
        elif r.tipo == "pomodoro":
            self.avisar(f"{prefijo}Terminó tu bloque de concentración. Toma cinco minutos de descanso.")
            self.agregar(time.time() + 300, "", "descanso")
        elif r.tipo == "descanso":
            self.avisar("Se acabó el descanso. Cuando quieras, empezamos otro bloque.")
        else:
            self.avisar(f"{prefijo}Te recuerdo: {r.texto}.")

    def _bucle(self) -> None:
        while True:
            try:
                self.revisar()
            except Exception as e:
                log.warning("Error revisando recordatorios: %s", e)
            time.sleep(1)
