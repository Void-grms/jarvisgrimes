"""Memorias explícitas en Markdown y consulta local de notas por tema."""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import re
import stat
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .acciones.sistema import carpeta
from .config import ruta_proyecto
from .respuesta import Respuesta
from .semantica import MODELO, UMBRAL, Semantica
from .texto import Frase, normalizar, sin_acentos


_DESTINO = r"(?:obsidian|(?:mi |la )?(?:memoria|base de conocimientos?)|mis (?:notas|conocimientos))"
log = logging.getLogger("conocimiento")
_GUARDAR = re.compile(
    rf"^(?:(?:por favor|oye)[, ]+)?(?:recuerda que|aprende que|(?:guarda|almacena|anota|agrega)"
    rf" (?:en |a ){_DESTINO}(?: que| lo siguiente)?)(?=$|[\s:,])"
    r"(?:\s*[:,]\s*|\s+)?(?P<dato>.*)$", re.I | re.S)
_CONSULTAR = re.compile(
    rf"^(?:(?:que (?:recuerdas|guardaste|anotaste|tienes guardado) (?:en {_DESTINO} )?"
    r"(?:sobre|de|del|acerca de)|que (?:dicen mis notas|hay en mis notas) (?:sobre|de)|"
    rf"(?:busca|encuentra|revisa|consulta) (?:en )?{_DESTINO}(?: sobre| acerca de)?)"
    r"(?: (?P<tema>.+))?)$")
_ULTIMA = re.compile(rf"^guarda (?:tu (?:ultima )?respuesta|lo que (?:acabas de decir|dijiste)) en {_DESTINO}$")
_VACIAS = set("a al aquel aquella como con cual cuando de del el en era es esta estaba la las los lo "
              "me mi mis para por que sobre su sus tu tus un una unos unas y".split())
_ANTIGUA = re.compile(r"^-\s*\[\d{2}/\d{2} \d{2}:\d{2}\]\s*")
_CLASES = {"dato": "dato", "decision": "decisión", "tarea": "tarea", "pendiente": "tarea"}
_CON_TIPO = re.compile(
    rf"^(?:recuerda|guarda|anota)(?: en {_DESTINO})? como (?P<tipo>dato|decisi[oó]n|tarea|pendiente)"
    r"(?: que)?(?:\s*[:,]\s*|\s+)(?P<dato>.+)$", re.I | re.S)
_META = re.compile(r"^(tags|etiquetas|aliases|proyecto|tipo)\s*:\s*(.*)$", re.I)
_DELIBERAR = set("ayudame ayudar decidir conviene convendria deberia elegir escojo escoger "
                 "entre mejor opcion opciones puedo quiero seria si o vale pena".split())


@dataclass(frozen=True)
class Evidencia:
    ruta: str
    linea: int
    titulo: str
    fragmento: str


def _palabras(texto: str) -> set[str]:
    palabras = set(re.findall(r"[^\W_]+", sin_acentos(texto.casefold()))) - _VACIAS
    return {p[:-1] if len(p) >= 5 and p.endswith("s") else p for p in palabras}


def _mascara(palabras):
    # Dos notas pueden compartir bits. Solo usamos el filtro para descartar
    # ausencias seguras; los documentos candidatos se leen y comparan literalmente.
    resultado = 0
    for palabra in palabras:
        resultado |= 1 << (hash(palabra) % 2048)
    return resultado


def _etiquetas(valores):
    if isinstance(valores, str):
        valores = re.split(r"[,;]|\s+y\s+", valores, flags=re.I)
    etiquetas = set()
    for valor in valores or ():
        nombre = re.sub(r"[^\w/-]+", "-", str(valor).strip().lstrip("#").casefold()).strip("-/")
        if nombre and any(c.isalpha() for c in nombre):
            etiquetas.add(nombre)
    return sorted(etiquetas)


def _enlace_proyecto(proyecto):
    if not proyecto:
        return ""
    nombre = str(proyecto).strip().replace("\\", "/")
    if nombre.startswith("Proyectos/"):
        base = nombre[len("Proyectos/"):].removesuffix(".md")
        if re.fullmatch(r"[^/\\#|^\[\]\x00-\x1f]+", base) and base not in (".", ".."):
            return f"[[Proyectos/{base}]]"
        nombre = base
    nombre = nombre.removesuffix(".md")
    from .proyecto_md import nombre_archivo
    return f"[[Proyectos/{nombre_archivo(nombre)}]]"


