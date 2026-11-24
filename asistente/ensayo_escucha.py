"""Ensayo voluntario del reconocimiento local. Solo conserva resultados en memoria."""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass


FRASES = (
    "Jarvis, abre Word.",
    "No abras YouTube.",
    "Abre YouTube.",
    "Quiero que me digas Fernando.",
    "Escribe: abre YouTube.",
    "Busca costos del proyecto en mis notas.",
    "Abre la nota de la tesis en Obsidian.",
    "Prepara mi sesión de tesis.",
    "¿Por qué abriste eso?",
    "Es práctico, yo practico y ayer practicó.",
    "No cierres esa ventana.",
    "Deshazlo.",
)


def _palabras(texto):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", texto.casefold()))


@dataclass(frozen=True)
class Lectura:
    esperada: str
    texto: str
    estado: str
    audio: float
    demora: float

    @property
    def coincide(self):
        return self.estado == "aceptada" and _palabras(self.esperada) == _palabras(self.texto)


class EnsayoEscucha:
    def __init__(self, reloj=time.perf_counter):
        self._reloj = reloj
        self._lock = threading.Lock()
        self._numero = 0
        self._desde = 0.0
        self._activo = False
        self._lecturas = []
        self._motivo = ""

    def _avanzar(self):
        self._numero += 1
        self._desde = self._reloj()

    def iniciar(self):
        with self._lock:
            if not self._activo:
                self._lecturas.clear()
                self._motivo = ""
                self._activo = True
                self._avanzar()

    def detener(self, motivo=""):
        with self._lock:
            if self._activo:
                self._activo = False
                self._motivo = motivo
                self._avanzar()

    @property
    def activo(self):
        with self._lock:
            return self._activo

    def vista(self):
        with self._lock:
            return (self._numero, self._activo), tuple(self._lecturas), self._motivo

    def turno(self, inicio):
        """Rechaza audio que cruce un cambio de modo o de frase, incluso en cola."""
        with self._lock:
            if inicio < self._desde or (self._activo and len(self._lecturas) == len(FRASES)):
                return None
            return self._numero, self._activo

    def registrar(self, turno, texto, estado, audio, demora):
        with self._lock:
            if turno != (self._numero, True) or not self._activo or len(self._lecturas) >= len(FRASES):
                return
            self._lecturas.append(Lectura(FRASES[len(self._lecturas)], texto, estado, audio, demora))
            self._avanzar()
