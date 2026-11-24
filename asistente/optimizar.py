"""Ajuste automático: prueba combinaciones en ESTA laptop y deja las más rápidas en config.toml.

1. Graba dos frases con tu micrófono (como le hablas a Jarvis).
2. Reconocimiento de voz: Whisper small y base con distinto número de hilos.
3. Modelo de lenguaje: el actual y los de [llm] candidatos, en una charla corta de 4 turnos
   (mide cuánto tarda en decir la primera frase y revisa que responda bien).
4. Muestra el resumen y, si aceptas, guarda los cambios (con copia en config.toml.bak).
"""
from __future__ import annotations

import ctypes
import difflib
import json
import queue
import re
import sys
import threading
import time
import tomllib
import uuid

import requests

from .config import DATOS, RAIZ, cargar
from .texto import normalizar

FRASES = ["Jarvis, quiero que me digas qué hora es.",
          "Recuérdame mañana a las ocho revisar la tarea de la maestría."]
CONTEXTO = "Hoy es jueves 19 de noviembre, 01:40. Pendientes: 23 en Clementina, 15 correos sin leer."
CHARLA = [  # (pregunta, respuestas aceptables o None)
    ("Hola Jarvis, ¿cómo estás?", None),
    ("Dame un consejo corto para concentrarme estudiando de noche.", None),
    ("¿Cuánto es 15 por 12?", ("180", "ciento ochenta")),
    ("¿Y si ese resultado lo divido entre 4?", ("45", "cuarenta y cinco")),
]
MODOS_ENERGIA = {
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "máxima eficiencia energética",
    "00000000-0000-0000-0000-000000000000": "equilibrado",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "mejor rendimiento",
    "ded574b5-45a0-4f42-8737-46345c09c238": "máximo rendimiento",
}


# ---------------------------------------------------------------- energía de Windows

def estado_energia() -> tuple[str, bool]:
    """Texto como 'con batería (64 %) · modo equilibrado' y si eso puede frenar las pruebas."""
    if sys.platform != "win32":
        return "desconocida", False
    partes, lento = [], False

    class _Estado(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("bandera", ctypes.c_ubyte), ("porcentaje", ctypes.c_ubyte),
                    ("ahorro", ctypes.c_ubyte), ("restante", ctypes.c_ulong), ("total", ctypes.c_ulong)]

    try:
        e = _Estado()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(e)):
            if e.ac == 1:
                partes.append("enchufada")
            else:
                partes.append("con batería" + (f" ({e.porcentaje} %)" if e.porcentaje <= 100 else ""))
                lento = True
            if e.ahorro == 1:
                partes.append("ahorro de batería activado")
                lento = True
    except Exception:
        pass
    try:
        guid = (ctypes.c_ubyte * 16)()
        if ctypes.windll.powrprof.PowerGetEffectiveOverlayScheme(ctypes.byref(guid)) == 0:
            modo = MODOS_ENERGIA.get(str(uuid.UUID(bytes_le=bytes(guid))), "personalizado")
            partes.append(f"modo {modo}")
            lento = lento or modo == "máxima eficiencia energética"
    except Exception:
        pass
    return " · ".join(partes) or "desconocida", lento


# ---------------------------------------------------------------- Ollama

def _completo(nombre: str) -> str:
    return nombre if ":" in nombre else nombre + ":latest"


def instalados(url: str) -> set[str]:
    r = requests.get(f"{url}/api/tags", timeout=10)
    return {_completo(m["name"]) for m in r.json().get("models", [])}


def bajar_modelo(url: str, nombre: str) -> bool:
    """ollama pull con barra de progreso en la consola."""
    print(f"  Descargando {nombre} (solo la primera vez)...")
    try:
        with requests.post(f"{url}/api/pull", json={"model": nombre, "stream": True},
                           stream=True, timeout=(10, 900)) as r:
            ultimo = ""
            for linea in r.iter_lines():
                if not linea:
                    continue
                d = json.loads(linea)
                if "error" in d:
                    print(f"\n  No se pudo descargar {nombre}: {d['error']}")
                    return False
                total, hecho = d.get("total"), d.get("completed")
                if total and hecho is not None:
                    txt = f"    {hecho / 1e9:.2f} de {total / 1e9:.2f} GB ({100 * hecho / total:.0f} %)"
                else:
                    txt = f"    {d.get('status', '')}"
                if txt != ultimo:
                    print("\r" + txt.ljust(60), end="", flush=True)
                    ultimo = txt
                if d.get("status") == "success":
                    print()
                    return True
    except requests.RequestException as e:
        print(f"\n  No se pudo descargar {nombre}: {e}")
    return False


