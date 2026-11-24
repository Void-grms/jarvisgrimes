"""Identifica una pestaña por ventana, control nativo y fragmento único de esta apertura."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psutil

from ..actividad import ResultadoAccion, ResultadoDeshacer
from . import pantalla

_PUENTE = Path(__file__).with_name("pestana_uia.ps1")
_PORTADAS = {"https://www.youtube.com/", "https://www.google.com/"}


@dataclass(frozen=True)
class Apertura:
    url: str
    programa: str

    @classmethod
    def para(cls, url, programa):
        if url in _PORTADAS and programa in {"chrome", "edge"}:
            return cls(f"{url}#jarvis-{uuid4().hex}", "msedge" if programa == "edge" else "chrome")
        return None

    def identificar(self):
        ventana = pantalla._ventana()
        if ventana:
            try:
                proceso = psutil.Process(ventana[1])
                if proceso.name().lower() == self.programa + ".exe":
                    creado = proceso.create_time()
                    direcciones = self._direcciones()
                    datos = pantalla._uia(ventana, "identificar", puente=_PUENTE, urls=direcciones)
                    if isinstance(datos.get("id"), str) and datos["id"]:
                        def cerrar():
                            if pantalla._ventana() != ventana:
                                return ResultadoDeshacer("Selecciona la pestaña original y vuelve a decir «deshazlo».", True)
                            if not proceso.is_running() or psutil.Process(ventana[1]).create_time() != creado:
                                return ResultadoDeshacer("La sesión original del navegador terminó.")
                            cierre = pantalla._uia(ventana, "cerrar", puente=_PUENTE,
                                                   urls=direcciones, id=datos["id"])
                            if cierre.get("cerrada"):
                                return ResultadoDeshacer("Cerré la pestaña que abrí.")
                            if cierre.get("distinta"):
                                return ResultadoDeshacer("No cerré nada. Selecciona la pestaña original para reintentar.", True)
                            return ResultadoDeshacer("No pude verificar el cierre de la pestaña.")
                        return ResultadoAccion("Verifiqué la pestaña abierta.", cerrar)
            except (OSError, psutil.Error):
                pass
        return ResultadoAccion("Solicité la apertura. No pude identificar una pestaña para deshacerla.")

    def _direcciones(self):
        p = urlsplit(self.url)
        recurso = p.path + "#" + p.fragment
        return [prefijo + host + recurso for prefijo in ("", p.scheme + "://")
                for host in {p.netloc, p.netloc.removeprefix("www.")}]