def _fragmento(texto, busqueda):
    if len(texto) <= 220:
        return texto
    primera = next((m.start() for m in re.finditer(r"[^\W_]+", texto)
                    if _palabras(m[0]) & busqueda), 0)
    inicio = max(0, primera - 60)
    if inicio:
        borde = texto.find(" ", inicio, primera)
        if borde >= 0:
            inicio = borde + 1
    fin = min(len(texto), inicio + 215)
    if fin < len(texto):
        borde = texto.rfind(" ", max(inicio, fin - 28), fin + 1)
        if borde > inicio:
            fin = borde
    return ("…" if inicio else "") + texto[inicio:fin].strip() + ("…" if fin < len(texto) else "")


def _enlace(ruta: Path) -> bool:
    # Las junctions de Windows también pueden apuntar fuera de la carpeta.
    try:
        estado = ruta.lstat()
    except OSError:
        return True  # el otro editor puede mover o bloquear una nota durante la consulta
    return stat.S_ISLNK(estado.st_mode) or bool(
        getattr(estado, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class Conocimiento:
    def __init__(self, raiz: Path, notas_rapidas: Path, *, semantica=None):
        self.raiz = Path(raiz)
        self.notas_rapidas = Path(notas_rapidas)
        self._filtros = {}
        self._lectura = threading.Lock()
        self.semantica = semantica
        self._consulta_actual = threading.local()

    @property
    def busqueda_parcial(self):
        return getattr(self._consulta_actual, "parcial", False)

    @classmethod
    def desde_config(cls, cfg: dict):
        documentos = cfg.get("documentos", {}).get("carpeta")
        jarvis = Path(documentos) if documentos else carpeta("jarvis")
        opciones = cfg.get("conocimiento", {})
        ubicacion = opciones.get("carpeta")
        motor = None
        if opciones.get("busqueda_semantica") is True:
            umbral = opciones.get("umbral_semantico", UMBRAL)
            if type(umbral) not in (int, float) or not 0 <= umbral <= 1:
                umbral = UMBRAL
            modelo = opciones.get("modelo_embeddings", MODELO)
            if isinstance(modelo, str) and modelo.strip():
                motor = Semantica(cfg.get("llm", {}).get("url", ""), modelo.strip(), umbral)
        return cls(ruta_proyecto(ubicacion) if ubicacion else jarvis / "Conocimiento",
                   jarvis / "notas.md", semantica=motor)

    def guardar(self, dato: str, *, tipo="dato", proyecto="", etiquetas=()) -> str:
        dato = unicodedata.normalize("NFC", dato.strip())
        if not dato:
            return "Dime qué quieres recordar."
        tipo = _CLASES.get(normalizar(tipo))
        if not tipo:
            return "Elige dato, decisión o tarea para clasificar la nota."
        vinculo = _enlace_proyecto(proyecto)
        tags = _etiquetas(etiquetas)
        identidad = dato if (tipo == "dato" and not vinculo and not tags) else json.dumps(
            [dato, tipo, vinculo, tags], ensure_ascii=False)
        # Un mismo dictado tiene una única ruta aunque se repita la orden.
        huella = hashlib.sha256(identidad.encode("utf-8")).hexdigest()[:16]
        titulo = " ".join(re.findall(r"[^\W_]+", dato)[:8])[:64] or "Nota"
        cuerpo = f"# {titulo}\n\n{dato}\n"
        tags_yaml = json.dumps(sorted({"jarvis", tipo, *tags}), ensure_ascii=False)
        propiedades = f"tipo: {tipo}\nproyecto: {json.dumps(vinculo, ensure_ascii=False)}\ntags: {tags_yaml}"
        contenido = f"---\nfecha: {date.today().isoformat()}\n{propiedades}\n---\n{cuerpo}"
        # Publica solo la nota completa. Dos órdenes simultáneas no sobrescriben
        # una memoria existente ni los cambios que alguien haya hecho en el editor.
        temporal = None
        try:
            self.raiz.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.raiz,
                                             delete=False, suffix=".tmp") as f:
                temporal = Path(f.name)
                f.write(contenido)
            for numero in itertools.count(1):
                sufijo = "" if numero == 1 else f" - {numero}"
                destino = self.raiz / f"Nota - {titulo} - {huella}{sufijo}.md"
                try:
                    if os.name == "nt":
                        temporal.rename(destino)  # Windows rechaza un destino existente
                    else:
                        os.link(temporal, destino)
                    break
                except FileExistsError:
                    if not _enlace(destino) and destino.is_file():
                        previa = destino.read_text(encoding="utf-8-sig")
                        if (previa == cuerpo and identidad == dato) or (
                                previa.startswith("---\nfecha: ") and previa.split("\n", 2)[-1]
                                == contenido.split("\n", 2)[-1]):
                            return "Esa nota ya está guardada."
        except (OSError, UnicodeDecodeError):
            log.exception("Error al guardar una nota de conocimiento")
            return "No pude guardar la nota. Comprueba el acceso a la carpeta de conocimiento."
        finally:
            if temporal:
                temporal.unlink(missing_ok=True)
        from .diario import Diario
        try:
            Diario(self.raiz).registrar(f"{tipo.capitalize()}: {dato}", fuente=destino)
        except (OSError, ValueError):
            log.exception("Memoria guardada, falló el registro del diario")
            return "Guardé la nota. No pude registrar la actividad en el diario."
        return "Guardé la nota en tu base de conocimiento."

    def _archivos(self):
        if self.notas_rapidas.is_file() and not _enlace(self.notas_rapidas):
            yield self.notas_rapidas
        if not self.raiz.is_dir() or _enlace(self.raiz):
            return
        for base, carpetas, archivos in os.walk(self.raiz):
            carpeta_actual = Path(base)
            carpetas[:] = sorted(n for n in carpetas if not n.startswith(".") and not _enlace(carpeta_actual / n))
            for nombre in sorted(archivos):
                ruta = carpeta_actual / nombre
                if (carpeta_actual == self.raiz / "Diario"
                        and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", nombre, re.I)):
                    continue  # la consulta de actividad conserva su propio historial
                if (ruta.suffix.lower() == ".md" and not nombre.startswith(".")
                        and not nombre.startswith("notas borradas ") and ruta != self.notas_rapidas
                        and not _enlace(ruta)):
                    yield ruta

    def _lineas(self, ruta):
        titulo = "Notas rápidas" if ruta == self.notas_rapidas else ruta.stem
        nombre_automatico = re.fullmatch(r"Nota - .+ - [0-9a-f]{16}(?: - \d+)?", ruta.stem)
        nombre = set() if nombre_automatico else _palabras(titulo)
        propiedades = set()
        seccion = set()
        palabras_titulo = nombre
        en_propiedades = lista = comentario = False
        with ruta.open(encoding="utf-8-sig") as archivo:
            for numero, linea in enumerate(archivo, 1):
                linea = linea.strip()
                if linea == "---" and (numero == 1 or en_propiedades):
                    en_propiedades = not en_propiedades
                    continue
                if en_propiedades:
                    campo = _META.match(linea)
                    if campo:
                        if (campo[1].lower() == "tipo"
                                and campo[2].strip().strip("'\"") == "informe_de_investigacion"):
                            return  # El informe es una síntesis, no una fuente primaria.
                        lista = not campo[2].strip()
                        propiedades.update(_palabras(campo[2]))
                    elif lista and linea.startswith("- "):
                        propiedades.update(_palabras(linea))
                    else:
                        lista = False
                    continue
                if comentario or linea.startswith("<!--"):
                    comentario = "-->" not in linea
                    continue
                if not linea or linea == "---":
                    continue
                if linea.startswith("# "):
                    titulo = linea[2:].strip()
                    palabras_titulo = nombre | _palabras(titulo)
                    seccion = set()
                    continue
                if re.match(r"^#{2,6}\s", linea):
                    seccion = _palabras(linea)
                    continue
                fragmento = _ANTIGUA.sub("", linea).lstrip("-*# ").strip()
                if fragmento:
                    yield numero, titulo, fragmento, palabras_titulo | propiedades | seccion

    def _ruta_fuente(self, ruta):
        return ruta.relative_to(self.raiz).as_posix() if ruta.is_relative_to(self.raiz) else ruta.absolute().as_posix()

    def _consultar(self, tema, limite, parciales, *, solo_boveda=False, diversificar=False):
        busqueda = _palabras(tema)
        if parciales:
            busqueda -= _palabras(" ".join(_DELIBERAR))
        if not busqueda or limite <= 0:
            return [], False
        limite = min(limite, 16)
        mejores = []
        frase = normalizar(tema)
        error_lectura = False
        mascara = _mascara(busqueda)
        with self._lectura:
            vistas = set()
            for ruta in self._archivos():
                vistas.add(ruta)
                if solo_boveda and not ruta.is_relative_to(self.raiz):
                    continue
                try:
                    estado = ruta.stat()
                    firma = (estado.st_mtime_ns, estado.st_ctime_ns, estado.st_ino, estado.st_size)
                    anterior = self._filtros.get(ruta)
                    if anterior and anterior[0] == firma:
                        coincidencia = anterior[1] & mascara
                        if not coincidencia or (not parciales and coincidencia != mascara):
                            continue
                    archivo = self._ruta_fuente(ruta)
                    filtro = 0
                    for numero, titulo, fragmento, contexto in self._lineas(ruta):
                        cuerpo = _palabras(fragmento)
                        todas = cuerpo | contexto
                        filtro |= _mascara(todas)
                        comun = busqueda & todas
                        if not comun or (not parciales and comun != busqueda):
                            continue
                        puntos = (len(comun), len(busqueda & cuerpo), int(frase in normalizar(fragmento)))
                        breve = _fragmento(fragmento, busqueda)
                        repetidas = [(p, e) for p, e in mejores if e.fragmento == breve
                                     and (not diversificar or e.ruta == archivo)]
                        if any(p >= puntos for p, _ in repetidas):
                            continue
                        for previa in repetidas:
                            mejores.remove(previa)
                        if diversificar:
                            locales = [(p, e) for p, e in mejores if e.ruta == archivo]
                            if len(locales) == 2:
                                if puntos <= locales[-1][0]:
                                    continue
                                mejores.remove(locales[-1])
                        mejores.append((puntos, Evidencia(archivo, numero, titulo, breve)))
                        mejores.sort(key=lambda par: par[0], reverse=True)
                        del mejores[limite:]
                    self._filtros[ruta] = firma, filtro
                except (UnicodeDecodeError, OSError):
                    error_lectura = True
            for ruta in self._filtros.keys() - vistas:
                del self._filtros[ruta]
        return [e for _, e in mejores], error_lectura

    def _pasajes(self, solo_boveda):
        for ruta in self._archivos():
            if solo_boveda and not ruta.is_relative_to(self.raiz):
                continue
            archivo = self._ruta_fuente(ruta)
            try:
                for numero, titulo, linea, _ in self._lineas(ruta):
                    inicio = 0
                    while inicio < len(linea):
                        fin = min(len(linea), inicio + 220)
                        if fin < len(linea):
                            espacio = linea.rfind(" ", inicio + 140, fin + 1)
                            if espacio != -1:
                                fin = espacio
                        fragmento = ("…" if inicio else "") + linea[inicio:fin].strip() + ("…" if fin < len(linea) else "")
                        yield Evidencia(archivo, numero, titulo, fragmento)
                        inicio = fin + 1 if linea[fin:fin+1] == " " else fin
            except (OSError, UnicodeError):
                continue

    def vigentes(self, evidencias):
        """Comprueba ruta, línea y extracto antes de citar una nota que otro editor pudo cambiar."""
        evidencias = list(evidencias)
        por_archivo = {}
        for evidencia in evidencias:
            por_archivo.setdefault(evidencia.ruta, {}).setdefault(evidencia.linea, []).append(evidencia)
        validas = set()
        for ruta in self._archivos():
            esperadas = por_archivo.get(self._ruta_fuente(ruta))
            if not esperadas:
                continue
            try:
                for numero, titulo, linea, _ in self._lineas(ruta):
                    for e in esperadas.get(numero, ()):
                        recorte = e.fragmento.removeprefix("…").removesuffix("…")
                        if e.titulo == titulo and (linea == e.fragmento or (recorte != e.fragmento and recorte in linea)):
                            validas.add(e)
            except (OSError, UnicodeError):
                continue
        return [e for e in evidencias if e in validas]

    def _recuperar(self, tema, limite, parciales, *, investigar=False):
        self._consulta_actual.parcial = False
        if limite <= 0 or not _palabras(tema):
            return [], False
        limite = min(16, limite)
        candidatos = min(16, 3 * limite) if investigar or self.semantica else limite
        literales, error = self._consultar(tema, candidatos, parciales, solo_boveda=investigar, diversificar=investigar)
        semanticos = []
        if self.semantica and (investigar or parciales or len(literales) < limite):
            semanticos, self._consulta_actual.parcial = self.semantica.buscar(tema, self._pasajes(investigar), candidatos)
        if investigar or parciales:
            # Fusión recíproca de rangos: una fuente respaldada por los dos métodos tiene prioridad.
            puntos, citas = {}, {}
            for lista in (literales, semanticos):
                vistas = set()
                for orden, e in enumerate(lista, 1):
                    ubicacion = (e.ruta, e.linea)
                    if ubicacion not in vistas:
                        vistas.add(ubicacion)
                        citas.setdefault(ubicacion, e)
                        puntos[ubicacion] = puntos.get(ubicacion, 0) + 1 / (60 + orden)
            seleccion = [citas[k] for k in sorted(puntos, key=puntos.get, reverse=True)]
        else:
            seleccion = list(dict.fromkeys(literales + semanticos))
        if semanticos:
            seleccion = self.vigentes(seleccion)
        if investigar:
            primeras, adicionales, vistas = [], [], set()
            for e in seleccion:
                if e.ruta not in vistas:
                    primeras.append(e)
                    vistas.add(e.ruta)
                elif not any(c.ruta == e.ruta for c in adicionales) and not any(
                        c.ruta == e.ruta and c.linea == e.linea for c in primeras):
                    adicionales.append(e)
            seleccion = primeras + adicionales
        else:
            unicas, lineas = {}, set()
            for e in seleccion:
                if (e.ruta, e.linea) not in lineas:
                    lineas.add((e.ruta, e.linea))
                    unicas.setdefault(e.fragmento, e)
            seleccion = list(unicas.values())
        return seleccion[:limite], error

    def investigar(self, tema, limite=5) -> list[Evidencia]:
        """Recupera pasajes citables de la bóveda, repartidos entre las notas relevantes."""
        return self._recuperar(tema, limite, True, investigar=True)[0]

    def evidencias(self, tema, *, limite=4, parciales=False) -> list[Evidencia]:
        """Devuelve citas literales con ruta y número de línea desde el Markdown actual."""
        return self._recuperar(tema, limite, parciales)[0]

    def buscar(self, tema: str) -> str:
        if not _palabras(tema):
            return "¿Sobre qué tema busco en tus notas?"
        mejores, error_lectura = self._recuperar(tema, 2, False)
        if not mejores:
            if self.busqueda_parcial:
                return "No encontré coincidencias literales. Quedan pasajes por comparar por significado."
            if error_lectura:
                return "No encontré coincidencias en las notas que pude leer."
            return f"No encontré notas sobre {tema}."
        return "En tus notas encontré " + " y ".join(f"«{e.fragmento}»" for e in mejores) + "."

    def procesar(self, texto: str, ultima_respuesta: str = "", *, proyecto="") -> Respuesta | None:
        frase = Frase(texto).sin_cortesia()
        def guardar(dato, tipo="dato", etiquetas=()):
            return self.guardar(dato, tipo=tipo, proyecto=proyecto() if callable(proyecto) else proyecto,
                                etiquetas=etiquetas)
        if frase.txt in {"donde guardas mi conocimiento", "donde esta mi base de conocimiento",
                         "donde esta mi carpeta de conocimiento"}:
            return Respuesta(f"Tu carpeta de conocimiento es {self.raiz}.")
        if _ULTIMA.fullmatch(frase.txt):
            if not ultima_respuesta:
                return Respuesta("Todavía no hay una respuesta que guardar.")
            return Respuesta(ejecutar=lambda: guardar(ultima_respuesta))
        m = _CON_TIPO.fullmatch(texto.strip()) or _GUARDAR.fullmatch(texto.strip())
        if m and (dato := m.group("dato").strip()):
            tipo = m.groupdict().get("tipo") or "dato"
            etiquetas = re.findall(r"(?<!\w)#([\w]+(?:[-/][\w]+)*)", dato)
            lista = re.search(r"[,;]?\s+(?:con las etiquetas?\s+|etiquetas?:\s*)([^.!?\n]+)[.!?]?$",
                             dato, flags=re.I)
            if lista and dato[:lista.start()].strip():
                etiquetas.extend(_etiquetas(lista[1]))
                dato = dato[:lista.start()].rstrip(",; ")
            return Respuesta(ejecutar=lambda: guardar(dato, tipo, etiquetas))
        if m:
            return Respuesta("Dime «recuerda que», seguido del dato que quieras guardar.")
        m = _CONSULTAR.fullmatch(frase.txt)
        if m:
            # La consulta devuelve texto hablado. El contenido nunca entra en el enrutador de órdenes.
            return Respuesta(self.buscar(frase.grupo(m, "tema")))
        return None
