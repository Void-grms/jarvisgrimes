"""Micrófono → frases de voz (VAD) → texto (faster-whisper, en CPU)."""
from __future__ import annotations

import collections
import queue
import threading
import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions, get_speech_timestamps
from huggingface_hub.utils import LocalEntryNotFoundError

from .texto import normalizar

TASA = 16000
MARCO_MS = 30
MUESTRAS_MARCO = TASA * MARCO_MS // 1000  # 480


class Oido:
    """Escucha continua. Emite cada frase completa como array float32.

    `silenciado` se activa mientras el asistente habla. Si `interrumpir` está activo, mientras habla
    sigue escuchando: compara lo que oye el micrófono con lo que sale por el parlante y, si le hablas
    encima con claridad, llama a `al_interrumpir` (se calla) y graba lo que dices.
    """

    def __init__(self, cfg_escucha: dict, al_nivel=None, al_empezar=None):
        self.al_nivel = al_nivel        # volumen del micrófono 0..1 (para el widget)
        self.al_empezar = al_empezar    # se detectó que empezaste a hablar
        self.pausado = threading.Event()  # micrófono silenciado desde el widget
        self.vad = webrtcvad.Vad(cfg_escucha["agresividad_vad"])
        self.marcos_silencio = max(1, cfg_escucha["silencio_ms"] // MARCO_MS)
        self.max_marcos = 15000 // MARCO_MS  # 15 s máx por frase
        self.frases: queue.Queue[tuple[np.ndarray, float]] = queue.Queue()
        self.silenciado = threading.Event()
        self._crudo: queue.Queue[tuple[bytes, float]] = queue.Queue()
        self._stream: sd.RawInputStream | None = None
        # Interrupción por voz
        self.interrumpible = bool(cfg_escucha.get("interrumpir", True))
        self.sensibilidad = float(cfg_escucha.get("sensibilidad_interrupcion", 3.0))
        self.nivel_salida = None        # función: volumen que sale ahora por el parlante (la pone la app)
        self.al_interrumpir = None      # función: callarse
        self.eco: float | None = None   # cuánto de mi voz entra al micrófono (se calibra solo)

    def _callback(self, indata, frames, t, status):  # hilo de audio: hacer lo mínimo
        self._crudo.put((bytes(indata), time.perf_counter()))

    def _abrir_stream(self) -> None:
        self._stream = sd.RawInputStream(samplerate=TASA, blocksize=MUESTRAS_MARCO,
                                         channels=1, dtype="int16", callback=self._callback)
        self._stream.start()

    def iniciar(self) -> None:
        self._abrir_stream()
        threading.Thread(target=self._segmentar, daemon=True).start()

    def reiniciar(self) -> None:
        """Reabre el micrófono (tras suspender/reanudar o si cambió el dispositivo)."""
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        sd._terminate()
        sd._initialize()
        self._abrir_stream()

    def _segmentar(self) -> None:
        previo = collections.deque(maxlen=10)  # 300 ms antes de hablar, para no cortar la 1.ª sílaba
        voz: list[bytes] = []
        en_voz, silencio, activos = False, 0, collections.deque(maxlen=8)
        hablando_desde, razones, seguidos, forzado = None, [], 0, False
        while True:
            marco, fin = self._crudo.get()
            if len(marco) != MUESTRAS_MARCO * 2:
                continue
            if self.pausado.is_set():
                voz, en_voz, silencio, forzado, hablando_desde = [], False, 0, False, None
                previo.clear()
                activos.clear()
                continue
            muestras = np.frombuffer(marco, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(muestras * muestras))) / 32768.0
            if self.silenciado.is_set() and not forzado:
                voz, en_voz, silencio = [], False, 0
                activos.clear()
                if not (self.interrumpible and self.nivel_salida and self.al_interrumpir):
                    previo.clear()
                    continue
                # ---- ¿me está hablando encima? (doble habla) ----
                ahora = time.monotonic()
                ref = float(self.nivel_salida())
                if hablando_desde is None:
                    if ref <= 0.01:  # el modelo aún piensa o la reproducción carece de referencia
                        previo.clear()
                        continue
                    hablando_desde, razones, seguidos = ahora, [], 0
                    previo.clear()
                previo.append(marco)
                if ahora - hablando_desde < 0.6:  # primeros 0,6 s: mide cuánto eco hay
                    if ref > 0.01:
                        razones.append(rms / ref)
                    continue
                if razones:
                    med = float(np.median(razones))
                    self.eco = med if self.eco is None else 0.6 * self.eco + 0.4 * med
                    razones = []
                eco = self.eco if self.eco is not None else 0.5
                umbral = max(self.sensibilidad * eco * ref, 0.015)
                es_voz = self.vad.is_speech(marco, TASA)
                seguidos = seguidos + 1 if (es_voz and rms > umbral) else max(0, seguidos - 2)
                if seguidos >= 8 and self.al_interrumpir():  # ~240 ms y se detuvo el parlante
                    forzado, en_voz, voz, silencio = True, True, list(previo), 0
                    if self.al_empezar:
                        self.al_empezar()
                continue
            if hablando_desde is not None and not self.silenciado.is_set():
                # El prebúfer de interrupción contiene el final de la voz del asistente.
                # Una frase nueva debe empezar solo con muestras del usuario.
                previo.clear()
                activos.clear()
                hablando_desde = None
            if self.al_nivel:
                self.al_nivel(min(1.0, rms * 32768.0 / 6000))
            es_voz = self.vad.is_speech(marco, TASA)
            if not en_voz:
                previo.append(marco)
                activos.append(es_voz)
                if sum(activos) >= 5:  # ~150 ms de voz real en 240 ms
                    en_voz, voz, silencio = True, list(previo), 0
                    activos.clear()
                    if self.al_empezar:
                        self.al_empezar()
                continue
            voz.append(marco)
            silencio = 0 if es_voz else silencio + 1
            if silencio >= self.marcos_silencio or len(voz) >= self.max_marcos:
                audio = np.frombuffer(b"".join(voz), dtype=np.int16).astype(np.float32) / 32768.0
                if len(audio) > TASA * 0.35:  # descarta ruidos cortos
                    self.frases.put((audio, fin))
                voz, en_voz, silencio, forzado = [], False, 0, False
                previo.clear()


