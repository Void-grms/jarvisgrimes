"""Guardado explícito de los resultados de una investigación en Obsidian."""
from __future__ import annotations

import html
import itertools
import logging
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

from .conocimiento import _enlace
from .diario import Diario
from .proyecto_md import nombre_archivo


def _texto_plano(texto):
    linea = html.escape(" ".join(texto.split()), quote=False)
    return re.sub(r"([\\\[\]*_\x60!#|~])", r"\\\1", linea)


def guardar_informe(conocimiento, tema, evidencias, *, resumen="") -> Path:
    raiz = Path(conocimiento.raiz).absolute()
    tema = " ".join(tema.split())
    citas = list(evidencias)
    if not tema or not citas:
        raise ValueError("Primero investiga un tema con fuentes de la bóveda.")
    if conocimiento.vigentes(citas) != citas:
        raise ValueError("Una fuente cambió. Repite la investigación para guardarla con citas actuales.")
    partes = ["---", "tipo: informe_de_investigacion", f"fecha: {date.today().isoformat()}",
              "---", "", "# " + _texto_plano(tema), ""]
    if resumen:
        partes.extend([_texto_plano(resumen), ""])
    partes.extend(["## Pasajes consultados", ""])
    for cita in citas:
        relativa = Path(cita.ruta)
        if relativa.is_absolute() or relativa.drive or ".." in relativa.parts:
            raise ValueError("La fuente citada debe estar en la bóveda.")
        enlace = "../" + quote(relativa.as_posix(), safe="/")
        partes.extend([f"> {_texto_plano(cita.fragmento)}", "",
                       f"[{_texto_plano(relativa.as_posix())}]({enlace}), línea {cita.linea}.", ""])
    contenido = "\n".join(partes).encode("utf-8")
    carpeta = raiz / "Informes"
    if _enlace(raiz):
        raise ValueError("La bóveda debe ser una carpeta local accesible.")
    carpeta.mkdir(exist_ok=True)
    if _enlace(carpeta) or not carpeta.is_dir():
        raise ValueError("La carpeta Informes debe estar dentro de la bóveda.")
    base = "Investigación - " + nombre_archivo(tema)
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(dir=carpeta, suffix=".tmp", delete=False) as archivo:
            temporal = Path(archivo.name)
            archivo.write(contenido)
        for numero in itertools.count(1):
            sufijo = "" if numero == 1 else f" ({numero})"
            destino = carpeta / (base + sufijo + ".md")
            try:
                if os.name == "nt":
                    temporal.rename(destino)
                else:
                    os.link(temporal, destino)
                break
            except FileExistsError:
                if (not _enlace(destino) and destino.is_file() and destino.stat().st_size == len(contenido)
                        and destino.read_bytes() == contenido):
                    return destino
    finally:
        if temporal:
            temporal.unlink(missing_ok=True)
    try:
        Diario(raiz).registrar("Guardé el informe sobre " + tema, fuente=destino)
    except OSError:
        logging.getLogger("investigacion").exception("El informe se guardó, pero no se pudo actualizar el diario.")
    return destino