def asegurar_modelo(url: str, nombre: str) -> bool:
    try:
        if _completo(nombre) in instalados(url):
            print(f"Modelo de lenguaje {nombre}: ya está instalado.")
            return True
    except requests.RequestException:
        print("No pude hablar con Ollama: ábrelo (el icono de la llama) y vuelve a intentar.")
        return False
    return bajar_modelo(url, nombre)


# ---------------------------------------------------------------- audio

def niveles(audio, tasa: int = 16000) -> dict:
    """Pico, voz, ruido de fondo, relación voz/ruido (dB) y energía en agudos (banda ancha)."""
    import numpy as np
    marcos = audio[: audio.size // 480 * 480].reshape(-1, 480)
    rms = np.sqrt((marcos ** 2).mean(axis=1)) if marcos.size else np.zeros(1)
    ruido, voz = float(np.percentile(rms, 10)), float(np.percentile(rms, 95))
    espectro = np.abs(np.fft.rfft(audio)) ** 2
    f = np.fft.rfftfreq(audio.size, 1 / tasa)
    agudos = espectro[(f > 4200) & (f < 7800)].sum() / max(espectro[(f > 300) & (f < 3800)].sum(), 1e-12)
    return {"pico": float(np.abs(audio).max()) if audio.size else 0.0, "ruido": ruido, "voz": voz,
            "snr": 20 * np.log10(max(voz, 1e-6) / max(ruido, 1e-6)), "agudos": float(agudos)}


def avisos_microfono(n: dict, nombre_disp: str = "") -> list[str]:
    avisos = []
    if n["pico"] > 0.97:
        avisos.append("El micrófono satura: baja su volumen en Windows (Configuración > Sonido > Entrada) a ~70 %.")
    elif n["voz"] < 0.02:
        avisos.append("Te oye muy bajito: sube el volumen del micrófono en Windows o acércate.")
    if n["snr"] < 15:
        avisos.append("Mucho ruido de fondo para tu voz (ventilador, música, eco).")
    if n["agudos"] < 0.002 or any(p in nombre_disp.lower() for p in ("hands-free", "manos libres", "headset")):
        avisos.append("Audio de banda estrecha: los audífonos Bluetooth usados como micrófono suenan como "
                      "teléfono y Whisper entiende mucho peor. Prueba con el micrófono de la laptop.")
    return avisos


def recortar_como_en_vivo(cfg_escucha: dict, audio) -> list:
    """Pasa una grabación por el mismo detector de voz que usa Jarvis en vivo."""
    import numpy as np
    from .stt import MUESTRAS_MARCO, TASA, Oido
    oido = Oido(cfg_escucha)
    threading.Thread(target=oido._segmentar, daemon=True).start()
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    pcm = np.concatenate([pcm, np.zeros(TASA, dtype=np.int16)])
    for i in range(0, pcm.size - MUESTRAS_MARCO + 1, MUESTRAS_MARCO):
        oido._crudo.put((pcm[i:i + MUESTRAS_MARCO].tobytes(), time.perf_counter()))
    time.sleep(0.3)
    trozos = []
    while True:
        try:
            trozos.append(oido.frases.get(timeout=1.0)[0])
        except queue.Empty:
            return trozos


def grabar(frase: str, segundos: float = 5.0):
    import sounddevice as sd
    from .stt import TASA
    input(f"\n  Presiona Enter y di con tu voz normal:\n     «{frase}»\n")
    print("  Grabando...", end="", flush=True)
    a = sd.rec(int(segundos * TASA), samplerate=TASA, channels=1, dtype="float32")
    sd.wait()
    print(" listo.")
    return a[:, 0].copy()


def voz_sintetica(cfg: dict) -> list:
    """Si no hay micrófono: Piper dice las frases (sirve para medir velocidad, no tu micrófono)."""
    import numpy as np
    from .stt import TASA
    from .tts import Voz
    voz = Voz.desde_config(cfg["voz"])
    clips = []
    for frase in FRASES:
        x = voz.sintetizar(frase).astype(np.float32) / 32768
        dur = x.size / voz.tasa
        clips.append(np.interp(np.arange(0, dur, 1 / TASA), np.arange(x.size) / voz.tasa, x).astype(np.float32))
    return clips


def similitud(a: str, b: str) -> float:
    """Parecido por palabras (una palabra mal cambia el sentido: «qué hora es» ≠ «que yo le he»)."""
    return difflib.SequenceMatcher(None, normalizar(a).split(), normalizar(b).split()).ratio()


# ---------------------------------------------------------------- pruebas

def probar_reconocimiento(cfg: dict, clips: list, dato) -> dict | None:
    from .stt import Transcriptor

    def medir(modelo: str, hilos: int, n: int) -> dict | None:
        try:
            tr = Transcriptor(dict(cfg["stt"], modelo=modelo, hilos=hilos))
        except Exception as e:
            dato(f"  {modelo} con {hilos} hilos: no se pudo cargar ({e})")
            return None
        tiempos, aciertos, textos = [], [], []
        for audio, ref in list(zip(clips, FRASES))[:n]:
            t = time.perf_counter()
            txt = tr.transcribir(audio)
            tiempos.append(time.perf_counter() - t)
            textos.append(txt)
            aciertos.append(similitud(txt, ref))
        r = {"modelo": modelo, "hilos": hilos, "tiempo": sum(tiempos) / len(tiempos),
             "acierto": sum(aciertos) / len(aciertos), "textos": textos}
        dato(f"  {modelo:<5} · {hilos} hilos: {r['tiempo']:.2f}s · acierto {r['acierto']:.0%} · "
             + " | ".join(f"«{t}»" for t in textos))
        return r

    print("  (probando el número de hilos con el modelo small...)")
    barrido = [r for h in (2, 4, 6, 8) if (r := medir("small", h, 1))]
    if not barrido:
        return None
    hilos = min(barrido, key=lambda r: r["tiempo"])["hilos"]
    print(f"  (comparando small y base con {hilos} hilos en las dos frases...)")
    small = medir("small", hilos, len(clips))
    base = medir("base", hilos, len(clips))
    disponibles = [r for r in (small, base) if r is not None]
    return max(disponibles, key=lambda r: (r["acierto"], -r["tiempo"]), default=None)


def _parece_espanol(t: str) -> bool:
    es = {"de", "que", "el", "la", "es", "y", "en", "un", "una", "para", "con", "te", "lo", "los",
          "las", "por", "tu", "mas", "muy", "estoy", "bien", "puedes", "hola", "si", "no"}
    en = {"the", "and", "is", "you", "to", "of", "for", "with", "are", "it", "this", "that"}
    palabras = normalizar(t).split()
    return sum(w in es for w in palabras) >= sum(w in en for w in palabras)


def probar_charla(cfg: dict, modelo: str, hilos: int, dato) -> dict | None:
    """Charla de 4 turnos con el mismo código que usa Jarvis (Cerebro)."""
    from .brain import Cerebro
    url = cfg["llm"]["url"].rstrip("/")
    cer = Cerebro(dict(cfg["llm"], modelo=modelo, hilos=hilos), cfg["usuario"]["nombre"])
    etiqueta = modelo + (f" con {hilos} hilos" if hilos else "")
    turnos: list[dict] = []
    try:
        t = time.perf_counter()
        requests.post(f"{url}/api/generate", json={"model": modelo, "prompt": "", "keep_alive": "10m",
                                                   "options": cer._opciones_carga()}, timeout=300).raise_for_status()
        carga = time.perf_counter() - t
        dato(f"  {etiqueta} (cargó en {carga:.1f}s):")
        for _ in cer.frases("hola", contexto=""):  # Jarvis hace esto al arrancar
            pass
        cer.olvidar()
        for pregunta, esperado in CHARLA:
            t = time.perf_counter()
            primera, partes = None, []
            for frase in cer.frases(pregunta, CONTEXTO):
                if primera is None:
                    primera = time.perf_counter() - t
                partes.append(frase)
            texto = " ".join(partes)
            ok = bool(texto) and (esperado is None or any(e in texto.lower() for e in esperado))
            m = cer.medicion
            turnos.append({"primera": primera if primera is not None else time.perf_counter() - t,
                           "leidos": m.get("prompt_tokens", 0), "tok_s": m.get("tok_s", 0.0),
                           "ok": ok, "texto": texto})
            x = turnos[-1]
            dato(f"    {'✓' if ok else '✗'} 1.ª frase {x['primera']:.1f}s · leyó {x['leidos']} tokens · "
                 f"{x['tok_s']:.1f} tok/s · «{texto[:80]}»")
    except Exception as e:
        dato(f"  {etiqueta}: falló ({e})")
        return None
    finally:
        try:  # libera la memoria para que no afecte al siguiente
            requests.post(f"{url}/api/generate", json={"model": modelo, "keep_alive": 0}, timeout=60)
        except requests.RequestException:
            pass
    resto = turnos[1:]
    res = {"modelo": modelo, "hilos": hilos, "carga": carga,
           "primera": sum(x["primera"] for x in resto) / len(resto),
           "tok_s": sum(x["tok_s"] for x in turnos) / len(turnos),
           "calidad": sum(1 for x, (_, e) in zip(turnos, CHARLA) if e and x["ok"]),
           "espanol": all(_parece_espanol(x["texto"]) for x in turnos)}
    dato(f"  ⇒ {etiqueta}: primera frase en {res['primera']:.1f}s de promedio · {res['tok_s']:.1f} tok/s · "
         f"cuentas bien {res['calidad']}/2" + ("" if res["espanol"] else " · ⚠ respondió en inglés"))
    return res


# ---------------------------------------------------------------- config.toml

_SECCION = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")


def _valor_toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def _separar_comentario(resto: str) -> tuple[str, str]:
    """'"x"   # nota' → ('"x"', '   # nota'), sin confundir un # dentro de comillas."""
    comilla, i = None, 0
    while i < len(resto):
        c = resto[i]
        if comilla:
            if c == "\\" and comilla == '"':
                i += 2
                continue
            if c == comilla:
                comilla = None
        elif c in "\"'":
            comilla = c
        elif c == "#":
            j = i
            while j > 0 and resto[j - 1] in " \t":
                j -= 1
            return resto[:j], resto[j:]
        i += 1
    return resto, ""


def poner_clave(texto: str, seccion: str, clave: str, valor, nota: str | None = None) -> str:
    """Cambia (o agrega) UNA clave dentro de UNA sección, línea por línea, sin tocar lo demás.

    `nota` reemplaza el comentario de esa línea (si no, se conserva el que tenía)."""
    fin = "\r\n" if "\r\n" in texto else "\n"
    lineas = texto.splitlines(keepends=True)
    actual, cabecera, ultima = None, None, None
    for i, linea in enumerate(lineas):
        cuerpo = linea.rstrip("\r\n")
        eol = linea[len(cuerpo):]
        m = _SECCION.match(cuerpo)
        if m:
            if actual == seccion:
                break  # ya pasamos la sección sin encontrar la clave
            actual = m.group(1).strip()
            if actual == seccion:
                cabecera = ultima = i
            continue
        if actual != seccion:
            continue
        if cuerpo.strip() and not cuerpo.lstrip().startswith("#"):
            ultima = i
        mk = re.match(rf"^(\s*){re.escape(clave)}(\s*=\s*)(.*)$", cuerpo)
        if mk:
            _, comentario = _separar_comentario(mk.group(3))
            if nota:
                comentario = f"   # {nota}"
            lineas[i] = f"{mk.group(1)}{clave}{mk.group(2)}{_valor_toml(valor)}{comentario}{eol or fin}"
            return "".join(lineas)
    nueva = f"{clave} = {_valor_toml(valor)}" + (f"   # {nota}" if nota else "") + fin
    if cabecera is None:  # la sección no existe: se agrega al final
        if lineas and not lineas[-1].endswith("\n"):
            lineas[-1] += fin
        return "".join(lineas) + f"{fin}[{seccion}]{fin}{nueva}"
    if not lineas[ultima].endswith("\n"):
        lineas[ultima] += fin
    lineas.insert(ultima + 1, nueva)
    return "".join(lineas)


def aplicar_cambios(cambios: list[tuple]) -> str:
    """Escribe los cambios en config.toml (copia en config.toml.bak). Verifica antes de escribir."""
    ruta = RAIZ / "config.toml"
    with open(ruta, encoding="utf-8", newline="") as f:
        antes = f.read()
    despues = antes
    for seccion, clave, valor, *nota in cambios:
        despues = poner_clave(despues, seccion, clave, valor, *nota)
    esperado = tomllib.loads(antes)
    for seccion, clave, valor, *_ in cambios:
        esperado.setdefault(seccion, {})[clave] = valor
    if tomllib.loads(despues) != esperado:
        raise ValueError("el cambio habría tocado otras partes del archivo; no lo guardé")
    with open(ruta.with_name("config.toml.bak"), "w", encoding="utf-8", newline="") as f:
        f.write(antes)
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write(despues)
    return str(ruta)


# ---------------------------------------------------------------- flujo

def ejecutar() -> None:
    cfg = cargar()
    lineas: list[str] = []

    def dato(t: str = "") -> None:
        print(t)
        lineas.append(t)

    def guardar() -> None:
        (DATOS / "optimizar.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")

    interactivo = sys.stdin is not None and sys.stdin.isatty()
    energia, lento = estado_energia()
    dato(f"Fecha: {time.strftime('%Y-%m-%d %H:%M')} · energía: {energia}")
    if lento and interactivo:
        print("\n  ⚠ Con batería o en modo ahorro todo va más lento y la prueba saldría peor de lo real.\n"
              "    Conecta el cargador y pon Configuración > Sistema > Energía > Modo de energía:\n"
              "    'Máximo rendimiento'. Luego presiona Enter (o Enter directo para seguir así).")
        input()
        energia, lento = estado_energia()
        dato(f"Energía ahora: {energia}")
    try:
        from .acciones.sistema import memoria, procesos_memoria
        usado, total, pct = memoria()
        dato(f"Memoria: {pct} % en uso ({usado:.1f} de {total:.1f} GB)")
        if pct >= 80 and interactivo:
            top = [f"{n} ({mb / 1024:.1f} GB)" for n, mb in procesos_memoria(6) if mb >= 200
                   and not n.startswith(("Jarvis", "Ollama"))]
            print("\n  ⚠ Tienes la memoria casi llena: las pruebas saldrían lentas por eso, no por Jarvis.\n"
                  + (f"    Lo que más ocupa: {', '.join(top[:4])}.\n" if top else "")
                  + "    Cierra lo que no estés usando y presiona Enter (o Enter directo para seguir así).")
            input()
            usado, total, pct = memoria()
            dato(f"Memoria ahora: {pct} % en uso")
    except Exception:
        pass
    cambios: list[tuple] = []
    nota = f"elegido por optimizar.bat el {time.strftime('%d/%m')}"
    resumen: list[str] = []

    print("\n== 1. Tu voz ==")
    clips: list = []
    if interactivo:
        try:
            import numpy as np
            import sounddevice as sd
            disp = sd.query_devices(kind="input")
            dato(f"Micrófono: {disp['name']}")
            cortes = 0
            for frase in FRASES:
                audio = grabar(frase)
                n = niveles(audio)
                dato(f"  nivel: voz {n['voz']:.3f} · ruido {n['ruido']:.4f} · voz/ruido {n['snr']:.0f} dB")
                for aviso in avisos_microfono(n, disp["name"]):
                    dato(f"  ⚠ {aviso}")
                trozos = recortar_como_en_vivo(cfg["escucha"], audio)
                if len(trozos) > 1:
                    cortes += 1
                    dato(f"  ⚠ Jarvis te habría cortado en {len(trozos)} partes.")
                silencio = np.zeros(3200, dtype=np.float32)
                clips.append(np.concatenate([x for t in trozos for x in (t, silencio)]) if trozos else audio)
                if not trozos:
                    dato("  ⚠ No detectó voz en esa grabación; uso la grabación completa.")
            if cortes and cfg["escucha"]["silencio_ms"] < 1000:
                nuevo = min(1000, cfg["escucha"]["silencio_ms"] + 200)
                cambios.append(("escucha", "silencio_ms", nuevo))
                resumen.append(f"Pausa para cortar tu frase: {cfg['escucha']['silencio_ms']} → {nuevo} ms")
        except Exception as e:
            dato(f"No pude usar el micrófono ({e}); sigo con voz sintética.")
            clips = []
    if not clips:
        try:
            clips = voz_sintetica(cfg)
            dato("Usando voz sintética (mide velocidad, no la calidad de tu micrófono).")
        except Exception as e:
            dato(f"Tampoco pude generar voz sintética: {e}")
    guardar()

    print("\n== 2. Reconocimiento de voz ==")
    if clips:
        elegido = probar_reconocimiento(cfg, clips, dato)
        if elegido:
            dato(f"⇒ Elegido: Whisper {elegido['modelo']} con {elegido['hilos']} hilos "
                 f"({elegido['tiempo']:.1f}s, acierto {elegido['acierto']:.0%})")
            if elegido["modelo"] != cfg["stt"]["modelo"]:
                cambios.append(("stt", "modelo", elegido["modelo"], nota))
            if elegido["hilos"] != cfg["stt"]["hilos"]:
                cambios.append(("stt", "hilos", elegido["hilos"]))
            resumen.append(f"Reconocimiento: {cfg['stt']['modelo']} con {cfg['stt']['hilos']} hilos → "
                           f"{elegido['modelo']} con {elegido['hilos']} hilos")
    guardar()

    print("\n== 3. Modelo de lenguaje ==")
    url = cfg["llm"]["url"].rstrip("/")
    try:
        ya = instalados(url)
    except requests.RequestException:
        ya = None
        dato("Ollama no responde: ábrelo y vuelve a correr optimizar.bat para esta parte.")
    if ya is not None:
        actual = cfg["llm"]["modelo"]
        modelos = [actual] + [m for m in cfg["llm"]["candidatos"] if _completo(m) != _completo(actual)]
        listos = [m for m in modelos if _completo(m) in ya or bajar_modelo(url, m)]
        resultados = [r for m in listos if (r := probar_charla(cfg, m, cfg["llm"]["hilos"], dato))]
        guardar()
        if resultados:
            buenos = [r for r in resultados if r["calidad"] >= 1 and r["espanol"]] or resultados
            mejor = min(buenos, key=lambda r: r["primera"])
            del_actual = next((r for r in buenos if r["modelo"] == actual), None)
            if del_actual and del_actual["primera"] <= mejor["primera"] * 1.15:
                mejor = del_actual  # no cambies de modelo por casi nada
            print(f"\n  (probando hilos para {mejor['modelo']}...)")
            for h in (4, 8):
                if h != mejor["hilos"] and (r := probar_charla(cfg, mejor["modelo"], h, dato)) \
                        and r["primera"] < mejor["primera"] * 0.9:
                    mejor = r
            dato(f"⇒ Elegido: {mejor['modelo']}" + (f" con {mejor['hilos']} hilos" if mejor["hilos"] else "")
                 + f" (primera frase en {mejor['primera']:.1f}s)")
            if mejor["modelo"] != actual:
                cambios.append(("llm", "modelo", mejor["modelo"], nota))
            if mejor["hilos"] != cfg["llm"]["hilos"]:
                cambios.append(("llm", "hilos", mejor["hilos"]))
            resumen.append(f"Modelo: {actual} → {mejor['modelo']}"
                           + (f" ({mejor['hilos']} hilos)" if mejor["hilos"] else ""))
    guardar()

    print("\n== Resumen ==")
    for r in resumen:
        dato("  " + r)
    if not cambios:
        dato("Tu configuración ya era la mejor de las probadas; no cambié nada.")
    elif not interactivo or input("\n¿Guardo estos ajustes en config.toml? [S/n] ").strip().lower() not in ("n", "no"):
        try:
            dato(f"Guardado en {aplicar_cambios(cambios)} (copia anterior en config.toml.bak).")
        except Exception as e:
            dato(f"No pude guardar los ajustes: {e}")
    else:
        dato("No guardé nada.")
    guardar()
    print(f"\nDetalle en {DATOS / 'optimizar.txt'}. Abre iniciar.bat para usar Jarvis.")
