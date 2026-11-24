"""Decisiones del backend con una ventana ficticia. Nunca consulta la pantalla personal."""
import unittest
from unittest.mock import patch

from asistente.acciones import pantalla


def control(id, nombre, tipo, *, click=None, edita=False, word=False):
    return dict(id=id, nombre=nombre, tipo=tipo, click=click, edita=edita, word=word,
                padre="p", foco=False)


class PantallaTest(unittest.TestCase):
    def setUp(self):
        self.ventana = self.enterContext(patch.object(pantalla, "_ventana", return_value=(10, 77)))
        self.uia = self.enterContext(patch.object(pantalla, "_uia"))
        self.boton = control("a", "Aceptar", "Button", click="Invoke")
        self.campo = control("b", "Búsqueda", "Edit", edita=True)
        self.vista = dict(titulo="Ventana de prueba", foco=self.campo,
                          controles=[self.boton, self.campo], incompleto=False)
        self.p = pantalla.Pantalla()

    def test_describe_ventana_y_controles(self):
        self.uia.return_value = self.vista
        self.assertIn("botón Aceptar", self.p.describir())
        self.uia.assert_called_once_with((10, 77), "leer")

    def test_una_orden_congela_destino_antes_de_la_confirmacion(self):
        self.uia.side_effect = [self.vista, {"estado": "clic"}]
        accion = self.p.preparar("clic", "aceptar")
        self.uia.assert_called_once_with((10, 77), "leer", etiqueta="aceptar", soloFoco=False)
        self.assertIn("Activé", accion())
        self.assertEqual(self.uia.call_args.kwargs["control"]["id"], "a")

    def test_cambio_de_ventana_despues_de_preparar(self):
        self.uia.return_value = self.vista
        self.ventana.side_effect = [(10, 77), (20, 88)]
        accion = self.p.preparar("clic", "Aceptar")
        self.assertIn("Cambió la ventana", accion())
        self.uia.assert_called_once()

    def test_control_o_titulo_cambiado_en_el_proveedor(self):
        self.uia.side_effect = [self.vista, {"error": "control"}]
        self.assertIn("control cambió", self.p.clic("Aceptar"))

    def test_dos_etiquetas_iguales_no_hacen_clic(self):
        self.vista["controles"].append(control("otro", "Aceptar", "Button", click="Invoke"))
        self.uia.return_value = self.vista
        self.assertIn("varios controles", self.p.clic("Aceptar"))
        self.uia.assert_called_once()

    def test_pedir_un_fragmento_no_activa_un_boton_distinto(self):
        self.boton["nombre"] = "Eliminar pedido"
        self.uia.return_value = self.vista
        self.assertIn("nombre completo", self.p.clic("pedido"))
        self.uia.assert_called_once()

    def test_objetivo_inexistente_devuelve_una_aclaracion_inmediata(self):
        self.uia.return_value = self.vista
        self.assertIn("No encontré", self.p.preparar("clic", "ausente"))
        self.uia.assert_called_once()

    def test_control_protegido_omitido_por_el_proveedor(self):
        self.vista["foco"] = None
        self.uia.return_value = self.vista
        self.assertIn("editable", self.p.escribir("prueba"))
        self.uia.assert_called_once()

    def test_escribir_en_foco_solicita_revalidacion(self):
        self.uia.side_effect = [self.vista, {"error": "foco"}]
        self.assertIn("Cambió el campo", self.p.escribir("hola"))
        self.assertTrue(self.uia.call_args.kwargs["foco"])

    def test_busqueda_activa_solo_boton_vecino(self):
        self.boton["nombre"] = "Buscar"
        self.uia.side_effect = [self.vista, {"estado": "buscado"}]
        self.assertIn("Envié", self.p.buscar("prueba"))
        self.assertEqual(self.uia.call_args.kwargs["boton"]["id"], self.boton["id"])

    def test_word_revalida_documento_y_seleccion(self):
        self.campo.update(edita=False, word=True, tipo="Document")
        self.uia.side_effect = [self.vista, {"estado": "word"}]
        origen = ("prueba", 1, 0, 0)
        with patch.object(pantalla, "_word", side_effect=[origen, None]) as word:
            accion = self.p.preparar("escribir", "texto")
            self.assertIn("Cambió la selección", accion())
            word.assert_called_with((10, 77), origen, "texto")


if __name__ == "__main__":
    unittest.main()
