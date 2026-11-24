"""Resumir textos largos con el modelo local (por partes si el texto es grande)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

ESQUEMA = {"type": "object",
           "properties": {"resumen": {"type": "string"},
                          "puntos": {"type": "array", "items": {"type": "string"}}},
           "required": ["resumen", "puntos"]}
SISTEMA = ("Eres un asistente que resume documentos en español con precisión. No inventes nada "
           "que no esté en el texto. Responde solo con el JSON pedido.")
TROZO = 6000
MAX_TROZOS = 5


def partir(texto: str, tam: int = TROZO) -> list[str]:
    trozos, actual = [], ""
    for parrafo in texto.split("\n"):
        if len(actual) + len(parrafo) > tam and actual:
            trozos.append(actual)
            actual = ""
        actual += parrafo + "\n"
        while len(actual) > tam:
            trozos.append(actual[:tam])
            actual = actual[tam:]
    if actual.strip():
        trozos.append(actual)
    return trozos


def resumir(cerebro, texto: str, titulo: str, modelo: str | None = None) -> dict:
    trozos = partir(texto)
    recortado = len(trozos) > MAX_TROZOS
    trozos = trozos[:MAX_TROZOS]
    if len(trozos) == 1:
        prompt = (f"Resume el documento «{titulo}». Da un resumen de 3 a 5 frases para escucharlo "
                  f"en voz alta y de 3 a 6 puntos clave.\n\nTEXTO:\n{trozos[0]}")
        res = cerebro.generar_json(prompt, ESQUEMA, sistema=SISTEMA, modelo=modelo, max_tokens=700)
    else:
        parciales = []
        for i, t in enumerate(trozos, 1):
            prompt = (f"Parte {i} de {len(trozos)} del documento «{titulo}». Resume esta parte en 2 o 3 "
                      f"frases y extrae sus puntos clave.\n\nTEXTO:\n{t}")
            parciales.append(cerebro.generar_json(prompt, ESQUEMA, sistema=SISTEMA, modelo=modelo, max_tokens=450))
        union = "\n".join(f"- {p['resumen']} " + " ".join(p.get("puntos", [])) for p in parciales)
        prompt = (f"Estos son resúmenes por partes del documento «{titulo}». Únelos en un resumen final "
                  f"de 3 a 5 frases para escucharlo en voz alta y de 4 a 7 puntos clave.\n\n{union}")
        res = cerebro.generar_json(prompt, ESQUEMA, sistema=SISTEMA, modelo=modelo, max_tokens=700)
    res["recortado"] = recortado
    return res


def guardar(res: dict, titulo: str, carpeta: Path) -> Path:
    destino = carpeta / "Resúmenes"
    destino.mkdir(parents=True, exist_ok=True)
    limpio = "".join(c for c in titulo if c not in '<>:"/\\|?*').strip()[:60] or "documento"
    ruta = destino / f"{datetime.now():%Y-%m-%d %H.%M} {limpio}.md"
    puntos = "\n".join(f"- {p}" for p in res.get("puntos", []))
    nota = "\n\n_Solo se resumió la primera parte del documento (es muy largo)._" if res.get("recortado") else ""
    ruta.write_text(f"# Resumen: {titulo}\n\n{res.get('resumen', '')}\n\n## Puntos clave\n\n{puntos}{nota}\n",
                    encoding="utf-8")
    return ruta
