import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PySide6.QtWidgets import QApplication

from asistente.app import Asistente
from asistente.ensayo_escucha import EnsayoEscucha, FRASES
from asistente.panel_ensayo import PanelEnsayo
from asistente.stt import TASA, Transcripcion


class EnsayoIntegrado(unittest.TestCase):
    def setUp(self):
        self.ahora = time.perf_counter()
        self.ensayo = EnsayoEscucha(lambda: self.ahora)
        self.app = Asistente.__new__(Asistente)
        self.app.estado = SimpleNamespace(mic_activo=True, ensayo_escucha=self.ensayo, modo="dormido", fallo=False)
        self.app.stt = Mock()
        self.app.motor = Mock(pendiente=None)
        self.app.responder = Mock()
        self.app._reposo = Mock()
        self.app._revisar_memoria = Mock()
        self.app._inicio_voz = self.app._fin_voz = self.app._interrumpido = 0.0
        self.app.estado.decir_usuario = Mock()
        self.audio = np.zeros(TASA // 2, dtype=np.float32)

    def probar(self, texto, segundos=1):
        self.app.stt.reconocer.return_value = Transcripcion("aceptada", texto)
        self.ahora += segundos
        self.app._procesar_audio(self.audio, self.ahora)

    def test_completar_y_repetir_orden_despues_de_la_ultima_hasta_salir(self):
        self.ensayo.iniciar()
        for indice, frase in enumerate(FRASES):
            self.probar(frase.replace("No abras", "Abre") if indice == 1 else frase)
        self.probar("Jarvis abre YouTube")
        turno, filas, _ = self.ensayo.vista()
        self.assertTrue(turno[1])
        self.assertEqual(len(filas), len(FRASES))
        self.assertFalse(filas[1].coincide)
        self.assertEqual(sum(fila.coincide for fila in filas), len(FRASES) - 1)
        self.assertEqual(filas[0].audio, .5)
        self.assertGreaterEqual(filas[0].demora, 0.0)
        self.assertEqual(self.app.stt.reconocer.call_count, len(FRASES))
        self.app.motor.entender.assert_not_called()
        self.app.responder.assert_not_called()
        self.app._revisar_memoria.assert_not_called()

    def test_audio_en_cola_y_en_reconocimiento_al_entrar_y_salir(self):
        self.ensayo.iniciar()
        self.probar("Abre Word", .1)  # empezaba antes del inicio del ensayo
        self.app.stt.reconocer.assert_not_called()
        self.app.stt.reconocer.side_effect = lambda _: (self.ensayo.detener(), Transcripcion("aceptada", "Sí"))[1]
        self.probar("Sí")
        self.assertFalse(self.ensayo.activo)
        self.assertFalse(self.ensayo.vista()[1])
        self.probar("Abre YouTube", .1)  # cola de micrófono al salir
        self.assertEqual(self.app.stt.reconocer.call_count, 1)
        self.app.stt.reconocer.side_effect = lambda _: (self.ensayo.iniciar(), Transcripcion("aceptada", "Sí"))[1]
        self.probar("Sí")  # se abrió el ensayo mientras se reconocía una orden anterior
        self.app.motor.entender.assert_not_called()
        self.app.responder.assert_not_called()
        self.assertFalse(self.ensayo.vista()[1])

    def test_dudosa_error_y_reinicio(self):
        self.ensayo.iniciar()
        self.app.stt.reconocer.return_value = Transcripcion("dudosa")
        self.app._procesar_audio(self.audio, self.ahora + 1)
        self.assertEqual(self.ensayo.vista()[1][0].estado, "dudosa")
        self.app.stt.reconocer.side_effect = RuntimeError("prueba")
        with self.assertLogs("asistente", level="ERROR"):
            self.app._procesar_audio(self.audio, self.ahora + 2)
        self.assertEqual(self.ensayo.vista()[1][1].estado, "error")
        self.ensayo.detener()
        self.ensayo.iniciar()
        self.assertFalse(self.ensayo.vista()[1])
        self.app.motor.entender.assert_not_called()

    def test_reanuda_con_una_orden_nueva_y_conserva_las_tildes_al_comparar(self):
        self.ensayo.iniciar()
        for frase in FRASES[:10]:
            self.probar(frase.replace("practicó", "practico"))
        self.assertFalse(self.ensayo.vista()[1][-1].coincide)
        self.app.motor.entender.assert_not_called()
        self.ensayo.detener()
        texto = "Jarvis, abre Word."
        self.app.motor.quitar_clave.return_value = True, "abre Word"
        self.probar(texto)
        self.app.motor.entender.assert_called_once_with(texto, por_voz=True)
        self.app.responder.assert_called_once()


class PanelEnsayoGuiado(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_panel_independiente_bloqueo_del_mic_y_fin_al_ocultar(self):
        panel = PanelEnsayo()
        panel.show()
        self.addCleanup(panel.close)
        self.assertFalse(panel.inicio.isEnabled())
        self.assertIn("widget", panel.indicacion.text())
        ensayo = EnsayoEscucha()
        estado = SimpleNamespace(ensayo_escucha=ensayo, mic_activo=False, fallo=False, modo="dormido")
        panel.conectar(estado, lambda: False)
        panel.inicio.click()
        self.assertFalse(estado.mic_activo)
        self.assertFalse(ensayo.activo)
        estado.mic_activo = True
        panel.actualizar()
        panel.inicio.click()
        self.assertIn("1 de 12", panel.indicacion.text())
        panel.saltar.click()
        self.assertIn("2 de 12", panel.indicacion.text())
        self.assertIn("Sin captura", panel.resultado.toPlainText())
        estado.mic_activo = False
        panel.actualizar()
        self.assertFalse(ensayo.activo)
        self.assertFalse(estado.mic_activo)
        estado.mic_activo = True
        panel.actualizar()
        panel.inicio.click()
        panel.hide()
        self.assertFalse(ensayo.activo)
        self.assertTrue(estado.mic_activo)
