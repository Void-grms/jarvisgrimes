"""Comandos del registro con ventanas, procesos y perfiles de prueba."""
import copy
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from asistente.acciones import ventanas
from asistente.comandos import Comandos
from asistente.config import DEFAULTS


class Escritorio(unittest.TestCase):
    def setUp(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["documentos"]["carpeta"] = self.enterContext(TemporaryDirectory())
        self.enterContext(patch("asistente.comandos.Recordatorios"))
        self.enterContext(patch("asistente.comandos.sistema.Apps"))
        self.enterContext(patch("asistente.comandos.carpetas_usuario", return_value=[]))
        self.cmd = Comandos(cfg, Mock(), Mock(), Mock(), Mock())
        self.word = ventanas.Ventana(5, "Word demo", "winword")
        self.chrome = ventanas.Ventana(6, "Chrome demo", "chrome")
        self.win = Mock()
        self.enterContext(patch.object(ventanas, "_windows", return_value=(self.win, Mock())))
        self.lista = self.enterContext(patch.object(ventanas, "listar", return_value=[self.word, self.chrome]))
        self.foco = self.enterContext(patch.object(ventanas, "enfocar", return_value=True))
        self.tabla = self.enterContext(patch.object(self.cmd.nav, "perfiles", return_value={
            "Default": ["Persona"], "Profile 1": ["Trabajo diario"], "Profile 2": ["Trabajo externo"]}))

    def test_minimizar_es_idempotente_y_conserva_el_alcance(self):
        minimizadas = set()
        self.win.ShowWindowAsync.side_effect = lambda hwnd, _: minimizadas.add(hwnd)
        self.win.IsIconic.side_effect = minimizadas.__contains__
        for pedido in ("Minimiza todas las aplicaciones, menos tú.", "Quiero que minimices todas las aplicaciones."):
            minimizadas.clear()
            r = self.cmd.procesar(pedido)
            self.assertFalse(minimizadas)
            self.assertEqual(r.ejecutar(), "Minimicé 2 ventanas.")
            self.assertEqual(minimizadas, {5, 6})
        self.chrome.minimizada = True
        self.win.ShowWindowAsync.reset_mock()
        r = self.cmd.procesar("Minimiza todas las pestañas")
        self.assertIn("ya estaban", r.ejecutar())
        self.win.ShowWindowAsync.assert_not_called()

    def test_informa_si_una_ventana_rechaza_el_cambio(self):
        self.win.IsIconic.side_effect = lambda hwnd: hwnd == 6
        with patch.object(ventanas.time, "monotonic", side_effect=[1, 3]):
            self.assertEqual(ventanas.minimizar_grupo(), "Windows confirmó 1 de 2 ventanas minimizadas.")

    def test_nueva_ventana_es_un_nuevo_handle_no_una_pestana(self):
        self.cmd.nav.ejecutable = Mock(return_value="chrome.exe")
        popen = self.enterContext(patch("asistente.acciones.navegador.subprocess.Popen"))
        nueva = ventanas.Ventana(7, "Nueva", "chrome")
        for frase in ("Abre una nueva ventana.", "Abre una ventana nueva en el navegador.",
                      "Abre una nueva ventana de Google Chrome."):
            self.lista.side_effect = [[self.chrome], [self.chrome, nueva]]
            self.assertIn("Abrí una nueva ventana", self.cmd.procesar(frase).ejecutar())
            popen.assert_called_with(["chrome.exe", "--new-window", "about:blank"])
            self.foco.assert_called_with(7)
        self.lista.side_effect = None
        self.lista.return_value = [ventanas.Ventana(6, "Solo cambió el título", "chrome")]
        with patch.object(ventanas.time, "monotonic", side_effect=[1, 1, 6]), patch.object(ventanas.time, "sleep"):
            self.assertFalse(ventanas.esperar_y_enfocar(("chrome",), {6: "Antes"}, espera=4, exigir_nueva=True))
        self.cmd.nav.abrir = Mock()
        self.cmd.procesar("Abre una nueva pestaña del navegador.").accion()
        self.cmd.nav.abrir.assert_called_once_with("about:blank", None, False)

    def test_bloc_nuevo_y_sel_de_perfil(self):
        with patch.object(ventanas, "abrir_bloc", return_value="Listo.") as abrir:
            for frase in ("Abra el vlog de notas.", "Abre un nuevo blog de notas.", "Abre otro Block de notas."):
                self.assertEqual(self.cmd.procesar(frase).ejecutar(), "Listo.")
            self.assertEqual(abrir.call_args_list, [call(False), call(True), call(True)])
        self.assertIsNone(self.cmd.nav.resolver_perfil("trabajo"))
        self.assertIsNone(self.cmd.nav.resolver_perfil("encrimes"))
        aclarar = self.cmd.procesar("Vamos a utilizar el perfil de encrimes.")
        self.assertIsNotNone(aclarar.siguiente)
        self.assertIn("Trabajo externo", aclarar.siguiente("Trabajo externo").texto)
        self.assertEqual(self.cmd.nav.perfil_pred, "Profile 2")

    def test_teclas_internas_y_fallos_de_windows(self):
        for combo, codigos in (("ctrl+n", [17, 78]), ("ctrl+shift+w", [17, 16, 87]),
                               ("win+d", [91, 68]), ("ctrl+mas", [17, 187])):
            self.assertEqual(ventanas.interpretar_teclas(combo), codigos)
        with patch.object(ventanas, "_enviar", return_value=False):
            self.assertFalse(ventanas.teclas("ctrl+n"))

    def test_mencion_bloc_se_aclara_antes_de_lanzar(self):
        with patch.object(ventanas, "abrir_bloc", return_value="Listo.") as abrir:
            r = self.cmd.procesar("Block de Notas.")
            self.assertFalse(r.accion or r.ejecutar)
            self.assertIsNone(r.siguiente("Todavía no lo obres.").ejecutar)
            n = r.siguiente("Si uno nuevo.")
            a = r.siguiente("Ábrelo.")
            abrir.assert_not_called()
            self.assertEqual(n.ejecutar(), "Listo.")
            self.assertEqual(a.ejecutar(), "Listo.")
            self.assertEqual(abrir.call_args_list, [call(True), call(False)])

    def test_opciones_en_la_misma_frase_requieren_una_eleccion(self):
        with patch.object(ventanas, "minimizar_grupo", return_value="Listo.") as minimizar:
            self.cmd.nav.abrir = Mock()
            self.enterContext(patch.object(ventanas, "abrir_y_enfocar",
                                          side_effect=lambda lanzar, *a, **k: lanzar()))
            r = self.cmd.procesar("Ok, siguiente prueba. Busca o minimiza todas las pestañas que estamos en "
                                  "clado ahorita, quiero que la minimices.")
            self.assertFalse(r.accion or r.ejecutar)
            consultar = r.siguiente("Buscar").siguiente("El Imperio inca")
            reducir = r.siguiente("Minimízalas.")
            minimizar.assert_not_called()
            self.cmd.nav.abrir.assert_not_called()
            self.assertEqual(reducir.ejecutar(), "Listo.")
            minimizar.assert_called_once_with(True)
            consultar.accion()
            self.cmd.nav.abrir.assert_called_once_with("https://www.google.com/search?q=El+Imperio+inca", None, False)

    def test_cierre_no_se_reenvia_a_otra_ventana(self):
        with (patch.object(ventanas, "activa", side_effect=[self.chrome, self.word]),
              patch.object(ventanas, "teclas") as teclas):
            r = self.cmd.procesar("cierra la pestaña")
            self.assertIn("Cambió", r.ejecutar())
            teclas.assert_not_called()
