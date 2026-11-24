import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from pptx import Presentation

from asistente.acciones.ventanas import Ventana
from asistente.acciones.word_activo import DocumentoActivo, SinOfficeActivo
from asistente.config import DEFAULTS
from asistente.motor import Motor
from asistente.proyectos import huella
from asistente.rutinas import _abrir_ficha, _nota_abierta
from asistente.store import Store


class RutinasPersonales(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["documentos"]["carpeta"] = str(self.base / "Documentos")
        self.boveda = self.base / "Bóveda"
        self.cfg["conocimiento"]["carpeta"] = str(self.boveda)
        self.captura = self.enterContext(patch("asistente.rutinas.word_activo.capturar_documento",
                                                side_effect=SinOfficeActivo("Otra ventana.")))
        self.reiniciar()
        self.decir("Trabajemos en el proyecto Tesis")
        self.decir("Queda pendiente revisar las citas")
        self.decir("Queda pendiente revisar los anexos")

    def reiniciar(self):
        db = Store(self.base / "jarvis.db")
        self.addCleanup(db._con.close)
        with patch("asistente.motor.Store", return_value=db):
            self.motor = Motor(self.cfg)
        self.p = self.motor.trabajo.proyectos

    def decir(self, texto):
        respuesta = self.motor.entender(texto, forzar=True, por_voz=True)
        self.motor.ejecutar(respuesta)
        self.motor.aplicar(respuesta)
        return respuesta.texto

    def word(self, nombre):
        ruta = self.base / nombre
        doc = Document()
        doc.add_paragraph(nombre)
        doc.save(ruta)
        return ruta

    def test_plantilla_usa_el_word_verificado_de_la_version_activa_y_primer_pendiente(self):
        uno, dos = self.word("Original.docx"), self.word("Revisado.docx")
        ppt = self.base / "Exposición.pptx"
        Presentation().save(ppt)
        clave = self.p.actual()
        self.p.registrar(clave, uno, huella(uno), dos, huella(dos), "Resumen en copia", [])
        self.p.registrar(clave, dos, huella(dos), ppt, huella(ppt), "Exposición", [])
        with (patch("asistente.rutinas._abrir_ficha", return_value="Ficha abierta.") as nota,
              patch("asistente.rutinas.word_activo.abrir_verificado", return_value=True) as office):
            res = self.decir("Jarvis, prepara mi sesión de tesis")
        self.assertIn("revisar las citas", res)
        self.assertNotIn("revisar los anexos", res)
        self.assertIn(dos.name, res)
        office.assert_called_once_with(dos)
        nota.assert_called_once_with(self.boveda, self.boveda / "Proyectos" / "Tesis.md")
        self.assertIn("No hay una rutina", self.decir("Reanuda la rutina"))
        self.p.anterior(clave)
        self.p.anterior(clave)
        self.assertEqual(self.p.word_de_trabajo(clave), uno)

    def test_reintenta_desde_word_tras_reiniciar_y_vincula_el_archivo_correcto(self):
        ruta = self.word("Citas.docx")
        with (patch("asistente.rutinas._abrir_ficha", return_value="Ficha abierta.") as nota,
              patch("asistente.rutinas.word_activo.abrir_verificado", return_value=True) as office):
            self.assertIn("rutina está en pausa", self.decir("Prepara mi sesión de tesis"))
            office.assert_not_called()
            self.reiniciar()
            self.captura.side_effect = None
            self.captura.return_value = DocumentoActivo(ruta, "word", 10)
            self.assertIn("Citas.docx", self.decir("Vincula este Word a la rutina"))
            self.assertIn("revisar las citas", self.decir("Reanuda la rutina"))
            nota.assert_called_once()
            office.assert_not_called()  # el Word elegido sigue al frente

    def test_prepar_solo_activa_la_orden_de_sesion_completa(self):
        with patch.object(self.motor.rutinas, "_ejecutar", return_value="Rutina iniciada.") as ejecutar:
            self.assertEqual("Rutina iniciada.", self.decir("Jarvis, prepar mi sesión de tesis"))
            ejecutar.assert_called_once()
            for frase in ("Hablamos de prepar mi sesión de tesis", "No prepar mi sesión de tesis",
                          "Escribe prepar mi sesión de tesis", "Prepar sesión de tesis", "Prepar Word"):
                with self.subTest(frase=frase):
                    self.assertIsNone(self.motor.rutinas.procesar(frase))

    def test_lee_la_casilla_editada_y_respeta_cambio_de_archivo(self):
        ruta = self.word("Lectura.docx")
        self.p.vincular_word(self.p.actual(), ruta)
        self.word("Lectura modificada.docx").replace(ruta)
        ficha = self.p.ruta_nota("Tesis")
        ficha.write_text(ficha.read_text("utf-8").replace("- [ ] revisar las citas", "- [x] revisar las citas"), "utf-8")
        with (patch("asistente.rutinas._abrir_ficha", return_value="Ficha abierta."),
              patch("asistente.rutinas.word_activo.abrir_verificado") as office):
            self.assertIn("se movió o cambió", self.decir("Prepara mi sesión de tesis"))
            office.assert_not_called()
            self.assertIn("revisar los anexos", self.decir("Omite este paso de la rutina"))

    def test_ensenar_guardar_confirmar_reemplazo_y_no_ejecutar_otras_ordenes(self):
        self.assertIn("Qué pasos", self.decir("Enséñame la rutina Lectura para el proyecto Tesis"))
        self.assertIn("admite", self.decir("Sube los archivos a la nube"))
        self.assertIn("¿Guardo", self.decir("dime el primer pendiente"))
        self.assertIn("Guardé la rutina", self.decir("Sí"))
        self.decir("Configura la rutina Lectura para el proyecto Tesis")
        self.assertIn("Reemplazo", self.decir("abre la ficha y abre el Word"))
        self.decir("No")
        self.reiniciar()
        self.assertIn("Lectura", self.decir("Qué rutinas tengo"))
        with (patch("asistente.rutinas._abrir_ficha") as nota,
              patch("asistente.rutinas.word_activo.abrir_verificado") as office):
            self.assertIn("revisar las citas", self.decir("ejecuta la rutina Lectura"))
            nota.assert_not_called()
            office.assert_not_called()
            self.assertIsNone(self.motor.rutinas.procesar("No prepares mi sesión de tesis"))
            self.assertIsNone(self.motor.rutinas.procesar("Escribe: prepara mi sesión de tesis"))

    def test_una_ficha_ya_abierta_no_lanza_otra_instancia_y_no_confunde_bovedas(self):
        ruta = self.p.ruta_nota("Tesis")
        appdata = self.base / "appdata"
        (appdata / "obsidian").mkdir(parents=True)
        registro = appdata / "obsidian" / "obsidian.json"
        registro.write_text(json.dumps({"vaults": {"a": {"path": str(self.boveda)}}}), "utf-8")
        (self.boveda / ".obsidian").mkdir()
        vista = self.boveda / ".obsidian" / "workspace.json"
        vista.write_text(json.dumps({"active":"x", "main":{"children":[{"id":"x","type":"leaf",
                        "state":{"type":"markdown","state":{"file":"Proyectos/Tesis.md"}}}]}}), "utf-8")
        v = Ventana(43, "Tesis - Bóveda - Obsidian v1.9", "obsidian")
        with (patch.dict(os.environ, {"APPDATA":str(appdata)}),
              patch("asistente.rutinas.ventanas.listar", return_value=[v]),
              patch("asistente.obsidian.os.startfile", create=True) as lanzar):
            self.assertIn("ya estaba abierta", _abrir_ficha(self.boveda, ruta))
            lanzar.assert_not_called()
            registro.write_text(json.dumps({"vaults": {"a": {"path": str(self.boveda)},
                                  "b": {"path": str(self.base / "otra" / "Bóveda")}}}), "utf-8")
            self.assertFalse(_nota_abierta(self.boveda, ruta))
