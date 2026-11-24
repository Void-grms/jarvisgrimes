"""Uso:
    python -m asistente                  # arranca el asistente (o despierta al que ya corre)
    python -m asistente sync             # revisa Gmail y Clementina una vez
    python -m asistente estado           # muestra lo que tiene guardado
    python -m asistente configurar       # abre el panel de escritorio
    python -m asistente conectar-google     # Gmail + Google Calendar
    python -m asistente conectar-whatsapp   # vincular WhatsApp Web (código QR)
    python -m asistente conectar-clementina
    python -m asistente latencia         # mide cuánto tarda cada pieza (sin micrófono)
    python -m asistente consola [--voz]  # háblale escribiendo (prueba comandos sin micrófono)
    python -m asistente diagnostico      # mide modelo, voz y micrófono (datos/diagnostico.txt)
    python -m asistente optimizar        # prueba combinaciones y guarda las más rápidas
    python -m asistente preparar         # descarga los modelos que pide config.toml
"""
from __future__ import annotations

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

# Avisos de Hugging Face que asustan y no importan (symlinks en Windows, token opcional).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

from .config import DATOS, cargar


def _logs(consola: bool = True) -> None:
    handlers: list[logging.Handler] = [
        RotatingFileHandler(DATOS / "asistente.log", maxBytes=1_000_000, backupCount=2,
                            encoding="utf-8")]
    if consola and sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for ruidoso in ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3", "huggingface_hub",
                    "faster_whisper", "filelock"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def cmd_run() -> None:
    from .app import Asistente, avisar_instancia_existente
    if avisar_instancia_existente():
        logging.info("Ya estaba corriendo: le pedí que salude.")
        return
    from .store import cargar_preferencias
    cfg = cargar_preferencias()
    try:
        from .interfaz import ejecutar_con_widget
        asistente: dict = {}

        def trabajo(estado):
            asistente["a"] = Asistente(estado, cfg)
            asistente["a"].correr()

        ejecutar_con_widget(trabajo, {"clic": lambda: asistente["a"].clic() if "a" in asistente else None},
                            cfg, oculto="--sin-widget" in sys.argv)
        return
    except ImportError as e:
        logging.warning("Sin interfaz (%s). Ejecuta reparar.bat para instalar las dependencias.", e)
    Asistente(cfg=cfg).correr()


def cmd_configurar() -> None:
    from .app import avisar_instancia_existente
    if avisar_instancia_existente(b"panel"):
        return
    from PySide6.QtWidgets import QApplication
    from .centro import Centro, icono
    from .store import cargar_preferencias
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Jarvis")
    app.setWindowIcon(icono())
    centro = Centro(cargar_preferencias())
    centro.mostrar()
    app.exec()


def cmd_preparar() -> None:
    """Descarga de antemano los modelos que pide config.toml (con barra de progreso)."""
    cfg = cargar()
    nombre = cfg["stt"]["modelo"]
    print(f"Reconocimiento de voz: Whisper {nombre} (la primera vez descarga entre 0,1 y 0,5 GB)")
    try:
        from faster_whisper.utils import _MODELS
        from huggingface_hub import snapshot_download
        snapshot_download(_MODELS.get(nombre, nombre), allow_patterns=[
            "config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"])
    except Exception as e:
        print(f"  (la descarga con progreso falló: {e}; pruebo la normal, sin barra)")
        from faster_whisper import WhisperModel
        WhisperModel(nombre, device="cpu", compute_type="int8")
    print("  Listo.")
    from .optimizar import asegurar_modelo
    asegurar_modelo(cfg["llm"]["url"].rstrip("/"), cfg["llm"]["modelo"])


def cmd_optimizar() -> None:
    from .optimizar import ejecutar
    ejecutar()


FRASE_PRUEBA = "Jarvis, quiero que me digas qué hora es."


