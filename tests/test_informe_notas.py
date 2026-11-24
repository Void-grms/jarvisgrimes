import tempfile
import unittest
from pathlib import Path

from asistente.conocimiento import Conocimiento
from asistente.informe_notas import guardar_informe


class Informes(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.raiz = self.base / "Bóveda"
        self.raiz.mkdir()
        self.c = Conocimiento(self.raiz, self.base / "rapidas.md")
        self.fuente = self.raiz / "Notas de clase #2.md"
        self.fuente.write_text("# Clase\n\nEl examen oral se realiza el viernes.\n", encoding="utf-8")
        self.citas = self.c.evidencias("examen")

    def test_enlace_legible_sin_duplicar_ni_reemplazar_una_edicion(self):
        def guardar():
            return guardar_informe(self.c, "Calendario del examen", self.citas)
        salida = guardar()
        md = salida.read_text("utf-8")
        self.assertIn("../Notas%20de%20clase%20%232.md", md)
        self.assertIn("línea 3", md)
        self.assertEqual(salida, guardar())
        salida.write_text(md + "\nComentario personal.\n", encoding="utf-8")
        self.assertNotEqual(salida, guardar())
        self.assertTrue(salida.read_text("utf-8").endswith("Comentario personal.\n"))
        self.assertEqual({e.ruta for e in self.c.investigar("examen")}, {self.fuente.name})

    def test_no_publica_citas_cambiadas(self):
        self.fuente.write_text("# Clase\n\nSe canceló el examen oral.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fuente cambió"):
            guardar_informe(self.c, "Calendario", self.citas)
        self.assertFalse((self.raiz / "Informes").exists())

    def test_no_guarda_un_informe_de_una_nota_fuera_de_la_boveda(self):
        self.c.notas_rapidas.write_text("Incluye la firma en la carátula.", encoding="utf-8")
        externas = self.c.evidencias("carátula")
        self.assertTrue(externas)
        with self.assertRaisesRegex(ValueError, "bóveda"):
            guardar_informe(self.c, "Firma", externas)
