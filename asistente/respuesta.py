"""Qué hace el asistente después de entender una frase."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass
class Respuesta:
    texto: str | Iterable[str] = ""                    # lo que dice (str o frases en streaming)
    accion: Callable[[], None] | None = None           # se ejecuta en paralelo mientras habla
    despues: Callable[[], None] | None = None          # se ejecuta cuando termina de hablar
    siguiente: Callable[[str], "Respuesta | None"] | None = None  # espera tu próxima frase
    espera_s: float = 60                               # cuánto espera esa respuesta
    al_expirar: Callable[[], "Respuesta | None"] | None = None
    al_cancelar: Callable[[], None] | None = None      # limpieza si dices "ya no" / "cancela"
    dormir: bool = False                               # queda en silencio hasta oír "Jarvis"
    relleno: bool = False                              # dice "Mmm, déjame pensar" mientras genera
    confirmar_voz: str = ""                            # pregunta antes de una apertura por voz sin invocación
    ejecutar: Callable[[], str] | None = None          # el resultado se comunica después de actuar
    origen: str = ""                                  # petición original si hay confirmación
    interpretacion: str = ""                          # operación resuelta, incluida su aclaración
