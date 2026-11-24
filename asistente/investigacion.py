"""Investigación bajo pedido con extractos verificables de las notas locales."""
from __future__ import annotations

import re
from pathlib import Path

from .conocimiento import Evidencia
from .respuesta import Respuesta
from .texto import Frase, es_no


_NOTAS = r"(?:mis notas|las notas(?: de obsidian)?|mi boveda|la boveda|obsidian)"
_ACCION = r"(?P<accion>investiga|investigar|compara|comparar|contrasta|reune)"
_PEDIDO = re.compile(rf"^{_ACCION} (?:(?:lo )?que dicen |en )?{_NOTAS}"
                     r"(?: (?:sobre|acerca de|con respecto a|para))?(?: (?P<tema>.+))?$")
_PEDIDO_INVERSO = re.compile(rf"^{_ACCION} (?P<tema>.+?) (?:en|usando) {_NOTAS}$")
_FUENTES = {"de donde salio eso", "de donde sale eso", "de donde sacaste eso", "de que notas salio",
            "muestrame las fuentes", "muestra las fuentes", "cuales son las fuentes", "dame las fuentes",
            "cita las fuentes", "dime las fuentes", "fuentes", "las fuentes"}
# Whisper base intercambia el inicio de «muéstrame» en este seguimiento oral.
_FUENTES_DICTADAS = re.compile(r"(?:mue|gue|hue|ue|wi|wue)[sz]tr?ame las fuentes")
_GUARDAR = {"guarda el informe", "guarda ese informe", "guarda el informe en obsidian",
            "guarda la investigacion", "guarda la investigacion en obsidian"}
_REPETIR = {"repite la investigacion", "actualiza la investigacion", "vuelve a investigar",
            "compara de nuevo"}


def _comparacion(a, b):
    """Solo señala coincidencias o diferencias comprobables en el texto citado."""
    textos = [e.fragmento.strip().casefold() for e in (a, b)]
    if textos[0] == textos[1]:
        return "coinciden"
    if any("…" in t for t in textos):
        return ""
    if any(re.sub(r"\bno ", "", t, count=1) == otro for t, otro in (textos, textos[::-1])):
        return "Las dos notas difieren en este punto."
    if not any(re.search(r"\d[.,]\d", t) for t in textos):
        numeros = [tuple(n.lstrip("0") or "0" for n in re.findall(r"[0-9]+", t)) for t in textos]
        if (numeros[0] and numeros[0] != numeros[1]
                and re.sub(r"[0-9]+", "#", textos[0]) == re.sub(r"[0-9]+", "#", textos[1])):
            return "Las dos notas dan cifras distintas."
    return ""


def _cita(e: Evidencia):
    nombre = e.titulo if Path(e.ruta).name.startswith("Nota - ") else e.ruta
    return f"«{e.fragmento}» ({nombre}, línea {e.linea})."


def _investigar(conocimiento, tema, comparar):
    hallazgos = conocimiento.investigar(tema, limite=5)
    parcial = (" Aún quedan pasajes por comparar por significado."
               if conocimiento.busqueda_parcial else "")
    if not hallazgos:
        return Respuesta(f"No encontré un pasaje sobre {tema} en esta búsqueda.{parcial}")

    citas = []
    rutas = set()
    for hallazgo in hallazgos:
        if hallazgo.ruta not in rutas:
            citas.append(hallazgo)
            rutas.add(hallazgo.ruta)
        if len(citas) == (2 if comparar else 3):
            break
    if len(citas) == 1 and not comparar:
        citas = hallazgos[:2]

    encabezado = ""
    if comparar:
        encabezado = (_comparacion(*citas) if len(citas) == 2 else
                      "Encontré una sola nota con evidencia en esta búsqueda.")
    if encabezado == "coinciden":
        a, b = citas
        texto = (f"Ambas notas recogen «{a.fragmento}». En {a.ruta}, línea {a.linea}, "
                 f"y en {b.ruta}, línea {b.linea}.")
    else:
        texto = " ".join(filter(None, [encabezado, *(_cita(e) for e in citas)]))

    def guardar():
        from .informe_notas import guardar_informe
        try:
            ruta = guardar_informe(conocimiento, tema, citas,
                                   resumen=texto if encabezado == "coinciden" else encabezado)
        except ValueError as e:
            return str(e)
        except OSError:
            return "No pude guardar el informe. Comprueba que tienes acceso de escritura a la bóveda."
        return f"Guardé {ruta.name} en la carpeta Informes de tu bóveda."

    def seguimiento(pedido):
        orden = Frase(pedido).sin_cortesia().txt
        if orden in _REPETIR:
            return _investigar(conocimiento, tema, comparar)
        if orden in _GUARDAR:
            return Respuesta(ejecutar=guardar, siguiente=seguimiento, espera_s=90)
        if orden not in _FUENTES and not _FUENTES_DICTADAS.fullmatch(orden):
            return None
        vigentes = conocimiento.vigentes(citas)
        fuentes = " ".join(f"{e.ruta}, línea {e.linea}." for e in vigentes)
        if len(vigentes) != len(citas):
            fuentes += " Alguna referencia cambió o ya no está disponible. Puedes decir «repite la investigación»."
        return Respuesta(fuentes.strip(), siguiente=seguimiento, espera_s=90)

    return Respuesta(texto + parcial, siguiente=seguimiento, espera_s=90)


def procesar_investigacion(frase: Frase, conocimiento):
    pedido = _PEDIDO.fullmatch(frase.txt) or _PEDIDO_INVERSO.fullmatch(frase.txt)
    if pedido is None:
        return None
    tema = frase.grupo(pedido, "tema")
    comparar = pedido["accion"] in {"compara", "comparar", "contrasta"}
    if tema:
        return _investigar(conocimiento, tema, comparar)
    def recibir_tema(t):
        if es_no(t):
            return Respuesta("De acuerdo.")
        return _investigar(conocimiento, t, comparar)
    return Respuesta("¿Qué tema quieres investigar en tus notas?", siguiente=recibir_tema)
