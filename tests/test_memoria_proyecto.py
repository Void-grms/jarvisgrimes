import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from docx import Document

from asistente.conocimiento import Conocimiento
from asistente.proyectos import huella
from asistente.store import Store
from asistente.trabajo import TrabajoOffice


class MemoriaProyecto(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.raiz / "proyectos.sqlite"
        self.conocimiento = Conocimiento(self.raiz / "Notas", self.raiz / "rapidas.md")
        self.conocimiento.raiz.mkdir()
        self.abrir()
        self.decir("Trabajemos en el proyecto Tesis")

    def abrir(self):
        store = Store(self.db)
        self.addCleanup(store._con.close)
        self.trabajo = TrabajoOffice(store, Mock(), self.conocimiento)

    def decir(self, texto):
        r = self.trabajo.procesar(texto)
        self.assertIsNotNone(r)
        return r.ejecutar() if r.ejecutar else r.texto

    def test_retoma_version_decision_reciente_y_pendientes_incluso_despues_de_reiniciar(self):
        base = self.raiz / "Base.docx"
        copia = self.raiz / "Copia revisada.docx"
        doc = Document()
        doc.add_paragraph("Versión original.")
        doc.save(base)
        doc.add_paragraph("Resultados.")
        doc.save(copia)
        proyectos = self.trabajo.proyectos
        proyectos.registrar(proyectos.actual(), base, huella(base), copia, huella(copia), "Revisión de Word", [])
        self.decir("Queda pendiente validar los datos")
        self.decir("Queda pendiente enviar la encuesta")
        self.decir("Queda pendiente cotejar las tablas")
        self.decir("Marca como hecho el pendiente enviar la encuesta")
        for texto in ("usar papel", "usar un formulario", "usar entrevistas", "incluir el anexo"):
            self.decir("Anota como decisión " + texto)
        self.abrir()
        estado = self.decir("Retomemos la tesis. ¿Qué dejamos pendiente y qué versión estábamos usando?")
        for dato in (copia.name, "incluir el anexo", "validar los datos", "cotejar las tablas"):
            self.assertIn(dato, estado)
        for dato in ("usar entrevistas", "enviar la encuesta"):
            self.assertNotIn(dato, estado)
        pendientes = self.decir("¿Qué dejamos pendiente?")
        self.assertIn("validar los datos", pendientes)
        self.assertIn("cotejar las tablas", pendientes)
        self.assertNotIn(copia.name, pendientes)
        self.assertNotIn("incluir el anexo", pendientes)
        self.decir("Marca como hecho el pendiente validar los datos")
        self.decir("Marca como hecho el pendiente cotejar las tablas")
        self.assertIn("No quedan pendientes", self.decir("Pendientes del proyecto"))

    def test_las_notas_vinculadas_leen_la_edicion_local_y_se_mantienen_separadas_de_los_pendientes(self):
        nota = self.conocimiento.raiz / "Muestreo.md"
        nota.write_text("# Muestreo\n\nEntrevistaremos a dos docentes.", encoding="utf-8")
        self.decir("Queda pendiente revisar la rúbrica")
        self.assertIn("dos docentes", self.decir("Vincula mis notas sobre muestreo a este proyecto"))
        nota.write_text("# Muestreo\n\nEntrevistaremos a cinco docentes.", encoding="utf-8")
        self.abrir()
        notas = self.decir("Consulta las notas del proyecto")
        self.assertIn("cinco docentes", notas)
        self.assertNotIn("dos docentes", notas)
        pendientes = self.decir("¿Qué pendientes tengo en este proyecto?")
        self.assertIn("revisar la rúbrica", pendientes)
        self.assertNotIn("muestreo", pendientes)

    def test_retoma_en_breve_y_enumera_todas_las_tareas_cuando_se_pide(self):
        for accion in ("leer", "entrevistar", "medir", "escribir", "revisar"):
            self.decir("Queda pendiente " + accion)
        breve = self.decir("Retomemos la tesis")
        self.assertIn("5 pendientes", breve)
        self.assertIn("leer", breve)
        self.assertNotIn("escribir", breve)
        self.assertIn("escribir", self.decir("¿Qué dejamos pendiente?"))
