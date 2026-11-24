import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from asistente.conocimiento import Conocimiento
from asistente.semantica import Semantica


class RecuperacionLocal(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.raiz = self.base / "Bóveda"
        self.raiz.mkdir()
        self.motor = Semantica("http://127.0.0.1:11434", umbral=.8)
        self.con = Conocimiento(self.raiz, self.base / "notas.md", semantica=self.motor)
        self.entradas = []
        self.vectores = self.enterContext(patch.object(self.motor, "_pedir", side_effect=self.embeddings))
        self.enterContext(patch.object(self.motor, "_comprobar_modelo"))

    def embeddings(self, sesion, textos):
        self.assertFalse(sesion.trust_env)
        self.entradas.extend(textos)
        return np.array([[1, 0] if any(p in t.casefold() for p in ("presupuesto", "desembolso", "tarifa"))
                         else [0, 1] for t in textos], dtype=np.float32)

    def nota(self, ruta, texto):
        destino = self.raiz / ruta
        destino.parent.mkdir(exist_ok=True, parents=True)
        destino.write_text(texto, encoding="utf-8")
        return destino

    def test_investigar_parafrasis_excluye_diario_informes_y_notas_fuera_de_boveda(self):
        self.nota("A.md", "# Transporte\n\nEl desembolso fue de ocho soles.\nLa tarifa de taxi es fija.")
        self.nota("B.md", "# Alojamiento\n\nLa tarifa incluye desayuno.")
        self.nota(".obsidian/oculto.md", "Nunca mandar configuración.")
        self.nota("Diario/2026-11-23.md", "Nunca mandar el historial.")
        self.nota("Informes/Generado.md", '---\ntipo: "informe_de_investigacion"\n---\nNunca mandar síntesis.')
        self.nota("Informes/Manual.md", "La tarifa sigue en negociación.")
        self.con.notas_rapidas.write_text("Nunca mandar el cuaderno externo en investigación.", encoding="utf-8")
        fuentes = self.con.investigar("presupuesto", 4)
        self.assertEqual(len({e.ruta for e in fuentes[:3]}), 3)
        self.assertEqual({e.ruta for e in fuentes}, {"A.md", "B.md", "Informes/Manual.md"})
        for e in fuentes:
            self.assertEqual((self.raiz / e.ruta).read_text("utf-8").splitlines()[e.linea - 1], e.fragmento)
        self.assertNotIn("Nunca mandar", " ".join(self.entradas))
        self.assertIn("desembolso", self.con.buscar("presupuesto"))

    def test_una_edicion_elimina_el_vector_viejo_y_la_cita_y_se_reutilizan_vectores_vivos(self):
        nota = self.nota("A.md", "El desembolso se pagará hoy.")
        otra = self.nota("B.md", "Unas flores rojas.")
        resultado = self.con.investigar("presupuesto")
        self.assertEqual(len(resultado), 1)
        self.entradas.clear()
        self.assertEqual(self.con.investigar("presupuesto"), resultado)
        self.assertEqual(len(self.entradas), 1)  # únicamente el embedding de la pregunta
        nota.write_text("Se cultivan tomates.", encoding="utf-8")
        otra.unlink()
        self.assertEqual(self.con.vigentes(resultado), [])
        self.assertEqual(self.con.investigar("presupuesto"), [])
        nota.write_text("El desembolso se pagará mañana.", encoding="utf-8")
        self.assertIn("mañana", self.con.investigar("presupuesto")[0].fragmento)
        nota.unlink()
        self.assertEqual(self.con.investigar("presupuesto"), [])

    def test_lotes_progresivos_sin_bloquear_consultas_y_cache_acotada(self):
        for i in range(6):
            self.nota(f"{i}.md", "La tarifa es adecuada." if i == 5 else f"Flores de la muestra {i}.")
        self.motor.nuevos_por_consulta = self.motor.lote = 2
        self.motor.capacidad = 3
        respuesta = self.con.buscar("presupuesto")
        self.assertIn("Quedan pasajes", respuesta)
        self.assertTrue(self.con.busqueda_parcial)
        self.con.investigar("presupuesto")
        self.assertEqual(self.con.investigar("presupuesto")[0].ruta, "5.md")
        self.assertLessEqual(len(self.motor._cache), 3)

    def test_redirecciones_fallos_y_servidores_externos_conservan_la_busqueda_literal(self):
        self.nota("Local.md", "Un viaje en tren")
        for url in ("https://modelo.example", "http://usuario@localhost:11434", "http://localhost/redirigir"):
            self.con.semantica = Semantica(url)
            with patch("asistente.semantica.requests.Session") as sesion:
                self.assertEqual(self.con.investigar("tren")[0].ruta, "Local.md")
                sesion.assert_not_called()
        self.con.semantica = Semantica("http://localhost:11434")
        with patch("asistente.semantica.requests.Session") as sesion:
            local = sesion.return_value.__enter__.return_value
            local.post.return_value = Mock(status_code=302)
            self.assertEqual(self.con.investigar("tren")[0].ruta, "Local.md")
            self.assertEqual(self.con.investigar("tren")[0].ruta, "Local.md")
            local.post.assert_called_once()
            self.assertFalse(local.trust_env)
            self.assertFalse(local.post.call_args.kwargs["allow_redirects"])

    def test_un_alias_cloud_localhost_o_un_modelo_sin_embeddings_no_reciben_pasajes(self):
        self.nota("Tesis.md", "Mi dato reservado de tesis")
        for modelo, info in (
                ("alias", {"remote_host": "https://ollama.com", "capabilities": ["embedding"], "details": {"format": "gguf"}}),
                ("alias", {"remote_model": "cloud-upstream", "capabilities": ["embedding"], "details": {"format": "gguf"}}),
                ("chat", {"capabilities": ["completion"], "details": {"format": "gguf"}}),
                ("grande:cloud", {}), ("grande-cloud:latest", {})):
            with self.subTest(modelo=modelo), patch("asistente.semantica.requests.Session") as sesion:
                self.con.semantica = Semantica("http://127.0.0.1:11434", modelo)
                cliente = sesion.return_value.__enter__.return_value
                cliente.post.return_value.status_code = 200
                cliente.post.return_value.json.return_value = info
                self.assertIn("dato reservado", self.con.investigar("tesis")[0].fragmento)
                for llamada in cliente.post.call_args_list:
                    self.assertTrue(llamada.args[0].endswith("/api/show"))
                    self.assertEqual(llamada.kwargs["json"], {"model": modelo})

    def test_literales_identicos_tienen_dos_fuentes_y_cita_larga_vuelve_a_la_linea(self):
        self.con.semantica = None
        texto = "El desembolso será mañana."
        self.nota("A.md", texto)
        self.nota("B.md", texto)
        fuentes = self.con.investigar("desembolso")
        self.assertEqual({e.ruta for e in fuentes}, {"A.md", "B.md"})
        linea = " ".join("el alumnado participó" for _ in range(40)) + " y la tarifa se pagó ayer"
        self.nota("Larga.md", linea)
        self.con.semantica = self.motor
        hojas = self.con.investigar("presupuesto")
        cita = next(e for e in hojas if e.ruta == "Larga.md")
        self.assertIn("tarifa se pagó", cita.fragmento)
        self.assertEqual(self.con.vigentes([cita]), [cita])
