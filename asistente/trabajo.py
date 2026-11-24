"""Trabajo por voz sobre archivos de Office, con versiones y contexto explícitos."""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from .proyectos import Proyectos, huella
from .respuesta import Respuesta
from .texto import Frase, es_no, numeros_a_digitos

log = logging.getLogger("trabajo")
_ORDINALES = "primera segunda tercera cuarta quinta sexta septima octava novena decima".split()
_RESUMIR = re.compile(r"^(?:resume(?:me)?|resumir|haz(?:me)? un resumen|prepara un resumen)\b")
_PRESENTAR = re.compile(r"^(?:prepara(?:me)?|haz(?:me)?|crea(?:me)?|genera(?:me)?|"
                        r"convierte|transforma|arma(?:me)?|pasa)\b")
_DOC = re.compile(r"\b(?:esto|lo que tengo abierto|la copia|"
                  r"(?:este|ese|el|mi) (?:word|informe|documento|archivo|texto)"
                  r"(?! (?:pdf|excel|de excel|de powerpoint))|"
                  r"(?:word|informe|documento|archivo) (?:abierto|actual|al frente))\b")
_EXTERNA = re.compile(r"\b(?:chat ?gpt|claude|gemini|copilot|perplexity)\b")
_PROYECTO = re.compile(r"^(?P<verbo>trabajemos en|empieza|inicia|crea|retomemos|retoma|continua con) "
                       r"(?:(?:el|la|un|mi) )?(?:proyecto )?(?P<nombre>.+)$")


@dataclass(frozen=True)
class Pedido:
    origen: Path
    firma: str
    proyecto: str
    modo: str
    texto: str
    cantidad: int = 6
    minutos: int | None = None
    diapositiva: int | None = None
    mostrar: bool = False


def detalle_ficha(ficha):
    if not ficha:
        return "Aún no hay un proyecto de trabajo."
    lineas = [ficha["nombre"]]
    v = ficha["version"]
    if v:
        lineas += [v["operacion"], v["ruta"]]
        for cambio in v["cambios"]:
            lineas += ["", cambio["ubicacion"]]
            if cambio.get("antes"):
                lineas.append("Antes · " + cambio["antes"])
            if cambio.get("despues"):
                lineas.append("Ahora · " + cambio["despues"])
    for nota in ficha["apuntes"]:
        lineas += ["", f"{nota['clase']} · {nota['texto']}"]
    return "\n".join(lineas)


