"""Ajustes editables del panel e inventario local, sin iniciar modelos ni sesiones."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import tomllib
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit

import tomlkit
from filelock import FileLock, Timeout

from .config import DATOS, RAIZ, cargar, con_defaults, ruta_proyecto


# Los tipos se comprueban también al guardar desde el widget o por voz.
_CAMPOS = {
    "interfaz": {"tamano": (int, 120, 320), "subtitulos": bool, "widget": bool},
    "voz": {"velocidad": (float, 0.5, 2.0), "tono_semitonos": (float, -3, 3),
            "pausa_ms": (int, 0, 500), "modelo_piper": str, "relleno": bool, "pronunciaciones": dict},
    "escucha": {"palabra_clave": str, "minutos_conversacion": (int, 1, 60),
                "silencio_ms": (int, 240, 2000), "agresividad_vad": (int, 0, 3),
                "interrumpir": bool, "confirmar_aperturas": bool, "saludar_al_iniciar": bool,
                "sugerir_pendientes": bool},
    "stt": {"modelo": str},
    "llm": {"modelo": str},
    "conocimiento": {"busqueda_semantica": bool},
}


def _validar(seccion: str, clave: str, valor):
    regla = _CAMPOS.get(seccion, {}).get(clave)
    if regla is None:
        raise ValueError("Ajuste desconocido.")
    if regla is bool and type(valor) is bool:
        return valor
    if regla is dict and (seccion, clave) == ("voz", "pronunciaciones"):
        from .pronunciacion import validar_pronunciaciones
        return validar_pronunciaciones(valor)
    if regla is str and isinstance(valor, str):
        valor = valor.strip()
        if clave == "palabra_clave":
            if (0 < len(valor) <= 40 and any(c.isalpha() for c in valor)
                    and re.fullmatch(r"[^\W_]+(?:[ -][^\W_]+)*", valor)):
                return valor
            raise ValueError("Escribe una palabra de activación de hasta 40 caracteres.")
        if valor and len(valor) <= 1024 and valor.isprintable():
            return valor
    if isinstance(regla, tuple):
        tipo, inferior, superior = regla
        tipos = (int, float) if tipo is float else (int,)
        if type(valor) in tipos and inferior <= valor <= superior and math.isfinite(valor):
            return tipo(valor)
    raise ValueError(f"Valor fuera de rango para {seccion}.{clave}.")


def guardar_ajustes(cambios: dict, ruta: Path | None = None) -> dict:
    """Fusiona solo las claves modificadas sobre la última copia del archivo."""
    ruta = Path(ruta) if ruta is not None else RAIZ / "config.toml"
    if not isinstance(cambios, dict) or any(s not in _CAMPOS or not isinstance(campos, dict)
                                           for s, campos in cambios.items()):
        raise ValueError("Se esperaba un grupo de ajustes.")
    validados = [(s, k, _validar(s, k, v)) for s, campos in cambios.items() for k, v in campos.items()]
    if not validados:
        return cargar(ruta)
    try:
        with FileLock(str(ruta) + ".lock", timeout=3):
            antes = ruta.read_bytes() if ruta.exists() else b""
            # parsear el TOML antes de tocarlo evita reemplazar un archivo dañado.
            original = tomllib.loads(antes.decode("utf-8"))
            doc = tomlkit.parse(antes.decode("utf-8"))
            cfg = con_defaults(original)
            actualizado = False
            for seccion, clave, valor in validados:
                if cfg[seccion][clave] == valor:
                    continue
                if seccion == "voz" and clave == "modelo_piper" and not _voz_disponible(ruta_proyecto(valor)):
                    raise ValueError("Falta el modelo de voz o su archivo .onnx.json.")
                if seccion == "stt" and valor not in _modelos_stt(valor):
                    raise ValueError("El modelo de reconocimiento no está en la caché local.")
                if seccion == "llm" and valor not in _modelos_conversacion(cfg, _modelos_ollama(cfg)):
                    raise ValueError("El modelo de conversación no está instalado en este equipo.")
                if seccion not in doc:
                    doc.add(seccion, tomlkit.table())
                doc[seccion][clave] = valor
                original.setdefault(seccion, {})[clave] = valor
                actualizado = True
            if actualizado:
                salida = doc.as_string().encode("utf-8")
                if b"\r\n" in antes and b"\n" not in antes.replace(b"\r\n", b""):
                    salida = salida.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                if tomllib.loads(salida.decode("utf-8")) != original:
                    raise ValueError("No se pudieron conservar las demás opciones del archivo.")
                _reemplazar(ruta, antes, salida)
            return con_defaults(original)
    except Timeout as exc:
        raise OSError("Se están guardando otros ajustes. Vuelve a intentarlo.") from exc


def _reemplazar(ruta: Path, antes: bytes, salida: bytes) -> None:
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=ruta.parent, delete=False, suffix=".tmp") as f:
            tmp = Path(f.name)
            f.write(salida)
            f.flush()
            os.fsync(f.fileno())
        if (ruta.read_bytes() if ruta.exists() else b"") != antes:
            raise OSError("La configuración cambió mientras se guardaba. Vuelve a intentarlo.")
        os.replace(tmp, ruta)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _voz_disponible(ruta: Path) -> bool:
    return (ruta.suffix.lower() == ".onnx" and ruta.is_file()
            and ruta.with_suffix(".onnx.json").is_file())


def _modelos_stt(actual: str) -> list[str]:
    from huggingface_hub.constants import HF_HUB_CACHE

    def completo(p: Path) -> bool:
        return all((p / nombre).is_file() for nombre in ("model.bin", "config.json", "tokenizer.json"))

    modelos = set()
    if completo(ruta_proyecto(actual)):
        modelos.add(actual)
    # Whisper consulta la referencia «main» en modo offline. Omitimos descargas
    # parciales y revisiones huérfanas que requerirían una conexión al arrancar.
    for repo in Path(HF_HUB_CACHE).glob("models--*--*"):
        try:
            revision = (repo / "refs" / "main").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or not completo(repo / "snapshots" / revision):
            continue
        _, autor, identificador = repo.name.split("--", maxsplit=2)
        if autor == "Systran" and identificador.startswith("faster-whisper-"):
            nombre = identificador.removeprefix("faster-whisper-")
        elif "whisper" in identificador.lower():
            nombre = f"{autor}/{identificador}"
        else:
            continue
        modelos.add(nombre)
        if f"{autor}/{identificador}" == actual:
            modelos.add(actual)
    return sorted(modelos)


def _ollama_local(cfg: dict) -> bool:
    try:
        return urlsplit(cfg["llm"]["url"]).hostname in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        return False


def _modelos_ollama(cfg: dict) -> list[str]:
    if not _ollama_local(cfg):
        return []
    raiz = Path(os.getenv("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")
    manifiestos = raiz / "manifests"
    modelos = set()
    for archivo in manifiestos.glob("*/*/*/*"):
        try:
            datos = json.loads(archivo.read_bytes())
            capas = [datos["config"], *datos["layers"]]
            if not datos["layers"] or not all(
                re.fullmatch(r"sha256:[0-9a-f]{64}", c["digest"])
                and (raiz / "blobs" / c["digest"].replace(":", "-")).is_file() for c in capas
            ):
                continue
        except (KeyError, TypeError, ValueError, OSError):
            continue
        registro, espacio, modelo, etiqueta = archivo.relative_to(manifiestos).parts
        if registro != "registry.ollama.ai":
            modelo = f"{registro}/{espacio}/{modelo}"
        elif espacio != "library":
            modelo = f"{espacio}/{modelo}"
        modelos.add(modelo if etiqueta == "latest" and cfg["llm"]["modelo"] == modelo else f"{modelo}:{etiqueta}")
    return sorted(modelos)


def _modelos_conversacion(cfg: dict, instalados: list[str]) -> list[str]:
    busqueda = cfg["conocimiento"]["modelo_embeddings"].removesuffix(":latest")
    return [m for m in instalados if m.removesuffix(":latest") != busqueda]


def estado_servicios(cfg: dict) -> dict:
    """Inventario en disco. No inicia Ollama, el micrófono ni verificaciones remotas."""
    from .conocimiento import Conocimiento

    voz_actual = cfg["voz"]["modelo_piper"]
    voces = {p.relative_to(RAIZ).as_posix() for p in (RAIZ / "voces").glob("*.onnx") if _voz_disponible(p)}
    if _voz_disponible(ruta_proyecto(voz_actual)):
        voces.add(voz_actual)
    stt = _modelos_stt(cfg["stt"]["modelo"])
    instalados = _modelos_ollama(cfg)
    llm = _modelos_conversacion(cfg, instalados)
    servicios = []

    def agregar(nombre, estado, detalle):
        servicios.append({"nombre": nombre, "estado": estado, "detalle": detalle})

    agregar("Voz · Piper", "En disco" if voz_actual in voces else "Falta el modelo", Path(voz_actual).stem)
    actual_stt = cfg["stt"]["modelo"]
    agregar("Reconocimiento · Whisper", "En disco" if actual_stt in stt else "Fuera de caché", actual_stt)
    actual_llm = cfg["llm"]["modelo"]
    estado_llm = "En disco" if actual_llm in llm else "Falta el modelo"
    agregar("Conversación · Ollama", estado_llm if _ollama_local(cfg) else "Servidor personalizado", actual_llm)
    puente = RAIZ / "asistente" / "acciones" / "pantalla_uia.ps1"
    control = os.name == "nt" and puente.is_file() and shutil.which("powershell.exe") and find_spec("win32gui")
    agregar("Control de pantalla", "Instalado" if control else "No disponible", "Controles accesibles de Windows")
    from .acciones.sistema import Apps
    apps = Apps()
    for nombre in ("ChatGPT", "Claude"):
        agregar(nombre, "En el índice" if apps.buscar(nombre) else "Fuera del índice",
                "Aplicación de escritorio para encargos")
    raiz = Conocimiento.desde_config(cfg).raiz
    agregar("Memoria · Markdown", "Carpeta local" if raiz.is_dir() else "Al guardar la primera nota", str(raiz))
    agregar("Obsidian", "Bóveda conectada" if (raiz / ".obsidian").is_dir() else "Carpeta Markdown", str(raiz))
    modelo = cfg["conocimiento"]["modelo_embeddings"]
    local = _ollama_local(cfg)
    disponible = any(m.removesuffix(":latest") == modelo.removesuffix(":latest") for m in instalados)
    if not local:
        estado_busqueda = "Requiere Ollama local"
    elif not disponible:
        estado_busqueda = "Falta el modelo"
    else:
        estado_busqueda = "Modelo en disco"
    semantica = local and disponible and cfg["conocimiento"]["busqueda_semantica"]
    metodo = "Palabras y significado" if semantica else "Búsqueda por palabras"
    agregar("Búsqueda en notas", estado_busqueda, f"{modelo} · {metodo}")
    gmail = cfg["gmail"]["habilitado"]
    token = any((DATOS / n).is_file() for n in ("google_token.json", "gmail_token.json"))
    agregar("Gmail", ("Autorización guardada" if token else "Por conectar") if gmail else "Desactivado",
            "Sincronización de correo")
    calendario = cfg["clementina"]
    sesion = calendario["modo"] == "sesion"
    preparado = (DATOS / "navegador-clementina").is_dir() if sesion else bool(calendario.get("url_ics"))
    agregar("Clementina", ("Configurado" if preparado else "Por configurar") if calendario["habilitado"]
            else "Desactivado", "Sesión del navegador" if sesion else "Calendario académico")
    return {"voces": sorted(voces), "modelos_stt": stt, "modelos_llm": llm, "servicios": servicios}
