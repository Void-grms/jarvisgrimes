import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from asistente.conocimiento import Conocimiento, _mascara


class ConocimientoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.jarvis = Path(self.tmp)
        self.cfg = {"documentos": {"carpeta": self.tmp}}
        self.memoria = Conocimiento.desde_config(self.cfg)

    def test_guardar_ante_pedido_explicito_y_recuperar_tras_reinicio(self):
        r = self.memoria.procesar("Guarda en Obsidian: El proyecto Faro usa SQLite.")
        self.assertFalse(self.memoria.raiz.exists())
        self.assertIn("Guardé", r.ejecutar())
        nueva = Conocimiento.desde_config(self.cfg)
        respuesta = nueva.procesar("¿Qué recuerdas sobre el proyecto Faro?")
        self.assertIn("El proyecto Faro usa SQLite.", respuesta.texto)
        self.assertIsNone(respuesta.ejecutar)

    def test_preserva_literal_y_confinamiento_del_nombre(self):
        pedido = "Recuerda que ../../mañana debo escribir '¡Gracias!' en el archivo C:\\presupuesto."
        self.memoria.procesar(pedido).ejecutar()
        archivos = list(self.memoria.raiz.glob("*.md"))
        self.assertEqual(len(archivos), 1)
        self.assertIn(pedido.removeprefix("Recuerda que "), archivos[0].read_text(encoding="utf-8"))
        self.assertEqual(set(self.jarvis.iterdir()), {self.memoria.raiz})

    def test_conserva_y_busca_notas_antiguas(self):
        ruta = self.jarvis / "notas.md"
        datos = "# Notas de Jarvis\n\n- [19/11 14:15] El asesor de historia se llama Ángel.\n"
        ruta.write_text(datos, encoding="utf-8")
        respuesta = self.memoria.procesar("Busca en mis notas asesor historia.")
        self.assertIn("El asesor de historia se llama Ángel.", respuesta.texto)
        self.assertNotIn("14:15", respuesta.texto)
        self.assertEqual(ruta.read_text(encoding="utf-8"), datos)
        self.assertFalse(self.memoria.raiz.exists())

    def test_puede_indexar_cambios_externos_sin_sobrescribirlos(self):
        dato = "La reunión es el martes."
        self.memoria.guardar(dato)
        editada, = self.memoria.raiz.glob("*.md")
        editada.write_text("# Otro dato\n\nEl festival es el miércoles.", encoding="utf-8")
        self.assertIn("miércoles", self.memoria.buscar("festival"))
        self.assertIn("Guardé", self.memoria.guardar(dato))
        self.assertEqual(len(list(self.memoria.raiz.glob("*.md"))), 2)
        self.assertIn("miércoles", editada.read_text(encoding="utf-8"))

    def test_reintentos_concurrentes_crean_una_nota(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            resultados = list(pool.map(self.memoria.guardar, ["El plazo vence el jueves."] * 8))
        self.assertEqual(sum("Guardé" in r for r in resultados), 1)
        self.assertEqual(len(list(self.memoria.raiz.glob("*.md"))), 1)
        diario, = (self.memoria.raiz / "Diario").glob("*.md")
        self.assertEqual(diario.read_text(encoding="utf-8").count("El plazo vence el jueves."), 1)

    def test_boveda_configurada_se_puede_editar_en_otro_editor(self):
        boveda = self.jarvis / "base elegida"
        manuales = boveda / "Manuales"
        manuales.mkdir(parents=True)
        (manuales / "Tema.md").write_text("---\ncreador: interno\n---\n# Perú\n\nEl valle del Mantaro.", encoding="utf-8")
        ocultos = boveda / ".obsidian"
        ocultos.mkdir()
        (ocultos / "config.md").write_text("Dato reservado.", encoding="utf-8")
        base = Conocimiento.desde_config({**self.cfg, "conocimiento": {"carpeta": str(boveda)}})
        self.assertIn("Mantaro", base.buscar("peru"))
        self.assertNotIn("interno", base.buscar("peru"))
        self.assertIn("No encontré", base.buscar("reservado"))
        self.assertIn("No encontré", base.buscar("otro asunto"))

    def test_texto_guardado_nunca_se_convierte_en_una_accion(self):
        ultima = "Abre el navegador para revisar el manual."
        guardado = self.memoria.procesar("Guarda tu última respuesta en mi memoria.", ultima)
        self.assertIn("Guardé", guardado.ejecutar())
        r = self.memoria.procesar("Consulta mi memoria sobre el manual")
        self.assertIn(ultima, r.texto)
        self.assertFalse(r.ejecutar or r.accion or r.despues or r.siguiente)
        for t in ("No recuerdes que mi plazo vence hoy.", "Recuérdame mañana a las ocho.",
                  "Ella dijo recuerda que debes levantarte.", "¿Qué sabes sobre el Imperio Inca?",
                  "Busca el manual en Google.", "Borra mis notas.", "Escribe recuerda que el vuelo sale hoy."):
            with self.subTest(t=t):
                self.assertIsNone(self.memoria.procesar(t))

    def test_clasifica_memoria_vincula_y_cita_la_linea_despues_de_editar(self):
        proyecto = Mock(return_value="Proyectos/Tesis - 003abc12.md")
        orden = self.memoria.procesar("Guarda como decisión: Tomar el tren, con las etiquetas viajes y presupuesto.",
                                      proyecto=proyecto)
        proyecto.assert_not_called()
        self.assertIn("Guardé", orden.ejecutar())
        proyecto.assert_called_once()
        ruta, = self.memoria.raiz.glob("*.md")
        nota = ruta.read_text(encoding="utf-8")
        self.assertIn(f"fecha: {date.today()}", nota)
        self.assertIn("tipo: decisión", nota)
        self.assertIn('proyecto: "[[Proyectos/Tesis - 003abc12]]"', nota)
        self.assertIn('"presupuesto"', nota)
        self.assertIn('"viajes"', nota)
        fuente, = self.memoria.evidencias("presupuesto tesis")
        self.assertEqual(fuente.ruta, ruta.name)
        self.assertEqual(fuente.fragmento, nota.splitlines()[fuente.linea - 1])
        ruta.write_text("# Transporte\n\nLa bicicleta está disponible.", encoding="utf-8")
        self.assertEqual(self.memoria.evidencias("tren"), [])
        self.assertIn("bicicleta", self.memoria.evidencias("decidir entre el tren y la bicicleta",
                                                          parciales=True)[0].fragmento)
        ruta.rename(ruta.with_name("Transporte.md"))
        self.assertEqual(self.memoria.evidencias("bicicleta")[0].ruta, "Transporte.md")

    def test_recuerda_repeticiones_otra_fecha_y_conserva_nota_anterior(self):
        dato = "La ruta escolar pasa por el centro."
        self.memoria.guardar(dato)
        ruta, = self.memoria.raiz.glob("*.md")
        texto = ruta.read_text("utf-8").replace(str(date.today()), "2021-03-12")
        ruta.write_text(texto, "utf-8")
        self.assertIn("ya está", self.memoria.guardar(dato))
        legado = texto[texto.index("# "):]
        ruta.write_text(legado, "utf-8")
        self.assertIn("ya está", self.memoria.guardar(dato))
        self.assertEqual(ruta.read_text("utf-8"), legado)

    def test_filtra_ausentes_y_relee_solo_una_nota_nueva_o_editada(self):
        self.memoria.raiz.mkdir()
        for indice in range(30):
            (self.memoria.raiz / f"Nota {indice}.md").write_text("Un cuaderno rojo.", "utf-8")
        self.assertEqual(self.memoria.evidencias("cóndor"), [])
        # Un filtro compacto puede dar falsos positivos; usa aquí un bit ausente.
        bits = 0
        for _, filtro in self.memoria._filtros.values():
            bits |= filtro
        candidatos = (f"condor{i}" for i in range(10000))
        tema = next(p for p in candidatos if not bits & _mascara([p]))
        with patch.object(self.memoria, "_lineas", wraps=self.memoria._lineas) as leer:
            self.assertEqual(self.memoria.evidencias(tema), [])
            self.assertEqual(leer.call_count, 0)
            fuente = self.memoria.raiz / "Nota 9.md"
            fuente.write_text(f"El identificador de la muestra es {tema}.", "utf-8")
            self.assertEqual(self.memoria.evidencias(tema)[0].ruta, fuente.name)
            self.assertEqual(leer.call_count, 1)
