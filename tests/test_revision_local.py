import hashlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from docx import Document
from lxml import etree
from PIL import Image

from asistente.revision_local import revisar_word


class RevisarWord(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archivo = self.raiz / "Evaluación.docx"
        d = Document()
        d.add_heading("Resultados del curso", 0)
        parrafo = d.add_paragraph("La evaluación ")
        parrafo.add_run("incluyó").bold = True
        parrafo.add_run(" catorce estudiantes.")
        tabla = d.add_table(rows=1, cols=2)
        tabla.cell(0, 0).text = "Muestra"
        tabla.cell(0, 1).text = "14"
        img = io.BytesIO()
        Image.new("RGB", (12, 12), "blue").save(img, "PNG")
        img.seek(0)
        d.add_picture(img)
        d.sections[0].header.paragraphs[0].text = "Cabecera trimestral"
        d.add_heading("Conclusiones", 1)
        d.add_paragraph("La evaluación se completó en una semana.")
        d.save(self.archivo)
        self.original = self.archivo.read_bytes()

    def test_resumir_y_corregir_solo_resumen_en_dos_versiones(self):
        c = SimpleNamespace(generar_json=Mock(return_value={"resumen": "Participaron catorce estudiantes."}))
        res = revisar_word(self.archivo, "Resume este informe", c)
        self.assertIn("Muestra | 14", c.generar_json.call_args.args[0])
        self.assertEqual(res.tablas, 1)
        self.assertEqual(res.cambios[0].antes, "")
        self.assertEqual(res.cambios[0].despues, "Participaron catorce estudiantes.")
        self.assertEqual(hashlib.sha256(res.copia.read_bytes()).hexdigest(), res.huella_copia)
        copia = Document(res.copia)
        self.assertEqual([p.text for p in copia.paragraphs[:3]],
            ["Resultados del curso", "Resumen", "Participaron catorce estudiantes."])
        with ZipFile(self.archivo) as original, ZipFile(res.copia) as ver:
            for parte in original.namelist():
                if parte != "word/document.xml":
                    self.assertEqual(ver.read(parte), original.read(parte), parte)
        self.assertEqual(etree.tostring(copia.tables[0]._tbl, method="c14n"),
                         etree.tostring(Document(self.archivo).tables[0]._tbl, method="c14n"))
        self.assertTrue(copia.paragraphs[3].runs[1].bold)
        def acortar(prompt, *_args, **_kwargs):
            filas = json.loads(prompt.split("Párrafos\n", 1)[1])
            self.assertEqual([f["texto"] for f in filas], ["Participaron catorce estudiantes."])
            return {"cambios": [{"id": filas[0]["id"], "texto": "Participaron 14 estudiantes."}]}
        c.generar_json.side_effect = acortar
        ver = revisar_word(res.copia, "Más corto", c, modo="solo_resumen")
        self.assertEqual(ver.cambios[0].antes, res.cambios[0].despues)
        self.assertEqual(Document(ver.copia).paragraphs[2].text, "Participaron 14 estudiantes.")
        self.assertEqual(Document(res.copia).paragraphs[2].text, "Participaron catorce estudiantes.")
        self.assertEqual(self.archivo.read_bytes(), self.original)

    def test_revisa_solo_cuerpo_y_no_acepta_ids_de_tabla(self):
        def corregir(prompt, *_args, **_kwargs):
            filas = json.loads(prompt.split("Párrafos\n", 1)[1])
            self.assertFalse(any(f["texto"] == "Muestra" for f in filas))
            fila = next(f for f in filas if "catorce" in f["texto"])
            return {"cambios": [{"id": fila["id"], "texto": fila["texto"].replace("catorce", "14")}]}
        c = SimpleNamespace(generar_json=Mock(side_effect=corregir))
        r = revisar_word(self.archivo, "Escribe los números con cifras", c, modo="revision")
        self.assertEqual(len(r.cambios), 1)
        self.assertEqual(Document(r.copia).paragraphs[1].text, "La evaluación incluyó 14 estudiantes.")
        c.generar_json.side_effect = None
        c.generar_json.return_value = {"cambios": [{"id": 3, "texto": "otra tabla"}]}
        with self.assertRaisesRegex(ValueError, "fuera de la sección"):
            revisar_word(self.archivo, "Revisa", c, modo="revision")
        self.assertEqual(set(self.raiz.iterdir()), {self.archivo, r.copia})

    def test_cancelacion_o_modificacion_concurrente_no_publican(self):
        senal = threading.Event()
        def cancelar(*_, **__):
            senal.set()
            return {"resumen": "Se perdió la instrucción."}
        c = SimpleNamespace(generar_json=Mock(side_effect=cancelar))
        with self.assertRaises(InterruptedError):
            revisar_word(self.archivo, "Resume", c, cancelado=senal)
        self.assertEqual(list(self.raiz.iterdir()), [self.archivo])
        def cambiar(*_, **__):
            doc = Document(self.archivo)
            doc.add_paragraph("Corrección hecha por el autor.")
            doc.save(self.archivo)
            return {"resumen": "Catorce personas."}
        c.generar_json.side_effect = cambiar
        with self.assertRaisesRegex(ValueError, "cambió"):
            revisar_word(self.archivo, "Resume", c)
        self.assertEqual(list(self.raiz.iterdir()), [self.archivo])
        self.assertEqual(Document(self.archivo).paragraphs[-1].text, "Corrección hecha por el autor.")

    def test_una_tabla_danada_no_se_publica(self):
        from asistente.materiales import escribir_cuerpo_word
        def estropear(datos, doc, temporal):
            doc.tables[0].cell(0, 1).text = "incorrecto"
            escribir_cuerpo_word(datos, doc, temporal)
        cerebro = SimpleNamespace(generar_json=Mock(return_value={"resumen": "Catorce estudiantes."}))
        with patch("asistente.revision_local.escribir_cuerpo_word", side_effect=estropear):
            with self.assertRaisesRegex(ValueError, "verificación"):
                revisar_word(self.archivo, "Resume", cerebro)
        self.assertEqual(list(self.raiz.iterdir()), [self.archivo])
        self.assertEqual(self.archivo.read_bytes(), self.original)

    def test_versiones_simultaneas_no_se_sobrescriben(self):
        c = SimpleNamespace(generar_json=Mock(return_value={"resumen": "Catorce estudiantes."}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(lambda _: revisar_word(self.archivo, "Resume", c), range(2)))
        self.assertNotEqual(resultados[0].copia, resultados[1].copia)
        self.assertEqual([Document(r.copia).paragraphs[2].text for r in resultados],
                         ["Catorce estudiantes."] * 2)

    def test_una_url_externa_o_seccion_ambigua_no_envia_nada(self):
        c = SimpleNamespace(generar_json=Mock(), url="https://externo.invalid")
        with self.assertRaisesRegex(ValueError, "en este equipo"):
            revisar_word(self.archivo, "Resume", c)
        c.url = "http://127.0.0.1:11434"
        with self.assertRaisesRegex(ValueError, "resumen editable"):
            revisar_word(self.archivo, "Solo el resumen", c, modo="solo_resumen")
        c.generar_json.assert_not_called()

    def test_si_el_modelo_mezcla_cifras_y_periodos_recupera_frases_del_original(self):
        doc = Document()
        doc.add_paragraph("En junio, el huerto consumió veinte litros diarios. "
                          "En julio, el consumo bajó a quince litros diarios.")
        doc.save(self.archivo)
        c = SimpleNamespace(generar_json=Mock(return_value={"resumen": "En junio, el huerto consumió 20 y 15 litros."}))
        r = revisar_word(self.archivo, "Resume los consumos de junio y julio en dos frases.", c)
        resumen = r.cambios[0].despues
        self.assertEqual(resumen, doc.paragraphs[0].text)
        c.generar_json.return_value = {"cambios": [{"id": 2, "texto": "En junio se gastaron 20 y 15 litros."}]}
        r2 = revisar_word(r.copia, "Más corto, en una sola frase.", c, modo="solo_resumen")
        self.assertIn("En junio, el huerto consumió veinte litros diarios, y en julio", r2.cambios[0].despues)
        self.assertIn("quince litros diarios", r2.cambios[0].despues)

    def test_un_resumen_existente_identificado_por_estilo_termina_en_el_proximo_titulo(self):
        doc = Document()
        doc.add_heading("Resumen ejecutivo", level=1)
        doc.add_paragraph("El huerto usa sensores.")
        doc.add_heading("Metodología", level=1)
        doc.add_paragraph("Se midió todo el terreno.")
        doc.save(self.archivo)
        def revisar(prompt, *_, **__):
            filas = json.loads(prompt.split("Párrafos\n", 1)[1])
            self.assertEqual(len(filas), 1)
            self.assertNotIn("midió", prompt)
            return {"cambios": [{"id": filas[0]["id"], "texto": "Hay sensores en el huerto."}]}
        c = SimpleNamespace(generar_json=revisar)
        r = revisar_word(self.archivo, "Aclara el resumen", c, modo="solo_resumen")
        copia = Document(r.copia)
        self.assertEqual(copia.paragraphs[1].text, "Hay sensores en el huerto.")
        self.assertEqual(copia.paragraphs[-1].text, "Se midió todo el terreno.")
