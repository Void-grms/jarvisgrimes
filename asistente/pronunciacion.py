"""Diccionario de lectura para la salida hablada de Jarvis."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


_MARCAS = {"youtube": "Yutúb", "whatsapp": "Guatsáp", "jarvis": "Yárvis", "wi-fi": "wifi"}
_PALABRA = re.compile(r"[^\W_]+(?:[ .’'-][^\W_]+)*\.?", re.UNICODE)
# Reservar código, URLs, rutas y nombres con extensión antes de buscar las palabras del diccionario.
_LITERAL = (r"\x60[^\x60\n]*\x60|(?:https?://|www\.)[^\s<>]+|"
            r"(?:[a-z]:[\\/]|\\\\)[^\n<>\"|]*?(?:\.[a-z0-9]{1,9}\b|(?=[,;!?\n]|$))|"
            r"[«\"“][^»\"”\n]*?\.[a-z0-9]{1,9}[»\"”]|"
            r"\b(?:archivo|documento|copia|presentación|informe|versión)\s+[^\\/:*?\"<>|,;\n]{1,160}?"
            r"\.(?:docx?|pptx?|xlsx?|pdf|md|txt|csv|jpg|png|zip)\b|"
            r"(?<!\w)[^\s\\/:*?\"<>|]+\.[a-z0-9]{1,9}\b")


def validar_pronunciaciones(valores):
    if not isinstance(valores, dict) or len(valores) > 200:
        raise ValueError("El diccionario admite hasta 200 pronunciaciones.")
    resultado = {}
    vistas = set()
    for escrita, hablada in valores.items():
        if not all(isinstance(s, str) for s in (escrita, hablada)):
            raise ValueError("Cada pronunciación necesita una palabra y su lectura.")
        escrita, hablada = (unicodedata.normalize("NFC", s.strip()) for s in (escrita, hablada))
        if not all(1 <= len(s) <= 80 and _PALABRA.fullmatch(s) and any(c.isalpha() for c in s)
                   for s in (escrita, hablada)):
            raise ValueError("Usa palabras o nombres de hasta 80 caracteres en cada campo.")
        if escrita.casefold() in vistas:
            raise ValueError(f"Ya hay una pronunciación para «{escrita}».")
        vistas.add(escrita.casefold())
        resultado[escrita] = hablada
    return resultado


@lru_cache(maxsize=8)
def _patron(claves):
    palabras = "|".join(re.escape(p) for p in sorted(claves, key=len, reverse=True))
    return re.compile(r"(?P<literal>" + _LITERAL + r")|(?<!\w)(?P<palabra>" + palabras + r")(?!\w)", re.I)


def aplicar_pronunciaciones(texto, personales):
    palabras = dict(_MARCAS)
    palabras.update((k.casefold(), v) for k, v in (personales or {}).items())
    patron = _patron(tuple(palabras))
    return patron.sub(lambda m: palabras.get(m[0].casefold(), m[0]) if m.lastgroup == "palabra" else m[0], texto)
