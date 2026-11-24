"""Regresiones de escucha sin abrir el micrófono, el parlante ni aplicaciones."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from asistente.config import DEFAULTS
from asistente.stt import MUESTRAS_MARCO, TASA, Oido, Transcripcion, Transcriptor


def segmento(texto, prob=-0.2, silencio=0.03, compresion=1.0):
    return SimpleNamespace(text=texto, avg_logprob=prob, no_speech_prob=silencio,
                           compression_ratio=compresion)


class PruebasTranscripcion(unittest.TestCase):
    def setUp(self):
        with patch("asistente.stt.WhisperModel") as modelo:
            self.modelo = modelo.return_value
            self.modelo.transcribe.return_value = ([], None)  # precalentamiento
            self.stt = Transcriptor(DEFAULTS["stt"])
        self.modelo.reset_mock()
        self.audio = np.random.default_rng(7).normal(0, 0.02, TASA).astype(np.float32)

    def reconocer_segmentos(self, *segmentos):
        self.modelo.transcribe.return_value = (iter(segmentos), None)
        with patch("asistente.stt.get_speech_timestamps", return_value=[{"start": 400, "end": 6000}]):
            return self.stt.reconocer(self.audio)

    def test_silencio_ruido_e_impulso_no_llegan_al_decodificador(self):
        tonos = np.sin(np.arange(TASA, dtype=np.float32) * 0.1) * 0.2
        for audio in (np.array([]), np.zeros(TASA), np.ones(TASA), self.audio, tonos,
                      np.pad([0.79], (5000, 8000)), np.array([np.nan])):
            with self.subTest(muestras=len(audio)):
                self.assertEqual(self.stt.reconocer(audio), Transcripcion("sin_voz"))
        self.modelo.transcribe.assert_not_called()

    def test_una_negacion_incierta_invalida_la_frase_entera(self):
        for negacion in (segmento("No", prob=-1.2), segmento("No", silencio=0.8),
                         segmento("No", compresion=3.0)):
            with self.subTest(negacion=negacion):
                resultado = self.reconocer_segmentos(negacion, segmento("abras YouTube"))
                self.assertEqual(resultado, Transcripcion("dudosa"))

    def test_una_orden_clara_usa_todo_el_audio(self):
        self.assertEqual(self.reconocer_segmentos(segmento("Abre Word.")),
                         Transcripcion("aceptada", "Abre Word."))
        (audio,), opciones = self.modelo.transcribe.call_args
        self.assertEqual(len(audio), len(self.audio))
        self.assertIsNone(opciones["initial_prompt"])
        self.assertNotIn("abre", opciones["hotwords"].lower())

    def test_respuestas_breves(self):
        for texto in ("No.", "Sí.", "Gracias."):
            self.assertEqual(self.reconocer_segmentos(segmento(texto)), Transcripcion("aceptada", texto))


class PruebasCaptura(unittest.TestCase):
    def test_prebuffer_del_parlante_no_se_mezcla_con_la_frase_siguiente(self):
        oido = Oido(dict(DEFAULTS["escucha"], silencio_ms=120))
        oido.nivel_salida = lambda: 0.2
        oido.al_interrumpir = Mock(return_value=False)
        oido.vad = Mock()

        def marco(valor):
            return np.full(MUESTRAS_MARCO, valor, dtype=np.int16).tobytes()

        eco, usuario, silencio = marco(15000), marco(3000), marco(0)
        oido.vad.is_speech.side_effect = lambda datos, tasa: datos != silencio

        def marcos():
            oido.silenciado.set()
            yield from [eco] * 10
            oido.silenciado.clear()
            yield from [usuario] * 12
            yield from [silencio] * 5

        oido._crudo = Mock()
        oido._crudo.get.side_effect = ((marco, i * 0.03) for i, marco in enumerate(marcos()))
        with self.assertRaises(StopIteration):
            oido._segmentar()
        audio, _ = oido.frases.get_nowait()
        self.assertAlmostEqual(float(audio.max()), 3000 / 32768)
        self.assertEqual(np.count_nonzero(audio), 12 * MUESTRAS_MARCO)


if __name__ == "__main__":
    unittest.main()
