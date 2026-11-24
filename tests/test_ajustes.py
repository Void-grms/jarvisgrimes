import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from asistente import ajustes
from asistente.config import cargar


class Ajustes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raiz = Path(self.temp.name)
        self.ruta = self.raiz / "config.toml"

    def test_guarda_solo_cambios_y_conserva_comentarios_y_valores_privados(self):
        previo = ('[clementina]\r\nurl_ics = "privado#abc" # prueba\r\n\r\n'
                  '[extra]\r\ncadena = """[interfaz]\r\ntamano = 300"""\r\n'
                  '\r\n[interfaz]\r\nwidget = true  # mostrar orbe\r\n').encode()
        self.ruta.write_bytes(previo)
        cfg = ajustes.guardar_ajustes({"interfaz": {"widget": False, "tamano": 320}}, self.ruta)
        nuevo = self.ruta.read_bytes()
        self.assertTrue(nuevo.startswith(previo.split(b"widget = true")[0]))
        self.assertIn(b"widget = false  # mostrar orbe", nuevo)
        self.assertIn(b"tamano = 320", nuevo)
        self.assertNotIn(b"\n", nuevo.replace(b"\r\n", b""))
        self.assertEqual(cfg, cargar(self.ruta))
        self.assertEqual(cfg["clementina"]["url_ics"], "privado#abc")

    def test_cambio_de_clave_conserva_las_variantes_personalizadas(self):
        self.ruta.write_bytes(b'[escucha]\r\nvariantes_clave = ["yarvis", "alfa"]\r\n')
        cfg = ajustes.guardar_ajustes({"escucha": {"palabra_clave": "Alfred"}}, self.ruta)
        self.assertEqual(cfg["escucha"]["variantes_clave"], ["alfa"])
        self.assertEqual(cargar(self.ruta), cfg)
        cfg = ajustes.guardar_ajustes({"escucha": {"palabra_clave": "Jarvis"}}, self.ruta)
        self.assertEqual(cfg["escucha"]["variantes_clave"], ["yarvis", "alfa"])

    def test_validacion_no_publica_un_cambio_parcial(self):
        for seccion, clave, valor in [
                ("voz", "velocidad", True), ("voz", "velocidad", float("nan")),
                ("conocimiento", "busqueda_semantica", "false"),
                ("escucha", "palabra_clave", "!!!"), ("interfaz", "tamano", 319.5),
                ("interfaz", "tamano", 321), ("clementina", "url_ics", "prueba")]:
            with self.subTest(clave=clave, valor=valor), self.assertRaises(ValueError):
                ajustes.guardar_ajustes({"escucha": {"saludar_al_iniciar": False},
                                         seccion: {clave: valor}}, self.ruta)
        self.assertFalse(self.ruta.exists())

    def test_fallo_al_publicar_conserva_la_configuracion(self):
        previo = b"[voz]\nvelocidad = 1.25\n"
        self.ruta.write_bytes(previo)
        with patch.object(os, "replace", side_effect=PermissionError), self.assertRaises(PermissionError):
            ajustes.guardar_ajustes({"voz": {"velocidad": 1.3}}, self.ruta)
        self.assertEqual(self.ruta.read_bytes(), previo)
        self.assertEqual(list(self.raiz.glob("*.tmp")), [])

    def test_guardados_simultaneos_partiendo_de_archivo_vacio(self):
        cambios = [{"interfaz": {"tamano": 125}}, {"voz": {"pausa_ms": 200}},
                   {"escucha": {"confirmar_aperturas": True}}]
        with ThreadPoolExecutor(max_workers=3) as grupo:
            list(grupo.map(lambda c: ajustes.guardar_ajustes(c, self.ruta), cambios))
        cfg = cargar(self.ruta)
        for cambio in cambios:
            for seccion, campos in cambio.items():
                for clave, valor in campos.items():
                    self.assertEqual(cfg[seccion][clave], valor)

    def test_inventario_local_distingue_modelos_completos_y_no_expone_credenciales(self):
        modelos = self.raiz / "modelos"
        archivo = modelos / "manifests/registry.ollama.ai/library/mi-modelo/latest"
        archivo.parent.mkdir(parents=True)
        sha = "a" * 64
        capa = {"digest": "sha256:" + sha}
        archivo.write_text(json.dumps({"config": capa, "layers": [capa]}), encoding="utf-8")
        (modelos / "blobs").mkdir()
        (modelos / "blobs" / ("sha256-" + sha)).touch()
        (archivo.parent / "incompleto").write_text("{}", encoding="utf-8")
        cache = self.raiz / "cache"
        stt = cache / "models--Systran--faster-whisper-base"
        revision = "a" * 40
        (stt / "refs").mkdir(parents=True)
        (stt / "refs/main").write_text(revision, encoding="ascii")
        snapshot = stt / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for nombre in ("model.bin", "config.json", "tokenizer.json"):
            (snapshot / nombre).touch()
        voces = self.raiz / "voces"
        voces.mkdir()
        for nombre in ("muestra.onnx", "muestra.onnx.json", "incompleta.onnx"):
            (voces / nombre).touch()
        cfg = cargar(self.ruta)
        cfg["clementina"]["url_ics"] = "dato-de-prueba"
        cfg["voz"]["modelo_piper"] = "voces/muestra.onnx"
        cfg["llm"]["modelo"] = "mi-modelo"
        cfg["conocimiento"].update(modelo_embeddings="emb-prueba:pequeno", busqueda_semantica=True)
        with (patch.object(ajustes, "RAIZ", self.raiz), patch.object(ajustes, "DATOS", self.raiz),
              patch("asistente.config.RAIZ", self.raiz), patch.dict(os.environ, OLLAMA_MODELS=str(modelos)),
              patch("huggingface_hub.constants.HF_HUB_CACHE", str(cache))):
            estado = ajustes.estado_servicios(cfg)
            self.assertEqual(estado["modelos_llm"], ["mi-modelo"])
            self.assertEqual(estado["modelos_stt"], ["base"])
            self.assertEqual(estado["voces"], ["voces/muestra.onnx"])
            self.assertNotIn("dato-de-prueba", str(estado))
            def busqueda():
                servicios = ajustes.estado_servicios(cfg)["servicios"]
                return next(s for s in servicios if s["nombre"] == "Búsqueda en notas")

            self.assertEqual(busqueda()["estado"], "Falta el modelo")
            self.assertIn("Búsqueda por palabras", busqueda()["detalle"])
            vector = archivo.parent.parent / "emb-prueba" / "pequeno"
            vector.parent.mkdir()
            vector.write_bytes(archivo.read_bytes())
            self.assertEqual(busqueda()["estado"], "Modelo en disco")
            self.assertIn("emb-prueba:pequeno · Palabras y significado", busqueda()["detalle"])
            self.assertEqual(ajustes.estado_servicios(cfg)["modelos_llm"], ["mi-modelo"])
            cfg["conocimiento"]["busqueda_semantica"] = False
            self.assertIn("Búsqueda por palabras", busqueda()["detalle"])
            self.assertEqual(ajustes.estado_servicios(cfg)["modelos_llm"], ["mi-modelo"])
            with patch.dict(cfg["llm"], url="https://modelo.example"):
                self.assertEqual(busqueda()["estado"], "Requiere Ollama local")
            (snapshot / "model.bin").unlink()
            self.assertEqual(ajustes.estado_servicios(cfg)["modelos_stt"], [])
            with self.assertRaises(ValueError):
                ajustes.guardar_ajustes({"stt": {"modelo": "base"}}, self.ruta)
