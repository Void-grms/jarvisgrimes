"""Rutinas locales de proyecto. Cada paso se verifica antes de avanzar."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from . import obsidian
from .acciones import ventanas, word_activo
from .conocimiento import _enlace
from .respuesta import Respuesta
from .texto import Frase, confirma_accion, es_no, normalizar


_PASOS = {"nota": "abrir la ficha en Obsidian", "word": "abrir el Word del proyecto",
          "pendiente": "leer el primer pendiente"}
_DICTADOS = {
    "nota": r"(?:(?:abre|abrir|muestra|muestrame) )?(?:la |mi )?(?:nota|ficha)(?: del proyecto)?(?: en obsidian)?",
    "word": r"(?:(?:abre|abrir|muestra|muestrame) )?(?:(?:la |mi )?ultima version (?:de|del) |(?:el |mi )?(?:ultimo )?)word(?: del proyecto)?",
    "pendiente": r"(?:(?:lee|leer|dime|recuerdame|muestrame) )?(?:el |mi )?(?:primer |siguiente )?pendiente(?: del proyecto)?",
}
_ENSENAR = re.compile(r"^(?:enseña|ensena|ensename|aprende|crea|define|guarda|configura) "
                      r"(?:(?:una|la|mi) )?rutina (?P<nombre>.+?) para (?:el |mi )?proyecto (?P<proyecto>.+)$")
_EJECUTAR = re.compile(r"^(?:(?:(?:prepara|inicia) (?:mi |la )?|prepar (?:mi |la ))sesion de (?P<sesion>.+)|"
                       r"(?:ejecuta|inicia|empieza) (?:mi |la )?rutina (?P<nombre>.+))$")
_PAUSA = "rutinas:pendiente"
_PREFIJO = "rutinas:nombre:"


@dataclass(frozen=True)
class Rutina:
    nombre: str
    proyecto: str
    pasos: tuple[str, ...]

    def __post_init__(self):
        if any(not isinstance(n, str) or not normalizar(n) or len(n) > 100 for n in (self.nombre, self.proyecto)):
            raise ValueError("Dime un nombre de proyecto y de rutina de hasta cien caracteres.")
        if not self.pasos or len(set(self.pasos)) != len(self.pasos) or any(p not in _PASOS for p in self.pasos):
            raise ValueError("Los pasos disponibles son la nota, el Word y el primer pendiente.")


_TESIS = Rutina("Sesión de tesis", "Tesis", ("nota", "word", "pendiente"))


def _pasos_dictados(texto):
    texto = re.sub(r"[,;]", " y ", texto)
    partes = re.split(r"\s+(?:y(?: luego| despues)?|luego|despues)\s+", Frase(texto).sin_cortesia().txt)
    pasos = []
    for parte in partes:
        parte = re.sub(r"^(?:(?:primero|luego|despues|al final|por ultimo|y) )+", "", parte.strip())
        if not parte or parte == "y":
            continue
        paso = next((clave for clave, patron in _DICTADOS.items() if re.fullmatch(patron, parte)), None)
        if paso is None or paso in pasos:
            return ()
        pasos.append(paso)
    return tuple(pasos)


def _nota_abierta(raiz, ruta):
    """Contrasta el título de la instancia de Obsidian con el archivo de la pestaña activa."""
    titulo = re.compile(rf"^{re.escape(ruta.stem)}\s+[-–—]\s+{re.escape(raiz.name)}"
                        r"(?:\s+[-–—]\s+Obsidian\b.*)?$", re.I)
    candidatas = [v for v in ventanas.listar() if v.proceso == "obsidian" and titulo.fullmatch(v.titulo)]
    if len(candidatas) != 1:
        return False
    # Dos bóvedas pueden compartir nombre de carpeta y nota. En ese caso
    # ningún título de la ventana permite asignarle una ruta inequívoca.
    registro = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    try:
        bovedas = json.loads(registro.read_text(encoding="utf-8-sig"))["vaults"]
        iguales = {Path(b["path"]).resolve() for b in bovedas.values()
                   if Path(b["path"]).name.casefold() == raiz.name.casefold()}
        if iguales != {raiz.resolve()}:
            return False
        vista = json.loads((raiz / ".obsidian" / "workspace.json").read_text(encoding="utf-8-sig"))
        activo = vista.get("active")
        por_leer = [vista.get("main", {})]
        while activo and por_leer:
            nodo = por_leer.pop()
            if nodo.get("id") == activo and nodo.get("type") == "leaf":
                estado = nodo.get("state", {})
                archivo = estado.get("state", {}).get("file")
                if estado.get("type") == "markdown" and archivo and (raiz / archivo).resolve() == ruta:
                    return candidatas[0]
                break
            por_leer.extend(nodo.get("children", []))
        # Obsidian puede tardar en volcar el workspace. Con una sola nota de
        # ese nombre en la bóveda, la coincidencia de título también es exacta.
        for carpeta, dirs, nombres in os.walk(raiz):
            base = Path(carpeta)
            dirs[:] = [d for d in dirs if not d.startswith(".") and not _enlace(base / d)]
            if any(n.casefold() == ruta.name.casefold() and base / n != ruta for n in nombres):
                return False
        return candidatas[0]
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        pass
    return False


def _abrir_ficha(raiz, ruta):
    abierta = _nota_abierta(raiz, ruta)
    if abierta:
        if abierta.minimizada and not ventanas.enfocar(abierta.hwnd):
            raise ValueError("La ficha está en una ventana que no pude restaurar.")
        return "La ficha ya estaba abierta en Obsidian."
    aviso = obsidian.abrir_nota(raiz, ruta)
    if not aviso.startswith("Pedí a Obsidian"):
        raise ValueError(aviso)
    limite = time.monotonic() + 6
    while True:
        if _nota_abierta(raiz, ruta):
            return "La ficha está abierta en Obsidian."
        if time.monotonic() >= limite:
            raise ValueError("Pedí abrir la ficha en Obsidian, pero aún no pude verificar su ventana.")
        time.sleep(.2)


class Rutinas:
    def __init__(self, store, proyectos, *, office_ocupado=None):
        self.store = store
        self.proyectos = proyectos
        self.office_ocupado = office_ocupado
        self._lock = FileLock(str(store.ruta) + ".rutinas.lock", timeout=0)

    def _buscar(self, nombre):
        clave = normalizar(nombre)
        dato = self.store.get_meta(_PREFIJO + clave)
        if dato:
            return Rutina(**json.loads(dato))
        return _TESIS if clave == normalizar(_TESIS.nombre) else None

    def _guardar(self, rutina):
        self.store.set_meta(_PREFIJO + normalizar(rutina.nombre), json.dumps(asdict(rutina), ensure_ascii=False))
        return f"Guardé la rutina {rutina.nombre} para el proyecto {rutina.proyecto}."

    def _ensenar(self, nombre, proyecto):
        def recibir(texto):
            if es_no(texto):
                return Respuesta("De acuerdo.")
            pasos = _pasos_dictados(texto)
            if not pasos:
                return Respuesta("La rutina admite la nota, el Word y el primer pendiente. "
                                 "Dime los pasos en el orden que quieres.", siguiente=recibir)
            try:
                rutina = Rutina(nombre, proyecto, pasos)
            except ValueError as exc:
                return Respuesta(str(exc))
            def confirmar(texto):
                if confirma_accion(texto):
                    return Respuesta(ejecutar=lambda: self._guardar(rutina))
                return Respuesta("De acuerdo.") if es_no(texto) else None
            verbo = "Reemplazo" if self._buscar(nombre) else "Guardo"
            lista = ", luego ".join(_PASOS[p] for p in pasos)
            return Respuesta(f"¿{verbo} {nombre} para {proyecto} con estos pasos? {lista}.", siguiente=confirmar)
        return Respuesta("¿Qué pasos quieres y en qué orden? Puedes decir «abre la ficha, abre el Word "
                         "y dime el primer pendiente».", siguiente=recibir)

    def _pendiente(self):
        dato = self.store.get_meta(_PAUSA)
        if dato:
            cursor = json.loads(dato)
            rutina = Rutina(**cursor["rutina"])
            indice = cursor["paso"]
            if type(indice) is int and 0 <= indice < len(rutina.pasos):
                return rutina, indice
        return None

    def _marcar(self, rutina, indice):
        valor = json.dumps({"rutina": asdict(rutina), "paso": indice}, ensure_ascii=False)
        self.store.set_meta(_PAUSA, valor if indice < len(rutina.pasos) else "")

    def _paso(self, paso, rutina, clave):
        if paso == "nota":
            ruta = self.proyectos.ruta_nota(rutina.proyecto)
            return _abrir_ficha(self.proyectos.raiz.resolve(), ruta.resolve())
        if paso == "word":
            ruta = self.proyectos.word_de_trabajo(clave)
            try:
                activo = word_activo.capturar_documento("word")
            except word_activo.ErrorDocumento:
                activo = None
            if activo and activo.ruta == ruta:
                return f"Word ya muestra {ruta.name}."
            if not word_activo.abrir_verificado(ruta):
                raise ValueError(f"No pude comprobar que Word mostrara {ruta.name}.")
            return f"Word muestra {ruta.name}."
        ficha = self.proyectos.ficha(clave)
        primero = next((a["texto"] for a in ficha["apuntes"] if a["clase"] == "Pendiente"), "")
        return f"Primer pendiente, {primero}." if primero else "No quedan pendientes anotados."

    def _ejecutar(self, rutina=None, omitir=False):
        try:
            with self._lock:
                if self.office_ocupado and self.office_ocupado():
                    return "Pausa el trabajo de Office en curso antes de preparar otro proyecto."
                if rutina is None:
                    pendiente = self._pendiente()
                    if pendiente is None:
                        return "No hay una rutina en pausa."
                    rutina, indice = pendiente
                    if omitir:
                        indice += 1
                else:
                    indice = 0
                clave = self.proyectos.elegir(rutina.proyecto, crear=False)
                self._marcar(rutina, indice)
                mensajes = []
                for i in range(indice, len(rutina.pasos)):
                    paso = rutina.pasos[i]
                    try:
                        mensajes.append(self._paso(paso, rutina, clave))
                    except (ValueError, OSError) as exc:
                        mensajes.extend([str(exc), "La rutina está en pausa."])
                        if paso == "word":
                            mensajes.append("Puedes poner tu Word guardado al frente y decir «vincula este Word a la rutina».")
                        mensajes.append("Di «reanuda la rutina» u «omite este paso de la rutina».")
                        return " ".join(mensajes)
                    self._marcar(rutina, i + 1)
                return " ".join([f"Sesión de {rutina.proyecto} lista.", *mensajes])
        except Timeout:
            return "Hay otra rutina en ejecución."
        except (ValueError, OSError) as exc:
            return str(exc)

    def _vincular(self):
        try:
            with self._lock:
                if self.office_ocupado and self.office_ocupado():
                    return "Pausa el trabajo de Office antes de cambiar de versión."
                pendiente = self._pendiente()
                if not pendiente or pendiente[0].pasos[pendiente[1]] != "word":
                    return "Puedes vincular el documento cuando una rutina esté esperando su Word."
                rutina, _ = pendiente
                doc = word_activo.capturar_documento("word")
                clave = self.proyectos.elegir(rutina.proyecto, crear=False)
                self.proyectos.vincular_word(clave, doc.ruta)
                return f"Vinculé {doc.ruta.name} a {rutina.proyecto}. Di «reanuda la rutina»."
        except Timeout:
            return "Espera a que termine el paso en curso."
        except (ValueError, OSError) as exc:
            return str(exc)

    def procesar(self, texto):
        frase = Frase(texto).sin_cortesia()
        n = frase.txt
        if n in {"que rutinas tengo", "mis rutinas", "lista mis rutinas"}:
            with self.store.transaccion() as con:
                filas = con.execute("SELECT valor FROM meta WHERE clave LIKE ? ORDER BY clave", (_PREFIJO + "%",))
                nombres = {normalizar(_TESIS.nombre): _TESIS.nombre}
                for fila in filas:
                    rutina = Rutina(**json.loads(fila[0]))
                    nombres[normalizar(rutina.nombre)] = rutina.nombre
            return Respuesta("Tus rutinas son " + ", ".join(nombres.values()) + ".")
        if n in {"reanuda la rutina", "continua la rutina", "sigue con la rutina", "omite este paso de la rutina"}:
            return Respuesta(ejecutar=lambda: self._ejecutar(omitir=n.startswith("omite")))
        if n == "cancela la rutina":
            def cancelar():
                try:
                    with self._lock:
                        if not self._pendiente():
                            return "No hay una rutina en pausa."
                        self.store.set_meta(_PAUSA, "")
                        return "Cancelé los pasos pendientes de la rutina."
                except Timeout:
                    return "Espera a que termine el paso en curso."
            return Respuesta(ejecutar=cancelar)
        if n in {"vincula este word a la rutina", "vincula el word abierto a la rutina"}:
            return Respuesta(ejecutar=self._vincular)
        if ensenar := _ENSENAR.fullmatch(n):
            return self._ensenar(frase.grupo(ensenar, "nombre"), frase.grupo(ensenar, "proyecto"))
        if ejecutar := _EJECUTAR.fullmatch(n):
            nombre = (f"Sesión de {frase.grupo(ejecutar, 'sesion')}" if ejecutar["sesion"]
                      else frase.grupo(ejecutar, "nombre"))
            rutina = self._buscar(nombre)
            if not rutina:
                return Respuesta(f"No conozco la rutina {nombre}. Puedes enseñármela con «crea la rutina "
                                 f"{nombre} para el proyecto», seguido de su nombre.")
            return Respuesta(ejecutar=lambda: self._ejecutar(rutina),
                             confirmar_voz=f"¿Preparo la rutina {rutina.nombre}?")
        return None
