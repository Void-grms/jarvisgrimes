"""Respuestas instantáneas sin LLM: pendientes, correos y hora, desde el caché local."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .store import Item, Store
from .texto import DIAS, MESES, Frase, hora_hablada, normalizar  # noqa: F401  (normalizar se reexporta)

INTENCIONES: list[tuple[str, re.Pattern]] = [
    ("avance", re.compile(r"^(?:en que (?:avanzo|puedo avanzar)|por donde (?:empiezo|comienzo)|"
                          r"que (?:hacemos|hago)(?: (?:ahora|primero))?|cual es mi siguiente paso)"
                          r"(?: (?:ahora|hoy))?$")),
    ("dormir", re.compile(r"^(descansa|duermete|duerme|a dormir|eso es todo|chau|adios|hasta luego|"
                          r"apagate|callate|silencio|modo silencio|no me escuches|deja de escuchar)$")),
    ("actualizar", re.compile(r"^(?:actualiza(?:r)?|sincroniza(?:r)?|refresca(?:r)?|revisa de nuevo|"
                              r"vuelve a revisar)(?: todo| (?:mis |los )?(?:pendientes|correos|tareas)|"
                              r" (?:gmail|clementina))?$")),
    ("correos", re.compile(r"\b(correo\w*|gmail|mail\w*|email\w*|bandeja)\b")),
    ("tareas", re.compile(r"\b(clementina|tareas?|entregas?|trabajos?|foros?|cursos?|maestria|actividad\w*|"
                          r"vence\w*|clases?|evaluacion\w*|examen\w*)\b")),
    ("pendientes", re.compile(r"\b(pendientes?|que tengo|por hacer|que me falta|resumen|agenda)\b")),
    ("hora", re.compile(r"\b(que hora|hora es|ahora es|que horas son|la hora|que dia|fecha de hoy|que fecha)\b")),
]

# Frases típicas por intención: si Whisper entendió casi bien ("que ahora es"), igual se reconoce.
EJEMPLOS = {
    "hora": ["que hora es", "dime la hora", "que horas son", "que dia es hoy", "que fecha es hoy"],
    "pendientes": ["que pendientes tengo", "que tengo que hacer", "que tengo para hoy", "que me falta hacer",
                   "dime mis pendientes", "que tengo pendiente"],
    "tareas": ["que tareas tengo", "que entregas tengo", "que hay en clementina", "que clases tengo"],
    "correos": ["tengo correos nuevos", "revisa mi correo", "tengo mensajes en el correo"],
}


def detectar(texto: str) -> str | None:
    n = Frase(texto).sin_cortesia().txt
    if re.match(r"^(?:que (?:es|son|significa|significan)|como (?:se |funciona|crear|abrir|hacer)|"
                r"para que (?:sirve|sirven)|explicame|que diferencia)\b", n):
        return None
    for nombre, patron in INTENCIONES:
        if patron.search(n):
            return nombre
    return detectar_aproximado(n)


def detectar_aproximado(n: str, umbral: float = 0.82) -> str | None:
    """Coincidencia aproximada con frases típicas (tolera errores de transcripción)."""
    import difflib
    n = re.sub(r"^(?:oye |dime |quiero saber |quiero que me digas |me dices |puedes decirme )", "", n)
    mejor, puntaje = None, 0.0
    for intencion, frases in EJEMPLOS.items():
        for f in frases:
            p = difflib.SequenceMatcher(None, n, f).ratio()
            if p > puntaje:
                mejor, puntaje = intencion, p
    return mejor if puntaje >= umbral else None


def periodo(texto: str) -> str | None:
    n = normalizar(texto)
    if re.search(r"\bhoy\b", n):
        return "hoy"
    if re.search(r"(?<!de la )(?<!en la )\bmanana\b", n):
        return "manana"
    if re.search(r"\bsemana\b", n):
        return "semana"
    return None


class Router:
    def __init__(self, store: Store, zona: str = "America/Lima"):
        self.store = store
        self.tz = ZoneInfo(zona)

    # ---------- fechas en lenguaje natural ----------
    def cuando(self, ts: float | None, tipo: str = "tarea", ahora: datetime | None = None) -> str:
        if ts is None:
            return "sin fecha"
        ahora = ahora or datetime.now(self.tz)
        d = datetime.fromtimestamp(ts, self.tz)
        hora = hora_hablada(d)
        dias = (d.date() - ahora.date()).days
        verbo = "vence" if tipo == "tarea" else "es"
        if d < ahora:
            return "ya empezó" if tipo == "clase" else f"venció el {d.day} de {MESES[d.month - 1]}"
        if dias == 0:
            return f"{verbo} hoy {hora}"
        if dias == 1:
            return f"{verbo} mañana {hora}"
        if dias < 7:
            return f"{verbo} el {DIAS[d.weekday()]} {hora}"
        return f"{verbo} el {d.day} de {MESES[d.month - 1]}"

    # ---------- respuestas ----------
    def responder(self, intencion: str, texto: str = "") -> str:
        if intencion == "hora":
            a = datetime.now(self.tz)
            h12 = a.hour % 12 or 12
            hora = (f"{'Es la' if h12 == 1 else 'Son las'} {h12}" + (f" y {a.minute}" if a.minute else "")
                    + (" de la mañana" if a.hour < 12 else " de la tarde" if a.hour < 19 else " de la noche"))
            return f"{hora}, {DIAS[a.weekday()]} {a.day} de {MESES[a.month - 1]}."
        p = periodo(texto)
        if intencion == "correos":
            return self.resumen_correos(detalle=True)
        if intencion == "tareas":
            return self.resumen_tareas(detalle=True, cuando=p)
        if intencion == "pendientes":
            return f"{self.resumen_tareas(detalle=True, cuando=p)} {self.resumen_correos(detalle=False)}"
        return ""

    def rango(self, cuando: str | None, ahora: datetime | None = None) -> tuple[float, float, str]:
        ahora = ahora or datetime.now(self.tz)
        inicio_hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        if cuando == "hoy":
            return ahora.timestamp() - 3600, (inicio_hoy + timedelta(days=1)).timestamp(), " para hoy"
        if cuando == "manana":
            return ((inicio_hoy + timedelta(days=1)).timestamp(),
                    (inicio_hoy + timedelta(days=2)).timestamp(), " para mañana")
        if cuando == "semana":
            return ahora.timestamp() - 3600, (inicio_hoy + timedelta(days=8)).timestamp(), " esta semana"
        return ahora.timestamp() - 3600, float("inf"), ""

    def resumen_tareas(self, detalle: bool = True, max_items: int = 3, cuando: str | None = None) -> str:
        if self.store.ultimo_sync("clementina") is None:
            return "Todavía no he podido revisar Clementina."
        desde, hasta, etiqueta = self.rango(cuando)
        tareas = [t for t in self.store.items(fuente="clementina")
                  if t.vence_ts is None or desde <= t.vence_ts < hasta]
        if not tareas:
            return f"En Clementina no tienes nada{etiqueta or ' pendiente'}."
        cuenta = {k: sum(t.tipo == k for t in tareas) for k in ("clase", "examen", "tarea")}
        nombres = {"clase": ("clase", "clases"), "examen": ("evaluación", "evaluaciones"),
                   "tarea": ("entrega", "entregas")}
        partes = [f"{n} {nombres[k][n != 1]}" for k, n in cuenta.items() if n]
        lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]
        texto = f"En Clementina tienes{etiqueta} {lista}."
        if detalle:
            texto += " Lo más cercano: " + " ".join(self._tarea(t) for t in tareas[:max_items])
            if len(tareas) > max_items:
                texto += f" Y {len(tareas) - max_items} más."
        return texto

    def _tarea(self, t: Item) -> str:
        curso = f", de {t.curso}" if t.curso else ""
        return f"{t.titulo}{curso}, {self.cuando(t.vence_ts, t.tipo)}."

    def resumen_correos(self, detalle: bool = True, max_items: int = 3) -> str:
        if self.store.ultimo_sync("gmail") is None:
            return "Todavía no he podido revisar tu Gmail."
        correos = sorted(self.store.items(fuente="gmail"), key=lambda c: c.vence_ts or 0, reverse=True)
        if not correos:
            return "No tienes correos importantes sin leer."
        n = len(correos)
        texto = f"Tienes {n} {'correo' if n == 1 else 'correos'} sin leer."
        if detalle:
            for c in correos[:max_items]:
                texto += f" De {c.detalle}: {c.titulo}."
            if n > max_items:
                texto += f" Y {n - max_items} más."
        return texto

    def contexto_breve(self) -> str:
        """Una línea para el LLM (corta, para no alargar el prompt)."""
        a = datetime.now(self.tz)
        nt = len(self.store.items(fuente="clementina"))
        nc = len(self.store.items(fuente="gmail"))
        return (f"Hoy es {DIAS[a.weekday()]} {a.day} de {MESES[a.month - 1]}, "
                f"{a:%H:%M}. Pendientes: {nt} en Clementina, {nc} correos sin leer.")
