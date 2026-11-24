"""Caché local en SQLite: los pendientes se guardan aquí para responder al instante."""
from __future__ import annotations

import sqlite3
import json
from contextlib import closing, contextmanager
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import DATOS


def cargar_preferencias(ruta_config: Path | None = None, ruta_db: Path | None = None) -> dict:
    """Traslada una voz guardada por versiones anteriores al archivo que edita el panel."""
    from .config import cargar
    ruta_db = ruta_db or DATOS / "pendientes.db"
    if ruta_db.is_file():
        with closing(sqlite3.connect(ruta_db)) as con, con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                fila = con.execute("SELECT valor FROM meta WHERE clave='ajuste_voz'").fetchone()
                if fila and fila[0]:
                    con.execute("BEGIN IMMEDIATE")
                    fila = con.execute("SELECT valor FROM meta WHERE clave='ajuste_voz'").fetchone()
                    if fila:
                        try:
                            anterior = json.loads(fila[0])
                            velocidad = float(anterior["velocidad"])
                            tono = float(anterior["tono_semitonos"])
                        except (KeyError, ValueError, TypeError):
                            pass
                        else:
                            if 0.5 <= velocidad <= 2 and -3 <= tono <= 3:
                                from .ajustes import guardar_ajustes
                                guardar_ajustes({"voz": {"velocidad": velocidad, "tono_semitonos": tono}},
                                                ruta=ruta_config)
                        con.execute("DELETE FROM meta WHERE clave='ajuste_voz'")
    return cargar(ruta_config)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS items (
    fuente   TEXT NOT NULL,      -- 'gmail' | 'clementina'
    id       TEXT NOT NULL,
    tipo     TEXT NOT NULL,      -- 'correo' | 'tarea' | 'evento' | 'aviso'
    titulo   TEXT NOT NULL,
    detalle  TEXT DEFAULT '',
    curso    TEXT DEFAULT '',
    vence_ts REAL,               -- epoch (s) o NULL
    url      TEXT DEFAULT '',
    actualizado_ts REAL NOT NULL,
    nota     TEXT DEFAULT '',    -- p. ej. el fragmento del correo
    PRIMARY KEY (fuente, id)
);
CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS proyectos (
    clave TEXT PRIMARY KEY, nombre TEXT NOT NULL, version INTEGER);
CREATE TABLE IF NOT EXISTS versiones (
    id INTEGER PRIMARY KEY, proyecto TEXT NOT NULL, anterior INTEGER, ruta TEXT NOT NULL,
    huella TEXT NOT NULL, operacion TEXT NOT NULL, cambios TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS versiones_proyecto ON versiones(proyecto, ruta);
CREATE TABLE IF NOT EXISTS apuntes_proyecto (
    id INTEGER PRIMARY KEY, proyecto TEXT NOT NULL, clase TEXT NOT NULL, texto TEXT NOT NULL,
    UNIQUE(proyecto, clase, texto));
"""


@dataclass
class Item:
    fuente: str
    id: str
    tipo: str
    titulo: str
    detalle: str = ""
    curso: str = ""
    vence_ts: float | None = None
    url: str = ""
    nota: str = ""


class Store:
    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta or DATOS / "pendientes.db"
        self._lock = threading.Lock()
        self._con = sqlite3.connect(self.ruta, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(ESQUEMA)
        try:  # bases creadas con la versión anterior no tienen la columna `nota`
            self._con.execute("ALTER TABLE items ADD COLUMN nota TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

    def reemplazar_fuente(self, fuente: str, items: list[Item]) -> None:
        """Sustituye todos los items de una fuente (lo que ya no aparece, se fue)."""
        ahora = time.time()
        with self._lock, self._con:
            self._con.execute("DELETE FROM items WHERE fuente=?", (fuente,))
            self._con.executemany(
                "INSERT OR REPLACE INTO items (fuente, id, tipo, titulo, detalle, curso, vence_ts,"
                " url, actualizado_ts, nota) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (fuente, i.id, i.tipo, i.titulo, i.detalle, i.curso, i.vence_ts, i.url, ahora, i.nota)
                    for i in items
                ],
            )
            self._con.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)", (f"sync_{fuente}", str(ahora))
            )

    @contextmanager
    def transaccion(self):
        with self._lock, self._con:
            self._con.execute("BEGIN IMMEDIATE")
            yield self._con

    def items(self, fuente: str | None = None, tipo: str | None = None) -> list[Item]:
        q, args = "SELECT * FROM items WHERE 1=1", []
        if fuente:
            q += " AND fuente=?"
            args.append(fuente)
        if tipo:
            q += " AND tipo=?"
            args.append(tipo)
        q += " ORDER BY vence_ts IS NULL, vence_ts, actualizado_ts DESC"
        with self._lock:
            filas = self._con.execute(q, args).fetchall()
        return [
            Item(r["fuente"], r["id"], r["tipo"], r["titulo"], r["detalle"], r["curso"],
                 r["vence_ts"], r["url"], r["nota"] or "")
            for r in filas
        ]

    def ultimo_sync(self, fuente: str) -> float | None:
        with self._lock:
            r = self._con.execute("SELECT valor FROM meta WHERE clave=?", (f"sync_{fuente}",)).fetchone()
        return float(r["valor"]) if r else None

    def set_meta(self, clave: str, valor: str) -> None:
        with self._lock, self._con:
            self._con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (clave, valor))

    def get_meta(self, clave: str) -> str | None:
        with self._lock:
            r = self._con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
        return r["valor"] if r else None
