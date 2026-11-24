"""La entrega por oraciones y el ritmo se prueban sin usar el parlante."""
import collections
import threading
import unicodedata
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from piper.phonemize_espeak import EspeakPhonemizer

from asistente.tts import Voz, preparar_lectura


class Sintesis(unittest.TestCase):
    def setUp(self):
        self.voz = Voz.__new__(Voz)
        self.voz.voz = Mock()
        self.voz.tasa = 1000
        self.voz.velocidad = 1.18
        self.voz.tono_semitonos = 0
        self.voz.pausa = 0.095

    @staticmethod
    def trozo(pcm):
        return SimpleNamespace(audio_int16_bytes=pcm.astype(np.int16).tobytes())

    def test_pausa_entre_oraciones_sin_duplicar_la_cola_de_piper(self):
        palabra = np.full(100, -1000, dtype=np.int16)
        fin_largo = np.concatenate((palabra, np.zeros(250, dtype=np.int16)))
        self.voz.voz.synthesize.return_value = [self.trozo(fin_largo), self.trozo(palabra)]
        pcm = self.voz.sintetizar("¡Hola! ¿Empezamos?")
        self.assertEqual(pcm.dtype, np.int16)
        self.assertEqual(pcm.size, 100 + 40 + 95 + 100)
        self.assertEqual(pcm[-1], -1000)

    def test_primera_oracion_disponible_antes_de_la_siguiente(self):
        primera = self.trozo(np.ones(100) * 2000)
        def generar():
            yield primera
            self.fail("sintetizó la segunda oración antes de entregar la primera")
        self.voz.voz.synthesize.return_value = generar()
        self.assertEqual(next(self.voz._oraciones_audio("Uno. Dos.")).size, 100)

    def test_el_cambio_de_tono_compensa_la_duracion_en_piper(self):
        self.voz.tono_semitonos = 1
        self.voz.voz.synthesize.return_value = [self.trozo(np.ones(200) * 2000)]
        pcm = self.voz.sintetizar("Sí.")
        cfg = self.voz.voz.synthesize.call_args.kwargs["syn_config"]
        factor = 2 ** (1 / 12)
        self.assertAlmostEqual(cfg.length_scale, factor / self.voz.velocidad)
        self.assertLessEqual(abs(pcm.size - 200 / factor), 1)

    def test_piper_distingue_la_silaba_tonica_con_ambas_formas_unicode(self):
        motor = EspeakPhonemizer()
        for palabra, vocal in (("público", "u"), ("publico", "i"), ("publicó", "o")):
            for forma in ("NFC", "NFD"):
                with self.subTest(palabra=palabra, forma=forma):
                    texto = unicodedata.normalize(forma, palabra)
                    fonemas = motor.phonemize("es-419", preparar_lectura(texto))[0]
                    self.assertEqual(fonemas[fonemas.index("ˈ") + 1], vocal)

    def test_las_pausas_y_la_pregunta_llegan_hasta_piper(self):
        self.voz.voz.synthesize.return_value = []
        self.voz.sintetizar("**Espera**… un momento... ¿Publicó el informe?")
        hablado = self.voz.voz.synthesize.call_args.args[0]
        self.assertEqual(hablado, "Espera, un momento. ¿Publicó el informe?")
        fonemas = EspeakPhonemizer().phonemize("es-419", hablado)
        self.assertEqual(len(fonemas), 2)
        self.assertIn(",", fonemas[0])
        self.assertEqual(fonemas[1][-1], "?")

    def test_lee_texto_de_interfaz_y_respeta_palabras_ambiguas(self):
        texto = "La Dra. Pérez publicó esto:\n• 1 GB de música\n• 2 GB de vídeo en [YouTube](https://ejemplo.com/video)."
        self.assertEqual(preparar_lectura(texto),
                         "La doctora Pérez publicó esto: un gigabyte de música. 2 gigabytes de vídeo en Yutúb.")
        self.assertEqual(preparar_lectura("El examen es a las 09:30."), "El examen es a las 9 y media.")
        self.assertEqual(preparar_lectura("2026. Yo publico mi informe_01."), "2026. Yo publico mi informe 01.")

    def test_flujo_completa_solo_la_pausa_que_el_dispositivo_aun_no_ha_emitido(self):
        v = self.voz
        v._lock = threading.Lock()
        v._trozos = collections.deque()
        v._salida = collections.deque()
        v._offset = v._pendientes = v._silencio_salida = 0
        v.al_nivel = None
        palabra = np.full(20, 900, dtype=np.int16)
        pausa = np.zeros(95, dtype=np.int16)
        v._encolar(palabra)
        # El dispositivo ya reprodujo la primera palabra y solicitó 30 ms más.
        v._callback(np.empty((50, 1), dtype=np.int16), 50, None, None)
        v._encolar(palabra, pausa)
        salida = np.empty((85, 1), dtype=np.int16)
        v._callback(salida, 85, None, None)
        np.testing.assert_array_equal(salida[:, 0], np.r_[pausa[:65], palabra])
        # Si ya hubo suficiente silencio mientras llegaba la respuesta, habla al instante.
        v._callback(np.empty((110, 1), dtype=np.int16), 110, None, None)
        v._encolar(palabra, pausa)
        self.assertEqual(v._pendientes, palabra.size)
