import unittest
from unittest.mock import Mock

from asistente.control_pantalla import escritura_libre, procesar_pantalla


class ControlPantallaTest(unittest.TestCase):
    def setUp(self):
        self.actuar = Mock(return_value="Resultado de la ventana simulada.")
        self.pantalla = Mock()
        self.pantalla.preparar.return_value = self.actuar

    def comprobar_orden(self, pedido, argumentos, ruta=procesar_pantalla):
        respuesta = ruta(pedido, self.pantalla)
        self.pantalla.preparar.assert_called_once_with(*argumentos)
        self.assertTrue(respuesta.confirmar_voz)
        self.assertFalse(respuesta.texto)
        self.actuar.assert_not_called()
        self.assertEqual(respuesta.ejecutar(), "Resultado de la ventana simulada.")
        self.actuar.assert_called_once_with()
        self.pantalla.reset_mock()
        self.actuar.reset_mock()

    def test_descripcion_solo_lee(self):
        self.pantalla.describir.return_value = "En Notas se ven Buscar y Archivo."
        for pedido in ("¿Qué ves en la pantalla?", "Describe la ventana activa.", "Dime qué hay aquí."):
            with self.subTest(pedido=pedido):
                r = procesar_pantalla(pedido, self.pantalla)
                self.assertEqual(r.texto, "En Notas se ven Buscar y Archivo.")
                self.assertIsNone(r.ejecutar)
        self.pantalla.preparar.assert_not_called()

    def test_clic_se_prepara_y_solo_actua_al_ejecutar(self):
        for pedido in ("Haz clic en Buscar.", "¿Puedes hacer clic en el botón «Buscar»?",
                       "Por favor, pulsa el botón Buscar", "quiero que hagas clic en Buscar"):
            with self.subTest(pedido=pedido):
                self.comprobar_orden(pedido, ("clic", "Buscar"))

    def test_clic_consecuente_requiere_si_inequivoco(self):
        r = procesar_pantalla("Haz clic en el botón Enviar mensaje", self.pantalla)
        self.assertIsNone(r.ejecutar)
        self.assertFalse(r.confirmar_voz)
        self.assertIn("Enviar mensaje", r.texto)
        self.assertIsNone(r.siguiente("Sí, pero no lo envíes."))
        self.assertIsNone(r.siguiente("No").ejecutar)
        self.actuar.assert_not_called()
        self.assertEqual(r.siguiente("Sí, confirmo.").ejecutar(), self.actuar.return_value)

    def test_por_el_nombre_del_control_pendiente(self):
        r = procesar_pantalla("Haz clic ahí", self.pantalla)
        self.pantalla.preparar.assert_not_called()
        self.assertIsNone(r.siguiente("Abre Word"))
        self.assertTrue(r.siguiente("Buscar").confirmar_voz)
        self.pantalla.preparar.assert_called_once_with("clic", "Buscar")
        self.actuar.assert_not_called()

    def test_busqueda_solo_en_la_ventana_pedida(self):
        for pedido in ("busca Ñandú en esta ventana", "busca aquí Ñandú",
                       "Busca en la ventana activa: Ñandú", "¿Puedes buscar Ñandú en esta página?"):
            with self.subTest(pedido=pedido):
                self.comprobar_orden(pedido, ("buscar", "Ñandú"))
        r = procesar_pantalla("Busca aquí.", self.pantalla)
        self.assertTrue(r.siguiente("Música en Lima").confirmar_voz)
        self.pantalla.preparar.assert_called_once_with("buscar", "Música en Lima")

    def test_escritura_literal_con_puntuacion_y_campo(self):
        for pedido, literal, campo in (
            ("Escribe: ¡Hola, Ana! ¿Vienes?", "¡Hola, Ana! ¿Vienes?", ""),
            ("Escribe: :)", ":)", ""),
            ("Escribe «el clima en el campo Nombre».", "el clima en el campo Nombre", ""),
            ('Escribe "Óscar" en el campo "Nombre completo".', "Óscar", "Nombre completo"),
            ("Escribe en el campo Saludo: Buenos días, Perú.", "Buenos días, Perú.", "Saludo"),
            ("Escribe aquí: Atención, 50 %.", "Atención, 50 %.", ""),
            ("escribe Café en el campo activo", "Café", ""),
        ):
            with self.subTest(pedido=pedido):
                self.comprobar_orden(pedido, ("escribir", literal, campo))

    def test_escritura_generica_se_deja_para_despues_de_otros_comandos(self):
        self.assertIsNone(procesar_pantalla("Escribe gran parte del texto.", self.pantalla))
        self.comprobar_orden("Escribe gran parte del texto.", ("escribir", "gran parte del texto.", ""),
                             escritura_libre)
        for pedido in ("Escribe un documento en Word sobre Lima", "Escribe lo que te diga",
                       "Escribe un mensaje a Ana por WhatsApp", "Busca una receta en Google"):
            self.assertIsNone(procesar_pantalla(pedido, self.pantalla))
        self.pantalla.preparar.assert_not_called()

    def test_menciones_y_negaciones_no_activan_controles(self):
        for texto in ("No hagas clic en Enviar", "Dije «escribe hola».", "Me dijeron busca aquí Lima.",
                      "Hacer clic en Buscar tarda mucho.", "Escribir ayuda a recordar.",
                      "No te pedí que hicieras clic en Buscar", "Si buscas aquí, ¿qué sucede?"):
            with self.subTest(texto=texto):
                self.assertIsNone(procesar_pantalla(texto, self.pantalla))
                self.assertIsNone(escritura_libre(texto, self.pantalla))
        self.pantalla.preparar.assert_not_called()
        self.pantalla.describir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
