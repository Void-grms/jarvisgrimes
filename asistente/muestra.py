"""Prueba la voz con los mismos fonemas, ritmo y pausas que una respuesta."""
from __future__ import annotations


def reproducir_muestra(opciones: dict, actual=None) -> None:
    from copy import copy
    import numpy as np
    import sounddevice as sd
    from .tts import Voz

    texto = opciones["texto_muestra"].strip()[:500]
    if not texto:
        raise ValueError("Escribe una frase para escucharla.")
    # Se comparte solo el motor de síntesis. El ritmo de la siguiente respuesta
    # del asistente sigue siendo el guardado aunque una muestra falle.
    voz = copy(actual) if actual is not None else Voz.desde_config(opciones)
    muestras = []
    for perfil in opciones.get("lecturas", [opciones]):
        voz.velocidad = perfil["velocidad"]
        voz.tono_semitonos = perfil["tono_semitonos"]
        voz.pausa = perfil["pausa_ms"] / 1000
        voz.pronunciaciones = perfil.get("pronunciaciones", {})
        audio = voz.sintetizar(texto)
        if not audio.size:
            raise ValueError("La frase no produjo audio.")
        muestras.append(audio)
    with sd.OutputStream(samplerate=voz.tasa, channels=1, dtype="int16") as salida:
        for i, audio in enumerate(muestras):
            if i:
                salida.write(np.zeros(round(voz.tasa * .7), dtype=np.int16))
            salida.write(audio)
