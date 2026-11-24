"""Proyectos y versiones locales. Volver atrás cambia el archivo de trabajo del proyecto."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

from .proyecto_md import NotaProyecto, nombre_archivo
from .texto import normalizar


def huella(ruta: Path) -> str:
    with ruta.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Proyectos:
    def __init__(self, store, conocimiento=None):
        self.store = store
        self.raiz = Path(conocimiento.raiz) if conocimiento is not None else None

    @contextmanager
    def _nota(self, con, clave):
        if self.raiz is None:
            yield None
            return
        fila = con.execute("SELECT nombre FROM proyectos WHERE clave=?", (clave,)).fetchone()
        if not fila:
            raise ValueError("No encontré ese proyecto.")
        nota = NotaProyecto(self.raiz, con, clave, fila[0])
        nota.importar(con, clave)
        yield nota.ruta
        nota.exportar(con, clave)

    def _diario(self, texto, ruta):
        if ruta is None:
            return
        try:
            from .diario import Diario
            Diario(self.raiz).registrar(texto, fuente=ruta)
        except Exception:
            logging.getLogger("proyectos").exception("El proyecto se guardó, pero no se pudo actualizar su diario.")

    def sincronizar_todos(self):
        with self.store.transaccion() as con:
            claves = [r[0] for r in con.execute("SELECT clave FROM proyectos ORDER BY clave")]
        for clave in claves:
            with self.store.transaccion() as con, self._nota(con, clave):
                pass
        return len(claves)

    def _buscar(self, con, nombre):
        clave = "nombre/" + normalizar(nombre)
        if con.execute("SELECT 1 FROM proyectos WHERE clave=?", (clave,)).fetchone():
            return clave
        mismo = [r[0] for r in con.execute("SELECT clave,nombre FROM proyectos")
                 if normalizar(r[1]) == normalizar(nombre)]
        if len(mismo) == 1:
            return mismo[0]
        if not mismo and self.raiz and (self.raiz / "Proyectos" / (nombre_archivo(nombre) + ".md")).is_file():
            con.execute("INSERT INTO proyectos(clave,nombre) VALUES (?,?)", (clave, nombre))
            return clave
        raise ValueError(f"No encontré un proyecto único llamado {nombre}. Puedes decir «trabajemos en el proyecto {nombre}».")

    def ruta_nota(self, nombre=None):
        if self.raiz is None:
            raise ValueError("Configura la carpeta de conocimiento antes de abrir la nota.")
        actual = self.actual()
        with self.store.transaccion() as con:
            clave = self._buscar(con, nombre) if nombre else actual
            if not clave:
                raise ValueError("Primero dime el nombre del proyecto.")
            with self._nota(con, clave) as ruta:
                return ruta

    @staticmethod
    def _activar(con, clave):
        con.execute("INSERT OR REPLACE INTO meta VALUES ('proyecto_actual',?)", (clave,))

    def actual(self):
        return self.store.get_meta("proyecto_actual")

    def elegir(self, nombre: str, crear=False) -> str:
        nombre = " ".join(nombre.split()).strip(" .¿?¡!")
        if not 1 <= len(nombre) <= 100 or not normalizar(nombre):
            raise ValueError("Dime un nombre de proyecto de hasta cien caracteres.")
        clave = "nombre/" + normalizar(nombre)
        with self.store.transaccion() as con:
            fila = con.execute("SELECT clave FROM proyectos WHERE clave=?", (clave,)).fetchone()
            if not fila and not crear:
                clave = self._buscar(con, nombre)
            else:
                con.execute("INSERT OR IGNORE INTO proyectos(clave,nombre) VALUES (?,?)", (clave, nombre))
            with self._nota(con, clave):
                self._activar(con, clave)
        return clave

    def para_archivo(self, ruta: Path):
        clave = self.actual()
        if clave and clave.startswith("nombre/"):
            return clave
        ruta = ruta.resolve()
        with self.store.transaccion() as con:
            anterior = con.execute("SELECT proyecto FROM versiones WHERE ruta=? COLLATE NOCASE "
                                   "ORDER BY id DESC LIMIT 1", (str(ruta),)).fetchone()
            clave = anterior[0] if anterior else "archivo/" + os.path.normcase(str(ruta))
            con.execute("INSERT OR IGNORE INTO proyectos(clave,nombre) VALUES (?,?)", (clave, ruta.stem))
            with self._nota(con, clave):
                self._activar(con, clave)
        return clave

    def apuntar(self, clave, clase, texto):
        if not clave:
            raise ValueError("Primero dime con qué proyecto trabajamos.")
        texto = " ".join(texto.splitlines()).strip()
        if not texto:
            raise ValueError("Dime qué quieres anotar en el proyecto.")
        with self.store.transaccion() as con, self._nota(con, clave) as ruta:
            guardado = con.execute("INSERT OR IGNORE INTO apuntes_proyecto(proyecto,clase,texto) VALUES (?,?,?)",
                                   (clave, clase, texto)).rowcount
        if guardado and clase in ("Decisión", "Pendiente"):
            self._diario(f"{clase} · {texto}", ruta)

    def completar(self, clave, texto):
        with self.store.transaccion() as con, self._nota(con, clave) as ruta:
            filas = con.execute("SELECT id,texto FROM apuntes_proyecto WHERE proyecto=? AND clase='Pendiente'",
                                (clave,)).fetchall()
            coincidencias = [f for f in filas if normalizar(f["texto"]) == normalizar(texto)]
            if len(coincidencias) != 1:
                raise ValueError("Dime el pendiente del proyecto tal como lo anotaste.")
            con.execute("UPDATE OR REPLACE apuntes_proyecto SET clase='Hecho' WHERE id=?", (coincidencias[0]["id"],))
        self._diario("Tarea completada · " + coincidencias[0]["texto"], ruta)

    def registrar(self, clave, origen, firma, copia, firma_copia, operacion, cambios):
        with self.store.transaccion() as con, self._nota(con, clave) as ruta:
            anterior = con.execute("SELECT id FROM versiones WHERE proyecto=? AND ruta=? COLLATE NOCASE "
                                   "AND huella=? ORDER BY id DESC LIMIT 1",
                                   (clave, str(origen), firma)).fetchone()
            if anterior:
                padre = anterior[0]
            else:
                padre = con.execute("INSERT INTO versiones(proyecto,ruta,huella,operacion,cambios) "
                                    "VALUES (?,?,?,'Original','[]')", (clave, str(origen), firma)).lastrowid
            version = con.execute("INSERT INTO versiones(proyecto,anterior,ruta,huella,operacion,cambios) "
                                  "VALUES (?,?,?,?,?,?)", (clave, padre, str(copia), firma_copia,
                                                           operacion, json.dumps(cambios, ensure_ascii=False))).lastrowid
            con.execute("UPDATE proyectos SET version=? WHERE clave=?", (version, clave))
        self._diario(f"{operacion} · {Path(copia).name}", ruta)

    def ficha(self, clave=None):
        clave = clave or self.actual()
        if not clave:
            return None
        with self.store.transaccion() as con:
            proyecto = con.execute("SELECT nombre,version FROM proyectos WHERE clave=?", (clave,)).fetchone()
            if not proyecto:
                return None
            with self._nota(con, clave):
                fila = con.execute("SELECT * FROM versiones WHERE id=?", (proyecto["version"],)).fetchone()
                notas = con.execute("SELECT clase,texto FROM apuntes_proyecto WHERE proyecto=? ORDER BY id",
                                    (clave,)).fetchall()
                version = dict(fila) if fila else {}
                if version:
                    version["cambios"] = json.loads(version["cambios"])
                return {"nombre": proyecto["nombre"], "version": version, "apuntes": [dict(n) for n in notas]}

    def anterior(self, clave):
        with self.store.transaccion() as con:
            fila = con.execute("SELECT p.version, a.id,a.ruta,a.huella FROM proyectos p "
                               "JOIN versiones v ON v.id=p.version JOIN versiones a ON a.id=v.anterior "
                               "WHERE p.clave=?", (clave,)).fetchone()
        if not fila:
            raise ValueError("Este proyecto aún no tiene una versión anterior.")
        ruta = Path(fila["ruta"])
        if not ruta.is_file() or huella(ruta) != fila["huella"]:
            raise ValueError("La versión anterior se movió o cambió en disco.")
        with self.store.transaccion() as con, self._nota(con, clave) as nota:
            cambio = con.execute("UPDATE proyectos SET version=? WHERE clave=? AND version=?",
                                 (fila["id"], clave, fila["version"]))
            if not cambio.rowcount:
                raise ValueError("La versión de trabajo cambió. Vuelve a pedir la anterior.")
        self._diario(f"Volví a la versión {ruta.name}", nota)
        return ruta

    def word_de_trabajo(self, clave):
        """Prioriza el Word de la rama activa, incluido el Word de origen de un PPT."""
        with self.store.transaccion() as con:
            fila = con.execute(
                """WITH RECURSIVE rama(id) AS (
                    SELECT version FROM proyectos WHERE clave=?
                    UNION SELECT v.anterior FROM versiones v JOIN rama ON v.id=rama.id
                )
                SELECT ruta,huella FROM versiones WHERE proyecto=? AND lower(substr(ruta,-5))='.docx'
                ORDER BY CASE WHEN id IN (SELECT id FROM rama) THEN 0 ELSE 1 END, id DESC LIMIT 1""",
                (clave, clave),
            ).fetchone()
        if not fila:
            raise ValueError("El proyecto aún no tiene un Word vinculado.")
        ruta = Path(fila["ruta"])
        if not ruta.is_absolute() or not ruta.is_file() or huella(ruta) != fila["huella"]:
            raise ValueError("El Word del proyecto se movió o cambió desde la última verificación.")
        return ruta

    def vincular_word(self, clave, ruta):
        """Vincula un Word guardado que la persona ha elegido explícitamente."""
        ruta = Path(ruta).resolve(strict=True)
        if ruta.suffix.lower() != ".docx" or not ruta.is_file():
            raise ValueError("Elige un Word guardado en formato DOCX.")
        firma = huella(ruta)
        with self.store.transaccion() as con, self._nota(con, clave) as nota:
            fila = con.execute("SELECT id FROM versiones WHERE proyecto=? AND ruta=? COLLATE NOCASE "
                               "AND huella=? ORDER BY id DESC LIMIT 1", (clave, str(ruta), firma)).fetchone()
            version = fila[0] if fila else con.execute(
                "INSERT INTO versiones(proyecto,ruta,huella,operacion,cambios) VALUES (?,?,?,'Word vinculado','[]')",
                (clave, str(ruta), firma)).lastrowid
            con.execute("UPDATE proyectos SET version=? WHERE clave=?", (version, clave))
        self._diario(f"Vinculé el Word {ruta.name}", nota)
