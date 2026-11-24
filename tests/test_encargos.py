import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from asistente.acciones import encargos


class PedidosDeEscritorio(unittest.TestCase):
    def setUp(self):
        self.abrir_revision = Mock()
        self.encargos = encargos.Encargos(self.abrir_revision)
        self.apps = Mock()

    def test_prepara_tres_tipos_con_revision_y_conserva_el_pedido(self):
        frases = [
            ("Usa Claude para crear un informe de Word sobre el río Rímac.", "Claude", "río Rímac"),
            ("Pídele a ChatGPT que corrija el documento de Lucía.", "ChatGPT", "Lucía"),
            ("Crea con la app de Claude una presentación sobre energía.", "Claude", "energía"),
            ("Convierte mis notas en una presentación con ChatGPT.", "ChatGPT", "mis notas"),
        ]
        with patch.object(encargos, "enviar_revisado") as enviar:
            for texto, destino, contenido in frases:
                r = self.encargos.procesar(texto, self.apps)
                self.assertIsNotNone(r.ejecutar)
                self.assertIn("panel", r.ejecutar())
                self.assertEqual(self.abrir_revision.call_args.args[0], destino)
                self.assertIn(contenido, self.abrir_revision.call_args.args[1])
            enviar.assert_not_called()
        self.apps.abrir.assert_not_called()

    def test_contexto_reciente_pertinente_y_limpiar(self):
        self.assertIsNone(self.encargos.procesar("Crea un informe de física.", self.apps))
        self.encargos.procesar("Mejor con ChatGPT.", self.apps).ejecutar()
        self.abrir_revision.assert_called_once_with("ChatGPT", "Crea un informe de física.")
        self.assertIsNone(self.encargos.procesar("¿Qué hora es?", self.apps))
        self.assertIsNone(self.encargos.procesar("Claude", self.apps))
        self.encargos.limpiar()
        self.assertIn("Todavía", self.encargos.procesar("Copia el encargo", self.apps).texto)
        with patch.object(encargos.time, "monotonic", side_effect=[20, 141]):
            self.encargos.procesar("Crea una presentación", self.apps)
            self.assertIsNone(self.encargos.procesar("Hazla con Claude", self.apps))

    def test_negaciones_menciones_y_apertura_simple(self):
        for t in ("No uses Claude para crear un informe", "Dime cómo crear un informe en ChatGPT",
                  "Crea una presentación sobre Claude", "Abre ChatGPT",
                  "Redacta un ejemplo sobre cómo trabajar con Claude", "Crea un informe pero no con Claude",
                  "Busca una hoja de cálculo con ChatGPT", "Escribe aquí trabajamos con Claude"):
            self.assertIsNone(self.encargos.procesar(t, self.apps), t)
        self.abrir_revision.assert_not_called()

    def test_copiar_y_pegar_solo_cuando_lo_piden(self):
        self.encargos = encargos.Encargos()
        self.enterContext(patch.object(encargos.ventanas, "abrir_y_enfocar", return_value=False))
        with patch("pyperclip.copy") as copiar, patch.object(encargos, "_operar") as operar:
            self.encargos.procesar("Crea un informe con Claude", self.apps).ejecutar()
            self.assertIn("informe", self.encargos.procesar("Léeme el encargo", self.apps).texto)
            copiar.assert_not_called()
            self.encargos.procesar("Copia el encargo", self.apps).ejecutar()
            self.assertIn("informe", copiar.call_args.args[0])
            self.assertIsNone(self.encargos.procesar("Pégalo en ChatGPT", self.apps).ejecutar)
            operar.assert_not_called()
            operar.return_value = True, "Dejé el texto."
            self.encargos.procesar("Pégalo en Claude", self.apps).ejecutar()
            self.assertEqual(operar.call_args.args, ("Claude", copiar.call_args.args[0], "pegar", self.apps))

    def test_revisar_antes_de_transferir_y_aclarar_destinos(self):
        with patch.object(encargos, "_operar") as operar:
            self.encargos.procesar("Crea un informe con Claude", self.apps).ejecutar()
            self.encargos.procesar("Pégalo en Claude", self.apps).ejecutar()
            self.assertEqual(self.abrir_revision.call_args.args, ("Claude", "Crea un informe"))
            self.assertIsNone(self.encargos.procesar("Usa Claude para crear un informe con ChatGPT", self.apps).ejecutar)
            self.assertIsNone(self.encargos.procesar("Escribe una comparación de Word con Claude", self.apps))
            operar.assert_not_called()

    def test_en_consola_el_encargo_queda_disponible_si_falta_la_app(self):
        modulo = encargos.Encargos()
        self.apps.buscar.return_value = None
        r = modulo.procesar("Usa ChatGPT para revisar mi documento.", self.apps)
        self.assertIn("menú Inicio", r.ejecutar())
        self.assertIn("revisar mi documento", modulo.procesar("lee el encargo", self.apps).texto)
        self.apps.abrir.assert_not_called()


class PuenteDeEncargos(unittest.TestCase):
    def setUp(self):
        self.ventana = SimpleNamespace(hwnd=123, proceso="claude")
        self.activa = self.enterContext(patch.object(encargos.ventanas, "activa", return_value=self.ventana))
        self.identidad = self.enterContext(patch.object(encargos.pantalla, "_ventana", return_value=(123, 90)))
        self.uia = self.enterContext(patch.object(encargos.pantalla, "_uia"))
        self.apps = self.enterContext(patch.object(encargos.sistema, "Apps")).return_value

    def test_solo_el_texto_revisado_y_la_aplicacion_elegida(self):
        texto = "Río y piñón.\n\nUna línea con ` y comillas."
        self.uia.return_value = {"estado": "enviado"}
        ok, _ = encargos.enviar_revisado("Claude", texto)
        self.assertTrue(ok)
        self.uia.assert_called_once_with((123, 90), "enviar", puente=encargos._PUENTE, proceso="claude", texto=texto)
        self.uia.return_value = {"error": "tiempo"}
        self.assertIn("antes de repetirlo", encargos.enviar_revisado("Claude", texto)[1])

    def test_ventana_distinta_ambigua_o_no_instalada(self):
        self.identidad.return_value = (200, 90)
        self.assertFalse(encargos.enviar_revisado("Claude", "Texto")[0])
        self.activa.return_value = SimpleNamespace(hwnd=123, proceso="chrome")
        with patch.object(encargos.ventanas, "de_programa", return_value=[self.ventana, self.ventana]):
            self.assertIn("varias", encargos.enviar_revisado("Claude", "Texto")[1])
        self.apps.buscar.return_value = None
        self.assertFalse(encargos.enviar_revisado("Claude", "Texto")[0])
        self.uia.assert_not_called()

    def test_solo_la_seleccion_actual_puede_importarse(self):
        self.uia.return_value = {"error": "sin_seleccion"}
        self.assertFalse(encargos.leer_seleccion("Claude")[0])
        self.uia.return_value = {"texto": "Resultados de la revisión."}
        self.assertEqual(encargos.leer_seleccion("Claude"), (True, "Resultados de la revisión."))
        self.assertEqual(self.uia.call_args.args[1], "seleccion")

    @unittest.skipUnless(os.name == "nt", "Puente de accesibilidad de Windows")
    def test_envio_con_compositor_y_boton_simulados(self):
        script = Path(__file__).with_name("simular_encargo_uia.ps1")
        prueba = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(script), str(encargos._PUENTE)], capture_output=True, timeout=20)
        self.assertEqual(prueba.returncode, 0, prueba.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
