import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from asistente.config import DEFAULTS
from asistente.motor import Motor
from asistente.store import Store


class VozObsidian(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.raiz = base / "Bóveda"
        self.raiz.mkdir()
        cfg = copy.deepcopy(DEFAULTS)
        cfg["documentos"]["carpeta"] = str(base)
        cfg["conocimiento"]["carpeta"] = str(self.raiz)
        db = Store(base / "prueba.sqlite")
        self.addCleanup(db._con.close)
        with patch("asistente.motor.Store", return_value=db):
            self.motor = Motor(cfg)

    def decir(self, texto):
        r = self.motor.entender(texto, forzar=True, por_voz=True)
        self.motor.ejecutar(r)
        self.motor.aplicar(r)
        return r.texto

    def test_de_voz_a_obsidian_y_de_la_casilla_editada_a_la_voz(self):
        self.decir("Jarvis, trabajemos en el proyecto Tesis")
        self.decir("Recuerda que las entrevistas requieren permiso")
        self.decir("Queda pendiente revisar las citas")
        self.assertIn("revisar las citas", self.decir("¿Qué hicimos hoy?"))
        ruta = self.raiz / "Proyectos" / "Tesis.md"
        contenido = ruta.read_text("utf-8")
        self.assertIn("- [ ] revisar las citas", contenido)
        ruta.write_text(contenido.replace("- [ ] revisar las citas", "- [x] revisar las citas"), "utf-8")
        self.assertIn("No quedan pendientes", self.decir("¿Qué dejamos pendiente?"))
        with patch("asistente.obsidian.os.startfile", create=True) as abrir:
            self.decir("Jarvis, abre la nota de la tesis en Obsidian")
            uri = urlsplit(abrir.call_args.args[0])
        self.assertEqual((uri.scheme, uri.netloc), ("obsidian", "open"))
        self.assertEqual(Path(parse_qs(uri.query)["path"][0]), ruta)

    def test_cita_la_fuente_y_espera_la_eleccion_explicita(self):
        nota = self.raiz / "Opciones.md"
        nota.write_text("# Entrevistas\n\nLas entrevistas presenciales requieren una semana.\n", encoding="utf-8")
        local = self.motor.cerebro = Mock(url="http://localhost:11434")
        generar = local.para_documento.return_value.generar_json
        generar.return_value = {"propuesta": "Reservar una semana para entrevistas", "fuentes": [1]}
        self.decir("Trabajemos en el proyecto Tesis")
        self.assertIn("opciones", self.decir("Ayúdame a decidir"))
        propuesta = self.decir("entre entrevistas presenciales y una encuesta")
        self.assertIn("Opciones.md, línea 3", propuesta)
        self.assertIn("Reservar una semana", propuesta)
        self.decir("No")
        ficha = self.raiz / "Proyectos" / "Tesis.md"
        self.assertNotIn("Reservar una semana", ficha.read_text("utf-8"))
        self.decir("Ayúdame a decidir entre entrevistas y una encuesta")
        self.decir("He decidido usar una encuesta breve")
        self.assertIn("usar una encuesta breve", ficha.read_text("utf-8"))
        self.assertIn("usar una encuesta breve", self.decir("¿Qué hicimos hoy?"))

    def test_fuente_releida_y_ninguna_transferencia_a_un_modelo_remoto(self):
        nota = self.raiz / "Sala.md"
        nota.write_text("# Alquiler\n\nLa sala abre los martes.\n", encoding="utf-8")
        remoto = self.motor.cerebro = Mock(url="https://modelo.example")
        self.assertIn("martes", self.decir("Ayúdame a decidir el alquiler de la sala"))
        nota.write_text("# Alquiler\n\nLa sala abre los jueves.\n", encoding="utf-8")
        siguiente = self.decir("Ayúdame a decidir el alquiler de la sala")
        self.assertIn("jueves", siguiente)
        self.assertNotIn("martes", siguiente)
        remoto.para_documento.assert_not_called()