@dataclass(frozen=True)
class Transcripcion:
    estado: Literal["aceptada", "dudosa", "sin_voz"]
    texto: str = ""


class Transcriptor:
    def __init__(self, cfg_stt: dict):
        self.idioma = cfg_stt["idioma"]
        self.beam = cfg_stt.get("beam", 3)
        self.pistas = cfg_stt.get("pistas", "")
        self.prompt = cfg_stt.get("prompt_inicial", "") or None
        self.vad = VadOptions(threshold=0.4, min_speech_duration_ms=96)
        opciones = dict(device="cpu", compute_type="int8", cpu_threads=cfg_stt["hilos"])
        try:
            self.modelo = WhisperModel(cfg_stt["modelo"], local_files_only=True, **opciones)
        except LocalEntryNotFoundError:  # primera instalación de un modelo
            self.modelo = WhisperModel(cfg_stt["modelo"], **opciones)
        # Calienta el decodificador aunque el filtro de voz descarte el silencio.
        segmentos, _ = self.modelo.transcribe(np.zeros(TASA, dtype=np.float32),
                                              language=self.idioma, beam_size=1, temperature=0.0)
        list(segmentos)

    def transcribir(self, audio: np.ndarray) -> str:
        return self.reconocer(audio).texto

    def reconocer(self, audio: np.ndarray) -> Transcripcion:
        if not audio.size or not np.isfinite(audio).all():
            return Transcripcion("sin_voz")
        audio = audio.astype(np.float32, copy=False)
        audio = audio - audio.mean()
        pico = float(np.max(np.abs(audio)))
        if pico < 0.001:
            return Transcripcion("sin_voz")
        if pico < 0.5:
            audio = audio * min(8.0, 0.5 / pico)  # limita también la amplificación del ruido
        # Silero valida que haya habla. Whisper recibe el audio completo para conservar
        # una negación débil o una primera sílaba que quede fuera de los límites del VAD.
        if not get_speech_timestamps(audio, self.vad):
            return Transcripcion("sin_voz")
        segmentos, _ = self.modelo.transcribe(
            audio, language=self.idioma, beam_size=self.beam, best_of=1, temperature=0.0,
            condition_on_previous_text=False, without_timestamps=True, initial_prompt=self.prompt,
            hotwords=self.pistas or None, vad_filter=False, no_speech_threshold=None)
        partes = []
        for s in segmentos:
            # Descarta la frase entera: quitar solo un trozo podría quitar un «no».
            if s.avg_logprob < -0.75 or s.no_speech_prob > 0.6 or s.compression_ratio > 2.4:
                return Transcripcion("dudosa")
            partes.append(s.text.strip())
        texto = " ".join(partes).strip()
        limpio = normalizar(texto)
        if not limpio:
            return Transcripcion("dudosa")
        if limpio in {"subtitulos realizados por la comunidad de amaraorg", "gracias por ver",
                      "gracias por ver el video", "suscribete"}:
            return Transcripcion("sin_voz")
        if self.prompt and limpio == normalizar(self.prompt):
            return Transcripcion("dudosa")
        return Transcripcion("aceptada", texto)
