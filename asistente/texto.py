"""Lenguaje hablado en español: normalizar frases, números, duraciones, horas y sí/no.

Todo se compara sobre una versión normalizada (minúsculas, sin tildes ni signos),
pero `Frase` permite recuperar el texto original de lo capturado (con tildes y
mayúsculas), que es lo que se usa para búsquedas, notas o títulos.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DIAS_N = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------- normalización
def sin_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_token(w: str) -> str:
    w = sin_acentos(w.lower())
    w = re.sub(r"(?<=\d)\.(?=\d)", ":", w)          # "5.30" → "5:30"
    return re.sub(r"[^a-z0-9:]", "", w).strip(":")


def normalizar(t: str) -> str:
    return " ".join(n for n in (norm_token(w) for w in t.split()) if n)


_BORDES = "¿?¡!.,;:\"'«»()[]…—-“”‘’ "

_CORTESIA_INICIO = re.compile(
    r"^(?:(?:oye|hey|ey|ya|bueno|ok|okay|okey|ahora|entonces|porfa|por favor|porfavor|"
    r"puedes|podrias|me puedes|me podrias|quiero que|necesito que|quisiera que|"
    r"seria posible|jarvis|hola)\s+)+")
_CORTESIA_FIN = re.compile(r"(?:\s+(?:por favor|porfa|porfavor|gracias|jarvis|ahora|ya))+$")


class Frase:
    """Frase hablada tokenizada: se busca con regex sobre `txt` (normalizado,
    un espacio entre palabras) y se recupera el original con `grupo()`."""

    def __init__(self, texto: str):
        self.texto = (texto or "").strip()
        pares = [(w, norm_token(w)) for w in self.texto.split()]
        pares = [(o, n) for o, n in pares if n]
        self.orig = [o for o, _ in pares]
        self.norm = [n for _, n in pares]
        self.txt = " ".join(self.norm)
        self._ini: list[int] = []
        pos = 0
        for n in self.norm:
            self._ini.append(pos)
            pos += len(n) + 1

    def _indices(self, a: int, b: int) -> list[int]:
        return [i for i, s in enumerate(self._ini) if s >= a and s + len(self.norm[i]) <= b]

    def original(self, a: int = 0, b: int | None = None) -> str:
        b = len(self.txt) if b is None else b
        return " ".join(self.orig[i] for i in self._indices(a, b)).strip(_BORDES)

    def grupo(self, m: re.Match, nombre: str | int) -> str:
        try:
            a, b = m.span(nombre)
        except (IndexError, error_regex):
            return ""
        return "" if a < 0 else self.original(a, b)

    def sin_span(self, a: int, b: int) -> "Frase":
        quitar = set(self._indices(a, b))
        return Frase(" ".join(o for i, o in enumerate(self.orig) if i not in quitar))

    def sin_cortesia(self) -> "Frase":
        f = self
        for _ in range(3):
            m = _CORTESIA_INICIO.match(f.txt)
            if m and m.end() and m.end() < len(f.txt):
                f = f.sin_span(0, m.end())
            m = _CORTESIA_FIN.search(f.txt)
            if m and m.start() > 0:
                f = f.sin_span(m.start(), len(f.txt))
        return f

    def __repr__(self) -> str:
        return f"Frase({self.txt!r})"


error_regex = re.error


def quitar_conectores(texto: str, conectores: tuple[str, ...] = ("que", "de", "para", "sobre")) -> str:
    """Quita conectores sueltos al inicio ("que saque la ropa" → "saque la ropa").
    No toca "qué" con tilde (interrogativo)."""
    palabras = texto.split()
    while palabras and palabras[0].lower().strip(_BORDES) in conectores:
        palabras.pop(0)
    return " ".join(palabras).strip(_BORDES)


# ---------------------------------------------------------------- sí / no
_SI = re.compile(r"^(?:si|sip|claro|dale|ok|okay|okey|de acuerdo|hazlo|adelante|afirmativo|"
                 r"correcto|exacto|listo|perfecto|va|sale|confirmo|confirmado|obvio|"
                 r"seguro|por supuesto|ya|esta bien|bueno)\b")
_NO = re.compile(r"^(?:no|nop|nel|negativo|mejor no|cancela|cancelalo|olvidalo|dejalo|"
                 r"detente|para|ninguno|tampoco|ni hablar)\b")


def es_si(t: str) -> bool:
    return bool(_SI.match(normalizar(t)))


def confirma_accion(t: str) -> bool:
    """Un «sí» seguido de una objeción no autoriza una operación pendiente."""
    return re.fullmatch(
        r"(?:si(?: (?:si|claro|dale|hazlo|adelante|confirmo|abrel[oa]))?|confirmo|confirmado|"
        r"de acuerdo|adelante|afirmativo|hazlo|abrel[oa]|claro|dale|correcto|ok|okay|por supuesto)"
        r"(?: por favor| gracias)?", normalizar(t)
    ) is not None


def es_no(t: str) -> bool:
    return bool(_NO.match(normalizar(t)))


# ---------------------------------------------------------------- números
_PALABRAS_NUM = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "noventa": 90, "cien": 100,
}
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50}
NUM = (r"(?:\d+|(?:treinta|cuarenta|cincuenta) y (?:uno|una|un|dos|tres|cuatro|cinco|seis|"
       r"siete|ocho|nueve)|" + "|".join(sorted(_PALABRAS_NUM, key=len, reverse=True)) + ")")


def valor_numero(s: str) -> int | None:
    s = s.strip()
    if s.isdigit():
        return int(s)
    m = re.fullmatch(r"(treinta|cuarenta|cincuenta) y (\w+)", s)
    if m:
        return _DECENAS[m.group(1)] + _PALABRAS_NUM.get(m.group(2), 0)
    return _PALABRAS_NUM.get(s)


def numeros_a_digitos(txt: str) -> str:
    return re.sub(rf"\b{NUM}\b", lambda m: str(valor_numero(m.group(0))), txt)


# ---------------------------------------------------------------- duraciones
_SEG = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _unidad(u: str) -> str:
    if u.startswith("seg"):
        return "s"
    if u.startswith("min"):
        return "m"
    if u.startswith(("hor", "hr")):
        return "h"
    if u.startswith("dia"):
        return "d"
    return "w"


DUR_RE = re.compile(
    rf"(?:\b(?:en|dentro de|por|de|durante) )?\b(?:(?P<n>{NUM}) (?P<u>segundos?|minutos?|mins?|"
    rf"horas?|hrs?|dias?|semanas?)(?: y (?:(?P<media>media)|(?P<n2>{NUM}) (?P<u2>minutos?|"
    rf"segundos?)))?|(?P<esp>media hora|un cuarto de hora|cuarto de hora|hora y media|"
    rf"un par de minutos|un minutito|un ratito))\b")

_ESPECIALES = {"media hora": 1800, "un cuarto de hora": 900, "cuarto de hora": 900,
               "hora y media": 5400, "un par de minutos": 120, "un minutito": 60,
               "un ratito": 600}


def parse_duracion(txt: str) -> tuple[int, tuple[int, int]] | None:
    m = DUR_RE.search(txt)
    if not m:
        return None
    if m.group("esp"):
        return _ESPECIALES[m.group("esp")], m.span()
    n = valor_numero(m.group("n")) or 0
    u = _unidad(m.group("u"))
    seg = n * _SEG[u]
    if m.group("media"):
        seg += _SEG[u] // 2
    if m.group("n2"):
        seg += (valor_numero(m.group("n2")) or 0) * _SEG[_unidad(m.group("u2"))]
    return (seg, m.span()) if seg > 0 else None


def duracion_hablada(seg: int) -> str:
    if seg < 60:
        return f"{seg} segundos"
    minutos = round(seg / 60)
    h, m = divmod(minutos, 60)
    partes = []
    if h:
        partes.append("una hora" if h == 1 else f"{h} horas")
    if m:
        partes.append("un minuto" if m == 1 else f"{m} minutos")
    return " y ".join(partes)


# ---------------------------------------------------------------- horas
_DIA = (r"(?:pasado manana|manana|hoy|el lunes|el martes|el miercoles|el jueves|el viernes|"
        r"el sabado|el domingo)")
HORA_RE = re.compile(
    rf"(?:\b(?P<dia>{_DIA}) )?\b(?:(?:a|para|hasta) (?:las|la)|alas|a eso de las) "
    rf"(?P<h>\d{{1,2}}(?::\d{{2}})?|{NUM})"
    rf"(?: y (?:(?P<media>media)|(?P<cuarto>cuarto)|(?P<mm>{NUM})))?"
    rf"(?: menos (?:(?P<mcuarto>cuarto)|(?P<mmenos>{NUM})))?"
    rf"(?: (?:de la|en la|por la) (?P<per>manana|tarde|noche|madrugada)| (?P<ampm>am|pm|a m|p m))?"
    rf"(?: (?:del |de )?(?P<dia2>{_DIA}))?\b")
MEDIODIA_RE = re.compile(rf"(?:\b(?P<dia>{_DIA}) )?\b(?:al|a el|para el|a la) (?P<x>mediodia|medianoche)\b")
SOLO_DIA_RE = re.compile(rf"(?<!de la )(?<!en la )(?<!por la )\b(?P<dia>{_DIA})\b")


def _base_dia(dia: str | None, ahora: datetime) -> tuple[date, bool]:
    """Devuelve (fecha, es_dia_semana_hoy). Día de semana igual a hoy → hoy (se corrige luego)."""
    if not dia or dia == "hoy":
        return ahora.date(), False
    if dia == "manana":
        return ahora.date() + timedelta(days=1), False
    if dia == "pasado manana":
        return ahora.date() + timedelta(days=2), False
    idx = DIAS_N.index(dia.replace("el ", ""))
    delta = (idx - ahora.weekday()) % 7
    return ahora.date() + timedelta(days=delta), delta == 0


def parse_hora(txt: str, ahora: datetime) -> tuple[datetime, tuple[int, int]] | None:
    tz = ahora.tzinfo
    m = HORA_RE.search(txt)
    if m:
        hs = m.group("h")
        if ":" in hs:
            h, mi = (int(x) for x in hs.split(":")[:2])
        else:
            h, mi = valor_numero(hs) or 0, 0
        if m.group("media"):
            mi = 30
        elif m.group("cuarto"):
            mi = 15
        elif m.group("mm"):
            mi = valor_numero(m.group("mm")) or 0
        if m.group("mcuarto") or m.group("mmenos"):
            h -= 1
            mi = 60 - (15 if m.group("mcuarto") else (valor_numero(m.group("mmenos")) or 0))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        per, ampm = m.group("per"), (m.group("ampm") or "").replace(" ", "")
        explicito = bool(per or ampm or h >= 13 or h == 0)
        if per in ("tarde", "noche") and h < 12:
            h += 12
        if per in ("manana", "madrugada") and h == 12:
            h = 0
        if ampm == "pm" and h < 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
        dia = m.group("dia") or m.group("dia2")
        fecha, mismo = _base_dia(dia, ahora) if dia and dia != "hoy" else (ahora.date(), True)
        if not mismo:
            if not explicito and 1 <= h <= 6:
                h += 12  # "mañana a las 3" casi siempre es de la tarde
            dt = datetime.combine(fecha, datetime.min.time(), tz).replace(hour=h, minute=mi)
            return dt, m.span()
        # Hoy (o "el miércoles" siendo miércoles): la próxima vez que llegue esa hora.
        candidatos = [h] if explicito or h >= 12 else [h, h + 12]
        for hh in candidatos:
            dt = ahora.replace(hour=hh, minute=mi, second=0, microsecond=0)
            if dt > ahora:
                return dt, m.span()
        hh = h + 12 if not explicito and 1 <= h <= 6 else candidatos[0]
        dias = 7 if dia and dia.startswith("el ") else 1
        dt = ahora.replace(hour=hh, minute=mi, second=0, microsecond=0) + timedelta(days=dias)
        return dt, m.span()

    m = MEDIODIA_RE.search(txt)
    if m:
        fecha, _ = _base_dia(m.group("dia"), ahora)
        h = 12 if m.group("x") == "mediodia" else 0
        dt = datetime.combine(fecha, datetime.min.time(), tz).replace(hour=h)
        if dt <= ahora:
            dt += timedelta(days=1)
        return dt, m.span()

    m = SOLO_DIA_RE.search(txt)
    if m and m.group("dia") != "hoy":
        fecha, mismo = _base_dia(m.group("dia"), ahora)
        dt = datetime.combine(fecha, datetime.min.time(), tz).replace(hour=9)
        if dt <= ahora:
            dt += timedelta(days=7 if mismo else 1)
        return dt, m.span()
    return None


def hora_hablada(d: datetime) -> str:
    h, m = d.hour, d.minute
    if h == 0 and m == 0:
        return "a medianoche"
    if h == 12 and m == 0:
        return "al mediodía"
    sufijo = "de la mañana" if h < 12 else ("de la tarde" if h < 19 else "de la noche")
    h12 = h % 12 or 12
    base = "a la 1" if h12 == 1 else f"a las {h12}"
    if m == 30:
        base += " y media"
    elif m == 15:
        base += " y cuarto"
    elif m:
        base += f" y {m}"
    return f"{base} {sufijo}"


def cuando_hablado(d: datetime, ahora: datetime) -> str:
    dias = (d.date() - ahora.date()).days
    hora = hora_hablada(d)
    if dias == 0:
        return f"hoy {hora}"
    if dias == 1:
        return f"mañana {hora}"
    if 1 < dias < 7:
        return f"el {DIAS[d.weekday()]} {hora}"
    return f"el {d.day} de {MESES[d.month - 1]} {hora}"


RANGO_RE = re.compile(
    rf"(?:\b(?P<dia>{_DIA}) )?\bde (?P<a>\d{{1,2}}(?::\d{{2}})?|{NUM})(?P<ma> y (?:media|cuarto))? a "
    rf"(?P<b>\d{{1,2}}(?::\d{{2}})?|{NUM})(?P<mb> y (?:media|cuarto))?"
    rf"(?P<per> (?:de la|en la|por la) (?:manana|tarde|noche))?(?: (?:del |de )?(?P<dia2>{_DIA}))?\b")


def parse_fecha_hora(txt: str, ahora: datetime) -> tuple[datetime, tuple[int, int], bool] | None:
    """Como parse_hora, pero dice si se mencionó una hora (False = solo un día → todo el día)."""
    r = parse_hora(txt, ahora)
    if not r:
        return None
    con_hora = bool(HORA_RE.search(txt) or MEDIODIA_RE.search(txt))
    return r[0], r[1], con_hora


def parse_rango(txt: str, ahora: datetime) -> tuple[datetime, datetime, list[tuple[int, int]]] | None:
    """'el viernes de 3 a 5 de la tarde' → (inicio, fin, [tramos del texto usados])."""
    m = RANGO_RE.search(txt)
    if not m:
        return None
    tramos = [m.span()]
    dia = m.group("dia") or m.group("dia2")
    if not dia:
        d = SOLO_DIA_RE.search(txt)
        if d:
            dia = d.group("dia")
            tramos.append(d.span())
    dia = f"{dia} " if dia else ""
    per = m.group("per") or ""
    r1 = parse_hora(f"{dia}a las {m.group('a')}{m.group('ma') or ''}{per}", ahora)
    r2 = parse_hora(f"{dia}a las {m.group('b')}{m.group('mb') or ''}{per}", ahora)
    if not (r1 and r2):
        return None
    ini, fin = r1[0], r2[0]
    if fin <= ini:
        fin = ini.replace(hour=fin.hour, minute=fin.minute)
        while fin <= ini:
            fin += timedelta(hours=12)
    return ini, fin, tramos
