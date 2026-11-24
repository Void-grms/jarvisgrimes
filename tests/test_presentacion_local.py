import io
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

from docx import Document
from lxml import etree
from pptx import Presentation

from asistente.acciones.documentos import escribir_pptx
from asistente.presentacion_local import acortar_presentacion, crear_presentacion, revisar_diapositiva


class PresentacionLocal(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.word = self.raiz / "Informe de campo.docx"
        doc = Document()
        doc.add_heading("Seguimiento de campo", level=0)
        doc.add_paragraph("La evaluación cubre catorce muestras.")
        doc.add_table(1, 1).cell(0, 0).text = "Hubo dos sedes."
        doc.add_heading("Conclusiones", 1)
        doc.add_paragraph("Las muestras conservaron su estabilidad.")
        doc.save(self.word)
        self.ppt = self.raiz / "Primera.pptx"
        self.contenido = [{"titulo": f"Lámina {i}", "puntos": [f"Un punto extenso para revisar en la lámina número {i}."],
                            "notas": f"Nota del orador {i}."} for i in range(2, 7)]
        escribir_pptx({"titulo": "Origen", "diapositivas": self.contenido}, self.ppt)

    def test_crea_seis_incluyendo_portada_con_citas_y_tiempo(self):
        inicial = self.word.read_bytes()
        generar = Mock(return_value={"titulo": "Contenido", "diapositivas": self.contenido[:4]})
        r = crear_presentacion(self.word, generar, cantidad=6, minutos=10, instruccion="Para la clase")
        self.assertEqual(r.total, 6)
        self.assertIn("Tabla 1, fila 1, celda 1. Hubo dos sedes.", generar.call_args.args[0])
        self.assertIn("10 minutos", generar.call_args.args[0])
        ppt = Presentation(r.ruta)
        self.assertEqual(len(ppt.slides), 6)
        self.assertIn("Conclusiones", [s.text for s in ppt.slides[-1].shapes if s.has_text_frame])
        ultimo = " ".join(s.text for s in ppt.slides[-1].shapes if s.has_text_frame)
        self.assertIn("Las muestras conservaron su estabilidad.", ultimo)
        self.assertEqual(self.word.read_bytes(), inicial)
        segundos = 0
        for slide in ppt.slides:
            m = re.search(r"Tiempo orientativo (\d+):(\d{2})", slide.notes_slide.notes_text_frame.text)
            segundos += int(m[1])*60 + int(m[2])
        self.assertEqual(segundos, 600)

    def test_cierre_sin_conclusiones_y_nombre_unico(self):
        doc = Document()
        doc.add_paragraph("Se registró la lluvia de abril.")
        doc.save(self.word)
        a = crear_presentacion(self.word, Mock(), cantidad=2)
        b = crear_presentacion(self.word, Mock(), cantidad=2)
        self.assertNotEqual(a.ruta, b.ruta)
        self.assertIn("carece", b.cambios[-1])
        final = " ".join(s.text for s in Presentation(b.ruta).slides[1].shapes if s.has_text_frame)
        self.assertIn("Ideas de cierre", final)
        self.assertIn("Se registró la lluvia de abril.", final)

    def test_modelo_inconsistente_no_publica_ni_trunca(self):
        generar = Mock(return_value={"titulo": "Mal", "diapositivas": self.contenido[:1]})
        previo = set(self.raiz.iterdir())
        with self.assertRaisesRegex(ValueError, "formato"):
            crear_presentacion(self.word, generar, cantidad=6)
        self.assertEqual(generar.call_count, 2)
        self.assertEqual(set(self.raiz.iterdir()), previo)

    def test_revision_conserva_otras_partes_e_incluso_orden_previo(self):
        # Presentación de PowerPoint con orden de visualización distinto al nombre slideN.xml.
        datos = self.ppt.read_bytes()
        with ZipFile(io.BytesIO(datos)) as entrada, ZipFile(self.ppt, "w") as salida:
            for m in entrada.infolist():
                contenido = entrada.read(m)
                if m.filename == "ppt/presentation.xml":
                    dom = etree.fromstring(contenido)
                    sld = dom.find("{*}sldIdLst")
                    sld.insert(2, sld[4])
                    contenido = etree.tostring(dom, encoding="UTF-8")
                salida.writestr(m, contenido)
        original = self.ppt.read_bytes()
        modelo = Mock(return_value={"cambios": [{"id": 2, "texto": "Texto breve."}]})
        r = revisar_diapositiva(self.ppt, 3, "La tercera tiene demasiado texto", modelo)
        self.assertIn("Lámina 5", modelo.call_args.args[0])
        nuevo = Presentation(r.ruta)
        self.assertEqual(len(nuevo.slides), 6)
        self.assertIn("•  Texto breve.", [s.text for s in nuevo.slides[2].shapes if s.has_text_frame])
        self.assertEqual(self.ppt.read_bytes(), original)
        with ZipFile(self.ppt) as previo, ZipFile(r.ruta) as hecho:
            self.assertEqual(hecho.namelist(), previo.namelist())
            self.assertEqual([n for n in hecho.namelist() if previo.read(n) != hecho.read(n)],
                             ["ppt/slides/slide5.xml"])

    def test_fuera_de_rango_y_contenido_no_reducido(self):
        previo = set(self.raiz.iterdir())
        with self.assertRaisesRegex(ValueError, "entre 1 y 6"):
            revisar_diapositiva(self.ppt, 20, "más corta", Mock())
        modelo = Mock(return_value={"cambios": [{"id": 2, "texto": "un texto mucho más extenso " * 10}]})
        with self.assertRaisesRegex(ValueError, "cambio válido"):
            revisar_diapositiva(self.ppt, 3, "demasiado texto", modelo)
        self.assertEqual(set(self.raiz.iterdir()), previo)

    def test_cancelacion_y_cambio_concurrente_no_dejan_copia(self):
        previo = set(self.raiz.iterdir())
        evento = threading.Event()
        def cancelar(*args, **kwargs):
            evento.set()
            return {"titulo": "Contenido", "diapositivas": self.contenido[:2]}
        with self.assertRaises(InterruptedError):
            crear_presentacion(self.word, cancelar, cantidad=4, cancelar=evento)
        def modificar(*args, **kwargs):
            doc = Document(self.word)
            doc.add_paragraph("Una edición nueva.")
            doc.save(self.word)
            return {"titulo": "Contenido", "diapositivas": self.contenido[:2]}
        with self.assertRaisesRegex(ValueError, "origen cambió"):
            crear_presentacion(self.word, modificar, cantidad=4)
        self.assertEqual(set(self.raiz.iterdir()), previo)

    def test_acortar_comprueba_portada_cierre_y_orden(self):
        origen = self.ppt.read_bytes()
        r = acortar_presentacion(self.ppt, 3, minutos=4)
        reducido = Presentation(r.ruta)
        anterior = Presentation(self.ppt)
        self.assertEqual([s._element.xml for s in reducido.slides], [anterior.slides[i]._element.xml for i in (0, 2, 5)])
        self.assertIn("Nota del orador 3.", reducido.slides[1].notes_slide.notes_text_frame.text)
        self.assertIn("Tiempo orientativo 1:20", reducido.slides[1].notes_slide.notes_text_frame.text)
        self.assertEqual(self.ppt.read_bytes(), origen)

    def test_acortar_detecta_salto_a_diapositiva(self):
        ppt = Presentation(self.ppt)
        ppt.slides[1].shapes[1].click_action.target_slide = ppt.slides[-1]
        ppt.save(self.ppt)
        previo = set(self.raiz.iterdir())
        with self.assertRaisesRegex(ValueError, "navegación interna"):
            acortar_presentacion(self.ppt, 2)
        self.assertEqual(set(self.raiz.iterdir()), previo)
