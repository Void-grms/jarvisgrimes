import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from asistente.acciones import sistema
from asistente.comandos import Comandos, nombre_dictado, pide_nombre, preferencia_nombre
from asistente.config import DEFAULTS
from asistente.router import detectar


class Ordenes(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["documentos"]["carpeta"] = self.tmp.name
        cfg["plataformas"] = {"campus": "https://campus.example/"}
        with (patch("asistente.comandos.Recordatorios"), patch("asistente.comandos.carpetas_usuario", return_value=[]),
              patch.object(sistema, "ARCHIVO_APPS", Path(self.tmp.name) / "apps.json")):
            self.cmd = Comandos(cfg, Mock(), Mock(), Mock(), Mock())
        self.word = {"Name": "Word", "AppID": "word-de-prueba"}
        self.cmd.apps.apps = [self.word, {"Name": "WordPad", "AppID": "editor-de-prueba"}]
        self.cmd.apps.abrir = Mock()
        self.cmd.nav.abrir = Mock()
        self.cmd.nav.poner_video = Mock()
        self.enterContext(patch("asistente.comandos.ventanas.listar", return_value=[]))
        self.enterContext(patch("asistente.comandos.ventanas.abrir_y_enfocar",
                                side_effect=lambda lanzar, *args, **kwargs: lanzar()))

    def test_orden_de_apertura_diferida(self):
        for frase in ("Abre Word.", "Abre worth.", "Quiero que abras world.",
                      "Por favor, abre güord.", "Quiero que me abras Word."):
            with self.subTest(frase=frase):
                r = self.cmd.procesar(frase)
                self.assertEqual(r.confirmar_voz, "¿Abro Word?")
                self.cmd.apps.abrir.assert_not_called()
                r.ejecutar()
                self.cmd.apps.abrir.assert_called_once_with(self.word)
                self.cmd.apps.abrir.reset_mock()

    def test_instalacion_y_aproximaciones(self):
        for nombre in ("Wo", "Wor", "Woord", "Word y YouTube"):
            self.assertIsNone(self.cmd.apps.buscar(nombre))
        self.cmd.apps.apps = [{"Name": "WordPad"}, {"Name": "Microsoft Word 2016"}]
        self.assertEqual(self.cmd.apps.buscar("güord")["Name"], "Microsoft Word 2016")
        self.cmd.apps.apps = [{"Name": "WordPad"}]
        self.assertIsNone(self.cmd.apps.buscar("worth"))

    def test_una_mencion_o_queja_no_autoriza(self):
        for frase in ("ChatGPT, ChatGPT.", "Este abierau de Google.", "No te peis que habría dado YouTube.",
                      "No te pedí que abrieras YouTube.", "¿Cómo bloquear la computadora?",
                      "No quiero que tomes una captura.", "¿Qué es un pomodoro?",
                      "Abre campus aunque no te lo pedí.", "Abre ejemplo.com pero todavía no.",
                      "Quiero que abras y worth la predación."):
            with self.subTest(frase=frase):
                r = self.cmd.procesar(frase)
                self.assertTrue(r is None or (not r.accion and not r.despues and not r.ejecutar))
        r = self.cmd.procesar("Quiero que abras y worth la predación.")
        self.assertTrue(r.siguiente)
        self.assertEqual(r.siguiente("Word.").confirmar_voz, "¿Abro Word?")

    def test_destinos_google_y_youtube(self):
        for frase, destino in (("Abre YouTube.", "https://www.youtube.com/"),
                               ("Quiero que abras Google.", "https://www.google.com/"),
                               ("busca world music", "https://www.google.com/search?q=world+music"),
                               ("busca noticias sobre YouTube en Google",
                                "https://www.google.com/search?q=noticias+sobre+YouTube"),
                               ("busca en YouTube música peruana",
                                "https://www.youtube.com/results?search_query=m%C3%BAsica+peruana")):
            with self.subTest(frase=frase):
                r = self.cmd.procesar(frase)
                self.assertTrue(r.confirmar_voz)
                self.cmd.nav.abrir.assert_not_called()
                r.accion()
                self.cmd.nav.abrir.assert_called_once_with(destino, None, False)
                self.cmd.nav.abrir.reset_mock()
        r = self.cmd.procesar("Pon música criolla.")
        self.assertFalse(r.accion)
        self.assertIsNone(r.siguiente("sí, pero no lo hagas"))
        r.siguiente("sí").accion()
        self.cmd.nav.poner_video.assert_called_once_with("música criolla", None, False)

    def test_cierre_y_guardado_ambiguos(self):
        r = self.cmd.procesar("Se han abierto varias pestañas. Quiero que la cierre y sin necesito "
                              "de guardar algo guarda.")
        self.assertIn("guarda", r.texto.lower())
        self.assertFalse(r.accion or r.despues or r.siguiente)

    def test_contexto_solo_se_usa_si_falta_el_destino(self):
        r = self.cmd.procesar("ChatGPT, ChatGPT.")
        abrir = r.siguiente("ábrelo")
        self.assertEqual(abrir.confirmar_voz, "¿Abro ChatGPT?")
        abrir.accion()
        self.cmd.nav.abrir.assert_called_once_with("https://chatgpt.com/", None, False)
        self.cmd.nav.preguntar = Mock()
        self.cmd.revisar_encargo = Mock()
        consulta = r.siguiente("busca el origen del vals peruano")
        consulta.ejecutar()
        self.cmd.revisar_encargo.assert_called_once_with("ChatGPT", "el origen del vals peruano")
        self.cmd.nav.preguntar.assert_not_called()
        for nuevo in ("abre Word", "busca el archivo tesis", "busca en Google comida", "no"):
            self.assertIsNone(r.siguiente(nuevo))
        self.cmd.nav.abrir.reset_mock()
        self.cmd.procesar("Abre Google y busca el vals peruano").accion()
        self.cmd.nav.abrir.assert_called_once_with("https://www.google.com/search?q=el+vals+peruano", None, False)

    def test_cambio_de_nombre(self):
        relato = ("Yo digo a de que hay que cambiar el nombre porque me está diciendo Patrick, "
                  "quiero que me digas Fernando.")
        for frase, nombre in ((relato, "Fernando"),
                              ("Ya no quiero que me llames Patrick, quiero que me digas Fernando.", "Fernando"),
                              ("Llámame José Luis, por favor.", "José Luis"),
                              ("Mi nombre es María.", "María"), ("Quiero que me digas Fernando.", "Fernando")):
            self.assertEqual(preferencia_nombre(frase), nombre)
        for frase in ("Quiero que me digas la hora.", "No quiero que me digas Fernando.",
                      "Anota que mi nombre es Fernando.", "Llámame mañana.",
                      "Quiero que me digas adivinanzas.", "No me digas mentiras.",
                      "¿Qué significa cambiar el nombre de un archivo?"):
            self.assertIsNone(preferencia_nombre(frase))
            self.assertFalse(pide_nombre(frase))
        self.assertTrue(pide_nombre("Abre, para empezar, cambié el nombre. Ya no me digas patrón "
                                    "y siguen afrenando."))
        self.assertIsNone(nombre_dictado("Abre YouTube."))

    def test_intenciones_con_efecto_requieren_un_pedido(self):
        self.assertEqual(detectar("Actualiza mis pendientes, por favor."), "actualizar")
        self.assertEqual(detectar("Descansa"), "dormir")
        for frase in ("¿Cómo se actualiza un programa?", "¿Qué significa decir silencio?", "No, actualiza no."):
            self.assertNotIn(detectar(frase), ("actualizar", "dormir"))


if __name__ == "__main__":
    unittest.main()
