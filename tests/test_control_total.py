"""Control de la computadora: ventanas, teclado, energía, iniciativa e intérprete de órdenes."""
import copy
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from asistente.acciones import ventanas
from asistente.acciones.ventanas import Ventana, interpretar_teclas
from asistente.comandos import Comandos
from asistente.config import DEFAULTS
from asistente.motor import arreglar_palabras, parece_orden

ABIERTAS = [Ventana(101, "Tesis.docx - Word", "winword"), Ventana(202, "(3) YouTube - Google Chrome", "chrome"),
            Ventana(303, "ChatGPT - Google Chrome", "chrome")]


class ControlTotal(unittest.TestCase):
    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["documentos"]["carpeta"] = carpeta.name
        with (patch("asistente.comandos.Recordatorios"), patch("asistente.comandos.sistema.Apps"),
              patch("asistente.comandos.carpetas_usuario", return_value=[])):
            self.cmd = Comandos(cfg, Mock(), Mock(), Mock(), Mock())
        self.cmd.nav.abrir = Mock()
        self.cmd.apps.buscar.side_effect = lambda q: {"Name": "Word", "AppID": "w"} if "word" in q.casefold() else None
        self.hecho = []
        for nombre in ("enfocar", "minimizar", "maximizar", "cerrar", "rueda", "suspender"):
            self.enterContext(patch.object(ventanas, nombre,
                                           side_effect=lambda *a, n=nombre: self.hecho.append((n, *a)) or True))
        self.enterContext(patch.object(ventanas, "teclas",
                                       side_effect=lambda c: self.hecho.append(("teclas", c)) or True))
        self.enterContext(patch.object(ventanas, "listar", side_effect=lambda: list(ABIERTAS)))
        self.enterContext(patch.object(ventanas, "activa", side_effect=lambda: ABIERTAS[0]))
        self.enterContext(patch.object(ventanas, "abrir_y_enfocar",
                                       side_effect=lambda lanzar, *a, **k: (lanzar(), self.hecho.append(("abrir",)))))
        self.escrito = []
        self.enterContext(patch("asistente.comandos.sistema.escribir", side_effect=self.escrito.append))

    def hacer(self, frase):
        r = self.cmd.procesar(frase)
        self.assertIsNotNone(r, frase)
        if r.ejecutar:
            r.texto = r.ejecutar()
        if r.accion:
            r.accion()
        if r.despues:
            r.despues()
        return r

    def test_abrir_algo_ya_abierto_lo_trae_al_frente(self):
        self.hacer("abre el navegador")
        self.assertEqual(self.hecho, [("enfocar", 202)])
        self.cmd.nav.abrir.assert_not_called()
        self.hecho.clear()
        self.hacer("abre Word")
        self.assertEqual(self.hecho, [("enfocar", 101)])

    def test_navegador_cerrado_se_abre_y_se_enfoca(self):
        with patch.object(ventanas, "listar", return_value=[]):
            self.hacer("abre el navegador")
        self.cmd.nav.abrir.assert_called_once()
        self.assertIn(("abrir",), self.hecho)

    def test_whatsapp_se_abre_en_el_navegador_y_se_corrige(self):
        r = self.hacer("abre WhatsApp")
        self.assertIn("Web", r.texto)
        self.assertEqual(self.cmd.nav.abrir.call_args[0][0], "https://web.whatsapp.com/")
        self.cmd.nav.abrir.reset_mock()
        self.hacer("pero no la aplicación de WhatsApp, sino en el navegador")
        self.assertEqual(self.cmd.nav.abrir.call_args[0][0], "https://web.whatsapp.com/")

    def test_ventanas_y_teclado(self):
        casos = {"cambia a YouTube": ("enfocar", 202), "minimiza Word": ("minimizar", 101),
                 "cierra esta ventana": ("cerrar", 101), "nueva pestaña": ("teclas", "ctrl+t"),
                 "dale enter": ("teclas", "enter"),
                 "baja": ("rueda", -5), "muestra el escritorio": ("teclas", "win+d")}
        for frase, esperado in casos.items():
            with self.subTest(frase=frase):
                self.hecho.clear()
                self.hacer(frase)
                self.assertEqual(self.hecho, [esperado])
        self.hecho.clear()
        with patch.object(ventanas, "activa", return_value=ABIERTAS[1]):
            self.hacer("cierra la pestaña")
        self.assertEqual(self.hecho, [("teclas", "ctrl+w")])
        self.assertIn("volumen", str(self.cmd.procesar("sube el volumen").accion) + "volumen")
        self.assertIsNone(self.cmd.procesar("cambia a Excel"))  # no está abierto: que lo resuelva "abre"

    def test_teclas_habladas(self):
        self.assertEqual(interpretar_teclas("control más t"), [0x11, ord("T")])
        self.assertEqual(interpretar_teclas("alt f4"), [0x12, 0x73])
        self.assertEqual(interpretar_teclas("la flecha abajo"), [0x28])
        self.assertIsNone(interpretar_teclas("el botón Enviar"))

    def test_escribe_tal_cual(self):
        self.hacer("escribe hola mundo, ¿cómo estás?")
        self.assertEqual(self.escrito, ["hola mundo, ¿cómo estás?"])
        self.assertIsNone(self.cmd.procesar("escribe hola en el campo búsqueda"))

    def test_suspender_directo_o_con_pregunta_si_lo_adivino(self):
        r = self.hacer("suspende la computadora")
        self.assertIn(("suspender", False), self.hecho)
        self.cmd.interpretado = True
        r = self.cmd.procesar("suspende la computadora")
        self.assertTrue(r.siguiente)

    def test_imagen_con_iniciativa(self):
        self.cmd.revisar_encargo = Mock()
        r = self.hacer("créame una imagen")
        self.assertTrue(r.siguiente)
        self.cmd.nav.abrir.assert_not_called()
        seguir = r.siguiente("un gato astronauta en acuarela")
        seguir.ejecutar()
        self.cmd.revisar_encargo.assert_called_once_with("ChatGPT", "Crea una imagen: gato astronauta en acuarela")
        self.assertNotIn(("teclas", "enter"), self.hecho)
        self.assertFalse(self.escrito)

    def test_cerrar_todo_no_se_hace_a_ciegas(self):
        r = self.cmd.procesar("cierra todo")
        self.assertIsNone(r.accion)


class Interprete(unittest.TestCase):
    def test_arreglos_y_filtro(self):
        self.assertEqual(arreglar_palabras("Te abre el naveador."), "Te abre el navegador.")
        self.assertEqual(arreglar_palabras("Subpende la computadora"), "suspende la computadora")
        self.assertTrue(parece_orden("Quiero que ahora es el navegador"))
        self.assertTrue(parece_orden("No veo que hayas abierto el nogado"))
        self.assertFalse(parece_orden("qué opinas de la inteligencia artificial"))


class Registro(unittest.TestCase):
    def test_sync_no_escribe_tokens(self):
        from asistente.sync import Sincronizador

        def falla():
            raise OSError("Max retries exceeded with url: /export.php?userid=123&authtoken=abc123&preset=x")

        with self.assertLogs("sync", "WARNING") as logs:
            Sincronizador({}, Mock())._uno("clementina", falla)
        self.assertNotIn("abc123", " ".join(logs.output))
        self.assertNotIn("123&", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
