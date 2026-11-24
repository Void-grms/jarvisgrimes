import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from asistente.diario import Diario


class PruebasDiario(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.raiz = Path(self.tmp) / "Bóveda de prueba"
        self.ahora = datetime(2026, 11, 22, 23, 59)
        self.diario = Diario(self.raiz, reloj=lambda: self.ahora)

    def test_escribe_fuente_y_lee_cambios_manuales_de_ayer(self):
        fuente = self.raiz / "Proyectos" / "Tesis #2.md"
        fuente.parent.mkdir(parents=True)
        fuente.write_text("Este es el proyecto", encoding="utf-8")
        diario = self.diario.registrar("Corregimos la tercera diapositiva.", fuente=fuente)
        self.assertEqual(diario, self.raiz / "Diario" / "2026-11-22.md")
        texto = diario.read_text(encoding="utf-8")
        self.assertIn("../Proyectos/Tesis%20%232.md", texto)
        diario.write_text(texto + "- Cambié el plazo a diciembre", encoding="utf-8")
        self.diario.registrar("Terminamos el anexo")
        self.assertIn("diciembre\n- 23:59 Terminamos", diario.read_text(encoding="utf-8"))
        self.ahora = datetime(2026, 11, 23, 0, 2)
        self.diario.registrar("Probamos el informe")
        for pregunta in ("¿Qué hicimos ayer?", "Lee mi diario del día veintidós de noviembre de dos mil veintiséis",
                         "¿Qué hicimos el domingo pasado?", "¿Qué hicimos el 22/11/2026?",
                         "Consulta el diario de 2026-11-22"):
            with self.subTest(pregunta=pregunta):
                respuesta = self.diario.procesar(pregunta).texto
                self.assertIn("Corregimos la tercera", respuesta)
                self.assertIn("diciembre", respuesta)
                self.assertNotIn("Ver nota", respuesta)
                self.assertNotIn("Probamos", respuesta)
        self.assertIn("Probamos", self.diario.procesar("¿En qué trabajamos hoy?").texto)

    def test_registros_concurrentes_breves_y_sin_perdidas(self):
        def escribir(i):
            return Diario(self.raiz, reloj=lambda: self.ahora).registrar(f"Actividad {i}.")
        with ThreadPoolExecutor(max_workers=6) as hilos:
            rutas = list(hilos.map(escribir, range(15)))
        texto = rutas[0].read_text(encoding="utf-8")
        self.assertEqual(texto.count("# Diario"), 1)
        self.assertEqual(texto.count("Actividad "), 15)
        for indice in range(15):
            self.assertIn(f"Actividad {indice}.", texto)
        breve = self.diario.procesar("¿Qué hicimos hoy?").texto
        self.assertIn("15 entradas", breve)
        self.assertEqual(breve.count("Actividad "), 3)

    def test_una_consulta_no_crea_notas_ni_intercepta_otros_pedidos(self):
        self.assertIn("No hay actividades", self.diario.procesar("¿Qué hicimos hoy?").texto)
        self.assertFalse(self.raiz.exists())
        self.assertIsNone(self.diario.procesar("¿Qué hicimos con las diapositivas?"))
        self.assertIn("fecha no es válida", self.diario.procesar("¿Qué hicimos el 31 de febrero?").texto)

    def test_no_usa_un_diario_o_fuente_que_apunten_fuera(self):
        fuera = Path(self.tmp) / "externa"
        fuera.mkdir()
        (fuera / "2026-11-22.md").write_text("contenido externo", encoding="utf-8")
        self.raiz.mkdir()
        ruta = self.raiz / "Diario"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(fuera), str(ruta))
        else:
            ruta.symlink_to(fuera, target_is_directory=True)
        self.assertIn("No pude leer", self.diario.procesar("¿Qué hicimos hoy?").texto)
        with self.assertRaises(OSError):
            self.diario.registrar("No escribir")
        self.assertEqual((fuera / "2026-11-22.md").read_text(encoding="utf-8"), "contenido externo")
        if os.name == "nt":
            ruta.rmdir()
        else:
            ruta.unlink()
        archivo = self.diario.registrar("Seguimos la revisión", fuente=fuera / "2026-11-22.md")
        self.assertNotIn("Ver nota", archivo.read_text(encoding="utf-8"))
