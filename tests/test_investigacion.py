import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from asistente.config import DEFAULTS
from asistente.conocimiento import Evidencia
from asistente.investigacion import procesar_investigacion
from asistente.motor import Motor
from asistente.store import Store
from asistente.texto import Frase


class Investigacion(unittest.TestCase):
    def setUp(self):
        alojamiento = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.raiz = alojamiento / "Bóveda"
        self.raiz.mkdir()
        self.a = self.raiz / "Visita A.md"
        self.b = self.raiz / "Visita B.md"
        self.a.write_text("# Visita A\n\nEl aforo de la sala es de 20 personas.\n", encoding="utf-8")
        self.b.write_text("# Visita B\n\nEl aforo de la sala es de 30 personas.\n", encoding="utf-8")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["conocimiento"]["carpeta"] = str(self.raiz)
        cfg["conocimiento"]["busqueda_semantica"] = False
        cfg["documentos"]["carpeta"] = str(alojamiento)
        db = Store(alojamiento / "test.sqlite")
        self.addCleanup(db._con.close)
        with patch("asistente.motor.Store", return_value=db):
            self.motor = Motor(cfg)

    def decir(self, texto):
        r = self.motor.entender(texto, forzar=True, por_voz=True)
        self.motor.ejecutar(r)
        self.motor.aplicar(r)
        return r.texto

    def test_compara_muestra_rutas_y_guarda_informe_solo_por_pedido(self):
        texto = self.decir("Jarvis, compara lo que dicen mis notas sobre el aforo de la sala")
        self.assertIn("cifras distintas", texto)
        for ruta, num in ((self.a, 20), (self.b, 30)):
            self.assertIn(f"{num} personas", texto)
            self.assertIn(f"{ruta.name}, línea 3", texto)
        self.assertFalse((self.raiz / "Informes").exists())
        self.assertIn("Visita A.md, línea 3", self.decir("¿De dónde salió eso?"))
        self.assertIn("Guardé", self.decir("Guarda el informe"))
        guardado, = (self.raiz / "Informes").glob("*.md")
        informe = guardado.read_text(encoding="utf-8")
        self.assertIn("20 personas", informe)
        self.assertIn("30 personas", informe)
        self.assertIn("Visita%20B.md", informe)

    def test_fuentes_se_revalidan_y_la_copia_se_rechaza_si_cambian(self):
        self.decir("Investiga en mis notas sobre el aforo de la sala")
        self.a.write_text("# Visita A\n\nEl aforo de la sala es de 80 personas.\n", encoding="utf-8")
        texto = self.decir("Muéstrame las fuentes")
        self.assertNotIn("Visita A.md", texto)
        self.assertIn("Visita B.md", texto)
        self.assertIn("cambió", texto)
        self.assertIn("cambió", self.decir("Guarda la investigación"))
        self.assertFalse((self.raiz / "Informes").exists())
        self.assertIn("80 personas", self.decir("Repite la investigación"))

    def test_otro_turno_descarta_fuentes_anteriores(self):
        self.decir("Investiga el aforo de la sala en mis notas")
        self.assertIsNotNone(self.motor.pendiente)
        self.decir("¿Qué hicimos hoy?")
        self.assertIsNone(self.motor.pendiente)
        self.assertIsNone(self.motor.boveda.procesar("Muéstrame las fuentes"))

    def test_fuentes_breves_y_transcripcion_observada_solo_durante_la_investigacion(self):
        with patch("asistente.obsidian.os.startfile", create=True) as abrir:
            self.decir("Jarvis, investigar en mis notas el aforo")
            for pedido in ("Wistame las fuentes.", "Guéstame las fuentes.", "Las fuentes", "Fuentes"):
                self.assertIn("Visita A.md, línea 3", self.decir(pedido))
                self.assertIsNone(self.motor.boveda.procesar(pedido))
            abrir.assert_not_called()

    def test_una_nota_y_busqueda_sin_resultados(self):
        self.b.unlink()
        self.assertIn("una sola nota", self.decir("Compara mis notas sobre el aforo"))
        self.assertIn("No encontré", self.decir("Investiga en mis notas sobre oxitocina"))
        self.assertIsNone(self.motor.pendiente)

    def test_acuerdo_literal_y_cobertura_parcial(self):
        consulta = Mock(busqueda_parcial=True)
        fragmento = "Solo se visitará la sala norte."
        consulta.investigar.return_value = [Evidencia("A.md", 3, "A", fragmento),
                                            Evidencia("B.md", 5, "B", fragmento)]
        r = procesar_investigacion(Frase("Compara mis notas sobre la sala"), consulta)
        self.assertIn("Ambas notas recogen", r.texto)
        self.assertEqual(r.texto.count(fragmento), 1)
        self.assertIn("Aún quedan", r.texto)
