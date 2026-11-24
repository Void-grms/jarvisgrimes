"""Órdenes explícitas para la ventana activa. Conserva el texto original al escribir."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from .respuesta import Respuesta
from .texto import confirma_accion, es_no, normalizar

if TYPE_CHECKING:
    from .acciones.pantalla import Pantalla


_VER = re.compile(
    r"(?:(?:dime )?que (?:ves|hay|aparece|se ve|se muestra) (?:en (?:mi |la |esta )?"
    r"(?:pantalla|ventana|pagina)(?: activa| actual)?|aqui)|"
    r"(?:describe|describeme|describir|mira|revisa) (?:lo que (?:ves|hay) en )?"
    r"(?:mi |la |esta )?(?:pantalla|ventana|pagina)(?: activa| actual)?)$")
_MARCO = r"(?:aqu[ií]|en (?:esta (?:ventana|pantalla|p[aá]gina|aplicaci[oó]n)|la ventana (?:activa|actual)))"
_BUSCAR = re.compile(r"^(?:busca|b[uú]scame)\s+(.+)$", re.I | re.S)
_CLIC = re.compile(
    r"^(?:(?:haz|da|dale)\s+(?:un\s+)?cli(?:c|ck)"
    r"(?:\s+(?:en|sobre|a|al))?|(?:pulsa|presiona)(?=\s+(?:el\s+)?bot[oó]n))"
    r"(?:\s+(.*))?$", re.I | re.S)
_ESCRIBIR = re.compile(r"^(?:escribe|teclea)(?:\s*[:,]\s*|\s+|$)(.*)$", re.I | re.S)
_CAMPO = r"(?:el\s+)?(?:campo|cuadro(?: de texto)?|casilla)"
_FOCO = r"(?:aqu[ií]|en (?:este campo|el campo (?:activo|actual|enfocado)|el cursor))"
_PETICION = re.compile(
    r"^(?:puedes|podr[ií]as|quiero que|necesito que|quisiera que)\s+(\w+)\b", re.I)
_VERBOS = {"hacer": "haz", "hagas": "haz", "dar": "da", "des": "da", "pulsar": "pulsa",
           "pulses": "pulsa", "presionar": "presiona", "presiones": "presiona", "buscar": "busca",
           "busques": "busca", "escribir": "escribe", "escribas": "escribe", "teclear": "teclea",
           "describir": "describe", "describas": "describe", "decirme": "dime"}
_ETIQUETA = re.compile(r"^(?:(?:el|la|un|una)\s+)?(?:bot[oó]n|opci[oó]n|enlace|pestaña|control)\s+", re.I)
_SENSIBLE = re.compile(
    r"\b(?:enviar|envia|send|publicar|publica|publish|comprar|compra|pagar|paga|pay|buy|"
    r"eliminar|elimina|borrar|borra|delete|remove|descartar|descarta|discard|"
    r"aceptar|acepta|confirmar|confirma|autorizar|autoriza|aprobar|aprueba|"
    r"accept|confirm|approve|allow|instalar|install|compartir|share|cerrar|close|"
    r"transferir|firmar|submit|restablecer|reset|guardar|save|sobrescribir|reemplazar|continuar|"
    r"permitir|siguiente|continue|yes|ok|si)\b")
_COMILLAS = re.compile(r"""(?:«([^»]*)»|“([^”]*)”|"([^"]*)"|'([^']*)')[.!?]?\Z""", re.S)
_NUEVO_PEDIDO = re.compile(r"^(?:jarvis|oye|abre|abreme|busca|escribe|repite|dime|que ves|haz|"
                           r"pon|pulsa|quiero que|necesito que|puedes|podrias|explica)\b")


def _pedido(texto: str) -> str:
    cuerpo = texto.strip()
    if cuerpo.startswith("¿") and cuerpo.endswith("?"):
        cuerpo = cuerpo[1:-1].strip()
    cuerpo = re.sub(r"^por favor[\s,]+", "", cuerpo, flags=re.I)
    m = _PETICION.match(cuerpo)
    if m and (verbo := _VERBOS.get(m[1].casefold())):
        return verbo + cuerpo[m.end():]
    return cuerpo


def _literal(texto: str) -> str:
    texto = texto.strip()
    cita = _COMILLAS.fullmatch(texto)
    return next(valor for valor in cita.groups() if valor is not None) if cita else texto


def _etiqueta(texto: str) -> str:
    texto = re.sub(r"(?:,\s*|\s+)por favor[.!?]?$", "", texto.strip(), flags=re.I)
    return _literal(_ETIQUETA.sub("", texto.strip(" .!?")))


def _orden(pantalla: Pantalla, operacion: str, pregunta: str, *argumentos: str,
           siempre: bool = False) -> Respuesta:
    # Preparar identifica el destino. El motor ejecuta tras resolver la confirmación.
    ejecutar = pantalla.preparar(operacion, *argumentos)
    if isinstance(ejecutar, str):
        return Respuesta(ejecutar)
    if not siempre:
        return Respuesta(confirmar_voz=pregunta, ejecutar=ejecutar)

    def decidir(texto: str) -> Respuesta | None:
        if confirma_accion(texto):
            return Respuesta(ejecutar=ejecutar)
        return Respuesta("De acuerdo.") if es_no(texto) else None

    return Respuesta(pregunta, siguiente=decidir, espera_s=15)


