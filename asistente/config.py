"""Carga de configuración (config.toml) con valores por defecto."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"
DATOS.mkdir(exist_ok=True)

DEFAULTS: dict = {
    "usuario": {"nombre": "Usuario", "zona_horaria": "America/Lima"},
    "llm": {
        "url": "http://127.0.0.1:11434",
        "modelo": "qwen3.5:2b",
        "num_ctx": 2048,          # contexto de la charla (más grande = más memoria RAM)
        "max_tokens": 160,
        "temperatura": 0.6,
        "turnos_memoria": 6,
        "hilos": 0,               # 0 = automático (optimizar.bat elige el mejor)
        "candidatos": ["qwen3.5:2b-q4_K_M", "qwen3:1.7b"],  # los que prueba optimizar.bat
    },
    "stt": {"modelo": "small", "idioma": "es", "hilos": 4, "beam": 3,
            "pistas": "Jarvis, navegador, WhatsApp, YouTube, ChatGPT, Word, Excel, PowerPoint, Google.",
            "prompt_inicial": ""},
    "voz": {"modelo_piper": "voces/es_MX-ald-medium.onnx", "velocidad": 1.18, "pausa_ms": 95,
            "tono_semitonos": 0.7, "pronunciaciones": {},
            "relleno": False, "hilos": 2,  # hilos para la voz: pocos, para no pelear con el modelo
            "frases_relleno": ["Mmm, déjame pensar.", "A ver...", "Un momento.", "Buena pregunta.",
                               "Déjame ver."]},
    "escucha": {
        "palabra_clave": "jarvis",
        "variantes_clave": ["yarvis", "jarbis", "yarbis", "charvis", "harvis"],
        "minutos_conversacion": 5,
        "silencio_ms": 700,
        "agresividad_vad": 2,
        "interrumpir": True,              # puedes hablarle encima para callarlo o pedir otra cosa
        "sensibilidad_interrupcion": 3.0,  # más alto = hay que hablar más fuerte para interrumpir
        "confirmar_aperturas": False,     # true = pregunta "¿Abro X?" si no empezaste con "Jarvis"
        "saludar_al_iniciar": True,
        "sugerir_pendientes": True,
        "anunciar_resumen": False,
        "enfriamiento_saludo_min": 3,
        "espera_respuesta_saludo_s": 25,
        "resumen_matutino": True,
    },
    "gmail": {
        "habilitado": True,
        "consulta": "is:unread category:primary newer_than:7d",
        "max_correos": 15,
    },
    "clementina": {
        "habilitado": True,
        "modo": "ics",          # "ics" (recomendado) o "sesion"
        "url_ics": "",
        "navegador": "chrome",
    },
    "sync": {"minutos_entre_sync": 30},
    "navegador": {"programa": "chrome", "perfil_predeterminado": ""},
    "sitios": {},
    "documentos": {"carpeta": "", "modelo": ""},
    "conocimiento": {"carpeta": "", "busqueda_semantica": False,
                     "modelo_embeddings": "embeddinggemma:300m-qat-q4_0"},
    "clima": {"ciudad": "Trujillo", "latitud": -8.11, "longitud": -79.03},
    "navegador_jarvis": {"canal": "chrome"},
    "plataformas": {"clementina": "https://uvcv.edu.pe/my/", "aula virtual": "https://uvcv.edu.pe/my/",
                    "uvcv": "https://uvcv.edu.pe/my/"},
    "archivos": {"carpetas_extra": []},
    "interfaz": {"widget": True, "tamano": 200, "subtitulos": True},
    "preferencias": {"whatsapp": "web"},  # "web" = WhatsApp Web en el navegador; "app" = la aplicación
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def con_defaults(configurada: dict) -> dict:
    cfg = _merge(DEFAULTS, configurada)
    escucha = cfg["escucha"]
    if escucha["palabra_clave"].casefold().strip() != DEFAULTS["escucha"]["palabra_clave"]:
        # Las variantes preinstaladas son formas de pronunciar «Jarvis».
        # Mantiene las variantes propias que el usuario escribió en el archivo.
        preinstaladas = set(DEFAULTS["escucha"]["variantes_clave"])
        escucha["variantes_clave"] = [v for v in escucha["variantes_clave"]
                                      if v.casefold().strip() not in preinstaladas]
    return cfg


def cargar(ruta: Path | None = None) -> dict:
    ruta = ruta or RAIZ / "config.toml"
    if ruta.exists():
        with open(ruta, "rb") as f:
            return con_defaults(tomllib.load(f))
    return con_defaults({})


def ruta_proyecto(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else RAIZ / p
