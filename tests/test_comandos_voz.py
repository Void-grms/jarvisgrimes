import copy
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from asistente.comandos import Comandos
from asistente.config import DEFAULTS, cargar
from asistente.ajustes import guardar_ajustes
from asistente.respuesta import Respuesta


class ComandosVozTest(unittest.TestCase):
    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.ruta = Path(carpeta.name) / "config.toml"
        self.guardar = self.enterContext(patch("asistente.ajustes.guardar_ajustes",
            side_effect=lambda cambios: guardar_ajustes(cambios, self.ruta)))
        cfg = copy.deepcopy(DEFAULTS)
        cfg["documentos"]["carpeta"] = carpeta.name
        cfg["voz"].update(velocidad=1.18, tono_semitonos=0.7)
        self.memoria = {}
        self.store = Mock()
        self.store.get_meta.side_effect = self.memoria.get
        self.store.set_meta.side_effect = self.memoria.__setitem__
        with (patch("asistente.comandos.Recordatorios"), patch("asistente.comandos.sistema.Apps"),
              patch("asistente.comandos.carpetas_usuario", return_value=[]),
              patch.object(Comandos, "c_estado_pc", return_value=Respuesta("Estado del equipo."))):
            self.cmd = Comandos(cfg, self.store, Mock(), Mock(), Mock())
        self.voz = SimpleNamespace(velocidad=1.18, tono_semitonos=0.7)
        self.cmd.conectar_voz(self.voz)

    def test_quejas_sobre_la_voz_ajustan_en_la_direccion_correcta(self):
        for frase in ("Hablas demasiado lento.", "Tu voz está muy lenta.", "Hablas tan despacio."):
            with self.subTest(frase=frase):
                self.cmd.procesar("Habla normal.")
                self.assertIn("ritmo", self.cmd.procesar(frase).texto)
                self.assertGreater(self.voz.velocidad, 1.18)
        self.cmd.procesar("Hablas demasiado rápido.")
        self.assertAlmostEqual(self.voz.velocidad, 1.18)
        self.store.set_meta.assert_not_called()

    def test_energia_fluidez_despacio_y_restablecer(self):
        self.cmd.procesar("Habla con más energía.")
        self.assertGreater(self.voz.velocidad, 1.18)
        tono = self.voz.tono_semitonos
        self.assertGreater(tono, 0.7)
        v = self.voz.velocidad
        self.cmd.procesar("Habla más fluido.")
        self.assertGreater(self.voz.velocidad, v)
        self.assertEqual(self.voz.tono_semitonos, tono)
        v = self.voz.velocidad
        self.cmd.procesar("Más despacio.")
        self.assertLess(self.voz.velocidad, v)
        self.cmd.procesar("Habla normal.")
        self.assertEqual((self.voz.velocidad, self.voz.tono_semitonos), (1.18, 0.7))
        self.cmd.procesar("Tu voz suena triste.")
        self.assertGreater(self.voz.tono_semitonos, 0.7)
        self.cmd.procesar("Habla normal.")
        self.cmd.procesar("Tu voz está muy lenta, triste y sin fluidez.")
        self.assertGreater(self.voz.velocidad, 1.18)
        self.assertGreater(self.voz.tono_semitonos, 0.7)

    def test_latencia_del_equipo_y_menciones_no_cambian_el_audio(self):
        for frase in ("¿Por qué la computadora va tan lenta?", "¿Por qué respondes muy lento?"):
            self.assertEqual(self.cmd.procesar(frase).texto, "Estado del equipo.")
        self.assertIn("ritmo", self.cmd.procesar("¿Por qué hablas tan lento?").texto)
        for mencion in ("No quiero que hables más lento.", "Fernando habla más rápido."):
            self.assertIsNone(self.cmd.procesar(mencion))
        self.assertEqual((self.voz.velocidad, self.voz.tono_semitonos), (1.18, 0.7))

    def test_persistencia_solo_a_peticion(self):
        self.cmd.procesar("Habla más rápido.")
        self.assertFalse(self.ruta.exists())
        r = self.cmd.procesar("Habla con más energía y guárdalo.")
        self.assertIn("guardé", r.texto.lower())
        guardado = self.voz.velocidad, self.voz.tono_semitonos
        cfg = cargar(self.ruta)["voz"]
        self.assertEqual((cfg["velocidad"], cfg["tono_semitonos"]), guardado)
        self.cmd.procesar("Habla más rápido.")
        self.cmd.procesar("Habla normal.")
        self.assertEqual((self.voz.velocidad, self.voz.tono_semitonos), guardado)

    def test_limites_y_modo_texto(self):
        self.voz.velocidad = 2.0
        self.assertEqual(self.cmd.procesar("Más rápido.").texto, "Ya uso ese ajuste.")
        self.cmd.voz = None
        self.assertIn("modo texto", self.cmd.procesar("Habla con más energía.").texto)
        self.assertIn("modo texto", self.cmd.procesar("Guarda esta voz.").texto)
        self.store.set_meta.assert_not_called()

    def test_preferencia_anterior_ilegible(self):
        for valor in ("rota", "null", '{"velocidad": 900, "tono_semitonos": 0}'):
            self.memoria["ajuste_voz"] = valor
            self.cmd.conectar_voz(self.voz)
            self.assertEqual((self.voz.velocidad, self.voz.tono_semitonos), (1.18, 0.7))


if __name__ == "__main__":
    unittest.main()
