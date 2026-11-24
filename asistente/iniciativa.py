"""Propone trabajo local durante una consulta explícita sobre las tareas del usuario."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

from .acciones.documentos import escribir_docx, nombre_archivo
from .respuesta import Respuesta
from .router import Router, periodo
from .store import Item
from .texto import Frase, confirma_accion, es_no, normalizar

_CONSULTA = re.compile(
    r"^(?:(?:que|cuales son|dime|dime que|muestrame|revisa|lista|cuentame|tengo|ver|hay) )?"
    r"(?:(?:mis|los|las) )?(?:pendientes|tareas|entregas|examenes|evaluaciones|que tengo(?: que hacer)?|"
    r"que me (?:falta|queda)(?: por hacer)?)(?: (?:tengo|hay|tienen|son|pendientes|por hacer))?"
    r"(?: (?:para |de |esta )?(?:hoy|manana|semana))?$")
_DETALLES = {"detalles", "mas detalles", "dame los detalles", "dame mas detalles", "ver detalles",
             "que detalles hay", "cuentame mas", "de que trata"}
_DIA = 86400


def _texto(valor: str, limite: int) -> str:
    limpio = re.sub(r"<[^>]*>|https?://\S+", " ", html.unescape(valor), flags=re.I)
    limpio = " ".join(limpio.split())
    return textwrap.shorten(limpio, width=limite, placeholder="…")


def _guardar_esquema(carpeta: Path, item: Item, fecha: str) -> str:
    estudio = item.tipo == "examen"
    titulo = ("Plan de estudio" if estudio else "Esquema de entrega") + " — " + _texto(item.titulo, 140)
    contexto = [fecha]
    if item.curso:
        contexto.append("Curso: " + _texto(item.curso, 120))
    if detalle := _texto(item.detalle, 250):
        contexto.append(detalle)
    if estudio:
        pasos = [("Temas por repasar", "Lista los conceptos del temario y marca tres que necesites practicar."),
                 ("Práctica", "Escribe una pregunta por concepto y respóndela sin consultar tus apuntes."),
                 ("Repaso final", "Anota los errores que repetiste y programa un último repaso antes de la evaluación.")]
    else:
        pasos = [("Requisitos", "Anota el producto solicitado, el formato y los criterios de evaluación de la consigna."),
                 ("Borrador", "Divide el contenido en apartados. Añade a cada uno la evidencia o fuente que vas a utilizar."),
                 ("Revisión y entrega", "Contrasta el borrador con la rúbrica y revisa las referencias, el formato y el plazo.")]
    secciones = [{"titulo": "Datos de la actividad", "parrafos": contexto}]
    secciones.extend({"titulo": t, "parrafos": [p]} for t, p in pasos)
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=carpeta) as tmp:
            listo = escribir_docx({"titulo": titulo, "secciones": secciones}, Path(tmp) / "esquema.docx")
            while True:
                ruta = nombre_archivo(carpeta, titulo, ".docx")
                try:
                    if os.name == "nt":
                        listo.rename(ruta)
                    else:
                        os.link(listo, ruta)
                    break
                except FileExistsError:
                    continue
    except (OSError, ValueError):
        return "No pude guardar el esquema de Word. Revisa la carpeta de documentos configurada."
    return f"Guardé «{ruta.stem}» en la carpeta {carpeta.name}."


def proponer(router: Router, carpeta: Path, intencion: str, texto: str, resumen: str) -> Respuesta | None:
    explicita = intencion == "avance"
    consulta = Frase(texto).sin_cortesia().txt
    if not explicita and not _CONSULTA.fullmatch(consulta):
        return None
    ahora = datetime.now(router.tz)
    ts = ahora.timestamp()
    store = router.store
    sync = store.ultimo_sync("clementina")
    if sync is None or not 0 <= ts - sync < _DIA:
        return Respuesta("Necesito una agenda actualizada para recomendarte por dónde empezar.") if explicita else None
    inicio, fin, _ = router.rango(periodo(texto), ahora)
    tipo = "examen" if re.search(r"\b(?:examenes|evaluaciones)\b", consulta) else None
    if "entregas" in consulta:
        tipo = "tarea"
    candidato = next((i for i in store.items(fuente="clementina") if i.tipo in {"examen", "tarea"}
                      and (not tipo or i.tipo == tipo) and i.vence_ts is not None
                      and max(ts, inicio) < i.vence_ts < min(fin, ts + 2 * _DIA) and i.titulo.strip()), None)
    if candidato is None:
        return Respuesta("No veo entregas ni exámenes para las próximas 48 horas en tu agenda.") if explicita else None
    clave = hashlib.sha256(f"{candidato.id}:{candidato.vence_ts}".encode()).hexdigest()
    try:
        registro = json.loads(store.get_meta("propuestas_agenda") or "{}")
        vistos = {k: v for k, v in registro.get("vistos", {}).items() if v > ts}
    except (AttributeError, TypeError, ValueError):
        registro, vistos = {}, {}
    dia = ahora.date().isoformat()
    if not explicita and (registro.get("dia") == dia or clave in vistos):
        return None
    vistos[clave] = candidato.vence_ts
    store.set_meta("propuestas_agenda", json.dumps({"dia": dia, "vistos": vistos}))
    titulo = _texto(candidato.titulo, 85)
    cuando = router.cuando(candidato.vence_ts, candidato.tipo, ahora)
    fecha = datetime.fromtimestamp(candidato.vence_ts, router.tz).strftime("%d/%m/%Y a las %H:%M")
    formato = "plan de estudio" if candidato.tipo == "examen" else "esquema de entrega"
    pregunta = f"¿Preparo un {formato} en Word?"
    if explicita:
        mensaje = f"«{titulo}» {cuando}. {pregunta}"
    else:
        mensaje = f"{resumen} ¿Preparo un {formato} en Word para «{titulo}»?"

    def responder(t: str) -> Respuesta | None:
        n = normalizar(t)
        if n in _DETALLES:
            curso = f", de {_texto(candidato.curso, 80)}" if candidato.curso else ""
            detalle = _texto(candidato.detalle, 190)
            detalles = f"«{titulo}»{curso}, {cuando}."
            if detalle:
                detalles += f" {detalle.rstrip('.')}."
            return Respuesta(f"{detalles} {pregunta}", siguiente=responder, espera_s=30)
        if es_no(t):
            return Respuesta("De acuerdo.")
        if confirma_accion(t) or n in {"preparalo", "si preparalo", "prepara el esquema", "haz el esquema"}:
            def guardar():
                actual = next((i for i in store.items("clementina", candidato.tipo) if i.id == candidato.id), None)
                if (not actual or actual.vence_ts != candidato.vence_ts
                        or actual.vence_ts <= datetime.now(router.tz).timestamp()):
                    return "Esa actividad cambió. Revisemos los pendientes otra vez."
                return _guardar_esquema(carpeta, actual, fecha)
            return Respuesta(ejecutar=guardar)
        return None

    return Respuesta(mensaje, siguiente=responder, espera_s=30)