class TrabajoOffice:
    def __init__(self, store, avisar, conocimiento=None):
        self.proyectos = Proyectos(store, conocimiento=conocimiento)
        self.avisar = avisar
        self.conocimiento = conocimiento
        self.cerebro = None
        self.mostrar = None
        self.sugerir = True
        self._lock = threading.RLock()
        self._turno = threading.Lock()
        self._activo = None
        self._pausado = None
        self._hilos = 0

    def ocupado(self):
        with self._lock:
            return self._activo is not None

    def finalizando(self):
        with self._lock:
            return self._hilos > 0

    def seleccionar_proyecto(self, nombre):
        return self.proyectos.elegir(nombre, crear=True)

    def desde_archivo(self, ruta, modo, cantidad=6, minutos=None):
        if self.ocupado():
            return "Ya hay un trabajo en curso."
        if modo not in {"resumen", "presentacion"} or Path(ruta).suffix.lower() != ".docx":
            raise ValueError("Elige un Word .docx.")
        texto = ("Resume este Word en una copia y conserva las tablas." if modo == "resumen" else
                 "Prepara una exposición basada en este Word con una diapositiva final de conclusiones.")
        p = self._preparar(ruta, modo, texto, cantidad=cantidad, minutos=minutos, mostrar=modo == "resumen")
        return self._iniciar(p)

    def reanudar(self):
        with self._lock:
            return self._iniciar(self._pausado) if self._pausado else "No hay un trabajo en pausa."

    def cancelar(self, pausar=False):
        with self._lock:
            if not self._activo:
                if not pausar:
                    self._pausado = None
                return False
            pedido, evento = self._activo
            evento.set()
            self._pausado = pedido if pausar else None
            self._activo = None
            return True

    def _contexto(self, tipo, usar_proyecto=False):
        from .acciones.word_activo import capturar_documento, SinOfficeActivo
        ficha = self.proyectos.ficha() if usar_proyecto else None
        version = ficha and ficha["version"]
        extension = ".pptx" if tipo == "powerpoint" else ".docx"
        try:
            frente = capturar_documento(tipo).ruta
        except SinOfficeActivo:
            if not version:
                raise
            ruta = Path(version["ruta"])
            if ruta.suffix.lower() != extension:
                raise ValueError("Pon al frente el archivo de Office al que te refieres.")
            if not ruta.is_file() or huella(ruta) != version["huella"]:
                raise ValueError("La versión guardada en el proyecto cambió. Abre el archivo que quieres usar.")
            return ruta
        if version:
            recordada = Path(version["ruta"])
            if recordada.suffix.lower() == extension and recordada != frente:
                raise ValueError(f"Está al frente {frente.name}, pero trabajábamos en {recordada.name}. "
                                 "Abre la versión del proyecto o indica que quieres usar el archivo abierto.")
        return frente

    def _preparar(self, ruta, modo, texto, **opciones):
        if self.ocupado():
            raise ValueError("Ya hay un trabajo en curso. Puedes pausarlo antes de empezar otro.")
        ruta = Path(ruta).resolve(strict=True)
        return Pedido(ruta, huella(ruta), self.proyectos.para_archivo(ruta), modo, texto, **opciones)

    def _iniciar(self, pedido):
        with self._lock:
            if self._activo:
                return "Ya estoy trabajando en un archivo. Puedes pedirme el estado o decir «pausa el trabajo»."
            if not 2 <= pedido.cantidad <= 20 or (pedido.minutos is not None and not 1 <= pedido.minutos <= 180):
                return "Puedo preparar entre 2 y 20 diapositivas para una exposición de 1 a 180 minutos."
            evento = threading.Event()
            self._activo, self._pausado = (pedido, evento), None
            self._hilos += 1
            threading.Thread(target=self._trabajar, args=(pedido, evento), name="trabajo-office", daemon=True).start()
        return f"Trabajo sobre una copia de {pedido.origen.name}."

    def _producir(self, p, evento):
        from .presentacion_local import crear_presentacion, revisar_diapositiva
        from .revision_local import revisar_word
        from .brain import Cerebro
        from urllib.parse import urlsplit
        if self.cerebro is None:
            raise ValueError("El modelo local todavía está cargando.")
        if isinstance(self.cerebro, Cerebro):
            if urlsplit(self.cerebro.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Para trabajar con este archivo, configura Ollama en este equipo o usa Encargos para revisar un envío.")
            cerebro = self.cerebro.para_documento(evento)
        else:
            cerebro = self.cerebro
        if p.modo in {"resumen", "solo_resumen", "revision"}:
            res = revisar_word(p.origen, p.texto, cerebro, modo=p.modo, cancelado=evento)
            cambios = [dict(ubicacion=str(c.ubicacion), antes=c.antes, despues=c.despues) for c in res.cambios]
            prueba = f"Verifiqué {res.tablas} tabla{'s' if res.tablas != 1 else ''}." if res.tablas else ""
            return Path(res.copia), cambios, prueba
        if p.modo == "acortar":
            from .presentacion_local import acortar_presentacion
            res = acortar_presentacion(p.origen, p.cantidad, minutos=p.minutos, cancelar=evento)
            prueba = f"Comprobé que quedan {res.total} diapositivas."
        elif p.modo == "presentacion":
            res = crear_presentacion(p.origen, cerebro.generar_json, cantidad=p.cantidad, minutos=p.minutos,
                                     instruccion=p.texto, cancelar=evento)
            prueba = f"Verifiqué las {res.total} diapositivas."
        else:
            res = revisar_diapositiva(p.origen, p.diapositiva, p.texto, cerebro.generar_json, cancelar=evento)
            prueba = f"Verifiqué el cambio en la diapositiva {p.diapositiva}."
        return Path(res.ruta), [dict(ubicacion=str(c), antes="", despues="") for c in res.cambios], prueba

    def _trabajar(self, pedido, evento):
        from .acciones.word_activo import abrir_verificado
        copia = None
        try:
            with self._turno:
                if evento.is_set():
                    return
                if huella(pedido.origen) != pedido.firma:
                    raise ValueError("El archivo cambió desde que lo elegiste. Vuelve a pedir el trabajo.")
                copia, cambios, prueba = self._producir(pedido, evento)
                nombre = {"resumen": "Resumen de Word", "solo_resumen": "Corrección del resumen",
                          "revision": "Revisión de Word", "presentacion": "Presentación",
                          "acortar": "Versión breve de la exposición",
                          "diapositiva": f"Revisión de la diapositiva {pedido.diapositiva}"}[pedido.modo]
                self.proyectos.registrar(pedido.proyecto, pedido.origen, pedido.firma,
                                         copia, huella(copia), nombre, cambios)
                if evento.is_set():
                    self.avisar(f"La copia {copia.name} ya había terminado de guardarse al interrumpir.")
                    return
                try:
                    abierta = abrir_verificado(copia)
                except (OSError, ValueError):
                    abierta = False
                msg = f"Guardé {copia.name}. {prueba}".strip()
                if not abierta:
                    msg += " No pude comprobar que Office la abriera."
                if pedido.mostrar and self.mostrar:
                    self.mostrar(self.proyectos.ficha(pedido.proyecto))
                self.avisar(msg)
        except InterruptedError:
            pass
        except (ValueError, OSError) as e:
            if not evento.is_set():
                self.avisar((f"La copia quedó en {copia}. " if copia else "") + str(e))
        except Exception:
            log.exception("El trabajo de Office se detuvo")
            if not evento.is_set():
                self.avisar(f"La copia quedó en {copia}." if copia else
                            "No pude terminar el trabajo. Comprueba que Ollama esté disponible.")
        finally:
            with self._lock:
                self._hilos -= 1
                if self._activo and self._activo[1] is evento:
                    self._activo = None

    def _estado(self, ficha=None, *, solo_pendientes=False):
        ficha = ficha or self.proyectos.ficha()
        if not ficha:
            return "Aún no hay un proyecto de trabajo."
        apuntes = ficha["apuntes"]
        pendientes = [a["texto"].rstrip(" .") for a in apuntes if a["clase"] == "Pendiente"]
        frases = []
        if not solo_pendientes:
            frases.append(f"Proyecto {ficha['nombre']}.")
            if v := ficha["version"]:
                frases.append(f"La versión actual es {Path(v['ruta']).name}.")
            decision = next((a["texto"] for a in reversed(apuntes) if a["clase"] == "Decisión"), "")
            if decision:
                frases.append(f"La última decisión fue {decision.rstrip(' .')}.")
        if len(pendientes) > 3 and not solo_pendientes:
            frases.append(f"Quedan {len(pendientes)} pendientes. El primero es {pendientes[0]}.")
        elif len(pendientes) == 1:
            frases.append(f"Queda pendiente {pendientes[0]}.")
        elif pendientes:
            lista = ", ".join(pendientes[:-1]) + " y " + pendientes[-1]
            frases.append(f"Quedan pendientes {lista}.")
        elif solo_pendientes or ficha["version"] or apuntes:
            frases.append("No quedan pendientes anotados en este proyecto.")
        return " ".join(frases)

    def _ver(self):
        ficha = self.proyectos.ficha()
        if not ficha or not ficha["version"]:
            return self._estado(ficha)
        if self.mostrar:
            self.mostrar(ficha)
            return f"Te muestro los cambios de {Path(ficha['version']['ruta']).name}."
        return detalle_ficha(ficha)

    def _abrir(self, anterior=False):
        from .acciones.word_activo import abrir_verificado
        if self._activo:
            return "Hay un trabajo en curso. Puedes pausarlo antes de cambiar de versión."
        if anterior:
            ruta = self.proyectos.anterior(self.proyectos.actual())
        else:
            ficha = self.proyectos.ficha()
            if not ficha or not (v := ficha["version"]):
                return "Todavía no hay un archivo vinculado al proyecto."
            ruta = Path(v["ruta"])
            if not ruta.is_file() or huella(ruta) != v["huella"]:
                return "El archivo se movió o cambió desde la última verificación."
        abierta = abrir_verificado(ruta)
        return (f"La versión de trabajo es {ruta.name}. " +
                ("Está abierta." if abierta else "No pude comprobar que Office la abriera."))

    def _comparar(self, contiguo):
        from .acciones.word_activo import capturar_documento
        doc = capturar_documento("word")
        actual = doc.seleccion.strip() or doc.parrafo.strip()
        if contiguo and actual and doc.parrafo_anterior:
            anterior = doc.parrafo_anterior
            if self.mostrar:
                self.mostrar(dict(nombre=doc.ruta.stem, apuntes=[], version=dict(operacion="Comparación de párrafos",
                    ruta=str(doc.ruta), cambios=[dict(ubicacion="Word", antes=anterior, despues=actual)])))
            return f"El párrafo anterior dice «{anterior[:160]}». El actual dice «{actual[:160]}»."
        ficha = self.proyectos.ficha()
        v = ficha and ficha["version"]
        if not actual or not v or Path(v["ruta"]) != doc.ruta or huella(doc.ruta) != v["huella"]:
            return "Selecciona un párrafo cambiado en la copia del proyecto para compararlo con su versión anterior."
        candidatos = [c for c in v["cambios"] if actual in c["despues"] and c["despues"]]
        if len(candidatos) != 1:
            return "No encontré un cambio único para ese párrafo en la revisión."
        cambio = candidatos[0]
        if self.mostrar:
            self.mostrar({**ficha, "version": {**v, "cambios": [cambio]}})
        return (f"Antes decía «{cambio['antes'][:200]}». " if cambio["antes"] else "Ese texto es nuevo. ") + f"Ahora dice «{actual[:200]}»."

    def _pedir_presentacion(self, ruta, texto):
        n = numeros_a_digitos(Frase(texto).txt)
        numero = re.search(r"\b(\d+) (?:diapositivas?|laminas?)\b", n)
        duracion = re.search(r"\b(\d+) minutos?\b", n)
        p = self._preparar(ruta, "presentacion", texto, cantidad=int(numero[1]) if numero else 6,
                           minutos=int(duracion[1]) if duracion else None)
        if duracion or numero:
            return Respuesta(ejecutar=lambda: self._iniciar(p))
        def tiempo(t):
            norm = numeros_a_digitos(Frase(t).sin_cortesia().txt)
            if es_no(norm):
                return Respuesta("De acuerdo.")
            if norm in {"como tu veas", "sin duracion", "adelante"}:
                return Respuesta(ejecutar=lambda: self._iniciar(p))
            m = re.fullmatch(r"(?:tengo |son |unos )?(\d+)(?: minutos?)?", norm)
            if not m:
                return None
            return Respuesta(ejecutar=lambda: self._iniciar(replace(p, minutos=int(m[1]))))
        return Respuesta("¿Cuántos minutos tienes para exponer? También puedes decir «como tú veas».",
                         siguiente=tiempo, espera_s=45)

    def procesar(self, texto):
        r = self._interpretar(texto)
        if r is not None and r.ejecutar:
            accion = r.ejecutar
            def ejecutar():
                try:
                    return accion()
                except (OSError, ValueError) as e:
                    return str(e)
            r.ejecutar = ejecutar
        return r

    def _interpretar(self, texto):
        f = Frase(texto).sin_cortesia()
        n = f.txt
        if _EXTERNA.search(n):
            return None
        if n in {"espera", "pausa el trabajo", "espera un momento", "cancela el trabajo"}:
            if self.cancelar(pausar=n != "cancela el trabajo"):
                return Respuesta("Cancelé el trabajo." if n == "cancela el trabajo" else "Pausé el trabajo.")
            return None
        if n in {"continua el trabajo", "reanuda el trabajo", "sigue con el trabajo"}:
            return Respuesta(ejecutar=self.reanudar)
        if n in {"como va el trabajo", "que estas haciendo con el documento"}:
            with self._lock:
                activo = self._activo
            return Respuesta(f"Trabajo sobre {activo[0].origen.name}." if activo else
                             "El trabajo está en pausa." if self._pausado else self._estado())
        proyecto = _PROYECTO.fullmatch(n)
        if proyecto and n != "retoma la version anterior" and ("proyecto " in n or proyecto["verbo"] in {"retomemos", "retoma"}):
            nombre = re.split(r"[.?!¿¡]|,?\s+(?:qué (?:dejamos|quedó|versión)|d[oó]nde nos quedamos)\b",
                              f.grupo(proyecto, "nombre"), maxsplit=1, flags=re.I)[0].strip()
            def elegir():
                if self._activo:
                    return "Pausa el trabajo en curso antes de cambiar de proyecto."
                clave = self.proyectos.elegir(nombre, crear=proyecto["verbo"] not in {"retomemos", "retoma", "continua con"})
                return self._estado(self.proyectos.ficha(clave))
            return Respuesta(ejecutar=elegir)
        if n in {"que dejamos pendiente", "que dejamos pendientes", "que quedo pendiente", "que queda pendiente",
                 "que queda pendiente en este proyecto", "que pendientes tiene este proyecto",
                 "que pendientes tengo en este proyecto", "cuales son los pendientes del proyecto",
                 "que pendientes tenemos en el proyecto", "pendientes del proyecto"}:
            return Respuesta(self._estado(solo_pendientes=True))
        if n in {"que version estabamos usando", "donde nos quedamos", "estado del proyecto",
                 "que dejamos pendiente y que version estabamos usando"}:
            return Respuesta(self._estado())
        vinculo = re.fullmatch(r"vincula (?:mis |las )?notas sobre (?P<tema>.+?) (?:a este|al) proyecto", n)
        if vinculo and self.conocimiento:
            def vincular():
                tema = f.grupo(vinculo, "tema")
                self.proyectos.apuntar(self.proyectos.actual(), "Notas vinculadas", tema)
                return self.conocimiento.buscar(tema)
            return Respuesta(ejecutar=vincular)
        if n in {"consulta las notas del proyecto", "busca en las notas del proyecto"} and self.conocimiento:
            ficha = self.proyectos.ficha()
            if not ficha:
                return Respuesta("Primero elige un proyecto.")
            temas = [a["texto"] for a in ficha["apuntes"] if a["clase"] == "Notas vinculadas"]
            return Respuesta(ejecutar=lambda: " ".join(self.conocimiento.buscar(tema) for tema in temas)
                             if temas else self.conocimiento.buscar(ficha["nombre"]))
        hecho = re.fullmatch(r"marca como (?:hecho|terminado) (?:el pendiente )?(?P<tarea>.+)", n)
        if hecho and self.proyectos.actual():
            def completar():
                self.proyectos.completar(self.proyectos.actual(), f.grupo(hecho, "tarea"))
                return "Marqué el pendiente como hecho."
            return Respuesta(ejecutar=completar)
        apunte = re.fullmatch(r"(?:(?:en este proyecto |para este proyecto )?(?:anota en (?:el|este) proyecto|"
                              r"recuerda para este proyecto|anota como decision|queda pendiente|"
                              r"anota como pendiente))(?: que)? (?P<dato>.+)", n)
        if apunte:
            def guardar():
                clase = "Pendiente" if "pendiente" in n[:apunte.start("dato")] else "Decisión"
                self.proyectos.apuntar(self.proyectos.actual(), clase, f.grupo(apunte, "dato"))
                return "Lo guardé en el proyecto."
            return Respuesta(ejecutar=guardar)
        if n in {"que cambiaste", "muestrame los cambios", "muestrame que cambiaste", "ver los cambios"}:
            return Respuesta(ejecutar=self._ver)
        if n in {"deshaz el ultimo cambio", "vuelve a la version anterior", "retoma la version anterior"}:
            return Respuesta(ejecutar=lambda: self._abrir(anterior=True))
        if n in {"abre la copia", "abre la version del proyecto", "abre la ultima version"}:
            return Respuesta(ejecutar=self._abrir)
        if re.fullmatch(r"compara (?:este parrafo|la seleccion|esto) con (?:el anterior|"
                        r"el que teniamos antes|la version anterior)", n):
            return Respuesta(ejecutar=lambda: self._comparar(n.endswith("con el anterior")))
        nro = numeros_a_digitos(n)
        duracion = re.search(r"\b(\d+) minutos?\b", nro)
        if duracion and ("presentacion" in n or "diapositivas" in n or "exposicion" in n) and re.match(
                r"^(?:tengo|me (?:alcanza|da) el tiempo|revisa si|cabe|alcanza|crees que|evalua si)\b", n):
            from pptx import Presentation
            ruta = self._contexto("powerpoint", usar_proyecto=True)
            total = len(Presentation(str(ruta)).slides)
            minutos = int(duracion[1])
            if not 1 <= minutos <= 180:
                return Respuesta("Indícame una duración de 1 a 180 minutos.")
            msg = f"Tienes {total} diapositivas y {minutos} minutos, unos {round(60 * minutos / max(1, total))} segundos por diapositiva."
            objetivo = max(2, min(minutos, 20))
            if total <= objetivo or not self.sugerir:
                return Respuesta(msg)
            p = self._preparar(ruta, "acortar", texto, cantidad=objetivo, minutos=minutos)
            from .texto import confirma_accion
            def aceptar(t):
                if confirma_accion(t):
                    return Respuesta(ejecutar=lambda: self._iniciar(p))
                return Respuesta("De acuerdo.") if es_no(t) else None
            return Respuesta(f"{msg} ¿Preparo una copia de {objetivo} diapositivas?", siguiente=aceptar, espera_s=35)
        if re.fullmatch(r"(?:acorta|simplifica|abrevia) esta diapositiva|esta diapositiva (?:tiene demasiado texto|esta muy larga)", n):
            from .acciones.word_activo import capturar_documento
            doc = capturar_documento("powerpoint")
            if not doc.diapositiva:
                return Respuesta("Selecciona la diapositiva que quieres acortar.")
            p = self._preparar(doc.ruta, "diapositiva", texto, diapositiva=doc.diapositiva)
            return Respuesta(ejecutar=lambda: self._iniciar(p))
        diapo = re.fullmatch(r"(?:(?:acorta|abrevia|resume|simplifica|corrige) )?(?:la |esta )?"
                             r"(?:(?P<ord>" + "|".join(_ORDINALES) + r")(?: diapositiva)?|"
                             r"diapositiva (?:numero )?(?P<num>\d+))"
                             r"(?: (?:tiene demasiado texto|esta muy larga|es muy larga|"
                             r"hazla mas (?:corta|breve)|esta cargada))?", nro)
        if diapo and (len(n.split()) > 3 or n.startswith(("acorta", "abrevia", "resume", "simplifica", "corrige"))):
            indice = int(diapo["num"]) if diapo["num"] else _ORDINALES.index(diapo["ord"]) + 1
            ruta = self._contexto("powerpoint", usar_proyecto=True)
            p = self._preparar(ruta, "diapositiva", texto, diapositiva=indice)
            return Respuesta(ejecutar=lambda: self._iniciar(p))
        if re.match(r"^(?:espera |mejor |conserva |manten |cambia |modifica |acorta |corrige |edita )", n) and re.search(
                r"\b(?:solo (?:el )?resumen|(?:el )?resumen (?:solamente|mas (?:corto|breve)))\b", n):
            with self._lock:
                actual = self._activo[0] if self._activo else self._pausado
            if actual and actual.modo in {"resumen", "solo_resumen"}:
                self.cancelar(pausar=True)
                p = replace(actual, texto=texto)
            else:
                ruta = self._contexto("word", usar_proyecto=True)
                p = self._preparar(ruta, "solo_resumen", texto)
            return Respuesta(ejecutar=lambda: self._iniciar(p))
        if (_PRESENTAR.match(n) and re.search(r"\b(?:exposicion|presentacion|diapositivas|powerpoint)\b", n)
                and _DOC.search(n)):
            return self._pedir_presentacion(self._contexto("word"), texto)
        if _RESUMIR.match(n) and _DOC.search(n) and re.search(r"\b(?:copia|tablas?|cambiaste|cambios)\b", n):
            p = self._preparar(self._contexto("word"), "resumen", texto, mostrar="muestra" in n)
            return Respuesta(ejecutar=lambda: self._iniciar(p))
        if re.match(r"^(?:revisa|corrige|edita) (?:este |el |mi )?(?:word|documento|informe)\b", n) and "copia" in n:
            p = self._preparar(self._contexto("word"), "revision", texto, mostrar="muestra" in n)
            return Respuesta(ejecutar=lambda: self._iniciar(p))
        return None
