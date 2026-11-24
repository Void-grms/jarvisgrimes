import unittest
from unittest.mock import Mock, patch

from asistente.acciones import niveles
from asistente.acciones.niveles import Ajuste, interpretar
from asistente.comandos import Comandos


class Niveles(unittest.TestCase):
    def test_pedidos_del_registro(self):
        for frase, control in (
            ("A ver, quiero que subas todo el volumen.", "volumen"),
            ("Quiero que subas todo el brillo.", "brillo"),
            ("Sube todo el volumen, todo, todo, al 100%.", "volumen"),
            ("Pon el brillo al máximo.", "brillo"),
        ):
            with self.subTest(frase=frase):
                self.assertEqual(interpretar(frase), Ajuste(control, "fijar", 100))
        self.assertEqual(interpretar("Estoy revisando y no ha subido todo el volumen. "
                                     "Está exactamente en un 64%."), Ajuste("volumen", "leer"))
        self.assertEqual(interpretar("Solamente ha subido un 10%.", "volumen"), Ajuste("volumen", "leer"))
        self.assertEqual(interpretar("Al cien por ciento.", "brillo"), Ajuste("brillo", "fijar", 100))
        self.assertIsNone(interpretar("Al cien por ciento."))

    def test_incrementos_y_porcentajes(self):
        casos = {
            "baja el brillo": Ajuste("brillo", "variar", -10),
            "baja todo el brillo": Ajuste("brillo", "fijar", 0),
            "sube el sonido en 13 puntos": Ajuste("volumen", "variar", 13),
            "pon el brillo en noventa y siete por ciento": Ajuste("brillo", "fijar", 97),
            "baja el volumen al veinticinco por ciento": Ajuste("volumen", "fijar", 25),
            "volumen al mínimo": Ajuste("volumen", "fijar", 0),
            "sube el volumen en 999 por ciento": Ajuste("volumen", "aclarar"),
            "pon el brillo al -4%": Ajuste("brillo", "aclarar"),
            "pon el brillo al 1,5%": Ajuste("brillo", "aclarar"),
            "¿En cuánto está el brillo?": Ajuste("brillo", "leer"),
            "activa el sonido": Ajuste("volumen", "activar"),
            "silencia el sonido": Ajuste("volumen", "silenciar"),
        }
        for frase, esperado in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(interpretar(frase), esperado)
        for frase in ("No subas todo el volumen.", "¿Cómo puedo bajar el brillo?", "Anota que subí el volumen."):
            self.assertIsNone(interpretar(frase))

    def test_resultado_real_del_dispositivo(self):
        salida = Mock()
        salida.GetMasterVolumeLevelScalar.side_effect = [0.64, 0.92]
        salida.GetMute.return_value = False
        texto = niveles._volumen(Ajuste("volumen", "fijar", 100), salida)
        salida.SetMasterVolumeLevelScalar.assert_called_once_with(1.0, None)
        self.assertIn("92 por ciento", texto)
        self.assertNotIn("100", texto)

    def test_quejas_y_consultas_solo_leen(self):
        salida = Mock()
        salida.GetMasterVolumeLevelScalar.return_value = 0.64
        salida.GetMute.return_value = True
        texto = niveles._volumen(Ajuste("volumen", "leer"), salida)
        self.assertIn("64 por ciento y silenciado", texto)
        salida.SetMute.assert_not_called()
        salida.SetMasterVolumeLevelScalar.assert_not_called()
        for _ in range(2):
            niveles._volumen(Ajuste("volumen", "silenciar"), salida)
        self.assertTrue(all(c.args == (True, None) for c in salida.SetMute.call_args_list))

    def test_brillo_individual_y_error_parcial(self):
        interno, externo = Mock(), Mock()
        interno.get_brightness.side_effect = [84, 94]
        externo.get_brightness.side_effect = OSError()
        with patch.object(niveles.time, "sleep"), self.assertLogs("niveles", level="WARNING"):
            texto = niveles._brillo(Ajuste("brillo", "variar", 10), [interno, externo])
        interno.set_brightness.assert_called_once_with(94)
        externo.set_brightness.assert_not_called()
        self.assertIn("pantalla 1 al 94 por ciento", texto)
        self.assertIn("pantalla 2 sin nivel verificable", texto)
        self.assertIn("no detectó", niveles._brillo(Ajuste("brillo", "leer"), []))

    def test_comando_diferido_y_aclaracion(self):
        cmd = Comandos.__new__(Comandos)
        cmd._ultimo_nivel = ("", 0.)
        cmd.interpretado = False
        with patch.object(niveles, "ejecutar", return_value="Volumen comprobado.") as ejecutar:
            r = cmd.procesar("Sube todo el volumen, todo, todo, al 100%.")
            ejecutar.assert_not_called()
            self.assertEqual(r.ejecutar(), "Volumen comprobado.")
            ejecutar.assert_called_once_with(Ajuste("volumen", "fijar", 100))
            cmd.procesar("Solamente ha subido un 10%.").ejecutar()
            ejecutar.assert_called_with(Ajuste("volumen", "leer"))
            cmd.interpretado = True
            dudosa = cmd.procesar("Sube el volumen")
            self.assertIsNone(dudosa.ejecutar)
            self.assertIsNone(dudosa.siguiente("No").ejecutar)
            self.assertIsNone(dudosa.siguiente("Abre Word"))
            dudosa.siguiente("Al 70%").ejecutar()
            ejecutar.assert_called_with(Ajuste("volumen", "fijar", 70))
