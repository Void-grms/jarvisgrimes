"""Recibo de la última operación de esta sesión, compartido con el widget."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from .respuesta import Respuesta
from .texto import normalizar


@dataclass(frozen=True)
class ResultadoDeshacer:
    mensaje: str
    reintentar: bool = False


@dataclass(frozen=True)
class ResultadoAccion:
    mensaje: str
    deshacer: Callable[[], ResultadoDeshacer] | None = None


@dataclass
class Recibo:
    frase: str
    interpretacion: str
    resultado: str
    deshacer: Callable[[], ResultadoDeshacer] | None = None


class Actividad:
    def __init__(self):
        self._lock = RLock()
        self._ultimo: Recibo | None = None

    def detalle(self) -> str:
        with self._lock:
            r = self._ultimo
            if not r:
                return "Todavía no hay acciones registradas en esta sesión."
            return (f"Petición\n{r.frase}\n\nInterpretación\n{r.interpretacion}\n\nResultado\n{r.resultado}"
                    + ("\n\nPuedes decir «deshazlo»." if r.deshacer else ""))

    def dictado(self, frase: str, mensaje: str):
        with self._lock:
            self._ultimo = Recibo(frase, "Dictado en la ventana activa", mensaje or "Se envió la entrada de teclado.")

    def preparar(self, frase: str, r: Respuesta, *, orden=""):
        operaciones = [(k, fn) for k in ("ejecutar", "accion", "despues") if (fn := getattr(r, k))]
        if not operaciones:
            # Algunos comandos (p. ej., guardar una memoria) producen su resultado al resolverse.
            # Una orden posterior jamás debe deshacer una pestaña de un encargo anterior.
            if r.siguiente or r.relleno or not isinstance(r.texto, str) or not r.texto:
                return
            if not re.match(r"^(?:abre|guarda|recuerda que|escribe|crea|registra|elimina|cierra|"
                            r"modifica|actualiza|vincula|deshaz|borra|anota|cambia)\b", normalizar(orden or frase)):
                return
        interpretacion = r.interpretacion or r.confirmar_voz.strip("¿?") or (
            r.texto if operaciones and isinstance(r.texto, str) and r.texto else frase)
        recibo = Recibo(frase, interpretacion, "Pendiente de ejecución." if operaciones else r.texto)
        with self._lock:
            self._ultimo = recibo

        def envolver(fn):
            def ejecutar():
                with self._lock:
                    recibo.resultado = "En curso."
                try:
                    valor = fn()
                except Exception:
                    with self._lock:
                        recibo.resultado = "La operación falló."
                        recibo.deshacer = None
                    raise
                if isinstance(valor, ResultadoAccion):
                    resultado = valor
                elif isinstance(valor, str):
                    resultado = ResultadoAccion(valor)
                elif valor is False:
                    resultado = ResultadoAccion("No pude verificar el resultado.")
                else:
                    resultado = ResultadoAccion("Se envió la orden.")
                with self._lock:
                    recibo.resultado, recibo.deshacer = resultado.mensaje, resultado.deshacer
                return valor
            return ejecutar
        for nombre, operacion in operaciones:
            setattr(r, nombre, envolver(operacion))

    def consultar(self, n: str) -> Respuesta | None:
        n = n.removeprefix("por favor ").removesuffix(" por favor").removesuffix(" porfa")
        if re.fullmatch(r"(?:deshazlo|deshaz eso|deshaz (?:esa|la ultima) apertura|revierte esa apertura)", n):
            with self._lock:
                r = self._ultimo
            return Respuesta(ejecutar=lambda: self._deshacer(r))
        m = re.fullmatch(r"por que (?:abriste|abrir este|hiciste|ejecutaste) (.+)|que (?:acabas de hacer|hiciste)"
                         r"|(?:explica|muestra) (?:la|tu) ultima accion", n)
        if m:
            with self._lock:
                r = self._ultimo
                if not r:
                    return Respuesta(self.detalle())
                tema = m[1]
                if tema and tema not in {"eso", "esto", "esa pagina", "esa pestana", "esa aplicacion"}:
                    if tema not in normalizar(r.interpretacion + " " + r.frase):
                        return Respuesta(f"Mi última acción registrada fue «{r.interpretacion}».")
                return Respuesta(f"Recibí «{r.frase}». Interpreté «{r.interpretacion}». {r.resultado}")
        return None

    def _deshacer(self, r: Recibo | None) -> str:
        with self._lock:
            if r and r is self._ultimo and r.resultado in {"En curso.", "Pendiente de ejecución."}:
                return "La acción sigue en curso. Vuelve a decir «deshazlo» cuando termine."
            if not r or r is not self._ultimo or not r.deshacer:
                return "La última acción no tiene un deshacer disponible."
            operacion, r.deshacer = r.deshacer, None
        try:
            resultado = operacion()
        except Exception:
            resultado = ResultadoDeshacer("No pude verificar el resultado del cierre.")
        with self._lock:
            r.resultado = resultado.mensaje
            if resultado.reintentar:
                r.deshacer = operacion
        return resultado.mensaje
