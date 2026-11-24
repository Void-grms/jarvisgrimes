import os
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from asistente.ajustes import guardar_ajustes
from asistente.config import cargar
from asistente.panel import Panel


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt" and not os.environ.get("DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ruta = self.tmp / "config.toml"
        self.cfg = cargar(self.ruta)
        self.panel = Panel(self.cfg, {"voces": [self.cfg["voz"]["modelo_piper"]]})
        self.panel.setAttribute(Qt.WA_DontShowOnScreen)
        self.panel.show()
        self.app.processEvents()

    def tearDown(self):
        self.panel.confirmar_guardado(self.cfg, "")
        self.panel.close()
        self.panel.deleteLater()

    def test_vista_y_guardado_del_tamano_y_subtitulos(self):
        self.panel._botones.button(1).click()
        self.panel.deslizador.setValue(275)
        self.panel.campos["interfaz.subtitulos"].click()
        self.assertEqual(self.panel.campos["interfaz.tamano"].value(), 275)
        self.assertEqual(self.panel.vista_widget.orbe.ORBE, 275)
        self.assertEqual(self.panel.vista_widget.orbe.TEXTO, 0)
        self.assertFalse(self.panel.vista_widget.imagen.isNull())
        self.assertTrue(self.panel.boton_guardar.isEnabled())

        def guardar(cambios):
            self.cfg = guardar_ajustes(cambios, self.ruta)
            self.panel.confirmar_guardado(self.cfg)

        self.panel.guardar.connect(guardar)
        self.panel.boton_guardar.click()
        self.assertFalse(self.panel.boton_guardar.isEnabled())
        leido = cargar(self.ruta)
        self.assertEqual(leido["interfaz"]["tamano"], 275)
        self.assertFalse(leido["interfaz"]["subtitulos"])
        self.panel.campos["interfaz.subtitulos"].click()
        self.panel.confirmar_guardado(leido)
        self.assertEqual(self.panel.vista_widget.orbe.TEXTO, 0)
        self.assertFalse(self.panel.vista_widget.orbe.isVisible())

    def test_muestra_lee_edicion_sin_guardar_ni_reproducir_sonido(self):
        muestras = []
        self.panel.probar_voz.connect(muestras.append)
        self.panel.campos["voz.velocidad"].setValue(1.35)
        self.panel.texto_muestra.setText("Público, publico y publicó.")
        self.panel.boton_muestra.click()
        self.assertEqual(len(muestras), 1)
        self.assertEqual(muestras[0]["texto_muestra"], "Público, publico y publicó.")
        self.assertEqual(muestras[0]["velocidad"], 1.35)
        self.assertFalse(self.ruta.exists())
        self.panel.muestra_en_curso(True)
        self.panel.boton_muestra.click()
        self.assertEqual(len(muestras), 1)
        self.panel.resultado_muestra("Muestra terminada.")
        self.assertTrue(self.panel.boton_muestra.isEnabled())

    def test_cambio_desde_widget_conserva_otras_ediciones_del_panel(self):
        self.panel.campos["escucha.sugerir_pendientes"].setChecked(False)
        cfg = guardar_ajustes({"interfaz": {"tamano": 320}}, self.ruta)
        self.panel.sincronizar(cfg)
        self.assertEqual(self.panel.deslizador.value(), 320)
        self.assertEqual(self.panel.vista_widget.orbe.ORBE, 320)
        self.assertEqual(self.panel.cambios(), {"escucha": {"sugerir_pendientes": False}})

    def test_sistema_guarda_preferencia_de_busqueda_por_significado(self):
        self.panel._botones.button(3).click()
        control = self.panel.campos["conocimiento.busqueda_semantica"]
        self.assertTrue(control.isVisible())
        anterior = control.isChecked()
        control.click()
        self.panel.guardar.connect(lambda cambios: self.panel.confirmar_guardado(
            guardar_ajustes(cambios, self.ruta)))
        self.panel.boton_guardar.click()
        self.assertIs(cargar(self.ruta)["conocimiento"]["busqueda_semantica"], not anterior)
        self.assertFalse(self.panel.boton_guardar.isEnabled())