def _medir_ollama(cfg: dict, dato) -> None:
    import requests
    url, modelo = cfg["llm"]["url"].rstrip("/"), cfg["llm"]["modelo"]
    dato("Ollama " + requests.get(f"{url}/api/version", timeout=5).json().get("version", "?"))
    info = requests.post(f"{url}/api/show", json={"model": modelo}, timeout=20).json()
    det = info.get("details", {})
    dato(f"Modelo {modelo}: tamaño={det.get('parameter_size')} cuantización={det.get('quantization_level')} "
         f"familia={det.get('family')} capacidades={info.get('capabilities')}")
    instalados = [m["name"] for m in requests.get(f"{url}/api/tags", timeout=5).json().get("models", [])]
    dato(f"Modelos instalados: {', '.join(instalados) or '(ninguno)'}")
    for nombre in [modelo] + [m for m in instalados if m != modelo][:3]:
        for intento in (1, 2):  # el 2.º intento ya no incluye la carga a memoria
            cuerpo = {"model": nombre, "stream": False, "think": False, "keep_alive": "5m",
                      "messages": [{"role": "user", "content": "Responde en una frase: ¿qué es la fotosíntesis?"}],
                      "options": {"num_predict": 60, "num_ctx": cfg["llm"]["num_ctx"]}}
            t = time.perf_counter()
            r = requests.post(f"{url}/api/chat", json=cuerpo, timeout=300)
            if r.status_code == 400:  # modelos que no aceptan "think"
                cuerpo.pop("think")
                r = requests.post(f"{url}/api/chat", json=cuerpo, timeout=300)
            d = r.json()
            if "error" in d:
                dato(f"[{nombre}] error: {d['error']}")
                break
            s = 1e9
            n_eval, t_eval = d.get("eval_count", 0), max(d.get("eval_duration", 0) / s, 1e-6)
            msg = d.get("message") or {}
            dato(f"[{nombre} #{intento}] total {time.perf_counter() - t:.1f}s · carga {d.get('load_duration', 0) / s:.1f}s"
                 f" · leer pregunta {d.get('prompt_eval_duration', 0) / s:.2f}s"
                 f" · escribir {n_eval} tokens a {n_eval / t_eval:.1f} tok/s"
                 f" · pensó {len(msg.get('thinking') or '')} caracteres · «{(msg.get('content') or '')[:70]}»")
    for m in requests.get(f"{url}/api/ps", timeout=5).json().get("models", []):
        dato(f"En memoria: {m.get('name')} ocupa {m.get('size', 0) / 1e9:.1f} GB (en GPU {m.get('size_vram', 0) / 1e9:.1f} GB)")


def _probar_microfono(cfg: dict, tr, dato) -> None:
    import sounddevice as sd
    from .optimizar import avisos_microfono, niveles, recortar_como_en_vivo
    from .stt import TASA
    disp = sd.query_devices(kind="input")
    dato(f"Micrófono: {disp['name']} ({disp['default_samplerate']:.0f} Hz)")
    input(f"\nPRUEBA DE MICRÓFONO: presiona Enter y luego di, como le hablarías a Jarvis:\n   «{FRASE_PRUEBA}»\n")
    print("Grabando 6 segundos...")
    grab = sd.rec(int(6 * TASA), samplerate=TASA, channels=1, dtype="float32")
    sd.wait()
    grab = grab[:, 0].copy()
    n = niveles(grab, TASA)
    dato(f"Nivel: pico {n['pico']:.2f} · voz {n['voz']:.3f} · ruido de fondo {n['ruido']:.4f} · "
         f"voz/ruido {n['snr']:.0f} dB · agudos {n['agudos']:.4f}")
    for aviso in avisos_microfono(n, disp["name"]):
        dato(f"  ⚠ {aviso}")
    t = time.perf_counter()
    texto = tr.transcribir(grab)
    dato(f"Whisper (grabación completa): «{texto}» en {time.perf_counter() - t:.2f}s")
    # Mismo recorte que hace Jarvis en vivo (detector de voz + silencio_ms), para ver si te corta.
    trozos = recortar_como_en_vivo(cfg["escucha"], grab)
    if not trozos:
        dato("Recorte en vivo: no detectó voz (¿hablaste? ¿micrófono correcto?).")
    for k, a in enumerate(trozos, 1):
        t = time.perf_counter()
        dato(f"Recorte en vivo {k}/{len(trozos)} ({a.size / TASA:.1f}s): «{tr.transcribir(a)}» en {time.perf_counter() - t:.2f}s")


