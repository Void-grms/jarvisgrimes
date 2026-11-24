import unittest
from unittest.mock import Mock, patch

from asistente.actividad import Actividad, ResultadoAccion, ResultadoDeshacer
from asistente.acciones.pestana_propia import Apertura
from asistente.respuesta import Respuesta


class ActividadTest(unittest.TestCase):
    def test_una_accion_tardia_no_reemplaza_la_mas_reciente(self):
        actividad = Actividad()
        anterior = Respuesta("Abriendo YouTube", accion=lambda: ResultadoAccion("Abierto.", Mock()))
        actividad.preparar("abre YouTube", anterior)
        nueva = Respuesta(ejecutar=lambda: "Word está al frente.")
        actividad.preparar("abre Word", nueva)
        nueva.ejecutar()
        anterior.accion()
        self.assertIn("Word está al frente.", actividad.detalle())
        self.assertIn("no tiene", actividad.consultar("deshazlo").ejecutar())

    def test_solo_portadas_sin_consultas_ni_fragmentos_se_marcan(self):
        for url in ("https://www.google.com/search?q=tesis", "https://www.google.com/#algo",
                    "https://www.youtube.com/watch?v=123", "https://otro.test/", None):
            self.assertIsNone(Apertura.para(url, "chrome"))
        url = "https://www.youtube.com/"
        self.assertNotEqual(Apertura.para(url, "edge").url, Apertura.para(url, "edge").url)

    def test_cierre_exige_el_mismo_proceso_hwnd_url_y_control(self):
        objetivo = Apertura.para("https://www.youtube.com/", "edge")
        proceso = Mock()
        proceso.name.return_value = "msedge.exe"
        proceso.create_time.return_value = 1234
        ventana = (991, 888)
        with (patch("asistente.acciones.pestana_propia.psutil.Process", return_value=proceso),
              patch("asistente.acciones.pantalla._ventana", return_value=ventana) as foco,
              patch("asistente.acciones.pantalla._uia", return_value={"id": "tab-id"}) as puente):
            recibo = objetivo.identificar()
            self.assertIsNotNone(recibo.deshacer)
            foco.return_value = (992, 888)
            self.assertTrue(recibo.deshacer().reintentar)
            self.assertEqual(puente.call_count, 1)
            foco.return_value = ventana
            puente.return_value = {"distinta": True}
            self.assertTrue(recibo.deshacer().reintentar)
            enviado = puente.call_args
            self.assertEqual(enviado.args, (ventana, "cerrar"))
            self.assertEqual(enviado.kwargs["id"], "tab-id")
            self.assertIn(objetivo.url, enviado.kwargs["urls"])
            proceso.create_time.return_value = 5678
            intentos = puente.call_count
            self.assertFalse(recibo.deshacer().reintentar)
            self.assertEqual(puente.call_count, intentos)

    def test_puede_reintentar_solo_si_el_cierre_no_llego_a_enviarse(self):
        actividad = Actividad()
        cerrar = Mock(side_effect=[ResultadoDeshacer("Selecciona la original.", True), ResultadoDeshacer("Cerrada.")])
        r = Respuesta(accion=lambda: ResultadoAccion("Identificada.", cerrar))
        actividad.preparar("abre Google", r)
        r.accion()
        self.assertIn("Selecciona", actividad.consultar("deshazlo").ejecutar())
        self.assertEqual(actividad.consultar("deshazlo").ejecutar(), "Cerrada.")
        self.assertIn("no tiene", actividad.consultar("deshazlo").ejecutar())
