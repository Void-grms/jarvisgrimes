"""Comandos por voz: frases en español → acciones.

Todo se resuelve con reglas (sin pasar por el modelo), por eso responde al instante.
Lo que no coincide con ninguna regla pasa a las preguntas de pendientes y, al final,
a la conversación con el modelo.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .acciones import sistema, ventanas
from .acciones.archivos import (IndiceArchivos, archivo_desde_titulo, carpetas_usuario, extraer_texto,
                                fecha_hablada, lugar_hablado, texto_office_activo, ventana_activa)
from .acciones.clima import Clima
from .acciones import niveles
from .acciones.google import Calendario, GoogleNoConectado
from .acciones.navjarvis import NavegadorJarvis
from .acciones.resumir import guardar as guardar_resumen
from .acciones.resumir import resumir
from .acciones.sesiones import Sesiones, usuario_dictado
from .acciones.whatsapp import NecesitaQR, NoEncontrado, WhatsApp
from .acciones.documentos import TIPOS, Documentos, Pedido, detectar_tipo
from .acciones.navegador import APP_O_WEB, SERVICIOS, Navegador
from .acciones.pestana_propia import Apertura
from .acciones.notas import Notas
from .acciones.plantillas import Plantillas, limpiar_valor
from .acciones.recordatorios import Recordatorios
from .respuesta import Respuesta
from .actividad import ResultadoAccion
from .texto import (DIAS, Frase, confirma_accion, cuando_hablado, duracion_hablada, es_no, es_si, hora_hablada,
                    normalizar, parse_duracion, parse_fecha_hora, parse_hora, parse_rango,
                    quitar_conectores)

log = logging.getLogger("comandos")


def R(p: str) -> re.Pattern:
    return re.compile(p)


# Una preferencia se recuerda incluso después de reiniciar. Aceptamos una petición de
# trato, no cualquier frase que contenga «nombre» o «quiero que me digas».
_TRATO = R(r"^(?:(?:a partir de ahora|de ahora en adelante|desde ahora|mejor|por favor) )*"
           r"(?:(?:quiero|prefiero|quisiera) que me (?:llames|digas)|llamame|"
           r"puedes llamarme|me llamo|mi nombre es|cambia mi nombre a) (?P<nombre>.+)$")
_CONTEXTO_TRATO = R(r"\b(?:cambi(?:a|ar|e) (?:mi|el) nombre|no (?:quiero que )?me (?:digas|llames)|"
                    r"me (?:estas|esta|sigues) (?:diciendo|llamando))\b")
_OTRA_PERSONA = R(r"^(?:anota|apunta|escribe|dile|repite|traduce|si|cuando|ella|el dice|me dijo)\b")
_NO_NOMBRE = set(("que quien cuando como donde por si no al el la los las un una mi tu su me te es y o "
                  "para pero quiero dime digas llames llamame abre abrir abras cierra busca pon recuerda "
                  "guarda suena esta este esto eso algo nada hoy manana luego despues tarde noche ahora "
                  "hora gracias favor claro bien mejor hola jarvis si perfecto listo correcto cuanto cual "
                  "todo mas menos porque").split())


def nombre_dictado(texto: str) -> str | None:
    f = Frase(texto)
    candidato = f.original()
    # Un nombre personal se dicta en unas pocas palabras. Conservamos las tildes.
    if not re.fullmatch(r"[^\W\d_]+(?:[-'’ ][^\W\d_]+){0,3}", candidato) or len(candidato) > 48:
        return None
    if len(f.txt) < 2 or _NO_NOMBRE.intersection(f.norm):
        return None
    return candidato.title()


def preferencia_nombre(texto: str) -> str | None:
    f = Frase(texto)
    if _OTRA_PERSONA.match(f.txt):
        return None
    # Una rectificación puede llevar una explicación antes del nuevo nombre.
    if _CONTEXTO_TRATO.search(f.txt):
        for m in re.finditer(r"\b(?:quiero que me (?:llames|digas)|llamame|mi nombre es)\b", f.txt):
            if not re.search(r"\bno\s*$", f.txt[:m.start()]):
                f = Frase(f.original(m.start()))
                break
    cortes = re.search(r" (?:por favor|porfa|gracias|jarvis)$", f.txt)
    if cortes:
        f = f.sin_span(*cortes.span())
    m = _TRATO.fullmatch(f.txt)
    if not m:
        return None
    nombre = f.grupo(m, "nombre")
    if "me digas " in f.txt and not _CONTEXTO_TRATO.search(normalizar(texto)) and not nombre[:1].isupper():
        return None  # «quiero que me digas adivinanzas» es una petición de conversación
    return nombre_dictado(nombre)


def pide_nombre(texto: str) -> bool:
    f = Frase(texto)
    n = f.txt
    if _OTRA_PERSONA.match(n):
        return False
    if re.fullmatch(r"(?:quiero (?:que cambies|cambiar)|cambia|cambiar) (?:mi|el) nombre", n):
        return True
    if re.search(r"\b(?:mi|el) nombre\b", n) and re.search(r"\bno me (?:digas|llames)\b", n):
        return True
    m = re.fullmatch(r"(?:ya )?no me (?:digas|llames) (?P<anterior>[a-z]+)", n)
    if not m:
        return False
    anterior = f.grupo(m, "anterior")
    return anterior[:1].isupper() or m.group("anterior") in {"patron", "jefe", "senor", "amigo"}


# ------------------------------------------------------------------ reglas generales
SOLO = R(r"^(?:nada|nada por ahora|por ahora nada|nada gracias|nada mas|no nada|ninguna|nada hoy|"
         r"hoy nada|(?:hoy )?(?:voy a |quiero )?trabajar(?:e)? solo(?: hoy| por ahora)?|"
         r"trabajare solo(?: hoy)?|voy a estar solo|quiero estar solo|dejame solo|"
         r"dejame trabajar|no necesito nada|modo silencio|silencio|callate|no gracias)$")
DICTADO_INI = R(r"^(?:modo dictado|dictado|activa el dictado|empieza (?:a escribir|el dictado)|"
                r"escribe lo que (?:te )?diga|vamos a dictar|voy a dictar(?:te)?|dicta)$")
DICTADO_FIN = R(r"\b(?:fin del dictado|termina(?:r)? (?:el )?dictado|para de escribir|deja de escribir|"
                r"basta de dictado|deten(?:er)? (?:el )?dictado|sal(?:ir)? del dictado|fin dictado)\b")
DICTADO_ENTER = R(r"(?:^| )(?:nueva linea|nuevo parrafo|punto y aparte|salto de linea)$")
DICTADO_DESHACER = R(r"^(?:borra eso|deshacer|deshaz|borra lo ultimo)$")
AYUDA = R(r"\b(?:que puedes hacer|que sabes hacer|que haces|ayuda|comandos|que funciones tienes|"
          r"en que me (?:puedes )?ayudar|como te uso)\b")

MEDIA = R(r"^(?:(?P<pausa>pausa|pausala|pausa la musica|pausa el video|pon pausa|reanuda|reanudalo|"
          r"continua|play|dale play)|(?P<sig>siguiente|siguiente cancion|cancion siguiente|"
          r"pasa la cancion|salta la cancion|la siguiente)|(?P<ant>anterior|cancion anterior|la anterior))$")

_EQUIPO = r"\b(?:pc|computadora|laptop|equipo|compu|maquina|ordenador)\b"
BLOQUEAR = R(rf"^bloquea(?:r)? (?:la |el |mi )?(?:{_EQUIPO}|pantalla|sesion)$")
APAGAR = R(rf"^apaga(?:r)? (?:la |el |mi )?{_EQUIPO}$")
REINICIAR = R(rf"^reinicia(?:r)? (?:la |el |mi )?{_EQUIPO}$")
SUSPENDER = R(rf"^(?:suspende(?:r)?|hiberna(?:r)?|pon a dormir) (?:la |el |mi )?{_EQUIPO}$")
CANCELAR_APAGADO = R(r"\bcancela(?:r)? (?:el )?(?:apagado|reinicio)\b|\bno (?:la )?apagues\b")
CAPTURA = R(r"^(?:(?:toma|saca|haz|guarda) (?:una? )?)?(?:captura(?: (?:de |la )?pantalla)?|pantallazo|screenshot)$")

POMODORO = R(r"^(?:(?:empieza|pon(?:me)?|inicia|activa|programa) (?:un |el )?)?"
             r"(?:pomodoro|modo (?:estudio|concentracion|enfoque)|bloque de (?:estudio|concentracion))\b")
LISTAR_REC = R(r"\b(?:que|cuales) (?:recordatorios|alarmas|temporizadores)\b|\bmis (?:recordatorios|alarmas)\b")
CANCELAR_REC = R(r"\b(?:cancela|borra|elimina|quita)(?:r)? (?:todos )?(?:los |mis |las )?"
                 r"(?:recordatorios|alarmas|temporizadores)\b")
RECORDAR = R(r"^(?:recuerdame|recordarme|recordar|avisame|avisarme|me recuerdas|me avisas)\b")
TEMPORIZADOR = R(r"^(?:(?:pon(?:me)?|poner|crea|activa|programa) )?(?:un |una )?"
                 r"(?P<tipo>temporizador|timer|cronometro|alarma)\b")

NOTA_ADD = R(r"^(?:anota(?:me)?|apunta(?:me)?|toma (?:una )?nota|guarda (?:una )?nota|"
             r"agrega (?:una nota|a mis notas)|nota)(?: de| que| esto)?(?P<q> .+)$")
NOTA_LEER = R(r"\b(?:lee|leeme|dime|cuales son|que dicen) (?:mis|las) notas\b|\bque notas tengo\b")
NOTA_BORRAR = R(r"\b(?:borra|elimina|limpia)(?:r)? (?:mis|las|todas las) notas\b")

DOC_VERBOS = (r"(?:me )?(?:haz|hazme|hacer|hacerme|hagas|crea|creame|crear|crearme|crees|genera|"
              r"generame|generar|generes|prepara|preparame|preparar|prepares|redacta|redactame|"
              r"redactar|redactes|elabora|elaborame|elaborar|elabores|arma|armame|armar|escribe|"
              r"escribeme|escribir|escribas|necesito|quiero|quisiera)")
DOC_NOMBRES = (r"(?:documento|doc|word|informe|reporte|ensayo|carta|oficio|solicitud|resumen|"
               r"articulo|monografia|plan|guia|manual|acta|memorando|memo|pdf|presentacion|"
               r"diapositivas|power ?point|ppt|slides|excel|hoja de calculo|hoja de excel|"
               r"planilla|tabla|cuadro)")
DOCUMENTO = R(rf"^{DOC_VERBOS} (?:un |una |el |la |unas |unos )?(?P<clase>{DOC_NOMBRES})"
              r"(?: (?:en|de|tipo|formato) (?P<formato>word|pdf|power ?point|ppt|excel|diapositivas))?"
              r"(?: (?:sobre|de|acerca de|del|de la|para|con|que (?:hable|trate) (?:de|sobre)) (?P<tema>.+))?$")
PLANTILLA = R(r"^(?:llena(?:me)?|llenar|rellena(?:me)?|rellenar|completa(?:me)?|completar)"
              r"(?: la| el| los| las| mi| mis| una| un)? (?:plantilla|formulario|formato|ficha|campos)s?"
              r"(?: (?:de|del|de la|para|llamad[ao]))?(?: (?P<q>.+))?$")

LEER_CORREO = R(r"\b(?:lee(?:me)?|dime|que dice|de que trata|revisa|busca)\b.*\bcorreos?\b"
                r"(?:.*?\b(?:de|del) (?P<q>.+))?$")
ABRIR_CORREO = R(r"^(?:abre|abreme|abrir) (?:el )?(?:ultimo )?correo (?:de|del) (?P<q>.+)$")
CLIMA = R(r"\b(?:clima|que tiempo hace|como esta el tiempo|el tiempo (?:de )?(?:hoy|manana)|"
          r"temperatura|va a llover|llovera|hace frio|hace calor|pronostico)\b")

# ------------------------------------------------------------------ navegador
YT = r"(?:you ?tube|yutub|youtub|yutu|yu tub)"
PLAY = (r"(?:pon|ponme|poner|reproduce|reproduceme|reproducir|toca|tocame|quiero ver|"
        r"quiero escuchar|muestrame|ver)")
BUSCA = r"(?:busca|buscame|buscar)"
YT_ABRIR = R(rf"^(?:abre|abrir|abreme|entra a|entra en|ve a) {YT}$")
YT_CONSULTA = R(rf"^(?P<v>{PLAY}|{BUSCA}) en {YT} (?P<q>.+)$")
YT_DESTINO = R(rf"^(?P<v>{PLAY}|{BUSCA}) (?P<q>.+?) (?:en|por) {YT}$")
YT_COMPUESTO = R(rf"^(?:abre|abrir|abreme|entra a|entra en|ve a) {YT} y (?P<v>{PLAY}|{BUSCA}) (?P<q>.+)$")
MUSICA = R(rf"^(?P<v>{PLAY}) (?P<q>(?:musica|una cancion|la cancion|canciones|el video|un video|"
           r"videos|el tema)(?: .+)?)$")

IA_ALIAS = {
    "google_ia": (r"(?:el )?(?:modo|busqueda|buscador) (?:con |de )?(?:ia|i a|inteligencia artificial)"
                  r"(?: de google)?|google (?:con |de )?(?:ia|i a|inteligencia artificial)|"
                  r"(?:la )?(?:ia|i a|inteligencia artificial) de google|google ai|modo ai"),
    "chatgpt": r"chat ?g ?p ?t|chat ?gpt|chad ?gpt|chat ?jipiti|chat ?yipiti|chat ?ge ?pe ?te|chatgpt|chat gt|gpt",
    "claude": r"claude|claud|clod|clode|cloud|clau",
    "gemini": r"gemini|geminis|yemini|jemini|gemeni|geminy|yeminai",
    "perplexity": r"perplexity|perplexiti|perplejiti",
    "copilot": r"copilot|copailot|co pilot|copilo",
    "deepseek": r"deep ?seek|dipsik|deepsik|dip sik",
}
_S = "(?P<s>" + "|".join(IA_ALIAS.values()) + ")"
_PREG = (r"(?:pregunta|preguntale|preguntar|preguntarle|consulta|consultale|consultar|dile|decirle|"
         r"pidele|pide|pedirle|escribele|escribe|busca|buscar|investiga|averigua)")
IA_ABRIR = R(rf"^(?:abre|abrir|abreme|entra a|entra en|ve a|anda a) (?:la )?{_S}$")
IA_ABRE_Y = R(rf"^(?:abre|abrir|abreme|entra a|entra en|ve a|anda a|usa|usando|con|en) (?:la )?{_S}"
              rf"(?: y)? {_PREG}(?: (?:sobre|acerca de))? (?P<q>.+)$")
IA_PREG = R(rf"^{_PREG} (?:a |al |en |con |le a )?(?:la )?{_S}(?: (?:sobre|acerca de))? (?P<q>.+)$")
IA_FINAL = R(rf"^{_PREG} (?P<q>.+?) (?:a|al|en|con|usando) (?:la )?{_S}$")
GIA_1 = R(r"^(?:busca|buscame|buscar|pregunta|investiga|consulta|averigua) (?:en google )?con (?:la )?"
          r"(?:ia|i a|inteligencia artificial) (?P<q>.+)$")
GIA_2 = R(r"^(?:busca|buscame|buscar|investiga|averigua) (?P<q>.+?) con (?:la )?(?:ia|i a|inteligencia artificial)$")
_G = r"(?:google|gugel|gogle)"
GOOGLE = R(rf"^(?:busca|buscame|buscar|googlea|googlear|investiga|averigua) (?:en {_G} )?(?P<q>.+?)(?: en {_G})?$")
GOOGLE_COMPUESTO = R(rf"^(?:abre|abreme|abrir|entra a|ve a) {_G} y (?:luego )?{BUSCA} (?P<q>.+)$")
NAV_Y = R(r"^(?:abre|abrir|abreme) (?:el )?(?:navegador|chrome|google chrome|edge|microsoft edge) y (?P<resto>.+)$")
ABRIR = R(r"^(?:abre|abra|abreme|abrir|ejecuta|lanza|inicia|entra a|entra en|ve a|anda a|llevame a)"
          r"(?: el| la| los| las| mi| mis)?(?: programa| aplicacion| app| pagina web| pagina| sitio| web| carpeta)?"
          r"(?: de| del)? (?P<q>.+)$")
NAVEGADOR_SOLO = {"navegador", "chrome", "google chrome", "edge", "microsoft edge", "internet",
                  "una pestana", "nueva pestana", "una ventana", "nueva ventana", "otra ventana"}
CERRAR_PESTANAS = R(r"^(?:se (?:han abierto|abrieron) (?:varias|muchas|unas) pestanas )?"
                    r"(?:(?:quiero|necesito) que )?(?:(?:la|las) )?(?:cierres?|cierra|cerrar|cierralas?)"
                    r"(?: (?:esta|esa|las|todas las|la|mis) pestanas?)?(?: y .*)?$")
# ------------------------------------------------------------------ control de la computadora
# Atajos con nombre → teclas (lo que harías con el teclado).
ATAJOS = {
    "copia": "ctrl+c", "copialo": "ctrl+c", "copia eso": "ctrl+c", "copia esto": "ctrl+c",
    "pega": "ctrl+v", "pegalo": "ctrl+v", "pega eso": "ctrl+v", "pega aqui": "ctrl+v",
    "corta": "ctrl+x", "cortalo": "ctrl+x", "deshaz": "ctrl+z", "deshacer": "ctrl+z", "deshazlo": "ctrl+z",
    "rehaz": "ctrl+y", "rehacer": "ctrl+y", "selecciona todo": "ctrl+a", "seleccionar todo": "ctrl+a",
    "guarda": "ctrl+s", "guardalo": "ctrl+s", "guarda el archivo": "ctrl+s", "guarda el documento": "ctrl+s",
    "guarda los cambios": "ctrl+s", "nueva pestana": "ctrl+t", "siguiente pestana": "ctrl+tab",
    "pestana siguiente": "ctrl+tab", "pestana anterior": "ctrl+shift+tab", "anterior pestana": "ctrl+shift+tab",
    "reabre la pestana": "ctrl+shift+t", "recupera la pestana": "ctrl+shift+t",
    "recarga": "f5", "recarga la pagina": "f5", "actualiza la pagina": "f5", "refresca": "f5",
    "refresca la pagina": "f5", "atras": "alt+izquierda", "regresa": "alt+izquierda",
    "vuelve atras": "alt+izquierda", "pagina anterior": "alt+izquierda", "adelante": "alt+derecha",
    "pagina siguiente": "alt+derecha", "regresa a la pagina anterior": "alt+izquierda",
    "vuelve a la pagina anterior": "alt+izquierda", "pantalla completa": "f11", "sal de pantalla completa": "f11",
    "da enter": "enter", "dale enter": "enter", "enter": "enter", "envialo con enter": "enter",
    "escape": "esc", "cambia de ventana": "alt+tab", "la otra ventana": "alt+tab",
    "abre el administrador de tareas": "ctrl+shift+esc", "administrador de tareas": "ctrl+shift+esc",
    "busca en la pagina": "ctrl+f", "acerca": "ctrl+mas", "aleja": "ctrl+menos", "imprime": "ctrl+p",
    "borra eso": "ctrl+z", "nueva ventana": "ctrl+n",
}
ATAJO = R(r"^(?P<q>" + "|".join(sorted((re.escape(a) for a in ATAJOS), key=len, reverse=True)) +
          r")(?: por favor| porfa)?$")
TECLA = R(r"^(?:presiona|pulsa|aprieta|oprime|dale a|toca)\s+(?P<q>.+)$")
DESPLAZAR = R(r"^(?P<dir>baja|bajale|desplazate hacia abajo|scroll hacia abajo|ve mas abajo|mas abajo|sigue bajando|"
              r"sube|subele|desplazate hacia arriba|scroll hacia arriba|ve mas arriba|mas arriba|sigue subiendo)"
              r"(?: (?:la pagina|un poco|un poquito|mas|harto|bastante))*$")
ESCRITORIO = R(r"^(?:muestra(?:me)?|ve al|vete al|ir al|ensename) (?:el )?escritorio$")
MINIMIZAR_GRUPO = R(r"^(?:minimiza|minimizar|minimices) (?:todo|todas|todos)"
                    r"(?: (?:las |los |mis )?(?:aplicaciones|apps|ventanas|programas|pestanas))?"
                    r"(?: (?:del navegador|abiertas|abiertos))?(?: tod[oa]s)*(?: (?:menos|excepto) (?:tu|jarvis|el asistente))?$")
CONTENEDOR_NAV = R(r"^(?:(?:una? |la )?(?:nueva |otra )?(?P<tipo>ventana|pestana)(?: nueva)?)"
                   r"(?: (?:de |en |del |en el |de mi )?(?:navegador ?)?(?P<nav>google chrome|chrome|google|microsoft edge|edge)?)?$")
BLOC = R(r"^(?:(?:un |el )?(?P<nuevo>nuevo |otro ))?(?:el |un )?(?:blo[cgk]|block|vlog) de notas(?: (?P<fin>nuevo))?$")
BUSCAR_O_MINIMIZAR = R(r"^(?:(?:siguiente|otra) prueba )?(?:busca(?:r)? o minimiza(?:r)?|"
                       r"minimiza(?:r)? o busca(?:r)?) (?:todas (?:las |mis )?)?pestanas\b")
VENTANA_ESTADO = R(r"^(?P<v>minimiza(?:r)?|minimices|maximiza(?:r)?|agranda|oculta|restaura)"
                   r"(?: (?:la ventana|esta ventana|esto|esta|eso))?(?: (?:de |del |el |la |a )?(?P<q>.+))?$")
VENTANA_FRENTE = R(r"^(?:cambia(?:te)? a|ve a|vete a|pasa(?:te)? a|vuelve a|regresa a|muestrame|trae(?:me)?|enfoca|"
                   r"selecciona(?: la ventana de)?)\s+(?:la ventana (?:de |del )?|el |la |los |las )?(?P<q>.+?)"
                   r"(?: al frente| adelante| en primer plano)?$|"
                   r"^pon (?:el |la )?(?P<q2>.+?) (?:al frente|adelante|en primer plano)$")
CERRAR_SESION = R(r"^(?:cierra(?:r)?|cerrar) (?:la |mi )?sesion(?: de windows)?$")
CERRAR_VENTANA = R(r"^(?:cierra(?:r)?|cerrar|cierrame)\s+(?:(?P<esta>la ventana|esta ventana|esto|esta aplicacion|"
                   r"esa ventana|eso|este programa)|(?:el |la |los |las |todo |todos los |todas las )?(?P<q>.+))$")
APAGAR_PANTALLA = R(r"^(?:apaga|apagar) (?:la )?(?:pantalla|monitor)$")
CORRECCION_WEB = R(r"^(?:(?:no|pero|mejor|sino|ahi no|eso no)\b.*\b(?:en el navegador|en la web|version web|"
                   r"en chrome|la web)\b|(?:abre(?:me)? )?(?:el )?(?P<s>whatsapp|spotify|teams|outlook|discord) web$|"
                   r"(?:en el navegador|en la web|la version web)$)")
IMAGEN = R(r"^(?:crea(?:me)?|genera(?:me)?|haz(?:me)?|dibuja(?:me)?|disena(?:me)?|quiero|necesito|hazme)"
           r"(?: una| un)? (?:imagen|foto|ilustracion|dibujo|logo|logotipo|poster|afiche|fondo de pantalla|wallpaper)"
           r"(?: (?:de|del|con|sobre|que (?:sea|tenga|muestre)|para|donde) (?P<q>.+))?$")
IA_SIN_PREGUNTA = R(rf"^{_PREG}(?: (?:a|al|en|con))?(?: la)? {_S}$")
IA_ABRE_PREGUNTA = R(rf"^abre(?: la)? {_S} y (?:preguntale|pidele|dile|consultale)(?: (?:algo|una cosa))?$")
REDACTAR = R(r"^(?:escribe|escribeme|redacta|redactame)(?: aqui)? (?:un|una) (?P<tipo>correo|mensaje|respuesta|"
             r"parrafo|texto|carta corta|email|introduccion|conclusion|post|publicacion|comentario)\b(?P<resto>.*)$")
ESCRIBIR_DIRECTO = R(r"^(?:escribe|escribeme|teclea|digita)(?: aqui)?(?:\s*[:,]\s*|\s+)(?P<q>.+)$")
NUEVA = R(r"\b(?:nueva|nuevo|otra|otro) (?:ventana|pestana|instancia)\b")

PERFIL = R(r"(?:^| )(?:con|en|usando|desde) (?:el |mi |la )?(?:perfil|cuenta|usuario) "
           r"(?:de |del |de la )?(?P<perfil>.+?)(?= y |$)")
ELEGIR_PERFIL = R(r"^(?:usa|utiliza|usar|utilizar|vamos a (?:usar|utilizar)|cambia (?:a|al))"
                  r"(?: el| mi)? perfil (?:de |del )?(?P<perfil>.+)$")
INCOGNITO = R(r"(?:^| )(?:en )?(?:modo )?(?:incognito|privado|inprivate)\b")

CANCELA = R(r"^(?:cancela|cancelar|cancelalo|olvidalo|dejalo|mejor no|no|para)$")
ESTADO_PC = R(
    r"\b(?:cuant[ao]s? (?:de )?(?:memoria|ram|bateria|cpu|procesador)"
    r"|(?:como|en cuanto|cuanto) (?:esta|va|anda|tengo) (?:la |el |de )?(?:memoria|ram|bateria|cpu|procesador)"
    r"|(?:uso|consumo) de (?:la |el )?(?:memoria|ram|cpu|procesador)"
    r"|(?:memoria|ram|cpu|procesador|bateria) (?:estoy usando|uso|tengo|queda|libre|disponible)"
    r"|por que (?:vas|va|estas|esta|anda|andas|respondes|responde) (?:tan |muy |demasiado )?lent[oa]"
    r"|(?:esta|estas|va|vas|anda|andas|respondes) (?:muy |tan |super |bien |demasiado )?lent[oa]"
    r"|que (?:me )?esta consumiendo|que (?:programa )?consume (?:mas|tanto)"
    r"|que (?:esta|me esta) usando (?:la |tanta )?(?:memoria|ram))")
SALIR = R(r"^(?:cierrate|cierra jarvis|cierra el asistente|sal de jarvis|salir de jarvis|termina jarvis|"
          r"cierra el programa|apagate (?:del todo|por completo|completamente|de verdad))$")
SALTA = R(r"^(?:salta|saltar|siguiente|no se|omite|omitir|paso|en blanco|vacio|ninguno|no aplica)$")
COMO_VEAS = R(r"\b(?:como (?:tu )?(?:veas|quieras|creas|prefieras)|lo que (?:tu )?(?:veas|quieras|creas)|"
              r"proponlas|propon(?:las)? tu|tu decide|decide tu|da igual|normal|asi esta bien|"
              r"lo que sea|cualquiera|ninguno|nada mas|sin detalles)\b")

# ------------------------------------------------------------------ manos libres / día
_ESTILO_VOZ = (r"(?:normal|a (?:tu |un )?ritmo normal|(?:un poco )?(?:mas )?"
               r"(?:despacio|lento|rapido|fluido|fluida|alegre|animado)|"
               r"(?:con )?(?:mas )?(?:energia|fluidez))")
_ATRIBUTO_VOZ = (r"(?:(?:tan |muy |demasiado |bastante |un poco )?(?:lent[oa]|despacio|rapid[oa]|"
                 r"triste|cansad[oa]|apagad[oa]|decaid[oa]|monoton[oa])|sin (?:energia|fluidez)|poco fluid[oa])")
_QUEJA_VOZ = rf"{_ATRIBUTO_VOZ}(?: (?:y )?(?:suenas )?{_ATRIBUTO_VOZ}){{0,2}}"
VELOCIDAD = R(
    r"^(?:de ahora en adelante )?(?:"
    r"(?P<guardar>(?:guarda|recuerda) (?:esta voz|este ritmo|este ajuste de voz|esta preferencia de voz)"
    r"|usa siempre esta voz)"
    r"|(?P<consulta>por que (?:hablas|suena tu voz) " + _QUEJA_VOZ + r")"
    r"|(?:habla(?:me)?|hablar(?:me)?|(?:me )?hables)(?: siempre)? (?P<pedido>" + _ESTILO_VOZ + r")"
    r"|(?P<corto>mas (?:despacio|lento|rapido|fluido|energia|fluidez))"
    r"|(?:hablas|estas hablando|suenas|te oyes|tu voz (?:es|esta|suena|se oye)) (?P<queja>" + _QUEJA_VOZ + r")"
    r")(?P<recordar> siempre| de ahora en adelante| y (?:guardalo|recuerdalo|guarda este ajuste))?$")
RESUMEN_DIA = R(r"\b(?:resumen (?:de la manana|del dia|de hoy|matutino|diario)|como (?:esta|viene|se ve) mi dia|"
                r"briefing|que me espera hoy|ponme al dia|que hay para hoy)\b")

# ------------------------------------------------------------------ calendario
CAL_LISTAR = R(r"\bque (?:tengo|hay) en (?:mi|el) (?:calendario|agenda)\b|\b(?:mi|el) calendario de (?:hoy|manana)\b|"
               r"\beventos de (?:hoy|manana)\b|\bque eventos tengo\b")
CAL_CREAR = R(r"^(?:agenda(?:me)?|agendar|anota en (?:mi|el) calendario|pon(?:me)? en (?:mi|el) calendario|"
              r"crea(?:me)? (?:un )?evento|crear (?:un )?evento|anade (?:a|en) (?:mi|el) calendario|"
              r"agrega (?:a|en) (?:mi|el) calendario|agrega (?:un )?evento|programa (?:una )?(?:reunion|cita))\b")
CAL_FRASES = R(r"(?:^| )(?:en|a) (?:mi|el|google) (?:calendario|agenda|calendar)\b")

# ------------------------------------------------------------------ WhatsApp
WA = r"(?:whatsapp|whats app|wasap|wasa|guasap|watsap|wassap|wsp|whatsap|wats)"
WA_VINCULAR = R(rf"^(?:conecta|vincula|configura|enlaza)(?:r)? (?:el |mi )?{WA}$")
WA_ENVIAR = R(rf"^(?:manda(?:le)?|mandar|envia(?:le)?|enviar|escribe(?:le)?|escribir)(?: un| una)? (?:{WA}|mensaje"
              rf"(?: de {WA}| por {WA}| en {WA})?)(?: a| para) (?P<a>.+?) (?:diciendo(?:le)?|que diga|que dice|"
              r"con el mensaje|con el texto|que|:) (?P<msg>.+)$")
WA_ENVIAR2 = R(rf"^(?:dile|decirle|avisale|escribele|mandale|enviale) a (?P<a>.+?) (?:por|en) {WA}(?: que)? (?P<msg>.+)$")
WA_SIN_MSG = R(rf"^(?:manda(?:le)?|mandar|envia(?:le)?|enviar|escribe(?:le)?|escribir)(?: un| una)? (?:{WA}|mensaje"
               rf"(?: de {WA}| por {WA}| en {WA})?)(?: a| para) (?P<a>.+)$")
WA_NUEVOS = R(rf"\b(?:mensajes|chats) (?:nuevos|sin leer|pendientes)\b|\b{WA}\b.*\b(?:nuevos|sin leer|pendientes)\b|"
              rf"^(?:lee(?:me)?|revisa) (?:mis )?(?:mensajes|{WA}s?)(?: de {WA})?$")
WA_LEER = R(rf"^(?:lee(?:me)?|que (?:dice|dijo|me dijo|escribio)|revisa|abre)(?: el| los| mis| la| lo)? (?:ultimos? )?"
            rf"(?:mensajes?|chat|conversacion)(?: de {WA})? (?:de|con|del|que me mando) (?P<a>.+?)(?: (?:en|de|por) {WA})?$")
WA_BUSCAR = R(rf"^busca(?:me)? (?:en )?{WA} (?P<q>.+)$|^busca(?:me)? (?P<q2>.+?) en {WA}$")

# ------------------------------------------------------------------ selección, resumen, archivos, plataformas
LEER_SEL = R(r"^(?:lee(?:me)?|leer) (?:esto|lo seleccionado|la seleccion|el texto seleccionado|lo que seleccione|"
             r"en voz alta)$")
TRADUCIR = R(r"^(?:traduce(?:me)?|traducir)(?: esto| lo seleccionado| la seleccion| el texto)?"
             r"(?: al? (?P<idioma>ingles|espanol|portugues|frances|italiano|aleman|chino|japones|quechua))?$")
EXPLICAR = R(r"^(?:explicame|explica|que significa|que quiere decir) (?:esto|lo seleccionado|la seleccion)$")
RESUMIR = R(r"^(?:resume(?:me)?|resumir|hazme un resumen|haz un resumen|dame un resumen|resumen|de que trata)"
            r"(?: de| del)?(?: este| esta| el| la| lo| mi)? ?(?P<obj>pdf|documento|archivo|word|presentacion|"
            r"texto|esto|lo que tengo abierto|que tengo abierto|abierto|lo seleccionado|seleccion)?(?: (?P<q>.+))?$")
_TIPO_ARCH = (r"(?P<tipo>archivos?|documentos?|pdfs?|words?|excels?|presentacion(?:es)?|power ?points?|fotos?|"
              r"imagen(?:es)?|videos?|audios?|carpetas?)")
BUSCAR_ARCHIVO = R(rf"^(?:busca(?:me)?|encuentra(?:me)?|ubica|localiza|donde (?:esta|estan|deje|guarde|puse))"
                   rf"(?: el| la| los| las| mi| mis| un| una)? {_TIPO_ARCH}(?: de| del| llamad[oa]s?| que se llama)? (?P<q>.+)$")
ABRIR_ARCHIVO = R(r"^(?:abre(?:me)?|abrir)(?: el| la| mi)? (?P<tipo>archivo|documento|pdf|excel|presentacion|"
                  r"power ?point|foto|imagen|video)(?: de| del| llamad[oa]| que se llama)? (?P<q>.+)$")
LOGIN = R(r"^(?:entra(?:r)?|ingresa(?:r)?|accede|inicia(?:r)? sesion|logueate|abre)(?: a| en| al)?"
          r"(?: mi cuenta de| mi cuenta en| mi| la plataforma de| la| el)? (?P<q>.+)$")
LISTO_RE = R(r"^(?:listo|ya|ya esta|hecho|termine|continua|sigue)$")

AYUDA_TEXTO = ("Puedo abrir aplicaciones, usar los controles de tu pantalla, crear documentos "
               "y revisar tus pendientes. Puedes preguntarme qué veo en la ventana activa.")


class Comandos:
    def __init__(self, cfg: dict, store, router, cerebro, avisar):
        self.revisar_encargo = None
        self.cfg = cfg
        self.store = store
        self.router = router
        self.avisar = avisar
        self.cerebro = cerebro
        self.tz = ZoneInfo(cfg["usuario"]["zona_horaria"])
        carpeta = cfg["documentos"]["carpeta"]
        self.carpeta = Path(carpeta) if carpeta else sistema.carpeta("jarvis")
        self.nav = Navegador(cfg["navegador"], cfg.get("sitios"))
        self.apps = sistema.Apps()
        self.recordatorios = Recordatorios(avisar)
        self.notas = Notas(self.carpeta)
        self.clima = Clima(cfg["clima"], cfg["usuario"]["zona_horaria"])
        self.docs = Documentos(self.carpeta, cerebro, cfg["documentos"]["modelo"], abrir=sistema.abrir_ruta)
        self.plantillas = Plantillas(self.carpeta / "Plantillas", self.carpeta)
        self.calendario = Calendario(cfg["usuario"]["zona_horaria"])
        self.navj = NavegadorJarvis(cfg["navegador_jarvis"]["canal"])
        self.whatsapp = WhatsApp(self.navj)
        self.sesiones = Sesiones(self.navj)
        self.plataformas = {normalizar(k): (k, v) for k, v in cfg["plataformas"].items()}
        self.indice = IndiceArchivos(carpetas_usuario(cfg["archivos"]["carpetas_extra"]))
        self.voz = None  # la versión de voz lo conecta (para "habla más despacio")
        self.dictando = False
        self._ultimo_tema = ""
        self.ultima_busqueda_google: tuple[str, float] | None = None
        self._ultimo_nivel = ("", 0.0)
        self._perfil: str | None = None
        self._incognito = False
        self.cancelado_ts = 0.0  # "ya no quiero...": las tareas en curso no deben preguntar después
        self.interpretado = False  # la orden vino del modelo (frase mal escuchada): confirmar lo delicado
        self.ultimo_destino: tuple[str, float] | None = None  # (lo último que abrió, cuándo)
        self.preferencias = cfg.get("preferencias", {})

        self.generales = [
            (SOLO, self.c_solo), (SALIR, self.c_salir), (CERRAR_SESION, self.c_cerrar_sesion),
            (CERRAR_PESTANAS, self.c_cerrar_pestanas), (CORRECCION_WEB, self.c_correccion_web),
            (ATAJO, self.c_atajo), (ESCRITORIO, self.c_escritorio), (DESPLAZAR, self.c_desplazar),
            (APAGAR_PANTALLA, self.c_apagar_pantalla), (IMAGEN, self.c_imagen),
            (IA_SIN_PREGUNTA, self.c_ia_sin_pregunta), (IA_ABRE_PREGUNTA, self.c_ia_sin_pregunta),
            (REDACTAR, self.c_redactar),
            (VELOCIDAD, self.c_velocidad), (ESTADO_PC, self.c_estado_pc),
            (DICTADO_INI, self.c_dictado), (AYUDA, self.c_ayuda),
            (RESUMEN_DIA, self.c_resumen_dia),
            (CAL_LISTAR, self.c_cal_listar), (CAL_CREAR, self.c_cal_crear),
            (WA_VINCULAR, self.c_wa_vincular), (WA_ENVIAR, self.c_wa_enviar), (WA_ENVIAR2, self.c_wa_enviar),
            (WA_SIN_MSG, self.c_wa_sin_msg), (WA_LEER, self.c_wa_leer), (WA_NUEVOS, self.c_wa_nuevos),
            (WA_BUSCAR, self.c_wa_buscar), (LEER_SEL, self.c_leer_sel), (TRADUCIR, self.c_traducir),
            (EXPLICAR, self.c_explicar), (RESUMIR, self.c_resumir),
            (ABRIR_ARCHIVO, self.c_abrir_archivo), (BUSCAR_ARCHIVO, self.c_buscar_archivo),
            (MEDIA, self.c_media),
            (CANCELAR_APAGADO, self.c_cancelar_apagado), (BLOQUEAR, self.c_bloquear),
            (APAGAR, self.c_apagar), (REINICIAR, self.c_reiniciar), (SUSPENDER, self.c_suspender),
            (CAPTURA, self.c_captura), (POMODORO, self.c_pomodoro), (LISTAR_REC, self.c_listar_rec),
            (CANCELAR_REC, self.c_cancelar_rec), (RECORDAR, self.c_recordar),
            (TEMPORIZADOR, self.c_temporizador), (NOTA_LEER, self.c_nota_leer),
            (NOTA_BORRAR, self.c_nota_borrar), (NOTA_ADD, self.c_nota),
            (PLANTILLA, self.c_plantilla), (DOCUMENTO, self.c_documento),
            (ABRIR_CORREO, self.c_abrir_correo), (LEER_CORREO, self.c_leer_correo),
            (CLIMA, self.c_clima), (LOGIN, self.c_login),
            (TECLA, self.c_tecla), (VENTANA_ESTADO, self.c_ventana_estado), (VENTANA_FRENTE, self.c_ventana_frente),
            (CERRAR_VENTANA, self.c_cerrar_ventana), (ESCRIBIR_DIRECTO, self.c_escribir),
        ]
        self.navegacion = [
            (NAV_Y, self.c_nav_y), (YT_ABRIR, self.c_yt_abrir), (YT_COMPUESTO, self.c_youtube),
            (YT_CONSULTA, self.c_youtube), (YT_DESTINO, self.c_youtube),
            (MUSICA, self.c_youtube), (GIA_1, self.c_google_ia), (GIA_2, self.c_google_ia),
            (IA_ABRIR, self.c_ia_abrir), (IA_ABRE_Y, self.c_ia), (IA_PREG, self.c_ia),
            (IA_FINAL, self.c_ia), (GOOGLE_COMPUESTO, self.c_google), (GOOGLE, self.c_google), (ABRIR, self.c_abrir),
        ]

    def iniciar(self) -> None:
        self.recordatorios.iniciar()
        self.apps.actualizar_en_fondo()
        self.indice.actualizar_en_fondo()
        try:
            self.plantillas.carpeta.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------ despacho
    def procesar(self, texto: str) -> Respuesta | None:
        self._crudo = texto  # con signos y mayúsculas, para escribir tal cual
        f = Frase(texto).sin_cortesia()
        if not f.txt:
            return None
        if BLOC.fullmatch(f.txt):
            return Respuesta("¿Quieres abrir el Bloc de notas o empezar uno nuevo?",
                             siguiente=self._elegir_bloc, espera_s=20)
        if BUSCAR_O_MINIMIZAR.match(f.txt):
            return Respuesta("¿Quieres buscar algo o minimizar las ventanas del navegador?",
                             siguiente=self._buscar_o_minimizar, espera_s=20)
        if m := ELEGIR_PERFIL.fullmatch(f.txt):
            return self._elegir_perfil(f.grupo(m, "perfil"))
        if (r := self.c_nivel(texto)) is not None:
            return r
        if re.match(r"^(?:no|nunca|tampoco)(?: te)? (?:pedi|dije|peis|pedimos|dijimos|queria)\b", f.txt):
            return Respuesta("Entendido.")
        abrir = re.match(r"^(?:me )?abras ", f.txt)
        if abrir and re.search(r"\b(?:quiero|quisiera|necesito) que (?:me )?abras\b", normalizar(texto)):
            f = Frase("abre " + f.original(abrir.end()))
        for patron, fn in self.generales:
            m = (patron.search(f.txt) if patron in (ESTADO_PC, CLIMA, LISTAR_REC, CAL_LISTAR)
                 else patron.match(f.txt))
            if m and (r := fn(f, m)) is not None:
                return r
        self._perfil, self._incognito = None, False
        m = PERFIL.search(f.txt)
        if m:
            self._perfil = f.grupo(m, "perfil")
            f = f.sin_span(*m.span())
        m = INCOGNITO.search(f.txt)
        if m:
            self._incognito = True
            f = f.sin_span(*m.span())
        r = self._navegacion(f)
        if r:
            return r
        # Un nombre aislado (incluso si se repite) no es una orden de apertura.
        for servicio, (nombre, _, _) in SERVICIOS.items():
            alias = {"google": _G, "youtube": YT}.get(servicio, IA_ALIAS.get(servicio))
            if alias and re.fullmatch(rf"(?P<sitio>{alias})(?: (?P=sitio))?", f.txt):
                return Respuesta(f"¿Qué quieres hacer en {nombre}?", espera_s=20,
                                 siguiente=lambda t, s=servicio: self._en_servicio(s, t))
        if re.fullmatch(r"(?:este |eh )?ab[a-z]{2,7} (?:de |el )?(?:google|word|youtube)", f.txt):
            return Respuesta("¿Qué quieres que abra?", siguiente=self._destino_dictado, espera_s=15)
        return None

    def _navegacion(self, f: Frase) -> Respuesta | None:
        if self._perfil and not self.nav.resolver_perfil(self._perfil):
            nombres = ", ".join(self.nav.nombres_perfiles()) or "ninguno"
            return Respuesta(f"No encontré el perfil {self._perfil}. Tus perfiles son: {nombres}.")
        for patron, fn in self.navegacion:
            m = patron.search(f.txt)
            if m and (r := fn(f, m)) is not None:
                return r
        return None

    def _elegir_perfil(self, nombre: str) -> Respuesta:
        p = self.nav.resolver_perfil(nombre)
        if p:
            self.nav.perfil_pred = p[0]
            return Respuesta(f"Usaré el perfil {p[1]} en las próximas aperturas del navegador.")
        perfiles = self.nav.nombres_perfiles()
        if not perfiles:
            return Respuesta("No encontré perfiles locales en el navegador configurado.")
        return Respuesta("Dime el nombre completo del perfil. Veo " + ", ".join(perfiles[:5]) + ".",
                         siguiente=lambda t: self._elegir_perfil(t) if self.nav.resolver_perfil(t) else None,
                         espera_s=20)

    def _elegir_bloc(self, texto: str) -> Respuesta | None:
        n = Frase(texto).sin_cortesia().txt
        n = re.sub(r"^si ", "", n)
        if n in {"uno nuevo", "nuevo", "otro", "abre uno nuevo", "un nuevo bloc de notas"}:
            return Respuesta(ejecutar=lambda: ventanas.abrir_bloc(True))
        if n in {"abrelo", "abrirlo", "abrir", "el abierto", "el que esta abierto", "traelo al frente"}:
            return Respuesta(ejecutar=lambda: ventanas.abrir_bloc(False))
        if n == "si":
            return Respuesta("¿El que está abierto o uno nuevo?", siguiente=self._elegir_bloc, espera_s=15)
        if es_no(n) or n.startswith("todavia no"):
            return Respuesta("De acuerdo.")
        return None

    def _buscar_o_minimizar(self, texto: str) -> Respuesta | None:
        n = Frase(texto).sin_cortesia().txt
        if n in {"minimiza", "minimizar", "minimizalas", "minimizarlas", "minimiza las pestanas",
                 "minimiza todas las pestanas", "minimiza las ventanas del navegador"}:
            return Respuesta(ejecutar=lambda: ventanas.minimizar_grupo(True))
        if n in {"busca", "buscar", "una busqueda"}:
            return Respuesta("¿Qué quieres buscar?", siguiente=self._tema_busqueda, espera_s=20)
        return None

    def _tema_busqueda(self, texto: str) -> Respuesta | None:
        f = Frase(texto).sin_cortesia()
        if not f.txt or es_no(f.txt) or re.match(r"^(?:abre|busca|minimiza|escribe|cierra|cancela)\b", f.txt):
            return None
        return self.procesar(f"busca en Google {texto}")

    def _en_servicio(self, servicio: str, texto: str) -> Respuesta | None:
        f = Frase(texto).sin_cortesia()
        nombre = SERVICIOS[servicio][0]
        if f.txt in ("abrelo", "abrela", "abrirlo", "entrar", "entra", "abrir"):
            return self._navegacion(Frase(f"abre {nombre}"))
        m = re.fullmatch(r"(?:busca(?:me)?|buscar|consulta|pregunta(?:le)?|preguntar) (?P<q>.+)", f.txt)
        if not m:
            return None
        # Una petición con otro destino o con un archivo explícito tiene su propia ruta.
        destinos = (BUSCAR_ARCHIVO, WA_BUSCAR, IA_PREG, IA_FINAL, GIA_1, GIA_2, YT_CONSULTA, YT_DESTINO)
        if (any(patron.match(f.txt) for patron in destinos)
                or re.search(rf"\b(?:aqui|esta (?:pagina|pantalla|ventana)|(?:en|por|a) {_G})\b", f.txt)):
            return None
        q = f.grupo(m, "q")
        if servicio == "google":
            return self._navegacion(Frase(f"busca en Google {q}"))
        if servicio == "youtube":
            return self._navegacion(Frase(f"busca en YouTube {q}"))
        return self._navegacion(Frase(f"consulta a {nombre} {q}"))

    # ------------------------------------------------------------ utilidades
    def _suf(self) -> str:
        partes = []
        if self._perfil:
            p = self.nav.resolver_perfil(self._perfil)
            partes.append(f" con el perfil {p[1] if p else self._perfil}")
        if self._incognito:
            partes.append(" en modo incógnito")
        return "".join(partes)

    def _abrir_url(self, url: str | None, titulo: str = ""):
        """Abre en tu navegador y trae la ventana al frente (Windows a veces la deja atrás)."""
        perfil, incog = self._perfil, self._incognito
        def abrir():
            solicitud = None
            def lanzar():
                nonlocal solicitud
                solicitud = self.nav.abrir(url, perfil, incog)
            al_frente = ventanas.abrir_y_enfocar(lanzar, ventanas.NAVEGADORES, titulo=titulo, espera=6)
            if isinstance(solicitud, Apertura):
                return solicitud.identificar()
            return ResultadoAccion("Se envió la dirección al navegador.") if url else al_frente
        return abrir

    def _abrir_app(self, app: dict, nombre: str = ""):
        nombre = normalizar(nombre or app.get("Name", ""))
        procesos = ventanas.PROGRAMAS.get(nombre, ())
        return lambda: ventanas.abrir_y_enfocar(lambda: self.apps.abrir(app), procesos,
                                                titulo="" if procesos else nombre, espera=4)

    @staticmethod
    def _mostrar_programa(nombre: str, operacion, confirmar: str) -> Respuesta:
        def ejecutar():
            if operacion():
                return f"{nombre} está al frente."
            return f"No pude comprobar que {nombre} quedara al frente."
        return Respuesta(ejecutar=ejecutar, confirmar_voz=confirmar)

    @staticmethod
    def _confirmar(pregunta: str, si: Respuesta, no_texto: str = "Bien, no hago nada.") -> Respuesta:
        def manejar(t: str) -> Respuesta | None:
            if confirma_accion(t):
                return si
            return Respuesta(no_texto) if es_no(t) else None
        return Respuesta(pregunta, siguiente=manejar, espera_s=20)

    # ------------------------------------------------------------ estado del equipo / salir
    def c_estado_pc(self, f, m):
        t = f.txt
        if "bateria" in t:
            pct, enchufada = sistema.bateria()
            if pct is None:
                return Respuesta("No encuentro una batería; parece que estás conectado a la corriente.")
            return Respuesta(f"La batería está al {pct} %, " + ("cargando." if enchufada else "sin cargador."))
        partes = []
        if re.search(r"\b(?:cpu|procesador)\b", t):
            return Respuesta(f"El procesador está al {sistema.cpu_pct()} %.")
        usado, total, pct = sistema.memoria()
        partes.append(f"Estás usando el {pct} % de la memoria: {sistema.gb_hablado(usado)} de "
                      f"{sistema.gb_hablado(total)}.")
        try:
            top = [(n, mb) for n, mb in sistema.procesos_memoria(5) if mb >= 150][:3]
        except Exception:
            top = []
        if top:
            lista = [f"{n} con {sistema.gb_hablado(mb / 1024)}" for n, mb in top]
            partes.append("Lo que más ocupa: " + (", ".join(lista[:-1]) + " y " + lista[-1] if len(lista) > 1
                                                   else lista[0]) + ".")
        if pct >= 85:
            partes.append("Con la memoria tan llena, Windows usa el disco y todo va más lento, yo incluido. "
                          "Si cierras algo de eso, respondo más rápido.")
        elif "lent" in t:
            partes.append(f"La memoria está bien; el procesador va al {sistema.cpu_pct()} %.")
        return Respuesta(" ".join(partes))

    def c_salir(self, f, m):
        return Respuesta("Me cierro. Vuelvo cuando desbloquees la sesión o abras iniciar.bat.",
                         despues=lambda: os._exit(0))

    def c_cerrar_pestanas(self, f, m):
        if "pestana" not in f.txt:
            return Respuesta("¿Qué quieres cerrar?")
        if "guard" in f.txt:  # "ciérrala y si hay que guardar, guarda": no sé qué cambios hay en la página
            return Respuesta("No puedo comprobar qué cambios hay en esas pestañas. Guarda lo que necesites "
                             "y luego dime: cierra la pestaña.")
        objetivo = ventanas.activa()
        if not objetivo or objetivo.proceso not in ventanas.NAVEGADORES:
            return Respuesta("Pon al frente la ventana del navegador cuyas pestañas quieres cerrar.")
        todas = bool(re.search(r"\b(?:todas|las pestanas)\b", f.txt))

        def cerrar():
            actual = ventanas.activa()
            if not actual or actual.hwnd != objetivo.hwnd or actual.proceso != objetivo.proceso:
                return "Cambió la ventana activa. Vuelve a pedir el cierre en el navegador."
            if not ventanas.teclas("ctrl+shift+w" if todas else "ctrl+w"):
                return "Windows no recibió el atajo de cierre."
            return "Envié la orden de cierre al navegador."

        accion = Respuesta(ejecutar=cerrar)
        return self._confirmar("¿Cierro todas las pestañas de esta ventana?", accion) if todas else accion

    # ------------------------------------------------------------ control de la computadora
    def c_atajo(self, f, m):
        combo = ATAJOS[m.group("q")]
        return Respuesta("", accion=lambda: ventanas.teclas(combo))

    def c_tecla(self, f, m):
        codigos = ventanas.interpretar_teclas(m.group("q"))
        if not codigos:
            return None  # "pulsa el botón Enviar" lo resuelve el control de pantalla
        return Respuesta("", accion=lambda: ventanas.teclas(codigos))

    def c_desplazar(self, f, m):
        arriba = m.group("dir").startswith(("sube", "subele", "desplazate hacia arriba", "scroll hacia arriba",
                                            "ve mas arriba", "mas arriba", "sigue subiendo"))
        pasos = 10 if re.search(r"\b(?:mas|harto|bastante)\b", f.txt) else 5
        return Respuesta("", accion=lambda: ventanas.rueda(pasos if arriba else -pasos))

    def c_escritorio(self, f, m):
        return Respuesta("", accion=lambda: ventanas.teclas("win+d"))

    def c_apagar_pantalla(self, f, m):
        return Respuesta("Apago la pantalla. Mueve el mouse para volver.", despues=ventanas.apagar_pantalla)

    def c_cerrar_sesion(self, f, m):
        return self._confirmar("¿Cierro tu sesión de Windows? Se cerrarán tus programas.",
                               Respuesta("Cerrando sesión.", despues=ventanas.cerrar_sesion, dormir=True))

    def _ventana_de(self, q: str | None) -> ventanas.Ventana | None:
        """La ventana de la que hablas; sin nombre (o "esto"), la que tienes al frente."""
        qn = normalizar(q or "")
        if not qn or qn in ("esto", "esta", "eso", "la ventana", "esta ventana", "esa ventana", "la"):
            return ventanas.activa()
        return ventanas.buscar(qn)

    def c_ventana_estado(self, f, m):
        verbo = "minimiza" if m.group("v") == "minimices" else m.group("v")
        if MINIMIZAR_GRUPO.fullmatch(f.txt):
            solo_navegador = "pestanas" in f.txt or "del navegador" in f.txt
            return Respuesta(ejecutar=lambda: ventanas.minimizar_grupo(solo_navegador))
        if verbo.startswith("minimiz") and re.search(r"\btod[ao]s?\b", f.txt):
            return Respuesta("¿Quieres minimizar las ventanas del navegador o todas las aplicaciones?",
                             siguiente=self._aclarar_minimizar, espera_s=18)
        q = f.grupo(m, "q")
        v = self._ventana_de(q)
        if not v:
            return Respuesta(f"No veo abierto {q}.") if q else None
        if verbo.startswith(("minimiza", "oculta")):
            return Respuesta("", accion=lambda: ventanas.minimizar(v.hwnd))
        if verbo == "restaura":
            return Respuesta("", accion=lambda: ventanas.enfocar(v.hwnd))
        return Respuesta("", accion=lambda: ventanas.maximizar(v.hwnd))

    @staticmethod
    def _aclarar_minimizar(t: str) -> Respuesta | None:
        n = normalizar(t)
        if n in {"las del navegador", "navegador", "el navegador", "las pestanas", "las ventanas del navegador"}:
            return Respuesta(ejecutar=lambda: ventanas.minimizar_grupo(True))
        if n in {"todo", "todas", "todas las aplicaciones", "las aplicaciones", "todas las ventanas"}:
            return Respuesta(ejecutar=ventanas.minimizar_grupo)
        return None

    def c_ventana_frente(self, f, m):
        q = f.grupo(m, "q") or f.grupo(m, "q2")
        v = ventanas.buscar(q) if q else None
        if not v:
            return None  # no está abierto: que lo resuelvan "abre…" o el navegador
        return Respuesta("", accion=lambda: ventanas.enfocar(v.hwnd))

    def c_cerrar_ventana(self, f, m):
        q = f.grupo(m, "q")
        if q and re.search(r"\b(?:sesion|pestanas?|jarvis|asistente|dictado|el apagado|el reinicio)\b", normalizar(q)):
            return None
        if q and normalizar(q) in ("todo", "todos", "todas las ventanas", "todos los programas"):
            return Respuesta("Eso cerraría todo lo que tienes abierto. Dime qué programa cierro.")
        v = self._ventana_de(None if m.group("esta") else q)
        if not v:
            return Respuesta(f"No veo abierto {q}.") if q else Respuesta("No encuentro la ventana activa.")
        if v.proceso in ("explorer",) and not q:
            return Respuesta("Esa es la barra de Windows; no la cierro.")
        if v.proceso in ("windowsterminal", "conhost", "openconsole", "cmd") and re.search(
                r"cmd\.exe|iniciar|jarvis|python", v.titulo, re.I):
            return Respuesta("Esa es mi consola: si la cierras, me apago. Si quieres cerrarme, di: ciérrate.")
        nombre = q or "la ventana"
        return Respuesta(f"Cierro {nombre}." if q else "", accion=lambda: ventanas.cerrar(v.hwnd))

    def c_escribir(self, f, m):
        resto = f.grupo(m, "q")
        if re.search(r"\ben (?:el |la )?(?:campo|casilla|cuadro)\b", normalizar(resto)):
            return None  # "escribe X en el campo Y": lo hace el control de pantalla
        crudo = re.match(r"^\s*[¡¿]?(?:escr[ií]be(?:me)?|teclea|digita)(?:\s+aqu[ií])?(?:\s*[:,]\s*|\s+)(?P<t>.+)$",
                         getattr(self, "_crudo", ""), re.I | re.S)
        texto = (crudo.group("t") if crudo else f.original(m.start("q"))).strip().strip("«»\"“”")
        if not texto:
            return None
        return Respuesta("", accion=lambda: sistema.escribir(texto))

    def c_redactar(self, f, m):
        """'Escribe un correo para mi profe pidiendo permiso': lo redacta y lo escribe donde está el cursor."""
        if not self.cerebro:
            return None
        pedido = f.original()
        destino = ventanas.activa()

        def trabajo():
            texto = self.cerebro.redactar(pedido)
            if not texto:
                self.avisar("No pude redactarlo ahora.")
                return
            if destino and ventanas.activa() and ventanas.activa().hwnd != destino.hwnd:
                ventanas.enfocar(destino.hwnd)
            sistema.escribir(texto)
            self.avisar("Listo, lo escribí donde estaba el cursor. Si no te gusta, di: deshaz.")

        return Respuesta("Lo redacto y lo escribo donde está el cursor.", accion=self._fondo(trabajo, "redactar"))

    def _revisar_ia(self, nombre: str, pedido: str) -> Respuesta:
        if self.revisar_encargo is None:
            return Respuesta("Abre Configuración de Jarvis y entra en Encargos para revisar ese envío.")
        def preparar():
            self.revisar_encargo(nombre, pedido)
            return f"El encargo para {nombre} está listo para revisar."
        return Respuesta(ejecutar=preparar)

    def c_imagen(self, f, m):
        q = f.grupo(m, "q")
        if q:
            pedido = f"Crea una imagen: {f.original(m.start('q'))}"
            return self._revisar_ia("ChatGPT", pedido)

        def descrita(t: str) -> Respuesta | None:
            if es_no(t) or CANCELA.match(normalizar(t)):
                return Respuesta("Bien, lo dejo.")
            desc = re.sub(r"^(?:que sea |quiero (?:que sea )?|una imagen (?:de |con )?|de |un |una )", "",
                          t.strip(), flags=re.I)
            return self._revisar_ia("ChatGPT", f"Crea una imagen: {desc}")

        return Respuesta("¿Cómo quieres la imagen?", siguiente=descrita, espera_s=90)

    def c_ia_sin_pregunta(self, f, m):
        clave = self._servicio(m.group("s"))
        if not clave:
            return None
        nombre, con_q, inicio = SERVICIOS[clave]

        def pregunta(t: str) -> Respuesta | None:
            if es_no(t) or CANCELA.match(normalizar(t)):
                return Respuesta("Bien.")
            if clave in {"chatgpt", "claude"}:
                return self._revisar_ia(nombre, t.strip())
            return self._navegacion(Frase(f"pregúntale a {nombre} {t}"))

        if clave in {"chatgpt", "claude"}:
            return Respuesta(f"¿Qué quieres preguntarle a {nombre}?", siguiente=pregunta, espera_s=90)
        return Respuesta(f"Voy abriendo {nombre}. ¿Qué le pregunto?", accion=self._abrir_url(inicio, titulo=nombre),
                         siguiente=pregunta, espera_s=90)

    def c_correccion_web(self, f, m):
        """'No la aplicación, en el navegador' después de abrir algo: abre la versión web."""
        s = m.group("s")
        if not s and self.ultimo_destino and time.time() - self.ultimo_destino[1] < 180:
            s = self.ultimo_destino[0]
        if not s:
            s = next((k for k in APP_O_WEB if k in f.txt), None)
        if not s or s not in APP_O_WEB:
            return None
        nombre_app, web = APP_O_WEB[s]
        self.ultimo_destino = (s, time.time())
        return Respuesta(f"Abriendo {nombre_app} Web en el navegador.", accion=self._abrir_url(web, titulo=nombre_app))

    # ------------------------------------------------------------ modo silencio / dictado / ayuda
    def c_solo(self, f, m):
        return Respuesta("Perfecto, trabaja tranquilo. Si me necesitas, solo di Jarvis.", dormir=True)

    def c_dictado(self, f, m):
        self.dictando = True
        return Respuesta("Listo, dicta. Escribo donde esté el cursor. Para terminar, di: fin del dictado.")

    def dictar(self, texto: str) -> Respuesta:
        f = Frase(texto)
        if DICTADO_FIN.search(f.txt):
            self.dictando = False
            return Respuesta("Fin del dictado.")
        if DICTADO_DESHACER.match(f.txt):
            sistema.combinacion("ctrl", "z")
            return Respuesta()
        m = DICTADO_ENTER.search(f.txt)
        contenido = f.sin_span(*m.span()).texto if m else f.texto
        if contenido.strip():
            sistema.escribir(contenido.strip() + ("" if m else " "))
        if m:
            sistema.tecla("enter")
        return Respuesta()

    def c_ayuda(self, f, m):
        return Respuesta(AYUDA_TEXTO)

    # ------------------------------------------------------------ volumen y multimedia
    def c_nivel(self, texto: str) -> Respuesta | None:
        ultimo, instante = self._ultimo_nivel
        ahora = time.monotonic()
        ajuste = niveles.interpretar(texto, ultimo if ahora - instante < 60 else "")
        if ajuste is None:
            self._ultimo_nivel = ("", 0.0)
            return None
        self._ultimo_nivel = (ajuste.control, ahora)
        if ajuste.modo == "aclarar" or (self.interpretado and ajuste.modo != "leer"):
            def porcentaje(t: str) -> Respuesta | None:
                if es_no(t):
                    return Respuesta("De acuerdo.")
                cantidad = re.sub(r"^\s*(?:al?|en)\s+", "", t, flags=re.I)
                pedido = niveles.interpretar(f"pon el {ajuste.control} al {cantidad}")
                if pedido and pedido.modo == "fijar":
                    return Respuesta(ejecutar=lambda: niveles.ejecutar(pedido))
                return None
            return Respuesta(f"¿A qué porcentaje quieres el {ajuste.control}, entre cero y cien?",
                             siguiente=porcentaje, espera_s=18)
        return Respuesta(ejecutar=lambda: niveles.ejecutar(ajuste))

    def c_media(self, f, m):
        accion = "pausa" if m.group("pausa") else "siguiente" if m.group("sig") else "anterior"
        return Respuesta("", accion=lambda: sistema.multimedia(accion))

    # ------------------------------------------------------------ energía y captura
    def c_bloquear(self, f, m):
        return Respuesta("Bloqueando la computadora.", despues=sistema.bloquear, dormir=True)

    def c_apagar(self, f, m):
        si = Respuesta("Apago en un minuto. Si cambias de idea, di: cancela el apagado.",
                       accion=lambda: sistema.apagar())
        return self._confirmar("¿Seguro que apago la computadora?", si)

    def c_reiniciar(self, f, m):
        si = Respuesta("Reinicio en un minuto. Si cambias de idea, di: cancela el reinicio.",
                       accion=lambda: sistema.apagar(reiniciar=True))
        return self._confirmar("¿Seguro que reinicio la computadora?", si)

    def c_suspender(self, f, m):
        hibernar = "hiberna" in f.txt
        si = Respuesta("Hibernando." if hibernar else "Suspendo la computadora.",
                       despues=lambda: ventanas.suspender(hibernar), dormir=True)
        if self.interpretado:
            return self._confirmar("¿Suspendo la computadora?", si)
        return si

    def c_cancelar_apagado(self, f, m):
        return Respuesta("Cancelado.", accion=sistema.cancelar_apagado)

    def c_captura(self, f, m):
        sistema.captura()
        return Respuesta("Listo, guardé la captura en Imágenes, carpeta Capturas Jarvis.")

    # ------------------------------------------------------------ recordatorios
    def c_pomodoro(self, f, m):
        d = parse_duracion(f.txt)
        seg = d[0] if d else 25 * 60
        self.recordatorios.agregar(time.time() + seg, "", "pomodoro")
        return Respuesta(f"Listo, {duracion_hablada(seg)} de concentración. Me quedo en silencio "
                         "y te aviso al terminar.", dormir=True)

    def c_listar_rec(self, f, m):
        pend = self.recordatorios.pendientes()
        if not pend:
            return Respuesta("No tienes recordatorios pendientes.")
        ahora = datetime.now(self.tz)
        partes = []
        for r in pend[:4]:
            cuando = cuando_hablado(datetime.fromtimestamp(r.ts, self.tz), ahora)
            nombre = r.texto or {"temporizador": "un temporizador", "alarma": "una alarma",
                                 "pomodoro": "fin del pomodoro", "descanso": "fin del descanso"}.get(r.tipo, "un aviso")
            partes.append(f"{nombre[:1].upper() + nombre[1:]}, {cuando}.")
        return Respuesta(f"Tienes {len(pend)}: " + " ".join(partes))

    def c_cancelar_rec(self, f, m):
        n = len(self.recordatorios.pendientes())
        if not n:
            return Respuesta("No tienes recordatorios pendientes.")
        si = Respuesta(f"Listo, cancelé {n}.", accion=self.recordatorios.cancelar_todos)
        return self._confirmar(f"Tienes {n}. ¿Los cancelo todos?", si)

    def c_recordar(self, f, m):
        return self._programar(f.sin_span(0, m.end()), "recordatorio")

    def c_temporizador(self, f, m):
        tipo = "alarma" if m.group("tipo") == "alarma" else "temporizador"
        return self._programar(f.sin_span(0, m.end()), tipo)

    def _programar(self, resto: Frase, tipo: str, mensaje: str | None = None) -> Respuesta:
        ahora = datetime.now(self.tz)
        dur = parse_duracion(resto.txt)
        hora = None if dur else parse_hora(resto.txt, ahora)
        if dur:
            ts, span, cuando = time.time() + dur[0], dur[1], f"en {duracion_hablada(dur[0])}"
        elif hora:
            ts, span, cuando = hora[0].timestamp(), hora[1], cuando_hablado(hora[0], ahora)
        else:
            msg = mensaje if mensaje is not None else quitar_conectores(resto.original())
            pregunta = {"temporizador": "¿De cuánto tiempo?", "alarma": "¿Para qué hora?"}.get(
                tipo, "¿Cuándo te lo recuerdo?")
            return Respuesta(pregunta, siguiente=lambda t: self._programar(Frase(t), tipo, msg), espera_s=30)
        msg = mensaje if mensaje is not None else quitar_conectores(resto.sin_span(*span).original())
        if tipo == "recordatorio" and not msg:
            return Respuesta("¿Qué te recuerdo?",
                             siguiente=lambda t: self._guardar(ts, cuando, quitar_conectores(t), tipo),
                             espera_s=30)
        return self._guardar(ts, cuando, msg, tipo)

    def _guardar(self, ts: float, cuando: str, msg: str, tipo: str) -> Respuesta:
        self.recordatorios.agregar(ts, msg, tipo)
        if tipo == "temporizador":
            return Respuesta(f"Temporizador listo, te aviso {cuando}.")
        if tipo == "alarma":
            return Respuesta(f"Alarma puesta, sonará {cuando}.")
        return Respuesta(f"Listo, te lo recuerdo {cuando}.")

    # ------------------------------------------------------------ notas
    def c_nota(self, f, m):
        texto = f.grupo(m, "q")
        if not texto:
            return None
        # Una segunda frase puede ser parte del dictado o una orden independiente.
        dos = re.fullmatch(r"(.+?[.!?])\s+(?:este[, ]+|ahora[, ]+)?(?:abre|abra)\s+(.+)",
                           texto, flags=re.I | re.S)
        if dos:
            nota, destino = dos.groups()

            def decidir(t):
                n = normalizar(t)
                if n in {"solo la nota", "todo como una nota"}:
                    self.notas.agregar(texto if n.startswith("todo") else nota)
                    return Respuesta("Guardé la nota.")
                if not (confirma_accion(t) or n in {"ambas", "las dos", "las dos cosas"}):
                    return Respuesta("De acuerdo.") if es_no(t) else None
                apertura = self._abrir_destino(destino)
                self.notas.agregar(nota)
                if apertura is None:
                    return Respuesta("Guardé la nota. ¿Qué aplicación quieres abrir?",
                                     siguiente=self._destino_dictado, espera_s=15)
                apertura.confirmar_voz = ""
                if apertura.ejecutar:
                    abrir = apertura.ejecutar
                    apertura.ejecutar = lambda: "Guardé la nota. " + abrir()
                else:
                    apertura.texto = f"Guardé la nota. {apertura.texto}".strip()
                return apertura

            return Respuesta(f"¿Guardo la primera frase como nota y abro {destino}?",
                             siguiente=decidir, espera_s=20)
        self.notas.agregar(texto)
        return Respuesta("Anotado.")

    def c_nota_leer(self, f, m):
        notas = self.notas.ultimas(5)
        if not notas:
            return Respuesta("No tienes notas.")
        return Respuesta("Tus últimas notas: " + ". ".join(n.rstrip(".") for n in notas) + ".")

    def c_nota_borrar(self, f, m):
        si = Respuesta("Listo, las borré. Dejé una copia por si acaso.", accion=self.notas.borrar)
        return self._confirmar("¿Borro todas tus notas?", si)

    # ------------------------------------------------------------ plantillas
    def c_plantilla(self, f, m):
        nombre = f.grupo(m, "q")
        archivos = self.plantillas.listar()
        if not archivos:
            return Respuesta("Aún no hay plantillas. Guarda tu Word, Excel o PDF en la carpeta Plantillas.")
        p = self.plantillas.buscar(nombre) if nombre else (archivos[0] if len(archivos) == 1 else None)
        if not p:
            lista = ", ".join(a.stem for a in archivos[:6])
            return Respuesta(f"¿Cuál plantilla? Tienes: {lista}.", siguiente=self._plantilla_elegir, espera_s=40)
        return self._plantilla_empezar(p)

    def _plantilla_elegir(self, t: str) -> Respuesta:
        if CANCELA.match(normalizar(t)):
            return Respuesta("Bien, lo dejo.")
        p = self.plantillas.buscar(t)
        return self._plantilla_empezar(p) if p else Respuesta("No encontré esa plantilla.")

    def _plantilla_empezar(self, p: Path) -> Respuesta:
        campos = self.plantillas.campos(p)
        if not campos:
            return Respuesta(f"La plantilla {p.stem} no tiene campos. Escríbelos entre dobles llaves.")
        estado = {"p": p, "campos": campos, "valores": {}, "i": 0}
        return Respuesta(f"Vamos con {p.stem}. Son {len(campos)} campos; si alguno no aplica, di: salta. "
                         f"Primero: {self.plantillas.etiqueta(campos[0])}.",
                         siguiente=lambda t: self._plantilla_valor(estado, t), espera_s=90)

    def _plantilla_valor(self, e: dict, t: str) -> Respuesta:
        n = normalizar(t)
        if re.match(r"^(?:cancela|cancelar|olvidalo|dejalo)$", n):
            return Respuesta("Bien, cancelé el llenado.")
        campo = e["campos"][e["i"]]
        e["valores"][campo] = "" if SALTA.match(n) else limpiar_valor(campo, t)
        e["i"] += 1
        if e["i"] < len(e["campos"]):
            return Respuesta(f"{self.plantillas.etiqueta(e['campos'][e['i']])}.",
                             siguiente=lambda t2: self._plantilla_valor(e, t2), espera_s=90)
        ruta = self.plantillas.llenar(e["p"], e["valores"])
        return Respuesta(f"Listo, guardé {e['p'].stem} lleno en la carpeta Jarvis y te lo abrí.",
                         accion=lambda: sistema.abrir_ruta(ruta))

    # ------------------------------------------------------------ documentos
    def c_documento(self, f, m):
        clase, formato, tema = f.grupo(m, "clase"), f.grupo(m, "formato"), f.grupo(m, "tema")
        tipo = detectar_tipo(f.txt, clase, formato)
        genericos = {"documento", "doc", "word", "pdf", "excel", "power point", "powerpoint", "ppt",
                     "slides", "diapositivas", "hoja de calculo", "hoja de excel", "planilla", "presentacion"}
        if tema and re.fullmatch(r"(?:el |lo )?mismo(?: tema)?|eso|lo anterior|el tema anterior",
                                 normalizar(tema)) and self._ultimo_tema:
            tema = self._ultimo_tema
        p = Pedido(tipo, "" if normalizar(clase) in genericos else clase, tema)
        if not tema:
            return Respuesta("Claro. ¿Sobre qué tema?", siguiente=lambda t: self._doc_tema(p, t), espera_s=40)
        return self._doc_preguntar(p)

    def _doc_tema(self, p: Pedido, t: str) -> Respuesta:
        if CANCELA.match(normalizar(t)):
            return Respuesta("Bien, lo dejo.")
        p.tema = quitar_conectores(Frase(t).original(), ("sobre", "de", "acerca"))
        return self._doc_preguntar(p)

    def _doc_preguntar(self, p: Pedido) -> Respuesta:
        pregunta = {
            "pptx": "¿Cuántas diapositivas y para qué es? O di: como tú veas.",
            "xlsx": "¿Qué columnas necesitas? O di: propónlas tú.",
        }.get(p.tipo, "¿Para qué es y qué tan largo? Por ejemplo: un informe de dos páginas para la "
                      "maestría. O di: como tú veas.")
        return Respuesta(pregunta, siguiente=lambda t: self._doc_crear(p, t), espera_s=60)

    def _doc_crear(self, p: Pedido, t: str) -> Respuesta:
        n = normalizar(t)
        if CANCELA.match(n):
            return Respuesta("Bien, lo dejo.")
        p.detalles = "" if COMO_VEAS.search(n) else Frase(t).original()
        self._ultimo_tema = p.tema
        self.docs.crear_en_fondo(p, self.avisar)
        return Respuesta(f"Perfecto, preparo {p.descripcion()}. Te aviso cuando esté listo; "
                         "puede tardar uno o dos minutos.")

    # ------------------------------------------------------------ correos y clima
    def _correos(self):
        return sorted(self.store.items(fuente="gmail"), key=lambda c: c.vence_ts or 0, reverse=True)

    def _buscar_correo(self, quien: str):
        q = normalizar(quien)
        correos = self._correos()
        elegidos = [c for c in correos if q in normalizar(c.detalle) or q in normalizar(c.titulo)]
        if not elegidos:
            palabras = [w for w in q.split() if len(w) > 2 and w not in ("del", "los", "las")]
            elegidos = [c for c in correos if any(w in normalizar(c.detalle) for w in palabras)]
        return elegidos

    @staticmethod
    def _correo_texto(c) -> str:
        t = f"Correo de {c.detalle}: {c.titulo}."
        return t + (f" Dice: {c.nota}" if c.nota else "")

    def c_leer_correo(self, f, m):
        if self.store.ultimo_sync("gmail") is None:
            return Respuesta("Todavía no he podido revisar tu Gmail.")
        correos = self._correos()
        if not correos:
            return Respuesta("No tienes correos importantes sin leer.")
        quien = f.grupo(m, "q")
        if quien:
            elegidos = self._buscar_correo(quien)
            return Respuesta(self._correo_texto(elegidos[0]) if elegidos
                             else f"No veo correos sin leer de {quien}.")
        if re.search(r"\bcorreos\b", f.txt) and not re.search(r"\bultimo\b", f.txt):
            partes = " ".join(f"De {c.detalle}: {c.titulo}." for c in correos[:3])
            return Respuesta(f"Tienes {len(correos)} sin leer. {partes}")
        return Respuesta(self._correo_texto(correos[0]))

    def c_abrir_correo(self, f, m):
        quien = f.grupo(m, "q")
        elegidos = self._buscar_correo(quien)
        if not elegidos:
            return Respuesta(f"No encontré correo sin leer de {quien}.")
        url = elegidos[0].url
        return Respuesta(f"Abriendo el correo de {elegidos[0].detalle}.", accion=lambda: self.nav.abrir(url),
                         confirmar_voz="¿Abro ese correo?")

    def c_clima(self, f, m):
        if re.search(r"^(?:busca|buscame|buscar|googlea|pregunta|preguntale|investiga)\b|\bgoogle\b", f.txt):
            return None  # "busca en Google el clima de Lima" es una búsqueda, no el clima local
        dia = 2 if "pasado manana" in f.txt else 1 if re.search(r"(?<!de la )\bmanana\b", f.txt) else 0
        try:
            return Respuesta(self.clima.consultar(dia))
        except Exception as e:
            log.warning("Clima: %s", e)
            return Respuesta("No pude consultar el clima ahora.")

    # ------------------------------------------------------------ navegador
    def c_nav_y(self, f, m):
        resto = f.grupo(m, "resto")
        return self._navegacion(Frase(resto)) or Respuesta("¿Qué quieres hacer en el navegador?")

    def c_yt_abrir(self, f, m):
        return Respuesta(f"Abriendo YouTube{self._suf()}.", accion=self._abrir_url("https://www.youtube.com/"),
                         confirmar_voz="¿Abro YouTube?")

    def c_youtube(self, f, m):
        v = m.group("v")
        q = f.grupo(m, "q")
        perfil, incog = self._perfil, self._incognito
        if re.fullmatch(BUSCA, v):
            url, _ = self.nav.url_servicio("youtube", q)
            return Respuesta(f"Busco {q} en YouTube{self._suf()}.", accion=self._abrir_url(url),
                             confirmar_voz="¿Lo busco en YouTube?")
        reproducir = Respuesta(f"Pongo {q} en YouTube{self._suf()}.",
                               accion=lambda: self.nav.poner_video(q, perfil, incog))
        if m.re is MUSICA:
            if re.search(r"\b(?:en|por) (?:spotify|apple music|deezer|amazon music)\b", normalizar(q)):
                return Respuesta("Solo puedo reproducir esa búsqueda en YouTube.")
            return self._confirmar("¿Quieres reproducirlo en YouTube?", reproducir)
        reproducir.confirmar_voz = "¿Lo reproduzco en YouTube?"
        return reproducir

    def _servicio(self, texto_s: str) -> str | None:
        n = normalizar(texto_s)
        for clave, patron in IA_ALIAS.items():
            if re.fullmatch(patron, n):
                return clave
        return None

    def c_ia_abrir(self, f, m):
        clave = self._servicio(m.group("s"))
        if not clave:
            return None
        nombre, _, inicio = SERVICIOS[clave]
        return Respuesta(f"Abriendo {nombre}{self._suf()}.", accion=self._abrir_url(inicio),
                         confirmar_voz=f"¿Abro {nombre}?")

    def c_ia(self, f, m):
        clave = self._servicio(m.group("s"))
        q = quitar_conectores(f.grupo(m, "q"), ("que", "si"))
        if not clave or not q:
            return None
        nombre, con_q, _ = SERVICIOS[clave]
        if clave in {"chatgpt", "claude"}:
            return self._revisar_ia(nombre, q)
        perfil, incog = self._perfil, self._incognito
        accion = lambda: self.nav.preguntar(clave, q, perfil, incog)  # noqa: E731
        if con_q:
            return Respuesta(f"Le pregunto a {nombre}{self._suf()}.", accion=accion,
                             confirmar_voz=f"¿Le pregunto a {nombre}?")
        return Respuesta(f"Abro {nombre}{self._suf()}. Te dejé la pregunta copiada: pega con Control V "
                         "y presiona Enter.", accion=accion, confirmar_voz=f"¿Abro {nombre} con tu pregunta?")

    def c_google_ia(self, f, m):
        q = f.grupo(m, "q")
        perfil, incog = self._perfil, self._incognito
        return Respuesta(f"Le pregunto a la IA de Google{self._suf()}.",
                         accion=lambda: self.nav.preguntar("google_ia", q, perfil, incog),
                         confirmar_voz="¿Lo consulto en la IA de Google?")

    def c_google(self, f, m):
        q = f.grupo(m, "q")
        if not q:
            return None
        self.ultima_busqueda_google = (q, time.monotonic())
        url, _ = self.nav.url_servicio("google", q)
        return Respuesta(f"Busco {q} en Google{self._suf()}.", accion=self._abrir_url(url),
                         confirmar_voz="¿Lo busco en Google?")

    def c_abrir(self, f, m):
        r = self._abrir_destino(f.grupo(m, "q"), nueva=bool(NUEVA.search(f.txt)))
        if r:
            return r
        return Respuesta("¿Puedes repetir el nombre de la aplicación o del sitio?",
                         siguiente=self._destino_dictado, espera_s=15)

    def _destino_dictado(self, texto: str) -> Respuesta | None:
        return self._abrir_destino(Frase(texto).original())

    def _abrir_destino(self, q: str, nueva: bool = False) -> Respuesta | None:
        qn = normalizar(q)
        if not qn:
            return None
        self.ultimo_destino = (qn, time.time())
        bloc = BLOC.fullmatch(qn)
        if bloc:
            nuevo = bool(bloc["nuevo"] or bloc["fin"])
            return Respuesta(ejecutar=lambda: ventanas.abrir_bloc(nuevo),
                             confirmar_voz="¿Abro un Bloc de notas nuevo?" if nuevo else "¿Abro el Bloc de notas?")
        contenedor = CONTENEDOR_NAV.fullmatch(qn)
        if contenedor:
            nav = self.nav
            browser = contenedor["nav"]
            if browser:
                programa = "edge" if "edge" in browser else "chrome"
                if programa != nav.programa:
                    nav = Navegador({"programa": programa})
            perfil, incognito = self._perfil, self._incognito
            if contenedor["tipo"] == "ventana":
                return Respuesta(ejecutar=lambda: nav.nueva_ventana(perfil, incognito),
                                 confirmar_voz="¿Abro una nueva ventana del navegador?")
            return Respuesta("Abriendo una pestaña en el navegador.", confirmar_voz="¿Abro una pestaña nueva?",
                             accion=lambda: nav.abrir("about:blank", perfil, incognito))
        if qn in NAVEGADOR_SOLO:
            if not nueva and not self._perfil and not self._incognito and not self.nav.perfil_pred and qn not in (
                    "una pestana", "nueva pestana", "una ventana", "nueva ventana", "otra ventana"):
                v = ventanas.buscar(qn if qn in ventanas.PROGRAMAS else "navegador")
                if v:  # ya estaba abierto: lo trae al frente en vez de abrir otro
                    return self._mostrar_programa("El navegador", lambda: ventanas.enfocar(v.hwnd), "")
            return self._mostrar_programa("El navegador", self._abrir_url(None), "¿Abro el navegador?")
        alias = sistema.ALIAS_CARPETAS.get(re.sub(r"^(?:la )?carpeta (?:de |del )?", "", qn))
        if alias:
            ruta = sistema.carpeta(alias)
            return Respuesta(f"Abriendo {q[:1].upper() + q[1:]}.", accion=lambda: (ruta.mkdir(parents=True, exist_ok=True),
                                                               sistema.abrir_ruta(ruta)),
                             confirmar_voz=f"¿Abro la carpeta {q}?")
        if qn in APP_O_WEB:
            nombre_app, web = APP_O_WEB[qn]
            # WhatsApp se abre en el navegador salvo que en config.toml pidas la app.
            preferir_web = self.preferencias.get(qn, "web" if qn == "whatsapp" else "app") == "web"
            app = None if preferir_web else self.apps.buscar(nombre_app)
            if app:
                v = ventanas.buscar(qn)
                if v and not nueva:
                    return Respuesta(f"Ahí tienes {nombre_app}.", accion=lambda: ventanas.enfocar(v.hwnd))
                return Respuesta(f"Abriendo la aplicación de {nombre_app}.", accion=self._abrir_app(app),
                                 confirmar_voz=f"¿Abro {nombre_app}?")
            return Respuesta(f"Abriendo {nombre_app} Web en el navegador{self._suf()}.",
                             accion=self._abrir_url(web, titulo=nombre_app),
                             confirmar_voz=f"¿Abro {nombre_app} en el navegador?")
        sitio = self.nav.sitio(q)
        if sitio:
            nombre = sitio[0][:1].upper() + sitio[0][1:] if "." not in sitio[0] else sitio[0]
            return Respuesta(f"Abriendo {nombre}{self._suf()}.", accion=self._abrir_url(sitio[1]),
                             confirmar_voz=f"¿Abro {nombre}?")
        clave = self._servicio(q)
        if clave:
            return Respuesta(f"Abriendo {SERVICIOS[clave][0]}{self._suf()}.",
                             accion=self._abrir_url(SERVICIOS[clave][2]),
                             confirmar_voz=f"¿Abro {SERVICIOS[clave][0]}?")
        app = self.apps.buscar(q)
        if app:
            canonico = sistema.ALIAS_APPS.get(qn, app["Name"])
            return self.abrir_aplicacion(app, canonico, nueva)
        return None

    def abrir_aplicacion(self, app, nombre_canonico="", nueva=False):
        nombre = app["Name"]
        canonico = nombre_canonico or sistema.ALIAS_APPS.get(normalizar(nombre), nombre)
        self.ultimo_destino = (normalizar(canonico), time.time())
        if not nombre_canonico and normalizar(canonico) not in ventanas.PROGRAMAS:
            # Una pestaña del navegador podría llevar el mismo título que la app
            # aprendida. Con solo el AppID, dejar la activación en manos de Windows.
            def solicitar():
                self.apps.abrir(app)
                return f"Pedí a Windows que abra {nombre}."
            return Respuesta(ejecutar=solicitar, confirmar_voz=f"¿Abro {nombre}?")
        v = None if nueva else ventanas.buscar(canonico)
        if v:
            return self._mostrar_programa(nombre, lambda: ventanas.enfocar(v.hwnd), "")
        return self._mostrar_programa(nombre, self._abrir_app(app, canonico), f"¿Abro {nombre}?")


    # ------------------------------------------------------------ segundo plano (manos libres)
    def _fondo(self, trabajo, nombre: str = "eso"):
        """Envuelve una tarea lenta: corre en paralelo y avisa por voz si algo falla."""
        def envoltura():
            try:
                trabajo()
            except NecesitaQR:
                self.avisar(Respuesta("WhatsApp todavía no está vinculado. ¿Lo vinculamos ahora? Necesitarás "
                                      "tu celular para escanear un código.",
                                      siguiente=lambda t: self.c_wa_vincular(None, None) if confirma_accion(t) else Respuesta("Bien."),
                                      espera_s=25))
            except NoEncontrado as e:
                self.avisar(f"No encontré a {e} en WhatsApp.")
            except GoogleNoConectado as e:
                self.avisar(str(e))
            except Exception as e:  # noqa: BLE001
                log.exception("Falló %s", nombre)
                self.avisar(f"No pude completar {nombre}. {str(e)[:120]}")
        return envoltura

    def conectar_voz(self, voz) -> None:
        self.voz = voz

    def c_velocidad(self, f, m):
        voz = self.voz
        if voz is None:
            return Respuesta("Esta consola está en modo texto. La opción --voz activa la voz.")
        if m["consulta"]:
            return Respuesta(f"Mi ritmo está a {voz.velocidad:g} veces la velocidad de referencia.")
        estilo = m["queja"] or m["pedido"] or m["corto"] or ""
        anterior = voz.velocidad, voz.tono_semitonos
        if m["guardar"]:
            respuesta = "Guardé esta voz."
        elif "normal" in estilo:
            voz.velocidad = self.cfg["voz"]["velocidad"]
            voz.tono_semitonos = self.cfg["voz"]["tono_semitonos"]
            respuesta = "Volví a mi voz habitual."
        else:
            paso = 0.08
            respuesta = "Ajusté el ritmo."
            if any(p in estilo for p in ("lent", "despacio", "rapid")):
                rapido = "rapid" in estilo
                subir = not rapido if m["queja"] else rapido
                paso = 0.12 if subir else -0.12
                respuesta = "Aumenté el ritmo." if subir else "Bajé el ritmo."
            voz.velocidad = round(max(0.5, min(2.0, voz.velocidad + paso)), 2)
            if any(p in estilo for p in ("triste", "cansad", "apagad", "decaid", "monoton", "energia",
                                         "alegre", "animado")):
                voz.tono_semitonos = round(min(3.0, voz.tono_semitonos + 0.5), 2)
                respuesta = "Ajusté el ritmo y el tono."
        cambiado = anterior != (voz.velocidad, voz.tono_semitonos)
        if not cambiado and not m["guardar"]:
            respuesta = "Ya uso ese ajuste."
        if m["guardar"] or m["recordar"] or "siempre" in f.norm or f.txt.startswith("de ahora en adelante "):
            from .ajustes import guardar_ajustes
            preferencia = {"velocidad": voz.velocidad, "tono_semitonos": voz.tono_semitonos}
            try:
                guardar_ajustes({"voz": preferencia})
            except (ValueError, OSError) as e:
                log.warning("No se pudo guardar la preferencia de voz: %s", e)
                return Respuesta("El ajuste quedó en uso. No pude guardarlo para la próxima vez.")
            self.cfg["voz"].update(preferencia)
            respuesta = "Ajusté la voz y guardé tu preferencia." if cambiado else "Guardé esta voz."
        return Respuesta(respuesta)

    # ------------------------------------------------------------ resumen del día
    def _dia_hablado(self, d: datetime) -> str:
        dias = (d.date() - datetime.now(self.tz).date()).days
        if dias == 0:
            return "hoy"
        if dias == 1:
            return "mañana"
        if 1 < dias < 7:
            return f"el {DIAS[d.weekday()]}"
        return f"el {d.day}/{d.month}"

    @staticmethod
    def _evento_txt(e: dict) -> str:
        return f"{e['titulo']}, {'todo el día' if e['todo_el_dia'] else hora_hablada(e['inicio'])}."

    def _correos_importantes(self) -> str:
        if self.store.ultimo_sync("gmail") is None:
            return ""
        correos = self._correos()
        if not correos:
            return "No tienes correos nuevos."
        imp = [c for c in correos if c.tipo == "importante"]
        lista, etiqueta = (imp, "importante") if imp else (correos, "sin leer")
        c = lista[0]
        if len(lista) == 1:
            return f"Tienes un correo {etiqueta}, de {c.detalle}: {c.titulo}."
        plural = "importantes" if imp else "sin leer"
        return f"Tienes {len(lista)} correos {plural}; el más reciente es de {c.detalle}: {c.titulo}."

    def resumen_dia(self, saludo: str = "") -> str:
        partes = [saludo] if saludo else []
        try:
            partes.append(self.clima.breve())
        except Exception as e:
            log.info("Sin clima: %s", e)
        tareas = self.router.resumen_tareas(detalle=True, cuando="hoy", max_items=3)
        if not tareas.startswith("Todavía"):
            partes.append(tareas)
        if self.calendario.conectado():
            try:
                eventos = self.calendario.eventos_dia(0)
                if eventos:
                    partes.append("En tu calendario: " + " ".join(self._evento_txt(e) for e in eventos[:3]))
            except Exception as e:
                log.warning("Calendar: %s", e)
        partes.append(self._correos_importantes())
        return " ".join(p for p in partes if p)

    def c_resumen_dia(self, f, m):
        return Respuesta(self.resumen_dia())

    # ------------------------------------------------------------ Google Calendar
    def c_cal_listar(self, f, m):
        if not self.calendario.conectado():
            return Respuesta("Tu Google Calendar no está conectado. Activa la API de Calendar y ejecuta "
                             "conectar-google.bat.")
        dia = 1 if re.search(r"(?<!de la )\bmanana\b", f.txt) else 0
        cuando = "mañana" if dia else "hoy"
        eventos = self.calendario.eventos_dia(dia)
        if not eventos:
            return Respuesta(f"No tienes eventos en tu calendario para {cuando}.")
        return Respuesta(f"Para {cuando} tienes: " + " ".join(self._evento_txt(e) for e in eventos[:5]))

    def c_cal_crear(self, f, m):
        if not self.calendario.conectado():
            return Respuesta("Para agendar necesito permiso de Google Calendar: activa la API de Calendar en "
                             "Google Cloud y ejecuta conectar-google.bat.")
        resto = f.sin_span(0, m.end())
        while (cm := CAL_FRASES.search(resto.txt)):
            resto = resto.sin_span(*cm.span())
        return self._evento(resto)

    def _evento(self, resto: Frase, titulo: str | None = None) -> Respuesta:
        ahora = datetime.now(self.tz)
        ini = fin = None
        todo = False
        rg = parse_rango(resto.txt, ahora)
        if rg:
            ini, fin, tramos = rg
            for a, b in sorted(tramos, reverse=True):
                resto = resto.sin_span(a, b)
        else:
            r = parse_fecha_hora(resto.txt, ahora)
            if r:
                ini, span, con_hora = r
                todo = not con_hora
                resto = resto.sin_span(*span)
                h = re.search(r"\bhasta (?:las|la) \S+(?: y (?:media|cuarto))?(?: (?:de la|en la) (?:manana|tarde|noche))?",
                              resto.txt)
                if h and not todo:
                    r2 = parse_hora(h.group(0).replace("hasta", "a", 1), ini)
                    if r2:
                        fin = r2[0]
                        resto = resto.sin_span(*h.span())
                d = parse_duracion(resto.txt)
                if d and not fin and re.match(r"(?:por|durante|de)\b", resto.txt[d[1][0]:]):
                    fin = ini + timedelta(seconds=d[0])
                    resto = resto.sin_span(*d[1])
            else:
                d = parse_duracion(resto.txt)
                if d and re.match(r"(?:en|dentro)\b", resto.txt[d[1][0]:]):
                    ini = ahora + timedelta(seconds=d[0])
                    resto = resto.sin_span(*d[1])
        if titulo is None:
            t = re.sub(r"^(?:llamado|que se llame|titulad[oa]|con el (?:titulo|nombre)|sobre|para|de)\s+", "",
                       resto.original(), flags=re.I)
            titulo = quitar_conectores(t, ("un", "una", "el", "la", "evento", "que"))
        titulo = titulo[:1].upper() + titulo[1:] if titulo else ""
        if ini is None:
            return Respuesta("¿Para qué día y hora lo agendo?", espera_s=40,
                             siguiente=lambda t: self._evento(Frase(t), titulo or None))
        if not titulo:
            return Respuesta("¿Cómo se llama el evento?", espera_s=40,
                             siguiente=lambda t: self._confirmar_evento(Frase(t).original(), ini, fin, todo))
        return self._confirmar_evento(titulo, ini, fin, todo)

    def _confirmar_evento(self, titulo: str, ini: datetime, fin, todo: bool) -> Respuesta:
        titulo = titulo[:1].upper() + titulo[1:]
        ahora = datetime.now(self.tz)
        if todo:
            cuando = f"para {self._dia_hablado(ini)}, todo el día"
        else:
            hasta = re.sub(r"^(?:a |al )", "", hora_hablada(fin)) if fin else ""
            cuando = cuando_hablado(ini, ahora) + (f", hasta {hasta}" if fin else "")

        def crear():
            self.calendario.crear(titulo, ini, fin, todo)
            self.avisar("Listo, quedó en tu calendario.")

        def responder(t: str) -> Respuesta:
            if confirma_accion(t):
                return Respuesta("Agendando.", accion=self._fondo(crear, "el evento"))
            return Respuesta("Bien, no lo agendé.")

        return Respuesta(f"¿Agendo «{titulo}» {cuando}?", siguiente=responder, espera_s=25)

    # ------------------------------------------------------------ WhatsApp
    def c_wa_vincular(self, f, m):
        def trabajo():
            self.avisar("Abrí WhatsApp Web en la ventana de Jarvis. Si ves un código QR, escanéalo con tu celular: "
                        "WhatsApp, Dispositivos vinculados, Vincular un dispositivo.")
            ok = self.whatsapp.vincular(150)
            self.avisar("Listo, WhatsApp quedó vinculado." if ok else "No se completó la vinculación.")
        return Respuesta("Voy a vincular WhatsApp.", accion=self._fondo(trabajo, "la vinculación de WhatsApp"),
                         confirmar_voz="¿Abro WhatsApp para vincularlo?")

    def _wa_preparar(self, a: str, msg: str) -> Respuesta:
        msg = quitar_conectores(msg, ("que",))
        msg = msg[:1].upper() + msg[1:]

        def trabajo():
            inicio = time.time()
            titulo = self.whatsapp.preparar_mensaje(a, msg)
            if self.cancelado_ts > inicio:  # dijo "ya no" mientras buscaba el chat
                self.whatsapp.borrar_borrador()
                return

            def confirmar(t: str) -> Respuesta:
                if confirma_accion(t):
                    def enviar():
                        self.avisar("Enviado." if self.whatsapp.enviar()
                                    else "No pude confirmar el envío; revisa la ventana de WhatsApp.")
                    return Respuesta("Enviando.", accion=self._fondo(enviar, "el envío"))
                return Respuesta("No lo envié.", accion=self.whatsapp.borrar_borrador)

            self.avisar(Respuesta(f"Encontré el chat {titulo}. El mensaje dice: {msg}. ¿Lo envío?",
                                  siguiente=confirmar, espera_s=30,
                                  al_expirar=lambda: Respuesta("No lo envié.", accion=self.whatsapp.borrar_borrador),
                                  al_cancelar=self.whatsapp.borrar_borrador))

        return Respuesta(f"Busco a {a} en WhatsApp.", accion=self._fondo(trabajo, "el mensaje"),
                         confirmar_voz=f"¿Abro WhatsApp para preparar el mensaje a {a}?")

    def c_wa_enviar(self, f, m):
        a, msg = f.grupo(m, "a"), f.grupo(m, "msg")
        return self._wa_preparar(a, msg) if a and msg else None

    def c_wa_sin_msg(self, f, m):
        a = f.grupo(m, "a")
        return Respuesta(f"¿Qué le digo a {a}?", espera_s=40,
                         siguiente=lambda t: Respuesta("Bien, no envío nada.") if CANCELA.match(normalizar(t))
                         else self._wa_preparar(a, Frase(t).original()))

    def _wa_leer(self, a: str) -> Respuesta:
        def trabajo():
            titulo, mensajes = self.whatsapp.ultimos_mensajes(a, 5)
            if not mensajes:
                self.avisar(f"No vi mensajes de texto recientes con {titulo}.")
                return
            partes = " ".join(f"{x['quien']}: {x['texto'][:220]}." for x in mensajes)
            self.avisar(f"Últimos mensajes con {titulo}. {partes}")
        return Respuesta(f"Abro el chat de {a}.", accion=self._fondo(trabajo, "la lectura del chat"),
                         confirmar_voz=f"¿Abro el chat de {a}?")

    def c_wa_leer(self, f, m):
        a = f.grupo(m, "a")
        return self._wa_leer(a) if a else None

    def c_wa_nuevos(self, f, m):
        def trabajo():
            lista = self.whatsapp.no_leidos()
            if not lista:
                self.avisar("No tienes chats sin leer en WhatsApp.")
                return
            partes = "; ".join(f"{x['chat']}, {x['n']}" for x in lista[:6])
            self.avisar(Respuesta(f"Tienes mensajes sin leer de: {partes}. ¿Te leo alguno?", espera_s=25,
                                  siguiente=lambda t: Respuesta("Bien.") if es_no(t) else
                                  self._wa_leer(quitar_conectores(Frase(t).original(),
                                                                  ("el", "la", "los", "de", "del", "chat", "si", "sí")))))
        return Respuesta("Reviso tu WhatsApp.", accion=self._fondo(trabajo, "la revisión de WhatsApp"),
                         confirmar_voz="¿Abro WhatsApp para revisar los mensajes?")

    def c_wa_buscar(self, f, m):
        q = f.grupo(m, "q") or f.grupo(m, "q2")
        if not q:
            return None

        def trabajo():
            filas = self.whatsapp.buscar(q)
            if not filas:
                self.avisar(f"No encontré nada con {q} en WhatsApp.")
                return
            partes = []
            for fila in filas[:3]:
                texto = next((x for x in fila[1:] if not re.fullmatch(r"[\d:/.\sapm]+|ayer|hoy", x.lower())), "")
                partes.append(f"En {fila[0]}: {texto[:150]}." if texto else f"El chat {fila[0]}.")
            self.avisar("Encontré: " + " ".join(partes))
        return Respuesta(f"Busco {q} en WhatsApp.", accion=self._fondo(trabajo, "la búsqueda en WhatsApp"),
                         confirmar_voz="¿Abro WhatsApp para buscarlo?")

    # ------------------------------------------------------------ texto seleccionado
    def c_leer_sel(self, f, m):
        texto = sistema.copiar_seleccion().strip()
        if not texto:
            return Respuesta("No veo texto seleccionado. Selecciona algo y vuelve a pedírmelo.")
        return Respuesta(texto[:1500])

    def c_traducir(self, f, m):
        texto = sistema.copiar_seleccion().strip()
        if not texto:
            return Respuesta("Selecciona el texto que quieres traducir y vuelve a pedírmelo.")
        idiomas = {"ingles": "inglés", "espanol": "español", "portugues": "portugués", "frances": "francés",
                   "italiano": "italiano", "aleman": "alemán", "chino": "chino", "japones": "japonés",
                   "quechua": "quechua"}
        idioma = idiomas.get(m.group("idioma") or "")
        if not idioma:
            espanol = len(re.findall(r"\b(?:el|la|de|que|y|en|los|las|por|para|con|una?)\b", normalizar(texto)))
            idioma = "inglés" if espanol >= max(2, len(texto.split()) // 10) else "español"
        esquema = {"type": "object", "properties": {"traduccion": {"type": "string"}}, "required": ["traduccion"]}

        def trabajo():
            res = self.docs.cerebro.generar_json(f"Traduce al {idioma} este texto, conservando el sentido:\n\n"
                                                 f"{texto[:4000]}", esquema, max_tokens=1500)["traduccion"]
            sistema.copiar(res)
            extra = f" Dice: {res}" if len(res) < 300 else " Pégala con Control V."
            self.avisar(f"Listo, copié la traducción al {idioma}.{extra}")
        return Respuesta(f"Traduciendo al {idioma}.", accion=self._fondo(trabajo, "la traducción"))

    def c_explicar(self, f, m):
        texto = sistema.copiar_seleccion().strip()
        if not texto:
            return Respuesta("Selecciona lo que quieres que te explique y vuelve a pedírmelo.")

        def frases():
            try:
                yield from self.docs.cerebro.frases(f"Explícame en palabras simples y en pocas frases: {texto[:3000]}", "")
            except Exception:
                yield "No pude pensar en eso; revisa que Ollama esté abierto."
        return Respuesta(texto=frases())

    # ------------------------------------------------------------ resumir documentos
    def c_resumir(self, f, m):
        obj, q = normalizar(f.grupo(m, "obj")), f.grupo(m, "q")
        empieza_trata = f.txt.startswith("de que trata")
        if not obj and not empieza_trata:
            return None  # "hazme un resumen de la revolución francesa" → es un documento nuevo
        seleccion = obj in ("lo seleccionado", "seleccion")
        ruta = None
        if obj in ("archivo", "pdf", "documento", "word", "presentacion") and q and not re.fullmatch(
                r"(?:que )?tengo abierto|abierto|de aqui", normalizar(q)):
            tipo = {"pdf": "pdf", "word": "word", "presentacion": "presentacion"}.get(obj)
            encontrados = self.indice.buscar(q, tipo, n=1)
            if not encontrados:
                return Respuesta(f"No encontré un archivo llamado {q}.")
            ruta = encontrados[0]
        titulo_ventana, exe = ventana_activa()
        texto_sel = sistema.copiar_seleccion().strip() if seleccion else ""
        if seleccion and not texto_sel:
            return Respuesta("No veo texto seleccionado.")
        modelo = self.cfg["documentos"]["modelo"] or None

        def trabajo():
            nombre, texto = "el texto seleccionado", texto_sel
            if not seleccion:
                if ruta:
                    nombre, texto = ruta.stem, extraer_texto(ruta)
                else:
                    office = texto_office_activo(exe)
                    if office:
                        nombre, texto = office
                    else:
                        archivo = archivo_desde_titulo(titulo_ventana, self.indice)
                        if not archivo:
                            self.avisar("No identifiqué qué documento tienes abierto. Pon su ventana al frente o "
                                        "dime su nombre, por ejemplo: resume el archivo tesis.")
                            return
                        nombre, texto = archivo.stem, extraer_texto(archivo)
            if len(texto.strip()) < 80:
                self.avisar("No pude leer texto en ese documento. Si es un PDF escaneado, no tiene texto que pueda leer.")
                return
            res = resumir(self.docs.cerebro, texto, nombre, modelo)
            guardar_resumen(res, nombre, self.carpeta)
            sistema.copiar(res.get("resumen", ""))
            extra = " Solo resumí la primera parte porque es muy largo." if res.get("recortado") else ""
            puntos = res.get("puntos", [])
            self.avisar(Respuesta(
                f"Resumen de {nombre}: {res.get('resumen', '')}{extra} ¿Te leo los puntos clave?", espera_s=25,
                siguiente=lambda t: Respuesta(" ".join(p.rstrip('.') + "." for p in puntos)) if es_si(t)
                else Respuesta("Bien. Lo dejé guardado en Documentos, carpeta Jarvis, Resúmenes.")))

        return Respuesta("Leo el documento y te lo resumo; dame un momento.", accion=self._fondo(trabajo, "el resumen"))

    # ------------------------------------------------------------ archivos
    _TIPO_HABLADO = {"archivo": None, "archivos": None, "documento": "documento", "documentos": "documento",
                     "pdf": "pdf", "pdfs": "pdf", "word": "word", "words": "word", "excel": "excel",
                     "excels": "excel", "presentacion": "presentacion", "presentaciones": "presentacion",
                     "power point": "powerpoint", "powerpoint": "powerpoint", "power points": "powerpoint",
                     "foto": "foto", "fotos": "foto", "imagen": "imagen", "imagenes": "imagen", "video": "video",
                     "videos": "video", "audio": "audio", "audios": "audio", "carpeta": "carpeta", "carpetas": "carpeta"}
    _EXT_HABLADA = {".docx": "Word", ".doc": "Word", ".pdf": "PDF", ".xlsx": "Excel", ".xls": "Excel",
                    ".csv": "CSV", ".pptx": "presentación", ".ppt": "presentación", ".jpg": "foto",
                    ".jpeg": "foto", ".png": "imagen", ".mp4": "video", ".txt": "texto"}

    def _describir(self, p: Path) -> str:
        tipo = "carpeta" if p.is_dir() else self._EXT_HABLADA.get(p.suffix.lower(), "archivo")
        try:
            fecha = f", modificado {fecha_hablada(p.stat().st_mtime)}"
        except OSError:
            fecha = ""
        nombre = p.stem if p.is_file() else p.name
        tipo = "" if normalizar(nombre).startswith(normalizar(tipo)) else f"{tipo} "
        return f"{tipo}{nombre}, {lugar_hablado(p)}{fecha}"

    def c_buscar_archivo(self, f, m):
        return self._archivos(f.grupo(m, "q"), self._TIPO_HABLADO.get(normalizar(m.group("tipo"))), abrir=False)

    def c_abrir_archivo(self, f, m):
        return self._archivos(f.grupo(m, "q"), self._TIPO_HABLADO.get(normalizar(m.group("tipo"))), abrir=True)

    def _archivos(self, q: str, tipo: str | None, abrir: bool) -> Respuesta:
        if not self.indice.listo:
            return Respuesta("Todavía estoy revisando tus carpetas; pídemelo en unos segundos.")
        res = self.indice.buscar(q, tipo, n=5)
        if not res:
            return Respuesta(f"No encontré nada con el nombre {q}.")
        p = res[0]
        if abrir and (len(res) == 1 or normalizar(p.stem) == normalizar(q)):
            return Respuesta(f"Abriendo {p.stem}.", accion=lambda: sistema.abrir_ruta(p),
                             confirmar_voz=f"¿Abro {p.stem}?")

        def responder(t: str) -> Respuesta | None:
            n = normalizar(t)
            for palabra, i in (("segundo", 1), ("tercero", 2), ("cuarto", 3)):
                if re.fullmatch(rf"(?:el |la )?{palabra}", n) and len(res) > i:
                    return Respuesta(f"Abriendo {res[i].stem}.", accion=lambda i=i: sistema.abrir_ruta(res[i]))
            if re.fullmatch(r"(?:abre |muestra |muestrame )?(?:la )?carpeta", n):
                return Respuesta("Te muestro dónde está.", accion=lambda: sistema.mostrar_en_carpeta(p))
            if re.search(r"\b(?:otros?|cuales|lista|demas)\b", n):
                otros = " ".join(f"{i}: {self._describir(x)}." for i, x in enumerate(res[1:], 2))
                return Respuesta(f"Los otros son: {otros} ¿Cuál abro?", siguiente=responder, espera_s=30)
            if confirma_accion(t) or re.fullmatch(r"(?:el |la )?primer[oa]?", n):
                return Respuesta(f"Abriendo {p.stem}.", accion=lambda: sistema.abrir_ruta(p))
            return Respuesta("Bien.") if es_no(t) else None

        inicio = f"Encontré {len(res)}. El mejor: " if len(res) > 1 else "Encontré "
        return Respuesta(f"{inicio}{self._describir(p)}. ¿Lo abro?", siguiente=responder, espera_s=30)

    # ------------------------------------------------------------ plataformas (iniciar sesión)
    def c_login(self, f, m):
        q = normalizar(f.grupo(m, "q"))
        if re.search(r"\b(?:perfil|navegador|chrome|edge|incognito|privado)\b", f.txt):
            return None  # pidió un perfil/navegador concreto: eso va por tu Chrome normal
        clave = q if q in self.plataformas else None
        if not clave:
            return None
        nombre, url = self.plataformas[clave]
        nombre = nombre[:1].upper() + nombre[1:]
        return Respuesta(f"Entrando a {nombre}.",
                         accion=self._fondo(lambda: self._login(nombre, url, self.sesiones.entrar(url)),
                                            f"la entrada a {nombre}"), confirmar_voz=f"¿Entro a {nombre}?")

    def _login(self, nombre: str, url: str, estado: str) -> None:
        if estado == "listo":
            self.avisar(f"Listo, ya estás en {nombre}.")
        elif estado == "falta_usuario":
            def escribir(t: str) -> Respuesta | None:
                n = normalizar(t)
                if CANCELA.match(n):
                    return Respuesta("Bien, lo dejo.")
                if re.match(r"^(?:abre|entra|ingresa|busca|pon|pregunta|recuerdame|agenda|manda|dile)\b", n):
                    return None  # era otra orden
                usuario = usuario_dictado(t)
                return Respuesta("Lo escribo.", accion=self._fondo(
                    lambda: self._login(nombre, url, self.sesiones.escribir_usuario(url, usuario)),
                    f"la entrada a {nombre}"))
            self.avisar(Respuesta(f"En {nombre} falta tu usuario o correo. Dímelo y lo escribo.",
                                  siguiente=escribir, espera_s=45))
        elif estado == "falta_clave":
            self.avisar(f"En {nombre} falta tu contraseña. Por seguridad no la escucho: escríbela tú en la "
                        "ventana de Jarvis y yo presiono Entrar.")
            nuevo = self.sesiones.esperar_clave(url, 120)
            if nuevo == "falta_clave":
                self.avisar(f"No vi la contraseña. Cuando quieras, dime: entra a {nombre}.")
            else:
                self._login(nombre, url, nuevo)
        else:
            self.avisar(f"No encontré el botón para entrar a {nombre}; revisa la ventana de Jarvis.")

    # ------------------------------------------------------------ descripción
    @staticmethod
    def tipos_documento() -> dict:
        return TIPOS
