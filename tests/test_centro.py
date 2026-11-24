import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from asistente.ajustes import guardar_ajustes
from asistente.app import Asistente
from asistente.centro import Centro
from asistente.config import cargar
from asistente.interfaz import Estado, crear_orbe
from asistente.store import Store, cargar_preferencias


class CentroIntegrado(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.raiz = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ruta = self.raiz / "prueba.toml"
        self.estado = Estado()
        self.centro = Centro(cargar(self.ruta), self.estado)
        self.enterContext(patch("asistente.centro.guardar_ajustes",
            side_effect=lambda cambios: guardar_ajustes(cambios, self.ruta)))
        self.enterContext(patch("asistente.interfaz.POSICION", self.raiz / "widget.json"))

    def test_widget_y_voz_se_aplican_a_sus_respectivos_hilos(self):
        orbe = self.centro.orbe = crear_orbe(self.estado)
        orbe.setAttribute(Qt.WA_DontShowOnScreen)
        self.addCleanup(orbe.close)
        esquina = orbe.geometry().bottomRight()
        self.centro.guardar({"voz": {"pausa_ms": 150, "velocidad": 1.35},
                             "interfaz": {"tamano": 240, "subtitulos": False, "widget": True}})
        self.assertTrue(orbe.isVisible())
        self.assertEqual(orbe.ORBE, 240)
        self.assertEqual(orbe.TEXTO, 0)
        self.assertEqual(orbe.geometry().bottomRight(), esquina)
        self.centro.ajustar_widget("widget", False)
        self.assertFalse(orbe.isVisible())
        self.assertEqual(cargar(self.ruta)["interfaz"], {"tamano": 240, "subtitulos": False, "widget": False})
        asistente = Asistente.__new__(Asistente)
        asistente.estado, asistente.cfg = self.estado, cargar(self.raiz / "inicial.toml")
        asistente.voz = SimpleNamespace(pausa=.095, velocidad=1.18, tono_semitonos=0.7)
        asistente._atender_panel()
        self.assertEqual((asistente.voz.pausa, asistente.voz.velocidad), (.15, 1.35))

    def test_muestra_pausa_captura_y_descarta_audio_que_se_solapa(self):
        a = Asistente.__new__(Asistente)
        a.estado, a.cfg = self.estado, cargar(self.ruta)
        a.oido = SimpleNamespace(pausado=threading.Event())
        a.voz, a.motor, a.stt = Mock(), Mock(pendiente=None), Mock()
        a._reposo = Mock()
        callback = Mock()
        opciones = {**a.cfg["voz"], "texto_muestra": "Publicó el informe."}
        a.estado.eventos_panel.put(("muestra", opciones, callback))
        with patch("asistente.muestra.reproducir_muestra",
                   side_effect=lambda *args: self.assertTrue(a.oido.pausado.is_set())) as reproducir:
            a._atender_panel()
        reproducir.assert_called_once_with(opciones, a.voz)
        callback.assert_called_once_with("Muestra reproducida.", False)
        self.assertFalse(a.oido.pausado.is_set())
        a._procesar_audio(np.zeros(16000), a._ignorar_audio_hasta + .2)
        a.stt.reconocer.assert_not_called()

    def test_la_migracion_de_voz_es_idempotente_y_respeta_el_panel(self):
        bd = self.raiz / "anterior.sqlite"
        store = Store(bd)
        self.addCleanup(store._con.close)
        store.set_meta("ajuste_voz", json.dumps({"velocidad": 1.42, "tono_semitonos": 1.3}))
        self.assertEqual(cargar_preferencias(self.ruta, bd)["voz"]["velocidad"], 1.42)
        self.assertIsNone(store.get_meta("ajuste_voz"))
        guardar_ajustes({"voz": {"velocidad": 1.1}}, self.ruta)
        self.assertEqual(cargar_preferencias(self.ruta, bd)["voz"]["velocidad"], 1.1)
