"""Recorre el panel real sin abrir aplicaciones ni enviar documentos del equipo."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from pptx import Presentation
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from asistente.acciones import encargos
from asistente.panel_encargos import EditorEncargo


class PanelEncargos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication(["jarvis-test", "-platform", "offscreen"])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.editor = EditorEncargo()
        self.addCleanup(self.editor.close)
        self.nota = self.raiz / "Notas de educación.md"
        self.nota.write_text("Se revisaron catorce evaluaciones.", encoding="utf-8")
        self.word = self.raiz / "Borrador.docx"
        doc = Document()
        doc.add_paragraph("El equipo publico su resumen.")
        doc.save(self.word)
        self.enviar = self.enterContext(patch.object(encargos, "enviar_revisado", return_value=(True, "Enviado.")))
        self.leer = self.enterContext(patch.object(encargos, "leer_seleccion"))
        self.abrir = self.enterContext(patch.object(QDesktopServices, "openUrl", return_value=True))

    def esperar(self, condicion):
        fin = time.monotonic() + 5
        while not condicion() and time.monotonic() < fin:
            self.qt.processEvents()
            time.sleep(.005)
        self.assertTrue(condicion(), "El panel no recibió el resultado del hilo de trabajo.")

    def revisar(self):
        boton = next(b for b in self.editor.findChildren(QPushButton) if b.text() == "Revisar encargo")
        boton.click()
        self.assertEqual(self.editor.paginas.currentIndex(), 1, self.editor.aviso.text())

    def elegir(self, ruta):
        if self.editor.tipo.currentData() == "revision":
            metodo, valor = "getOpenFileName", str(ruta)
        else:
            metodo, valor = "getOpenFileNames", [str(ruta)]
        with patch.object(QFileDialog, metodo, return_value=(valor, "")):
            self.editor.elegir.click()

    def recorrido(self, tipo, app, entrada, respuesta, extension):
        e = self.editor
        original = entrada.read_bytes()
        e.nuevo(app, "Prepara el material en español.", tipo)
        self.elegir(entrada)
        self.revisar()
        self.assertIn(str(entrada), e.cabecera.text())
        self.assertIn(app, e.cabecera.text())
        prompt = e.vista.toPlainText()
        self.assertIn("Prepara el material", prompt)
        self.enviar.assert_not_called()
        self.leer.assert_not_called()
        e.enviar.click()
        self.esperar(lambda: not e.enviando)
        self.assertTrue(e.enviado)
        self.enviar.assert_called_once_with(app, prompt)
        e.enviar.click()
        self.assertEqual(self.enviar.call_count, 1)
        e.importar.click()
        self.assertEqual(e.paginas.currentIndex(), 2)
        self.leer.return_value = (True, json.dumps(respuesta, ensure_ascii=False))
        e.tomar.click()
        self.esperar(lambda: not e.leyendo)
        self.leer.assert_called_once_with(app)
        self.assertEqual(json.loads(e.respuesta.toPlainText()), respuesta)
        destino = self.raiz / f"Resultado {tipo}{extension}"
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")):
            e.crear_archivo.click()
        self.assertFalse(destino.exists())
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destino), "")):
            e.crear_archivo.click()
        self.assertTrue(destino.is_file(), e.aviso.text())
        self.assertEqual(entrada.read_bytes(), original)
        self.abrir.assert_not_called()
        e.abrir_archivo.click()
        self.assertEqual(Path(self.abrir.call_args.args[0].toLocalFile()), destino)
        return destino

    def test_informe_desde_nota_hasta_word(self):
        salida = self.recorrido("informe", "ChatGPT", self.nota,
            {"titulo": "Evaluación", "secciones": [{"titulo": "Muestra", "parrafos": ["Catorce evaluaciones."]}]},
            ".docx")
        self.assertEqual(Document(salida).paragraphs[-1].text, "Catorce evaluaciones.")

    def test_revision_desde_word_hasta_copia(self):
        salida = self.recorrido("revision", "Claude", self.word,
            {"cambios": [{"id": 1, "texto": "El equipo publicó su resumen."}]}, ".docx")
        self.assertEqual(Document(salida).paragraphs[0].text, "El equipo publicó su resumen.")

    def test_presentacion_desde_documento_hasta_powerpoint(self):
        salida = self.recorrido("presentacion", "ChatGPT", self.word, {"titulo": "Balance",
            "diapositivas": [{"titulo": "Equipo", "puntos": ["Se publicó el resumen."], "notas": "Abrir el debate."}]},
            ".pptx")
        ppt = Presentation(salida)
        self.assertEqual(len(ppt.slides), 2)
        self.assertEqual(ppt.slides[1].notes_slide.notes_text_frame.text, "Abrir el debate.")

    def test_editar_archivos_y_pedido_requiere_revisar_su_contenido_actual(self):
        e = self.editor
        e.nuevo("Claude", "Informe inicial", "informe")
        self.elegir(self.nota)
        with patch.object(QFileDialog, "getOpenFileNames", return_value=([], "")):
            e.elegir.click()
        self.assertEqual(e.rutas, [self.nota])
        self.revisar()
        self.assertIn("catorce evaluaciones", e.vista.toPlainText())
        e.importar.click()
        e.respuesta.setPlainText("Borrador pegado manualmente.")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            e.tomar.click()
            e.nuevo("ChatGPT", "No debe reemplazar el trabajo", "presentacion")
        self.assertEqual(e.paginas.currentIndex(), 2)
        self.assertEqual(e.respuesta.toPlainText(), "Borrador pegado manualmente.")
        self.leer.assert_not_called()
        e.ver.click()
        e.volver.click()
        e.quitar.click()
        self.assertEqual(e.rutas, [])
        self.elegir(self.word)
        e.pedido.setPlainText("Informe final del equipo.")
        self.revisar()
        nuevo = e.vista.toPlainText()
        self.assertIn("Informe final del equipo.", nuevo)
        self.assertIn("El equipo publico", nuevo)
        self.assertNotIn("catorce evaluaciones", nuevo)
        self.assertNotIn(str(self.nota), e.cabecera.text())
        self.assertEqual(e.respuesta.toPlainText(), "")
        self.enviar.assert_not_called()
        e.enviar.click()
        self.esperar(lambda: not e.enviando)
        self.enviar.assert_called_once_with("Claude", nuevo)

    def test_formular_otro_encargo_igual_permite_autorizarlo_por_separado(self):
        e = self.editor
        e.nuevo("Claude", "Traduce este texto.", "consulta")
        self.revisar()
        e.enviar.click()
        self.esperar(lambda: not e.enviando)
        self.assertFalse(e.enviar.isEnabled())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            e.nuevo("Claude", "Traduce este texto.", "consulta")
        self.revisar()
        self.assertEqual(self.enviar.call_count, 1)
        self.assertTrue(e.enviar.isEnabled())
        e.enviar.click()
        self.esperar(lambda: not e.enviando)
        self.assertEqual(self.enviar.call_count, 2)
