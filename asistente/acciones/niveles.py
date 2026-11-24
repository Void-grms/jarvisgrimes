"""Volumen de la salida predeterminada y brillo de los monitores compatibles."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from ..texto import Frase, normalizar, valor_numero

log = logging.getLogger("niveles")
_CONTROL = r"(?:volumen|sonido|brillo)"
_SUBIR = {"sube", "subele", "subas", "aumenta", "aumentes", "eleva"}
_BAJAR = {"baja", "bajale", "bajes", "disminuye", "reduzcas", "reduce"}
_VERBO = "|".join(sorted(_SUBIR | _BAJAR | {"pon", "ponme", "pongas", "ajusta", "ajustes", "deja", "dejes"}))
_OBJETO = rf"(?:el |mi |todo el |todo mi )?(?:nivel (?:del? )?)?(?P<control>{_CONTROL})"
_ORDEN = re.compile(rf"(?:(?P<verbo>{_VERBO}) )?{_OBJETO}(?: de (?:la )?pantalla)?(?: (?P<resto>.*))?")
_VALOR = re.compile(r"(?:(?P<prep>al|a|en|hasta el|hasta|un) )?(?P<n>.+?)(?: por ciento| puntos?)?")
_MUTE = re.compile(r"(?P<off>silencia(?:lo)?(?: el (?:volumen|sonido|audio))?|mutea|mute|quita el sonido|"
                   r"quita el audio)|(?P<on>activa el sonido|quita el silencio|desmutea|devuelve el sonido)")
_CONSULTA = re.compile(rf"(?:(?:dime )?(?:en cuanto (?:esta|quedo)|(?:a )?cuanto (?:esta|tengo)|"
                       rf"cual es|como esta|a que nivel esta|comprueba|revisa|verifica|consulta|dime) "
                       rf"(?:el |mi )?(?:nivel (?:del? )?)?(?P<a>{_CONTROL})(?: actual| de la pantalla)?|"
                       rf"(?:dime )?(?:que|cuanto) (?P<b>{_CONTROL}) tengo)")
_QUEJA = re.compile(r"^(?:estoy revisando y |pero )?(?:no (?:ha |has |se ha |se |veo que |veo que se )"
                    r"(?:subi|suba|baj|cambi|ajust)|(?:solamente|apenas|solo) (?:ha |has |se ha )?"
                    r"(?:subido|bajado|cambiado)|sigue (?:igual|al? ))")


@dataclass(frozen=True)
class Ajuste:
    control: str
    modo: str
    valor: int = 0

    def objetivo(self, actual: int) -> int:
        nuevo = actual + self.valor if self.modo == "variar" else self.valor
        return min(100, max(0, nuevo))


def _numero(texto: str) -> int | None:
    if texto in ("maximo", "tope", "cien", "ciento"):
        return 100
    if texto in ("minimo", "cero"):
        return 0
    decenas = {"sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
    decena, _, unidad = texto.partition(" y ")
    if decena in decenas:
        u = valor_numero(unidad) if unidad else 0
        return decenas[decena] + u if u is not None and 0 <= u <= 9 else None
    if " y " in texto and valor_numero(unidad) not in range(1, 10):
        return None
    return valor_numero(texto)


def interpretar(texto: str, anterior: str = "") -> Ajuste | None:
    n = normalizar(texto)
    n = Frase(re.sub(r"^(?:a ver |este |ya )+", "", n)).sin_cortesia().txt

    def control(palabra):
        return "volumen" if palabra == "sonido" else palabra

    if _QUEJA.match(n):
        mencion = re.search(rf"\b{_CONTROL}\b", n)
        destino = control(mencion[0]) if mencion else anterior
        return Ajuste(destino, "leer") if destino else None
    if m := _CONSULTA.fullmatch(n):
        return Ajuste(control(m["a"] or m["b"]), "leer")
    if m := _MUTE.fullmatch(n):
        return Ajuste("volumen", "silenciar" if m["off"] else "activar")
    if anterior and re.fullmatch(r"(?:al|hasta el) .+", n):
        # Rectificación inmediata como «al 100 %» tras un pedido con destino.
        n = f"pon el {anterior} {n}"
    if m := re.fullmatch(rf"(?:(mas|menos) ({_CONTROL})|({_CONTROL}) mas (alto|bajo))", n):
        return Ajuste(control(m[2] or m[3]), "variar", 10 if (m[1] or m[4]) in ("mas", "alto") else -10)
    m = _ORDEN.fullmatch(n)
    if not m:
        return None
    destino = control(m["control"])
    verbo = m["verbo"]
    resto = re.sub(r"^(?:todo\b *)+", "", m["resto"] or "")
    sube, baja = verbo in _SUBIR, verbo in _BAJAR
    if not resto:
        if (sube or baja) and "todo" in n.split():
            return Ajuste(destino, "fijar", 100 if sube else 0)
        if sube or baja:
            return Ajuste(destino, "variar", 10 if sube else -10)
        return Ajuste(destino, "leer" if not verbo else "aclarar")
    valor = _VALOR.fullmatch(resto)
    cantidad = _numero(valor["n"]) if valor else None
    if re.search(r"\d[.,]\d|-\s*\d", texto) or cantidad is None or not 0 <= cantidad <= 100:
        # Un destino ilegible nunca se convierte en un incremento por defecto.
        return Ajuste(destino, "aclarar") if verbo else None
    relativo = ((sube or baja) and valor["prep"] not in ("a", "al", "hasta", "hasta el")
                and valor["n"] not in ("maximo", "minimo", "tope"))
    if relativo:
        return Ajuste(destino, "variar", -cantidad if baja else cantidad)
    return Ajuste(destino, "fijar", cantidad)


def _volumen(ajuste: Ajuste, salida) -> str:
    if ajuste.modo in ("silenciar", "activar"):
        salida.SetMute(ajuste.modo == "silenciar", None)
    elif ajuste.modo != "leer":
        actual = round(100 * salida.GetMasterVolumeLevelScalar())
        salida.SetMasterVolumeLevelScalar(ajuste.objetivo(actual) / 100, None)
    real = round(100 * salida.GetMasterVolumeLevelScalar())
    silencio = bool(salida.GetMute())
    return f"El volumen está al {real} por ciento" + (" y silenciado." if silencio else ".")


def _brillo(ajuste: Ajuste, pantallas) -> str:
    resultados = []
    for i, pantalla in enumerate(pantallas, 1):
        try:
            actual = pantalla.get_brightness()
            if ajuste.modo != "leer":
                pedido = ajuste.objetivo(actual)
                if pedido != actual:
                    pantalla.set_brightness(pedido)
                    # DDC/CI puede reflejar el nuevo nivel después de la escritura.
                    for espera in (0.12, 0.2, 0.3):
                        time.sleep(espera)
                        actual = pantalla.get_brightness()
                        if actual == pedido:
                            break
            estado = f"al {actual} por ciento"
        except Exception:
            log.warning("No se pudo leer o verificar el brillo de la pantalla %s", i, exc_info=True)
            estado = "sin nivel verificable"
        resultados.append(estado if len(pantallas) == 1 else f"pantalla {i} {estado}")
    if not resultados:
        return "Windows no detectó pantallas con control de brillo compatible."
    return "Brillo " + ", ".join(resultados) + "."


def ejecutar(ajuste: Ajuste) -> str:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        if ajuste.control == "volumen":
            from pycaw.pycaw import AudioUtilities
            dispositivo = AudioUtilities.GetSpeakers()
            if not dispositivo:
                return "Windows no detectó una salida de audio predeterminada."
            return _volumen(ajuste, dispositivo.EndpointVolume)
        import screen_brightness_control as sbc
        pantallas = [sbc.Display.from_dict(info) for info in sbc.list_monitors_info()]
        return _brillo(ajuste, pantallas)
    except Exception:
        log.warning("No se pudo consultar o verificar el %s", ajuste.control, exc_info=True)
        return f"No pude verificar el {ajuste.control} en este equipo."
    finally:
        pythoncom.CoUninitialize()
