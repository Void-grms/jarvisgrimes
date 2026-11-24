"""Llenar plantillas por voz.

Pon tus archivos en Documentos\\Jarvis\\Plantillas:
- Word o Excel: escribe los campos entre dobles llaves, por ejemplo {{nombre completo}}, {{DNI}}.
- PDF: formularios con campos rellenables (los de "rellenar y firmar").
Jarvis te pregunta cada campo y guarda una copia llena en Documentos\\Jarvis.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime
from pathlib import Path

from ..texto import normalizar

CAMPO_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
EXTENSIONES = (".docx", ".xlsx", ".pdf")


def _parrafos_docx(doc):
    """Todos los párrafos: cuerpo, tablas (incluso anidadas), encabezados y pies."""
    def de_contenedor(c):
        yield from c.paragraphs
        for t in getattr(c, "tables", []):
            for fila in t.rows:
                for celda in fila.cells:
                    yield from de_contenedor(celda)

    yield from de_contenedor(doc)
    for sec in doc.sections:
        for parte in (sec.header, sec.footer, sec.first_page_header, sec.first_page_footer):
            try:
                yield from de_contenedor(parte)
            except Exception:
                continue


def _reemplazar_parrafo(p, valores: dict[str, str]) -> None:
    if "{{" not in p.text:
        return
    # 1) Si cada campo cabe en un solo "run", se reemplaza ahí y se conserva el formato.
    for r in p.runs:
        if "{{" in r.text and "}}" in r.text:
            r.text = CAMPO_RE.sub(lambda m: valores.get(m.group(1).strip(), ""), r.text)
    if "{{" not in p.text:
        return
    # 2) Si Word partió el campo en varios trozos, se une el párrafo (formato del 1er trozo).
    nuevo = CAMPO_RE.sub(lambda m: valores.get(m.group(1).strip(), ""), p.text)
    if p.runs:
        p.runs[0].text = nuevo
        for r in p.runs[1:]:
            r.text = ""


class Plantillas:
    def __init__(self, carpeta: Path, salida: Path):
        self.carpeta = carpeta
        self.salida = salida
        self.etiquetas: dict[str, str] = {}  # nombre técnico → texto a preguntar (PDF)

    def listar(self) -> list[Path]:
        if not self.carpeta.exists():
            return []
        return sorted(p for p in self.carpeta.iterdir()
                      if p.suffix.lower() in EXTENSIONES and not p.name.startswith("~$"))

    def buscar(self, nombre: str) -> Path | None:
        archivos = self.listar()
        if not archivos or not nombre:
            return None
        q = normalizar(nombre)
        tabla = {normalizar(p.stem): p for p in archivos}
        if q in tabla:
            return tabla[q]
        for k, p in tabla.items():
            if q in k or k in q:
                return p
        parecido = difflib.get_close_matches(q, list(tabla), n=1, cutoff=0.6)
        return tabla[parecido[0]] if parecido else None

    def campos(self, ruta: Path) -> list[str]:
        ext = ruta.suffix.lower()
        encontrados: list[str] = []
        if ext == ".docx":
            from docx import Document
            for p in _parrafos_docx(Document(ruta)):
                encontrados += [m.strip() for m in CAMPO_RE.findall(p.text)]
        elif ext == ".xlsx":
            from openpyxl import load_workbook
            for ws in load_workbook(ruta).worksheets:
                for fila in ws.iter_rows():
                    for c in fila:
                        if isinstance(c.value, str):
                            encontrados += [m.strip() for m in CAMPO_RE.findall(c.value)]
        elif ext == ".pdf":
            from pypdf import PdfReader
            campos = PdfReader(ruta).get_fields() or {}
            encontrados = [n for n, d in campos.items() if d.get("/FT") in ("/Tx", None)]
            for n in encontrados:
                if campos[n].get("/TU"):
                    self.etiquetas[n] = str(campos[n]["/TU"])
        return list(dict.fromkeys(encontrados))

    def etiqueta(self, campo: str) -> str:
        texto = self.etiquetas.get(campo) or re.sub(r"[_\-.]+", " ", campo).strip()
        return texto[:1].upper() + texto[1:]

    def llenar(self, ruta: Path, valores: dict[str, str]) -> Path:
        self.salida.mkdir(parents=True, exist_ok=True)
        destino = self.salida / f"{ruta.stem} - {datetime.now():%Y-%m-%d %H.%M}{ruta.suffix}"
        ext = ruta.suffix.lower()
        if ext == ".docx":
            from docx import Document
            doc = Document(ruta)
            for p in _parrafos_docx(doc):
                _reemplazar_parrafo(p, valores)
            doc.save(destino)
        elif ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(ruta)
            for ws in wb.worksheets:
                for fila in ws.iter_rows():
                    for c in fila:
                        if isinstance(c.value, str) and "{{" in c.value:
                            c.value = CAMPO_RE.sub(lambda m: valores.get(m.group(1).strip(), ""), c.value)
            wb.save(destino)
        elif ext == ".pdf":
            from pypdf import PdfReader, PdfWriter
            writer = PdfWriter()
            writer.append(PdfReader(ruta))
            for pagina in writer.pages:
                writer.update_page_form_field_values(pagina, valores, auto_regenerate=False)
            writer.set_need_appearances_writer(True)
            with open(destino, "wb") as f:
                writer.write(f)
        return destino


def limpiar_valor(campo: str, valor: str) -> str:
    v = valor.strip().rstrip(".").strip()
    if re.search(r"dni|ruc|telefono|celular|codigo|numero|n°|nro", normalizar(campo)):
        if re.fullmatch(r"[\d\s\-]+", v):
            v = re.sub(r"[\s\-]", "", v)
    return v
