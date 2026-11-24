import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from asistente.ajustes import guardar_ajustes
from asistente.config import cargar
from asistente.muestra import reproducir_muestra
from asistente.panel import Panel
from asistente.pronunciacion import validar_pronunciaciones
from asistente.tts import preparar_lectura


class VozPersonal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_solo_la_lectura_de_palabras_completas_y_prioridad_personal(self):
        nombres = validar_pronunciaciones({"QGIS": "cu gis", "YouTube": "Yutú", "Sol": "Sól",
                                           "Sol Rojo": "Sol Rójo", "practico": "práctico"})
        original = "QGIS, YouTube y Sol Rojo. Solamente Sol. Yo practico y ayer practicó."
        self.assertEqual(preparar_lectura(original, nombres),
                         "cu gis, Yutú y Sol Rójo. Solamente Sól. Yo práctico y ayer practicó.")
        literal = "Mira el archivo QGIS final.docx, Sol.txt y https://QGIS.org/Ruta."
        self.assertEqual(preparar_lectura(literal, nombres), literal)
        self.assertEqual(preparar_lectura(r"Usa C:\Proyectos\Sol Rojo\QGIS.pdf y Sol.", nombres),
                         r"Usa C:\Proyectos\Sol Rojo\QGIS.pdf y Sól.")
        self.assertEqual(preparar_lectura("\x60QGIS\x60 y QGIS", nombres), "QGIS y cu gis")

    def test_guardado_nfc_limites_y_rechazo_antes_de_escribir(self):
        carpeta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ruta = carpeta / "personal.toml"
        cfg = guardar_ajustes({"voz": {"pronunciaciones": {"Mari\u0301a": "María"}}}, ruta)
        self.assertEqual(cfg["voz"]["pronunciaciones"], {"María": "María"})
        anterior = ruta.read_bytes()
        for incorrecto in ({"México": "Méjico", "MÉXICO": "México"}, {"Texto": "[[arca]]"}, {"Sol": ""}):
            with self.assertRaises(ValueError):
                guardar_ajustes({"voz": {"pronunciaciones": incorrecto}}, ruta)
            self.assertEqual(ruta.read_bytes(), anterior)

    def test_panel_compara_elige_y_conserva_la_lectura_preferida(self):
        carpeta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ruta = carpeta / "prueba.toml"
        cfg = cargar(ruta)
        panel = Panel(cfg, {})
        self.addCleanup(panel.deleteLater)
        perfiles = []
        panel.probar_voz.connect(perfiles.append)
        panel.guardar.connect(lambda c: panel.confirmar_guardado(guardar_ajustes(c, ruta)))
        panel.texto_muestra.setText("Practico con QGIS.")
        editor = panel.campos["voz.pronunciaciones"]
        editor.escrita.setText("QGIS")
        editor.hablada.setText("Cu gis")
        editor.hablada.returnPressed.emit()
        panel.campos["voz.velocidad"].setValue(1.41)
        panel.comparacion.escuchar.click()
        self.assertEqual(perfiles[-1]["texto_muestra"], "Practico con QGIS.")
        a, b = perfiles[-1]["lecturas"]
        self.assertEqual((a["pronunciaciones"], b["pronunciaciones"]), ({}, {"QGIS": "Cu gis"}))
        self.assertEqual(b["velocidad"], 1.41)
        self.assertFalse(panel.comparacion.usar_b.isEnabled())
        panel.muestra_en_curso(True)
        panel.muestra_en_curso(False)
        panel.resultado_muestra()
        panel.comparacion.usar_b.click()
        panel.boton_guardar.click()
        self.assertEqual(cargar(ruta)["voz"]["pronunciaciones"], {"QGIS": "Cu gis"})
        editor.lista.selectRow(0)
        editor.quitar.click()
        panel.boton_guardar.click()
        self.assertEqual(cargar(ruta)["voz"]["pronunciaciones"], {})

    def test_dos_lecturas_de_la_misma_frase_y_una_sola_salida(self):
        tomas = []
        class Sintetizador:
            velocidad = 1
            tono_semitonos = 0
            pausa = .1
            tasa = 1000
            pronunciaciones = {}

            def sintetizar(self, texto):
                tomas.append((texto, self.velocidad, self.pronunciaciones))
                return np.ones(20, dtype=np.int16)

        guardada = {"velocidad": 1.1, "tono_semitonos": .1, "pausa_ms": 40, "pronunciaciones": {}}
        b = {**guardada, "velocidad": 1.3, "pronunciaciones": {"Sol": "Sól"}}
        actual = Sintetizador()
        salida = Mock()
        with patch("sounddevice.OutputStream") as stream:
            stream.return_value.__enter__.return_value = salida
            reproducir_muestra({**guardada, "texto_muestra": "Ayer practicó.", "lecturas": [guardada, b]}, actual)
            stream.assert_called_once()
        self.assertEqual(tomas, [("Ayer practicó.", 1.1, {}), ("Ayer practicó.", 1.3, {"Sol": "Sól"})])
        datos = [c.args[0] for c in salida.write.call_args_list]
        self.assertEqual([len(t) for t in datos], [20, 700, 20])
        self.assertFalse(datos[1].any())
        self.assertEqual((actual.velocidad, actual.pronunciaciones), (1, {}))