def _pedir_dato(pregunta: str, recibir: Callable[[str], Respuesta]) -> Respuesta:
    def atender(texto: str) -> Respuesta | None:
        if es_no(texto):
            return Respuesta("De acuerdo.")
        if _NUEVO_PEDIDO.match(normalizar(texto)) or confirma_accion(texto):
            return None
        return recibir(texto)

    return Respuesta(pregunta, siguiente=atender, espera_s=20)


def _clic(pantalla: Pantalla, nombre: str) -> Respuesta:
    etiqueta = _etiqueta(nombre)
    n = normalizar(etiqueta)
    if n in {"", "ahi", "aqui", "alli", "ese", "esa", "eso", "ese boton", "este boton",
             "el boton", "el campo", "esa opcion", "este", "esta"}:
        return _pedir_dato("¿Cómo se llama el control en el que quieres hacer clic?",
                           lambda t: _clic(pantalla, t))
    return _orden(pantalla, "clic", f"¿Hago clic en «{etiqueta}»?", etiqueta,
                  siempre=bool(_SENSIBLE.search(n)))


def _escribir(pantalla: Pantalla, texto: str, campo: str = "") -> Respuesta:
    literal = _literal(texto)
    if not literal:
        return _pedir_dato("¿Qué texto quieres escribir?", lambda t: _escribir(pantalla, t, campo))
    etiqueta = _literal(campo.strip(" .!?"))
    if normalizar(etiqueta) in {"actual", "activo", "enfocado"}:
        etiqueta = ""
    destino = f"«{etiqueta}»" if etiqueta else "el campo activo"
    return _orden(pantalla, "escribir", f"¿Escribo en {destino}?", literal, etiqueta)


def _buscar(pantalla: Pantalla, consulta: str) -> Respuesta:
    q = _literal(consulta)
    if not q:
        return _pedir_dato("¿Qué quieres buscar en esta ventana?", lambda t: _buscar(pantalla, t))
    return _orden(pantalla, "buscar", "¿Hago esa búsqueda en esta ventana?", q)


def es_dictado_literal(texto: str) -> bool:
    cuerpo = _pedido(texto)
    m = _ESCRIBIR.fullmatch(cuerpo)
    return bool(m and (re.match(r"^(?:escribe|teclea)\s*[:,]", cuerpo, re.I) or _COMILLAS.fullmatch(m[1])))


def procesar_pantalla(texto: str, pantalla: Pantalla) -> Respuesta | None:
    """Se llama antes del buscador general. Solo toma órdenes con destino de pantalla."""
    cuerpo = _pedido(texto)
    if _VER.fullmatch(normalizar(cuerpo)):
        return Respuesta(pantalla.describir())
    m = _CLIC.fullmatch(cuerpo.rstrip(".!?"))
    if m:
        return _clic(pantalla, m[1] or "")
    m = _BUSCAR.fullmatch(cuerpo.rstrip(".!?"))
    if m:
        resto = m[1].strip()
        if re.fullmatch(_MARCO, resto, re.I):
            return _buscar(pantalla, "")
        consulta = re.fullmatch(rf"{_MARCO}[\s:,]+(.+)", resto, re.I | re.S)
        if not consulta:
            consulta = re.fullmatch(rf"(.+?)\s+{_MARCO}", resto, re.I | re.S)
        if consulta:
            return _buscar(pantalla, consulta[1])
    m = _ESCRIBIR.fullmatch(cuerpo)
    if not m:
        return None
    resto = m[1]
    # Las comillas y los dos puntos marcan un dictado literal de un solo turno.
    if es_dictado_literal(texto):
        return _escribir(pantalla, resto)
    foco = re.fullmatch(rf"{_FOCO}(?:[\s:,]+(.*))?", resto, re.I | re.S)
    if foco:
        return _escribir(pantalla, foco[1] or "")
    campo = re.fullmatch(rf"en\s+{_CAMPO}\s+([^:,]+?)\s*[:,]\s*(.+)", resto, re.I | re.S)
    if campo:
        return _escribir(pantalla, campo[2], campo[1])
    campo = re.fullmatch(rf"(.+?)\s+en\s+{_CAMPO}\s+(.+)", resto, re.I | re.S)
    if campo:
        return _escribir(pantalla, campo[1], campo[2])
    if re.match(rf"en\s+{_CAMPO}\b", resto, re.I):
        return Respuesta("Dime el texto seguido de «en el campo» y su nombre.")
    return None


def escritura_libre(texto: str, pantalla: Pantalla) -> Respuesta | None:
    """Se llama después de las órdenes de documentos, dictado, mensajes y servicios."""
    m = _ESCRIBIR.fullmatch(_pedido(texto))
    if not m:
        return None
    return _escribir(pantalla, m[1])
