import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from asistente.proyecto_md import NotaProyecto, nombre_archivo
from asistente.proyectos import Proyectos, huella
from asistente.store import Store


class BovedaProyecto(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.raiz = self.tmp / "Bóveda"
        self.store = Store(self.tmp / "db.sqlite")
        self.addCleanup(self.store._con.close)
        self.p = Proyectos(self.store, SimpleNamespace(raiz=self.raiz))

    def test_exporta_base_anterior_y_lee_ediciones_de_obsidian(self):
        antiguo = Proyectos(self.store)
        clave = antiguo.elegir("Tesis", crear=True)
        antiguo.apuntar(clave, "Decisión", "Usar entrevistas")
        antiguo.apuntar(clave, "Pendiente", "Validar el guion")
        self.assertEqual(self.p.sincronizar_todos(), 1)
        ruta = self.p.ruta_nota()
        self.assertEqual(ruta, self.raiz / "Proyectos" / "Tesis.md")
        texto = ruta.read_text("utf-8").replace("Usar entrevistas", "Usar encuestas")
        texto = texto.replace("- [ ] Validar el guion", "- [x] Validar el guion\n- [ ] Contar respuestas")
        texto = texto.replace("## Decisiones", "Anotación libre **importante**.\n\n## Decisiones")
        ruta.write_text(texto, encoding="utf-8")
        self.p.apuntar(clave, "Decisión", "Entregar el lunes")
        datos = self.p.ficha()["apuntes"]
        self.assertIn({"clase": "Decisión", "texto": "Usar encuestas"}, datos)
        self.assertIn({"clase": "Hecho", "texto": "Validar el guion"}, datos)
        self.assertNotIn({"clase": "Decisión", "texto": "Usar entrevistas"}, datos)
        self.p.completar(clave, "contar respuestas")
        self.assertIn("- [x] Contar respuestas", ruta.read_text("utf-8"))
        self.assertIn("Anotación libre **importante**.", ruta.read_text("utf-8"))
        antes = ruta.stat().st_mtime_ns
        self.p.elegir("Tesis")
        self.assertEqual(ruta.stat().st_mtime_ns, antes)

    def test_adopta_nota_existente_y_protege_archivos_verificados(self):
        carpeta = self.raiz / "Proyectos"
        carpeta.mkdir(parents=True)
        nota = carpeta / "Cartografía.md"
        nota.write_text("# Mi cuaderno\n\n## Decisiones\n- Mapear el valle\n\n"
                        "## Tareas\n- [ ] Dibujar el río\n", encoding="utf-8")
        clave = self.p.elegir("Cartografía")
        origen, copia = self.tmp / "Inicial.docx", self.tmp / "Plano.docx"
        origen.write_bytes(b"original")
        copia.write_bytes(b"modificada")
        self.p.registrar(clave, origen, huella(origen), copia, huella(copia), "Revisión", [])
        self.assertIn("Revisión (actual)", nota.read_text("utf-8"))
        texto = nota.read_text("utf-8").replace(str(copia), "X:/ruta/sin-verificar.docx")
        nota.write_text(texto, encoding="utf-8")
        self.assertEqual(self.p.ficha()["version"]["ruta"], str(copia))
        self.assertNotIn("sin-verificar", nota.read_text("utf-8"))
        self.assertEqual(self.p.anterior(clave), origen)
        self.assertIn("Original (actual)", nota.read_text("utf-8"))
        self.assertIn("# Mi cuaderno", nota.read_text("utf-8"))

    def test_apuntes_simultaneos_nombre_seguro_renombre_y_colision(self):
        clave = self.p.elegir("Plan/tesis", crear=True)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda i: self.p.apuntar(clave, "Pendiente", f"Tarea {i}"), range(8)))
        ruta = self.p.ruta_nota()
        self.assertEqual(ruta.parent, self.raiz / "Proyectos")
        self.assertEqual(len(self.p.ficha()["apuntes"]), 8)
        renombrada = ruta.with_name("Otro título.md")
        ruta.rename(renombrada)
        self.assertEqual(self.p.ruta_nota("Plan/tesis"), renombrada)
        clave_a = self.p.elegir("Tesis", crear=True)
        ruta_a = self.p.ruta_nota()
        self.p.elegir("Informe", crear=True)
        with self.store.transaccion() as con:
            con.execute("INSERT INTO proyectos(clave,nombre) VALUES ('archivo/a','Tesis')")
        self.p.ficha("archivo/a")
        self.p.apuntar("archivo/a", "Decisión", "Corresponde al archivo")
        self.assertNotIn("Corresponde al archivo", ruta_a.read_text("utf-8"))
        self.assertEqual(self.p.ruta_nota("Tesis"), ruta_a)
        self.assertNotEqual(self.p.actual(), clave_a)
        self.assertNotIn("/", nombre_archivo("../../fuera"))
        if os.name == "nt":
            self.assertNotEqual(nombre_archivo("CON"), "CON")

    def test_conflicto_de_editores_y_reintento_conservan_ambos_apuntes(self):
        clave = self.p.elegir("Tesis", crear=True)
        ruta = self.p.ruta_nota()
        publicar = NotaProyecto._publicar
        def editar(nota, contenido):
            ruta.write_text(ruta.read_text("utf-8").replace("## Decisiones", "## Decisiones\n- Edición externa"),
                            encoding="utf-8")
            publicar(nota, contenido)
        with patch.object(NotaProyecto, "_publicar", editar), self.assertRaisesRegex(ValueError, "combinar"):
            self.p.apuntar(clave, "Decisión", "Edición local")
        self.p.apuntar(clave, "Decisión", "Edición local")
        self.p.apuntar(clave, "Decisión", "Edición local")
        datos = self.p.ficha()["apuntes"]
        self.assertEqual([r["texto"] for r in datos], ["Edición externa", "Edición local"])
        diario = (self.raiz / "Diario" / (date.today().isoformat() + ".md")).read_text("utf-8")
        self.assertEqual(diario.count("Edición local"), 1)
        self.assertNotIn("Edición externa", diario)
