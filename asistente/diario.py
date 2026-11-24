"""Diario de trabajo local, legible y editable como nota diaria de Obsidian."""
from __future__ import annotations

import os
import re
import stat
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from filelock import FileLock

from .respuesta import Respuesta
from .texto import DIAS_N, MESES, Frase, numeros_a_digitos, sin_acentos


_PREGUNTA = re.compile(
    r"^(?:que (?:hicimos|hemos hecho)|en que (?:trabajamos|hemos trabajado)|"
    r"(?:lee|muestrame|consulta) (?:el |mi )?diario (?:de|del))"
    r" (?:el |la )?(?:dia )?(?P<fecha>.+)$")


def _fecha(pedido: str, hoy: date) -> date | None:
    pedido = numeros_a_digitos(sin_acentos(pedido.lower().strip()))
    if pedido in {"hoy", "ayer", "anteayer", "antes de ayer"}:
        dias = {"hoy": 0, "ayer": 1, "anteayer": 2, "antes de ayer": 2}
        return hoy - timedelta(days=dias[pedido])
    dia = pedido.removeprefix("pasado ").removesuffix(" pasado")
    if dia in DIAS_N:
        diferencia = (hoy.weekday() - DIAS_N.index(dia)) % 7
        return hoy - timedelta(days=diferencia or (7 if "pasado" in pedido else 0))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pedido):
        return date.fromisoformat(pedido)
    numerica = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", pedido)
    if numerica:
        dia, mes, anio = map(int, numerica.groups())
        return date(anio, mes, dia)
    fecha = re.fullmatch(r"(\d{1,2}) de ([a-z]+)(?: (?:de|del) (2 mil )?(\d{1,4}))?", pedido)
    if fecha:
        mes = fecha[2].replace("setiembre", "septiembre")
        if mes not in MESES:
            raise ValueError("Mes desconocido.")
        anio = int(fecha[4]) + (2000 if fecha[3] else 0) if fecha[4] else hoy.year
        return date(anio, MESES.index(mes) + 1, int(fecha[1]))
    return None


def _sin_enlace(ruta: Path):
    try:
        datos = ruta.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(datos.st_mode) or getattr(datos, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError("La ubicación del diario es un enlace. Elige una carpeta local de la bóveda.")


class Diario:
    def __init__(self, raiz: Path, *, reloj=datetime.now):
        self.raiz = Path(raiz).absolute()
        self.reloj = reloj

    def _ruta(self, fecha: date) -> Path:
        carpeta = self.raiz / "Diario"
        ruta = carpeta / (fecha.isoformat() + ".md")
        for parte in (self.raiz, carpeta, ruta):
            _sin_enlace(parte)
        return ruta

    def _referencia(self, fuente: str | Path | None) -> str:
        if not fuente:
            return ""
        ruta = self.raiz / fuente
        if not ruta.is_relative_to(self.raiz) or ".." in ruta.parts or ruta.suffix.lower() != ".md":
            return ""
        relativa = ruta.relative_to(self.raiz)
        try:
            parte = self.raiz
            for componente in relativa.parts:
                parte = parte / componente
                _sin_enlace(parte)
            if not ruta.is_file():
                return ""
        except OSError:
            return ""
        # La URL relativa respeta caracteres válidos en nombres de notas, como # o [.
        return " · [Ver nota](../" + quote(relativa.as_posix(), safe="/") + ")"

    def registrar(self, texto: str, *, fuente: str | Path | None = None) -> Path:
        """Añade un hecho confirmado. Devuelve el archivo que lo contiene."""
        texto = " ".join(texto.split())
        if not texto:
            raise ValueError("La entrada del diario está vacía.")
        instante = self.reloj()
        ruta = self._ruta(instante.date())
        ruta.parent.mkdir(parents=True, exist_ok=True)
        candado = ruta.parent / ".jarvis-diario.lock"
        _sin_enlace(candado)
        with FileLock(str(candado), timeout=5):
            self._ruta(instante.date())
            enlace = self._referencia(fuente)
            # Escribir al final evita reescribir los apuntes de Obsidian, incluso sin salto final.
            with ruta.open("a+b") as nota:
                nota.seek(0, os.SEEK_END)
                if nota.tell() == 0:
                    prefijo = f"# Diario del {instante.date().isoformat()}\n\n"
                else:
                    nota.seek(-1, os.SEEK_END)
                    prefijo = "" if nota.read(1) == b"\n" else "\n"
                entrada = f"{prefijo}- {instante:%H:%M} {texto}{enlace}\n"
                nota.write(entrada.encode("utf-8"))
                nota.flush()
                os.fsync(nota.fileno())
        return ruta

    def consultar(self, fecha: date) -> str:
        etiqueta = f"{fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}"
        ultimas = deque(maxlen=3)
        total = 0
        try:
            with self._ruta(fecha).open(encoding="utf-8-sig") as nota:
                cabecera = False
                for numero, linea in enumerate(nota):
                    linea = linea.strip()
                    if linea == "---" and (numero == 0 or cabecera):
                        cabecera = not cabecera
                        continue
                    if cabecera or not linea or linea.startswith(("#", "<!--", "---")):
                        continue
                    linea = re.sub(r"^(?:[-*]\s+)?(?:\d{2}:\d{2}\s+)?", "", linea)
                    linea = re.sub(r" · \[Ver nota\]\(\.\./[^)]*\)$", "", linea)
                    linea = re.sub(r"\[\[(?:[^]\n|]+\|)?([^]\n]+)\]\]", r"\1", linea)
                    if linea:
                        total += 1
                        ultimas.append(linea[:220].rstrip(" .") + ("…" if len(linea) > 220 else "."))
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            return f"No pude leer la nota del diario del {etiqueta}."
        if not total:
            return f"No hay actividades anotadas para el {etiqueta}."
        aviso = f" Te leo las tres últimas de {total} entradas." if total > len(ultimas) else ""
        return f"Diario del {etiqueta}.{aviso} " + " ".join(ultimas)

    def procesar(self, texto: str) -> Respuesta | None:
        frase = Frase(texto).sin_cortesia()
        m = _PREGUNTA.fullmatch(frase.txt)
        if not m:
            return None
        fecha_pedida = frase.grupo(m, "fecha")
        try:
            fecha = _fecha(fecha_pedida, self.reloj().date())
        except ValueError:
            return Respuesta("Esa fecha no es válida. Dime el día, el mes y el año.")
        if fecha is None:
            return None
        return Respuesta(self.consultar(fecha))
