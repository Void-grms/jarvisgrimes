"""Notas rápidas por voz, guardadas en Documentos\\Jarvis\\notas.md."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


class Notas:
    def __init__(self, carpeta: Path):
        self.ruta = carpeta / "notas.md"

    def agregar(self, texto: str) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not self.ruta.exists()
        with open(self.ruta, "a", encoding="utf-8") as f:
            if nuevo:
                f.write("# Notas de Jarvis\n\n")
            f.write(f"- [{datetime.now():%d/%m %H:%M}] {texto}\n")

    def ultimas(self, n: int = 5) -> list[str]:
        if not self.ruta.exists():
            return []
        lineas = [l for l in self.ruta.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
        return [re.sub(r"^- \[[^\]]*\]\s*", "", l) for l in lineas[-n:]]

    def borrar(self) -> None:
        if self.ruta.exists():
            self.ruta.rename(self.ruta.with_name(f"notas borradas {datetime.now():%Y-%m-%d %H.%M}.md"))
