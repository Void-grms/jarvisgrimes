import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from pptx import Presentation

from asistente.materiales import guardar_resultado, preparar_material


class Materiales(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.nota = self.raiz / "Sesión.md"
        self.nota.write_text("Se evaluó la atención en dos sedes.", encoding="utf-8-sig")
        self.informe = json.dumps({"titulo": "Evaluación", "secciones": [
            {"titulo": "Hallazgos", "parrafos": ["Se evaluaron dos sedes."]}]})
        self.doc = self.raiz / "Revisión.docx"
        doc = Document()
        doc.sections[0].header.paragraphs[0].text = "Cabecera institucional"
        doc.add_heading("Atención regional", 1)
        p = doc.add_paragraph()
        p.add_run("El equipo ").italic = True
        p.add_run("publico").bold = True
        p.add_run(" su informe.")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "Información actual."
        doc.add_paragraph("Código\tValor")
        doc.save(self.doc)

    def test_informe_desde_varias_fuentes_y_notas_pegadas(self):
        otra = self.raiz / "Notas.txt"
        otra.write_text("Evaluación de Piura.", encoding="utf-16")
        m = preparar_material("informe", "Incluye los hallazgos", [self.nota, otra, self.doc])
        for texto in ("atención", "Piura", "Atención regional", "Tabla 1, fila 1, celda 1: Información actual.",
                      "hallazgos"):
            self.assertIn(texto, m.prompt)
        destino = guardar_resultado(m, self.informe, self.raiz / "Informe")
        self.assertEqual(destino.suffix, ".docx")
        self.assertEqual(Document(destino).paragraphs[-1].text, "Se evaluaron dos sedes.")
        m = preparar_material("informe", "Notas de la visita a Trujillo", [])
        self.assertIn("Trujillo", m.prompt)

    def test_presentacion_incluye_portada_contenido_y_notas(self):
        m = preparar_material("presentacion", "", self.doc)
        respuesta = {"titulo": "Atención", "subtitulo": "Avances", "diapositivas": [
            {"titulo": "Resultados", "puntos": ["Se evaluaron dos sedes."], "notas": "Explicar el alcance."}]}
        ruta = guardar_resultado(m, "```json\n" + json.dumps(respuesta) + "\n```", self.raiz / m.nombre_sugerido)
        presentacion = Presentation(ruta)
        self.assertEqual(len(presentacion.slides), 2)
        self.assertIn("Explicar el alcance.", presentacion.slides[1].notes_slide.notes_text_frame.text)
        self.assertTrue(any("dos sedes" in shape.text for shape in presentacion.slides[1].shapes if shape.has_text_frame))

    def test_revision_conserva_partes_formato_tabla_y_original(self):
        inicial = self.doc.read_bytes()
        m = preparar_material("revision", "Corrige ortografía", [self.doc])
        self.assertIn('"ubicacion": "tabla"', m.prompt)
        cambios = {"cambios": [{"id": 2, "texto": "El equipo publicó su informe."},
                               {"id": 3, "texto": "Información actualizada."}]}
        salida = guardar_resultado(m, json.dumps(cambios), self.raiz / m.nombre_sugerido)
        doc = Document(salida)
        self.assertEqual(doc.paragraphs[0].style.name, "Heading 1")
        self.assertEqual(doc.paragraphs[1].runs[1].text, "publicó")
        self.assertTrue(doc.paragraphs[1].runs[0].italic)
        self.assertTrue(doc.paragraphs[1].runs[1].bold)
        self.assertEqual(doc.tables[0].cell(0, 0).text, "Información actualizada.")
        self.assertEqual(self.doc.read_bytes(), inicial)
        with ZipFile(self.doc) as original, ZipFile(salida) as editado:
            self.assertEqual(original.namelist(), editado.namelist())
            for nombre in original.namelist():
                if nombre != "word/document.xml":
                    self.assertEqual(original.read(nombre), editado.read(nombre))

    def test_revisiones_sin_cambios_fuente_cambiada_y_destino_existente(self):
        m = preparar_material("revision", "", self.doc)
        respuesta = '{"cambios":[]}'
        destino = guardar_resultado(m, respuesta, self.raiz / m.nombre_sugerido)
        self.assertEqual(destino.read_bytes(), self.doc.read_bytes())
        for archivo in (self.doc, destino):
            with self.assertRaises(FileExistsError):
                guardar_resultado(m, respuesta, archivo)
        d = Document(self.doc)
        d.add_paragraph("Edición posterior")
        d.save(self.doc)
        with self.assertRaisesRegex(ValueError, "original cambió"):
            guardar_resultado(m, respuesta, self.raiz / "posterior.docx")
        self.assertFalse((self.raiz / "posterior.docx").exists())

    def test_rechaza_parrafos_inexistentes_complejos_o_duplicados(self):
        m = preparar_material("revision", "", [self.doc])
        desde = set(self.raiz.iterdir())
        for cambios in ([{"id": 55, "texto": "x"}], [{"id": True, "texto": "x"}],
                        [{"id": 4, "texto": "Reemplazo del código"}],
                        [{"id": 3, "texto": "x"}, {"id": 3, "texto": "y"}]):
            with self.subTest(cambios=cambios), self.assertRaises(ValueError):
                guardar_resultado(m, json.dumps({"cambios": cambios}), self.raiz / "cambios.docx")
        self.assertEqual(desde, set(self.raiz.iterdir()))

    def test_fuentes_invalidas_y_json_invalido_no_dejan_archivos(self):
        falso = self.raiz / "inválido.docx"
        falso.write_text("archivo incorrecto", encoding="utf-8")
        for tipo, rutas in (("informe", [falso]), ("revision", [self.doc, self.doc]), ("revision", [self.nota]),
                            ("presentacion", []), ("informe", [self.raiz / "perdido.txt"])):
            with self.subTest(tipo=tipo, rutas=rutas), self.assertRaises(ValueError):
                preparar_material(tipo, "", rutas)
        m = preparar_material("informe", "", [self.nota])
        antes = set(self.raiz.iterdir())
        for respuesta in ('Texto suelto', '{"titulo": "uno", "titulo":"dos"}',
                          '{"titulo":"Informe","secciones":[]}', '{"titulo":"Informe", "secciones":[{}]}'):
            with self.subTest(respuesta=respuesta), self.assertRaises(ValueError):
                guardar_resultado(m, respuesta, self.raiz / m.nombre_sugerido)
        with patch("asistente.materiales.escribir_docx", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                guardar_resultado(m, self.informe, self.raiz / m.nombre_sugerido)
        self.assertEqual(antes, set(self.raiz.iterdir()))

    def test_dos_resultados_simultaneos_no_se_pisan(self):
        m = preparar_material("informe", "", self.nota)
        def guardar():
            try:
                return guardar_resultado(m, self.informe, self.raiz / "mismo.docx").is_file()
            except FileExistsError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(lambda _: guardar(), range(2)))
        self.assertCountEqual(resultados, [True, False])
        self.assertEqual(Document(self.raiz / "mismo.docx").paragraphs[0].text, "Evaluación")
