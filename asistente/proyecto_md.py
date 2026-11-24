"""Notas de proyecto que conservan el texto libre y las listas editadas en Obsidian."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import date
from pathlib import Path

from .texto import normalizar


_SECCIONES = {"Decisiones": ("Decisión",), "Pendientes": ("Pendiente", "Hecho"),
              "Notas vinculadas": ("Notas vinculadas",)}
_TITULOS = {normalizar(s): s for s in _SECCIONES}
_TITULOS["tareas"] = "Pendientes"
_CLASES = tuple(clase for lista in _SECCIONES.values() for clase in lista)
_MARCA = re.compile(r"^<!-- jarvis:proyecto:([0-9a-f]{16}) -->$", re.M)
_FIN = "<!-- /jarvis:versiones -->"
_LISTA = re.compile(r"^[-*+] +(.+?)\s*$")
_TAREA = re.compile(r"^[-*+] +\[([ xX])\] +(.+?)\s*$")


def nombre_archivo(nombre: str) -> str:
    nombre = re.sub(r'[\x00-\x1f<>:"/\\|?*#^[\]]+', " ", nombre)
    nombre = " ".join(nombre.split()).strip(". ")[:90].rstrip(". ") or "Proyecto"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", nombre, re.I):
        nombre = "Proyecto " + nombre
    return nombre


def _seguro(ruta):
    if ruta.is_symlink() or (ruta.exists() and (getattr(ruta.lstat(), "st_file_attributes", 0)
                                                & stat.FILE_ATTRIBUTE_REPARSE_POINT)):
        raise ValueError(f"La ruta {ruta.name} es un enlace. Usa una carpeta o nota local de la bóveda.")


def _listas(lineas):
    """Localiza exclusivamente viñetas de secciones editables, fuera de código y propiedades."""
    seccion = None
    finales, entradas = {}, {}
    valla = ""
    propiedades = bool(lineas and lineas[0].strip() == "---")
    for i, linea in enumerate(lineas):
        limpia = linea.strip()
        if propiedades:
            if i and limpia in ("---", "..."):
                propiedades = False
            continue
        if valla:
            if re.fullmatch(re.escape(valla[0]) + "{%d,}" % len(valla), limpia):
                valla = ""
            continue
        bloque = re.match(r"^\s{0,3}(\x60{3,}|~{3,})", linea)
        if bloque:
            valla = bloque[1]
            continue
        if re.match(r"^#{1,2}(?:\s|$)", linea):
            if seccion:
                finales[seccion] = i
            tit = re.fullmatch(r"##\s+(.+)", linea)
            seccion = _TITULOS.get(normalizar(tit[1])) if tit else None
            if seccion in finales:
                raise ValueError(f"Hay dos secciones «{seccion}» en la nota del proyecto. Reúnelas para sincronizarla.")
            continue
        if seccion == "Pendientes" and (m := _TAREA.match(linea)):
            entradas[i] = ("Hecho" if m[1].lower() == "x" else "Pendiente", m[2].strip())
        elif seccion and seccion != "Pendientes" and (m := _LISTA.match(linea)):
            entradas[i] = (_SECCIONES[seccion][0], m[1].strip())
    if seccion:
        finales[seccion] = len(lineas)
    return finales, entradas


class NotaProyecto:
    def __init__(self, raiz, con, clave, nombre):
        self.carpeta = Path(raiz).resolve() / "Proyectos"
        _seguro(self.carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        codigo = hashlib.sha256(clave.encode()).hexdigest()[:16]
        self.marca = f"<!-- jarvis:proyecto:{codigo} -->"
        self.nombre = nombre
        self.clave_meta = "nota_proyecto/" + hashlib.sha256(
            (os.path.normcase(str(self.carpeta)) + "\n" + clave).encode()).hexdigest()
        row = con.execute("SELECT valor FROM meta WHERE clave=?", (self.clave_meta,)).fetchone()
        self.anterior = json.loads(row[0]) if row else {}
        base = nombre_archivo(nombre)
        archivo = self.anterior.get("archivo", base + ".md")
        if Path(archivo).name != archivo or not archivo.lower().endswith(".md"):
            raise ValueError("La ruta de la nota del proyecto no es válida.")
        self.ruta = self.carpeta / archivo
        if row and not self.ruta.exists():
            # Si Obsidian renombró el archivo, recuperar su identidad dentro de Proyectos.
            encontradas = []
            for ruta in self.carpeta.glob("*.md"):
                try:
                    _seguro(ruta)
                    if self.marca in ruta.read_text(encoding="utf-8-sig").splitlines():
                        encontradas.append(ruta)
                except (ValueError, UnicodeError, OSError):
                    continue
            if len(encontradas) > 1:
                raise ValueError("Varias notas tienen la identidad de este proyecto. Conserva la marca en una sola.")
            if encontradas:
                self.ruta = encontradas[0]
        _seguro(self.ruta)
        self.datos = self.ruta.read_bytes() if self.ruta.exists() else None
        contenido = (self.datos or b"").decode("utf-8-sig").replace("\r\n", "\n")
        otra = _MARCA.search(contenido)
        if otra and otra[1] != codigo:
            self.ruta = self.carpeta / f"{base} - {codigo[:8]}.md"
            _seguro(self.ruta)
            self.datos = self.ruta.read_bytes() if self.ruta.exists() else None
            contenido = (self.datos or b"").decode("utf-8-sig").replace("\r\n", "\n")
            if self.datos is not None and self.marca not in contenido.splitlines():
                raise ValueError("Ya existe otra nota con el mismo nombre de proyecto.")
        self.nueva = self.datos is None
        self.contenido = (f"---\ntipo: proyecto\nfecha: {date.today()}\ntags: [proyecto]\n---\n\n# {nombre}\n"
                          if self.nueva else contenido)
        self.contenido_usuario = self._sin_versiones()
        self.lineas = self.contenido_usuario.splitlines()
        self.finales, self.entradas = _listas(self.lineas)

    def _sin_versiones(self):
        """El bloque de archivos solo presenta versiones registradas por Jarvis en SQLite."""
        apertura = list(re.finditer("^" + re.escape(self.marca) + "$", self.contenido, re.M))
        cierre = list(re.finditer("^" + re.escape(_FIN) + "$", self.contenido, re.M))
        if not apertura and not cierre:
            return self.contenido
        if len(apertura) != 1 or len(cierre) != 1 or apertura[0].end() > cierre[0].start():
            raise ValueError("La sección de versiones de la nota está incompleta. Revisa sus dos marcas de Jarvis.")
        return self.contenido[:apertura[0].start()] + self.contenido[cierre[0].end():].removeprefix("\n")

    @staticmethod
    def _apuntes(con, clave):
        filas = con.execute("SELECT clase,texto FROM apuntes_proyecto WHERE proyecto=? ORDER BY id", (clave,))
        return [(f[0], f[1]) for f in filas if f[0] in _CLASES]

    def importar(self, con, clave):
        if self.nueva:
            return
        actuales = self._apuntes(con, clave)
        base = [tuple(par) for par in self.anterior.get("apuntes", [])]
        for seccion, clases in _SECCIONES.items():
            if seccion not in self.finales:
                continue
            disco = list(dict.fromkeys(p for p in self.entradas.values() if p[0] in clases))
            ultimas = [p for p in base if p[0] in clases]
            db = [p for p in actuales if p[0] in clases]
            if disco == ultimas:
                continue
            # Conservar incorporaciones locales que aún no llegaron a la bóveda.
            fusion = [p for p in disco if p not in ultimas or p in db]
            fusion.extend(p for p in db if p not in ultimas and p not in fusion)
            if fusion != db:
                con.executemany("DELETE FROM apuntes_proyecto WHERE proyecto=? AND clase=?",
                                [(clave, clase) for clase in clases])
                con.executemany("INSERT INTO apuntes_proyecto(proyecto,clase,texto) VALUES (?,?,?)",
                                [(clave, *p) for p in fusion])

    def exportar(self, con, clave):
        apuntes = self._apuntes(con, clave)
        por_posicion = {}
        for seccion, clases in _SECCIONES.items():
            agregar = []
            for clase, dato in apuntes:
                if clase in clases and (clase, dato) not in self.entradas.values():
                    prefijo = {"Pendiente": "- [ ] ", "Hecho": "- [x] "}.get(clase, "- ")
                    agregar.append(prefijo + " ".join(dato.splitlines()))
            if seccion not in self.finales:
                agregar = ["", f"## {seccion}", *agregar]
            if agregar:
                pos = self.finales.get(seccion, len(self.lineas))
                if seccion in self.finales:
                    while pos and not self.lineas[pos-1].strip():
                        pos -= 1
                por_posicion.setdefault(pos, []).extend(agregar)
        resultado = []
        for i, linea in enumerate(self.lineas):
            resultado.extend(por_posicion.get(i, ()))
            if i not in self.entradas or self.entradas[i] in apuntes:
                resultado.append(linea)
        resultado.extend(por_posicion.get(len(self.lineas), ()))
        actual = con.execute("SELECT version FROM proyectos WHERE clave=?", (clave,)).fetchone()[0]
        versiones = con.execute("SELECT id,ruta,operacion FROM versiones WHERE proyecto=? ORDER BY id", (clave,)).fetchall()
        while resultado and not resultado[-1].strip():
            resultado.pop()
        resultado.extend(["", self.marca, "## Archivos y versiones", ""])
        if versiones:
            for v in versiones:
                estado = " (actual)" if v["id"] == actual else ""
                resultado.append(f"- {v['operacion']}{estado} · \x60{v['ruta']}\x60")
        else:
            resultado.append("Todavía no hay archivos vinculados.")
        resultado.extend([_FIN, ""])
        contenido = "\n".join(resultado)
        if self.nueva or contenido != self.contenido:
            self._publicar(contenido)
        meta = {"archivo": self.ruta.name, "apuntes": [list(par) for par in apuntes]}
        if meta != self.anterior:
            con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (self.clave_meta, json.dumps(meta, ensure_ascii=False)))

    def _publicar(self, contenido):
        salto = "\r\n" if self.datos and b"\r\n" in self.datos else "\n"
        codificacion = "utf-8-sig" if self.datos and self.datos.startswith(b"\xef\xbb\xbf") else "utf-8"
        temporal = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=self.carpeta, suffix=".tmp", delete=False) as f:
                temporal = Path(f.name)
                f.write(contenido.replace("\n", salto).encode(codificacion))
            _seguro(self.carpeta)
            _seguro(self.ruta)
            if self.nueva:
                try:
                    if os.name == "nt":
                        temporal.rename(self.ruta)
                    else:
                        os.link(temporal, self.ruta)
                except FileExistsError as exc:
                    raise ValueError("Obsidian acaba de crear esta nota. Repite el pedido para combinar los cambios.") from exc
            else:
                if self.ruta.read_bytes() != self.datos:
                    raise ValueError("Obsidian acaba de editar esta nota. Repite el pedido para combinar los cambios.")
                temporal.replace(self.ruta)
        finally:
            if temporal:
                temporal.unlink(missing_ok=True)
