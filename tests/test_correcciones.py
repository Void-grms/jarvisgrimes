import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from asistente.acciones.sistema import Apps
from asistente.comandos import Comandos
from asistente.config import cargar
from asistente.correcciones import Correcciones
from asistente.motor import Motor
from asistente.respuesta import Respuesta
from asistente.store import Store


class CorreccionesPersonales(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.bd = Path(tmp) / "correcciones.sqlite"
        self.store = Store(self.bd)
        self.addCleanup(self.store._con.close)
        self.c = Correcciones(self.store)
        self.apps = Apps.__new__(Apps)
        self.word = {"Name": "Word", "AppID": "office.word"}
        self.paint = {"Name": "Paint", "AppID": "microsoft.paint"}
        self.apps.apps = [self.word, self.paint]
        self.abrir = Mock(return_value=Respuesta("Aplicación seleccionada.", confirmar_voz="¿Abro Word?"))

    def pedir(self, texto):
        return self.c.procesar(texto, self.apps, self.abrir)

    def test_persiste_un_alias_incluso_de_fabrica_con_una_apertura_explicita(self):
        r = self.pedir("Cuando te pida abrir «World», me refiero a Microsoft Word.")
        self.assertFalse(self.c.listar())
        self.assertIn("World", r.ejecutar())
        self.abrir.assert_not_called()
        conexion = Store(self.bd)
        self.addCleanup(conexion._con.close)
        self.c = Correcciones(conexion)
        r = self.pedir("Quiero que abras la aplicación World")
        self.assertEqual(r.confirmar_voz, "¿Abro Word?")
        self.abrir.assert_called_once_with(self.word)

    def test_solo_resuelve_comandos_completos_nunca_texto_mencionado_o_dictado(self):
        self.pedir("Cuando pida abrir acuarela, me refiero a Paint.").ejecutar()
        for texto in ("Acuarela", "No abras acuarela", "Escribe: abre acuarela",
                      "Busca en mis notas acuarela", "Yo dije que abriera acuarela",
                      "Abre acuarela en una web", "Abre el archivo acuarela"):
            with self.subTest(texto=texto):
                self.assertIsNone(self.pedir(texto))
        self.abrir.assert_not_called()
        self.assertIsNotNone(self.pedir("Abre el acuarela"))
        self.abrir.assert_called_once_with(self.paint)

    def test_app_desinstalada_no_cae_en_otra_con_el_mismo_nombre(self):
        self.pedir("Cuando te diga abre acuarela, quiero decir Paint").ejecutar()
        self.apps.apps = [{"Name": "Paint", "AppID": "sustituto"}]
        self.assertIn("ya no aparece", self.pedir("Abre acuarela").texto)
        self.abrir.assert_not_called()

    def test_app_personal_con_titulo_compartido_se_lanza_por_su_identidad(self):
        comandos = Comandos.__new__(Comandos)
        comandos.apps = Mock()
        app = {"Name": "Taller del aula", "AppID": "ejemplo.taller"}
        with patch("asistente.comandos.ventanas.buscar") as buscar:
            r = comandos.abrir_aplicacion(app)
            self.assertEqual(r.confirmar_voz, "¿Abro Taller del aula?")
            self.assertIn("Windows", r.ejecutar())
        comandos.apps.abrir.assert_called_once_with(app)
        buscar.assert_not_called()

    def test_motor_distingue_orden_negacion_y_dictado_literal(self):
        cfg = cargar(self.bd.parent / "prueba.toml")
        cfg["documentos"]["carpeta"] = str(self.bd.parent)
        with patch("asistente.motor.Store", return_value=self.store):
            motor = Motor(cfg)
        motor.comandos = SimpleNamespace(dictando=False, apps=self.apps, abrir_aplicacion=self.abrir)
        motor.pantalla = Mock()
        motor.pantalla.preparar.return_value = lambda: "Texto escrito."

        def decir(texto):
            r = motor.entender(texto, forzar=True, por_voz=True)
            motor.ejecutar(r)
            motor.aplicar(r)
            return r

        decir("Jarvis, cuando te pida abrir acuarela, me refiero a Paint")
        decir("Jarvis, no abras acuarela")
        decir("Jarvis, escribe: Hello world, abre acuarela")
        self.abrir.assert_not_called()
        motor.pantalla.preparar.assert_called_once_with("escribir", "Hello world, abre acuarela", "")
        decir("Jarvis, abre acuarela")
        self.abrir.assert_called_once_with(self.paint)

    def test_destino_ambiguo_no_acepta_y_borrar_exige_la_misma_asociacion(self):
        self.apps.apps.append({"Name": "Word", "AppID": "word.otro"})
        self.assertIn("una sola", self.pedir("Cuando te pida abrir World, me refiero a Word").ejecutar())
        self.assertFalse(self.c.listar())
        self.apps.apps.pop()
        self.pedir("Cuando te pida abrir acuarela, me refiero a Word").ejecutar()
        anterior, = self.c.listar()
        self.pedir("Cuando te pida abrir acuarela, me refiero a Paint").ejecutar()
        self.assertFalse(self.c.borrar(anterior))
        actual, = self.c.listar()
        self.assertEqual(actual.nombre, "Paint")
        self.assertTrue(self.c.borrar(actual))
        self.assertIsNone(self.pedir("abre acuarela"))

    def test_panel_muestra_y_quita_solo_la_correccion_elegida(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from asistente.panel_correcciones import PanelCorrecciones
        app = QApplication.instance() or QApplication([])
        self.pedir("Cuando te pida abrir pincel, me refiero a Paint").ejecutar()
        self.pedir("Cuando te pida abrir World, me refiero a Microsoft Word").ejecutar()
        panel = PanelCorrecciones()
        panel.conectar(self.c)
        self.assertEqual(panel.lista.count(), 2)
        panel.lista.setCurrentIndex(1)
        panel.quitar.click()
        self.assertEqual([c.alias for c in self.c.listar()], ["pincel"])
        self.assertEqual(panel.lista.currentText(), "pincel → Paint")
        panel.close()
        app.processEvents()
