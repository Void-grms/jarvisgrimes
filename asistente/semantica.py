"""Recuperación por similitud con embeddings de Ollama en el equipo."""
from __future__ import annotations

import hashlib
import itertools
import logging
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import numpy as np
import requests


log = logging.getLogger("conocimiento")
MODELO = "embeddinggemma:300m-qat-q4_0"
UMBRAL = 0.32


def _endpoint(url):
    try:
        partes = urlsplit(url)
        if (partes.scheme in {"http", "https"} and partes.hostname in {"localhost", "127.0.0.1", "::1"}
                and not partes.username and not partes.password and not partes.query and not partes.fragment
                and partes.path in {"", "/"}):
            return url.rstrip("/") + "/api/embed"
    except (TypeError, ValueError):
        pass
    return ""


class Semantica:
    lote = 8
    nuevos_por_consulta = 48
    segundos_por_consulta = 5
    capacidad = 2048

    def __init__(self, url, modelo=MODELO, umbral=UMBRAL):
        self.endpoint = _endpoint(url)
        self.modelo = modelo
        self.umbral = float(umbral)
        self._cache = OrderedDict()  # hash del texto exacto → vector, únicamente en RAM
        self._lock = threading.Lock()
        self._inicio = 0
        self._reintentar = 0.0
        self._verificado_hasta = 0.0
        self._revision = None

    def _comprobar_modelo(self, sesion):
        if time.monotonic() < self._verificado_hasta:
            return
        r = sesion.post(self.endpoint.removesuffix("/embed") + "/show", json={"model": self.modelo},
                        timeout=(2, 5), allow_redirects=False)
        if r.status_code != 200:
            raise ValueError("Ollama no confirmó el modelo local")
        info = r.json()
        if (not isinstance(info, dict) or info.get("remote_model") or info.get("remote_host")
                or "embedding" not in (info.get("capabilities") or [])
                or (info.get("details") or {}).get("format") not in {"gguf", "safetensors"}):
            raise ValueError("Se necesita un modelo de embeddings instalado en este equipo")
        revision = info.get("modified_at")
        if revision != self._revision:
            self._cache.clear()
            self._inicio = 0
            self._revision = revision
        self._verificado_hasta = time.monotonic() + 60

    def _pedir(self, sesion, textos):
        respuesta = sesion.post(self.endpoint, json={
            "model": self.modelo, "input": textos, "keep_alive": "30s",
        }, timeout=(2, 20), allow_redirects=False)
        if respuesta.status_code != 200:
            raise ValueError(f"Ollama devolvió HTTP {respuesta.status_code}")
        vectores = np.asarray(respuesta.json()["embeddings"], dtype=np.float32)
        if (vectores.ndim != 2 or vectores.shape[0] != len(textos) or not vectores.shape[1]
                or not np.isfinite(vectores).all()):
            raise ValueError("Embeddings incompletos")
        normas = np.linalg.norm(vectores, axis=1, keepdims=True)
        if not np.isfinite(normas).all() or (normas == 0).any():
            raise ValueError("Vectores inválidos")
        return vectores / normas

    def buscar(self, tema, pasajes, limite):
        """Devuelve los mejores pasajes vivos y si quedaron pasajes sin vector."""
        if (not self.endpoint or not self.modelo or re.search(r"[:-]cloud(?:$|:)", self.modelo, re.I)
                or time.monotonic() < self._reintentar):
            return [], False
        with self._lock:
            if time.monotonic() < self._reintentar:
                return [], False
            pasajes = iter(pasajes)
            primero = next(pasajes, None)
            if primero is None:
                return [], False
            try:
                with requests.Session() as sesion:
                    sesion.trust_env = False
                    self._comprobar_modelo(sesion)
                    consulta = (f"task: search result | query: {tema[:2000]}"
                                if self.modelo.startswith("embeddinggemma") else tema[:2000])
                    vector = self._pedir(sesion, [consulta])[0]
                    return self._comparar(sesion, vector, itertools.chain((primero,), pasajes), limite)
            except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
                # Modelo no instalado, error del servidor o respuesta incompatible. El índice
                # literal sigue disponible. Ningún comando descarga modelos implícitamente.
                self._reintentar = time.monotonic() + 45
                log.info("Búsqueda semántica local en pausa (%s).", type(exc).__name__)
                return [], False
            finally:
                if hasattr(pasajes, "close"):
                    pasajes.close()

    def _comparar(self, sesion, vector, pasajes, limite):
        mejores = []
        pendientes = []
        nuevos = 0
        siguiente = None
        fin = time.monotonic() + self.segundos_por_consulta
        parcial = False

        def puntuar(evidencia, otro):
            similitud = float(np.dot(otro, vector))
            if similitud < self.umbral:
                return
            archivo = evidencia.ruta
            # Reservar espacio para fuentes diferentes aunque un documento repita el tema.
            del_archivo = [(v, e) for v, e in mejores if e.ruta == archivo]
            if any(e.fragmento == evidencia.fragmento for _, e in del_archivo):
                return
            if len(del_archivo) == 2:
                if similitud <= del_archivo[-1][0]:
                    return
                mejores.remove(del_archivo[-1])
            mejores.append((similitud, evidencia))
            mejores.sort(key=lambda par: par[0], reverse=True)
            del mejores[limite:]

        def procesar():
            unicos = {clave: texto for clave, texto, _ in pendientes}
            vectores = self._pedir(sesion, list(unicos.values()))
            for clave, nuevo in zip(unicos, vectores):
                self._cache[clave] = nuevo.copy()
                self._cache.move_to_end(clave)
            for clave, _, evidencia in pendientes:
                puntuar(evidencia, self._cache[clave])
            while len(self._cache) > self.capacidad:
                self._cache.popitem(last=False)
            pendientes.clear()

        for indice, evidencia in enumerate(pasajes):
            titulo = evidencia.titulo[:160]
            texto = (f"title: {titulo} | text: {evidencia.fragmento}" if self.modelo.startswith("embeddinggemma")
                     else f"{titulo}\n{evidencia.fragmento}")
            clave = hashlib.sha256(texto.encode("utf-8")).digest()
            if clave in self._cache:
                self._cache.move_to_end(clave)
                puntuar(evidencia, self._cache[clave])
            elif indice < self._inicio:
                parcial = True
            elif nuevos < self.nuevos_por_consulta and (not nuevos or time.monotonic() < fin):
                pendientes.append((clave, texto, evidencia))
                nuevos += 1
                if len(pendientes) >= self.lote:
                    procesar()
            else:
                parcial = True
                if siguiente is None:
                    siguiente = indice
        if pendientes:
            procesar()
        self._inicio = siguiente or 0
        # En español algunas paráfrasis cortas apenas superan el umbral. Cuando
        # hay una coincidencia fuerte, eliminar acompañantes mucho más débiles.
        corte = self.umbral
        if mejores:
            corte = max(corte, min(corte + .10, mejores[0][0] - .13))
        return [e for valor, e in mejores if valor >= corte], parcial
