import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docx import Document
from pptx import Presentation

from asistente.acciones.word_activo import DocumentoActivo, SinOfficeActivo
from asistente.config import cargar
from asistente.motor import Motor
from asistente.store import Store
from asistente.trabajo import TrabajoOffice


class FlujoOffice(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bd = self.raiz / "prueba.sqlite"
        self.store = Store(self.bd)
        self.addCleanup(self.store._con.close)
        cfg = cargar(self.raiz / "prueba.toml")
        cfg["documentos"]["carpeta"] = str(self.raiz)
        with patch("asistente.motor.Store", return_value=self.store):
            self.motor = Motor(cfg)
        self.t = self.motor.trabajo
        self.t.mostrar = Mock()
        self.t.cerebro = SimpleNamespace(generar_json=Mock(side_effect=self.generar))
        self.archivo = self.raiz / "Ensayo.docx"
        d = Document()
        d.add_paragraph("El alumnado completó la evaluación.")
        d.add_table(1, 1).cell(0, 0).text = "Grupo norte"
        d.add_heading("Conclusiones", 1)
        d.add_paragraph("Conviene repetir la evaluación.")
        d.save(self.archivo)
        self.original = self.archivo.read_bytes()
        self.frente = self.archivo
        self.capturar = self.enterContext(patch("asistente.acciones.word_activo.capturar_documento",
                                                 side_effect=self.activo))
        self.abrir = self.enterContext(patch("asistente.acciones.word_activo.abrir_verificado",
                                              side_effect=self.abierto))

    def activo(self, tipo):
        if self.frente is None:
            raise SinOfficeActivo("Office no está al frente.")
        return DocumentoActivo(self.frente, tipo, 111)

    def abierto(self, ruta):
        self.frente = ruta
        return True

    @staticmethod
    def generar(prompt, esquema, **kwargs):
        campos = esquema["properties"]
        if "resumen" in campos:
            return {"resumen": "El alumnado completó la evaluación."}
        if "diapositivas" in campos:
            return {"titulo": "Ensayo", "diapositivas": [
                {"titulo": f"Grupo {i}", "puntos": ["El alumnado completó la evaluación del curso."],
                 "notas": "Comentar el alcance."} for i in range(campos["diapositivas"]["minItems"])]}
        if "Párrafos\n" in prompt:
            fila = json.loads(prompt.split("Párrafos\n")[-1])[0]
            return {"cambios": [{"id": fila["id"], "texto": "Evaluación completada."}]}
        return {"cambios": [{"id": 2, "texto": "Evaluación completada."}]}

    def decir(self, texto):
        r = self.motor.entender(texto, forzar=True)
        self.motor.ejecutar(r)
        self.motor.aplicar(r)
        return r.texto

    def terminar(self):
        limite = time.monotonic() + 6
        while self.t.finalizando() and time.monotonic() < limite:
            time.sleep(.01)
        self.assertFalse(self.t.finalizando())
        return self.motor.avisos.get(timeout=2).texto

    def test_word_copia_correccion_panel_deshacer_y_memoria_reiniciada(self):
        self.decir("Trabajemos en el proyecto Tesis")
        self.decir("Anota como decisión usar el grupo norte")
        self.decir("Queda pendiente revisar la evaluación")
        self.assertIn("Ensayo.docx", self.decir("Resume esto en una copia, conserva las tablas y muéstrame qué cambiaste"))
        self.assertIn("Guardé", self.terminar())
        copia = self.frente
        self.assertEqual(Document(copia).tables[0].cell(0, 0).text, "Grupo norte")
        vista = self.t.mostrar.call_args.args[0]
        self.assertEqual(vista["version"]["cambios"][0]["ubicacion"], "Resumen")
        self.frente = None  # el panel de cambios está al frente
        self.decir("Espera, conserva la tabla original y cambia solo el resumen")
        self.assertIn("Guardé", self.terminar())
        breve = self.frente
        self.assertNotEqual(copia, breve)
        self.assertIn("Evaluación completada.", [p.text for p in Document(breve).paragraphs])
        self.assertIn(copia.name, self.decir("Deshaz el último cambio"))
        self.assertEqual(self.frente, copia)
        self.assertTrue(breve.is_file())
        self.assertEqual(self.archivo.read_bytes(), self.original)
        otra = Store(self.bd)
        self.addCleanup(otra._con.close)
        motor_nuevo = TrabajoOffice(otra, Mock())
        r = motor_nuevo.procesar("Retomemos la tesis. ¿Qué dejamos pendiente y qué versión estábamos usando?")
        texto = r.ejecutar()
        self.assertIn("grupo norte", texto)
        self.assertIn("revisar la evaluación", texto)
        self.assertIn(copia.name, texto)

    def test_pregunta_tiempo_recuerda_el_origen_y_corrige_la_tercera(self):
        self.assertIn("minutos", self.decir("Prepara mi exposición de mañana con el documento que tengo abierto"))
        self.frente = None
        self.decir("Tengo diez minutos")
        self.assertIn("6 diapositivas", self.terminar())
        previa = self.frente
        self.decir("La tercera tiene demasiado texto")
        self.assertIn("diapositiva 3", self.terminar())
        self.assertNotEqual(self.frente, previa)
        self.assertEqual(len(Presentation(self.frente).slides), 6)
        self.assertIn("Evaluación completada", str([s.text for s in Presentation(self.frente).slides[2].shapes if s.has_text_frame]))
        self.capturar.assert_any_call("powerpoint")

    def test_sugerir_solo_en_conversacion_y_esperar_el_si(self):
        self.decir("Convierte este informe en seis diapositivas y comprueba que estén las conclusiones")
        self.terminar()
        previa = self.frente
        pregunta = "Tengo tres minutos para esta presentación"
        self.assertIn("¿Preparo", self.decir(pregunta))
        self.assertFalse(self.t.ocupado())
        self.decir("No")
        self.assertEqual(self.frente, previa)
        self.decir(pregunta)
        self.decir("Sí")
        self.assertIn("3 diapositivas", self.terminar())
        self.assertEqual(len(Presentation(self.frente).slides), 3)
        self.assertTrue(previa.is_file())

    def test_interrumpir_y_reorientar_sin_publicar_el_primer_borrador(self):
        inicio = threading.Event()
        liberar = threading.Event()
        def generar(prompt, esquema, **kwargs):
            inicio.set()
            liberar.wait(3)
            return self.generar(prompt, esquema, **kwargs)
        self.t.cerebro.generar_json.side_effect = generar
        self.decir("Resume este documento en una copia")
        self.assertTrue(inicio.wait(2))
        self.decir("Espera, conserva la tabla y cambia solo el resumen")
        liberar.set()
        self.assertIn("Guardé", self.terminar())
        self.assertEqual(len(list(self.raiz.glob("*.docx"))), 2)
        self.assertEqual(self.abrir.call_count, 1)

    def test_pedir_ia_externa_o_hablar_de_un_tema_no_lee_word(self):
        for texto in ("Usa Claude para revisar este Word en una copia", "No resumas este archivo",
                      "Resume este correo en una copia", "Resume este archivo PDF en una copia",
                      "Crea una presentación sobre un documento legal",
                      "Prepara una exposición sobre la educación abierta"):
            self.assertIsNone(self.t.procesar(texto))
        self.capturar.assert_not_called()