def cmd_diagnostico() -> None:
    """Mide modelo, voz y micrófono en esta PC; guarda el resultado en datos/diagnostico.txt."""
    import os
    import platform
    cfg = cargar()
    lineas = [f"Fecha: {time.strftime('%Y-%m-%d %H:%M')}", f"Equipo: {platform.platform()} · {os.cpu_count()} hilos",
              f"Config: modelo {cfg['llm']['modelo']} · Whisper {cfg['stt']['modelo']} ({cfg['stt']['hilos']} hilos)"
              f" · silencio_ms {cfg['escucha']['silencio_ms']} · VAD {cfg['escucha']['agresividad_vad']}"]

    def dato(t: str) -> None:
        print(t)
        lineas.append(t)

    def guardar() -> None:
        (DATOS / "diagnostico.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print("\n== Modelo de lenguaje (Ollama) ==")
    try:
        _medir_ollama(cfg, dato)
    except Exception as e:
        dato(f"Error con Ollama: {e}")
    guardar()
    print("\n== Voz ==")
    tr = None
    try:
        import numpy as np
        from .stt import TASA, Transcriptor
        from .tts import Voz
        t = time.perf_counter()
        tr = Transcriptor(cfg["stt"])
        dato(f"Whisper {cfg['stt']['modelo']}: cargó en {time.perf_counter() - t:.1f}s")
        voz = Voz.desde_config(cfg["voz"])
        t = time.perf_counter()
        pcm = voz.sintetizar(FRASE_PRUEBA)
        dur = pcm.size / voz.tasa
        dato(f"Piper: {time.perf_counter() - t:.2f}s para generar {dur:.1f}s de voz")
        x = pcm.astype(np.float32) / 32768
        limpio = np.interp(np.arange(0, dur, 1 / TASA), np.arange(x.size) / voz.tasa, x).astype(np.float32)
        t = time.perf_counter()
        dato(f"Whisper con voz limpia: «{tr.transcribir(limpio)}» en {time.perf_counter() - t:.2f}s")
    except Exception as e:
        dato(f"Error con la voz: {e}")
    guardar()
    if tr is not None and sys.stdin is not None and sys.stdin.isatty():
        print("\n== Micrófono ==")
        try:
            _probar_microfono(cfg, tr, dato)
        except Exception as e:
            dato(f"Error con el micrófono: {e}")
    guardar()
    print(f"\nListo. Guardado en {DATOS / 'diagnostico.txt'}")


def cmd_sync() -> None:
    from .store import Store
    from .sync import Sincronizador
    errores = Sincronizador(cargar(), Store()).sincronizar()
    print("OK" if not errores else f"Errores: {errores}")
    cmd_estado()


def cmd_estado() -> None:
    from .router import Router
    from .store import Store
    cfg = cargar()
    r = Router(Store(), cfg["usuario"]["zona_horaria"])
    print(r.responder("pendientes"))


def cmd_google() -> None:
    from .acciones import google
    print("Se abrirá tu navegador para dar permiso a Gmail (solo lectura) y Google Calendar.")
    print("Antes, en Google Cloud activa también la 'Google Calendar API' (ver README).\n")
    google.conectar()
    print("Google conectado: Gmail y Calendar.")


def cmd_whatsapp() -> None:
    from .acciones.navjarvis import NavegadorJarvis
    from .acciones.whatsapp import WhatsApp
    print("Se abrirá WhatsApp Web en el navegador de Jarvis.")
    print("En tu celular: WhatsApp → Dispositivos vinculados → Vincular un dispositivo, y escanea el código.\n")
    ok = WhatsApp(NavegadorJarvis(cargar()["navegador_jarvis"]["canal"])).vincular(180)
    print("Listo: WhatsApp vinculado." if ok else "No se completó la vinculación.")


def cmd_clementina() -> None:
    import re
    import webbrowser
    from zoneinfo import ZoneInfo
    from .collectors import clementina
    from .config import RAIZ

    cfg = cargar()
    if cfg["clementina"]["modo"] == "sesion":
        clementina.conectar_sesion(cfg["clementina"])
        return
    print("Se abrirá Clementina → Exportar calendario.")
    print("  1) En 'Eventos a exportar' elige: Todos los eventos")
    print("  2) En 'Por un periodo de tiempo' elige: Eventos recientes y próximos")
    print("  3) Clic en 'Obtener URL del calendario' y copia el enlace que aparece.\n")
    webbrowser.open(f"{clementina.BASE}/calendar/export.php")
    url = input("Pega aquí el enlace y presiona Enter: ").strip()
    if "export_execute.php" not in url or "authtoken=" not in url:
        print("Ese no parece el enlace correcto (debe contener export_execute.php y authtoken).")
        return
    cfg["clementina"]["url_ics"] = url
    items = clementina.recolectar_ics(cfg["clementina"], ZoneInfo(cfg["usuario"]["zona_horaria"]))
    ruta = RAIZ / "config.toml"
    texto = ruta.read_text(encoding="utf-8")
    texto, n = re.subn(r'(?m)^url_ics\s*=.*$', f'url_ics = "{url}"', texto)
    if not n:
        texto += f'\n[clementina]\nurl_ics = "{url}"\n'
    ruta.write_text(texto, encoding="utf-8")
    print(f"\nListo. Encontré {len(items)} eventos próximos. Por ejemplo:")
    for i in items[:5]:
        print(f"  - [{i.tipo}] {i.titulo} — {i.curso}")


def cmd_latencia() -> None:
    from .brain import Cerebro
    from .tts import Voz
    import numpy as np
    from .stt import Transcriptor

    cfg = cargar()
    print("Cargando modelos...")
    t = time.perf_counter(); voz = Voz.desde_config(cfg["voz"]); print(f"  voz: {time.perf_counter()-t:.1f}s")
    t = time.perf_counter(); stt = Transcriptor(cfg["stt"]); print(f"  oído: {time.perf_counter()-t:.1f}s")
    t = time.perf_counter(); cer = Cerebro(cfg["llm"], cfg["usuario"]["nombre"]); cer.precalentar(); print(f"  cerebro ({cfg['llm']['modelo']}): {time.perf_counter()-t:.1f}s")

    audio = voz.sintetizar("Hola Jarvis, ¿qué pendientes tengo para esta semana?")
    tasa = voz.tasa
    a16 = np.interp(np.linspace(0, len(audio), int(len(audio) * 16000 / tasa)),
                    np.arange(len(audio)), audio.astype(np.float32) / 32768.0).astype(np.float32)
    print("\nMidiendo (3 rondas):")
    for pregunta in ["¿Qué me recomiendas para concentrarme mejor?",
                     "Cuéntame algo interesante en una frase.",
                     "¿Cómo organizo mi tarde de estudio?"]:
        t0 = time.perf_counter()
        texto = stt.transcribir(a16)
        t_stt = time.perf_counter() - t0
        t1 = time.perf_counter()
        gen = cer.frases(pregunta, "")
        primera = next(gen)
        t_llm = time.perf_counter() - t1
        t2 = time.perf_counter()
        voz.sintetizar(primera)
        t_tts = time.perf_counter() - t2
        for _ in gen:
            pass
        total = 0.5 + t_stt + t_llm + t_tts  # +0.5 s de silencio para saber que terminaste
        print(f"  oído {t_stt:.2f}s + cerebro {t_llm:.2f}s + voz {t_tts:.2f}s + silencio 0.50s"
              f" = {total:.2f}s   «{primera[:50]}»")
    print(f"\n(Transcripción de prueba: «{texto}»)")
    print("Meta: ≤ 3 s. Si te pasas, en config.toml baja [stt] modelo a \"tiny\" o usa un LLM más chico.")


def cmd_consola() -> None:
    """Mismo cerebro y comandos que la versión de voz, pero escribiendo."""
    import threading
    from .brain import Cerebro
    from .motor import Motor

    from .store import cargar_preferencias
    motor = Motor(cargar_preferencias())
    def revisar_encargo(destino, pedido):
        from PySide6.QtWidgets import QApplication
        from .centro import Centro, icono
        app = QApplication.instance() or QApplication([])
        app.setWindowIcon(icono())
        centro = Centro(cargar())
        centro._abrir_encargo(destino, pedido)
        app.exec()
    motor.encargos.abrir_revision = revisar_encargo
    cerebro = Cerebro(motor.cfg["llm"], motor.nombre)
    threading.Thread(target=cerebro.precalentar, daemon=True).start()
    motor.preparar(cerebro)
    voz = None
    if "--voz" in sys.argv:
        from .tts import Voz
        voz = Voz.desde_config(motor.cfg["voz"])
        motor.comandos.conectar_voz(voz)

    def mostrar(r):
        if r is None:
            return
        motor.ejecutar(r)
        if r.accion:
            threading.Thread(target=r.accion, daemon=True).start()
        if r.texto:
            frases = [r.texto] if isinstance(r.texto, str) else list(r.texto)
            print("Jarvis:", " ".join(frases))
            if voz:
                voz.decir(frases)
        if r.despues:
            r.despues()
        motor.aplicar(r)

    print("Escribe lo que le dirías a Jarvis (salir = Ctrl+C).\n")
    mostrar(motor.saludo(forzar=True))
    while True:
        try:
            linea = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        while not motor.avisos.empty():
            mostrar(motor.avisos.get())
        mostrar(motor.expirado())
        if linea:
            mostrar(motor.entender(linea, forzar=True))
        time.sleep(0.05)
        while not motor.avisos.empty():
            mostrar(motor.avisos.get())


COMANDOS = {"configurar": cmd_configurar, "consola": cmd_consola, "run": cmd_run, "preparar": cmd_preparar, "diagnostico": cmd_diagnostico, "optimizar": cmd_optimizar, "sync": cmd_sync, "estado": cmd_estado,
            "conectar-gmail": cmd_google, "conectar-google": cmd_google, "conectar-whatsapp": cmd_whatsapp, "conectar-clementina": cmd_clementina,
            "latencia": cmd_latencia}


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        return
    cmd = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "run"
    if cmd not in COMANDOS:
        print(__doc__)
        sys.exit(1)
    _logs(consola=cmd not in ("consola",) and sys.stdout is not None)
    COMANDOS[cmd]()


if __name__ == "__main__":
    main()
