"""Solicitudes UAC con una Shell simulada. No se elevan procesos durante las pruebas."""
import os
import unittest
from unittest.mock import Mock, patch

from asistente.acciones import administracion


@unittest.skipUnless(os.name == "nt", "requiere las bibliotecas de Windows")
class Administracion(unittest.TestCase):
    def setUp(self):
        self.lanzar = self.enterContext(patch("win32com.shell.shell.ShellExecuteEx"))
        self.esperar = self.enterContext(patch("win32event.WaitForSingleObject"))
        self.salida = self.enterContext(patch("win32process.GetExitCodeProcess", return_value=0))
        self.handle = Mock()
        self.lanzar.return_value = {"hProcess": self.handle}
        self.avisar = Mock()

    def test_consola_con_ruta_del_sistema_y_uac(self):
        r = administracion.procesar("Abre el Visor de eventos como administrador", self.avisar)
        self.lanzar.assert_not_called()
        r.accion()
        llamada = self.lanzar.call_args.kwargs
        carpeta = administracion._sistema()
        self.assertEqual(llamada["lpVerb"], "runas")
        self.assertEqual(llamada["lpFile"], str(carpeta / "mmc.exe"))
        self.assertIn(str(carpeta / "eventvwr.msc"), llamada["lpParameters"])
        self.esperar.assert_not_called()
        self.handle.Close.assert_called_once()
        self.assertIn("inició", self.avisar.call_args.args[0])

    def test_tareas_con_codigo_de_salida(self):
        r = administracion.procesar("Verifica los archivos del sistema", self.avisar)
        r.accion()
        self.assertEqual(self.lanzar.call_args.kwargs["lpParameters"], "/verifyonly")
        self.esperar.assert_called_once()
        self.assertIn("Terminó", self.avisar.call_args.args[0])
        self.salida.return_value = 5
        administracion.procesar("Vacía la caché DNS", self.avisar).accion()
        self.assertIn("código 5", self.avisar.call_args.args[0])

    def test_rechazo_de_uac_se_comunica_sin_reintentar(self):
        from pywintypes import error
        self.lanzar.side_effect = error(1223, "ShellExecuteEx", "cancelado")
        administracion.procesar("Abre Servicios como administrador", self.avisar).accion()
        self.avisar.assert_called_once_with("Windows canceló la solicitud de administrador.")
        self.lanzar.assert_called_once()

    def test_datos_dictados_y_menciones_no_se_convierten_en_argumentos(self):
        for texto in ("No abras servicios como administrador", "Cómo se abre el visor de eventos",
                      "Abre algo desconocido como administrador", "Abre Servicios como administrador y escribe algo"):
            r = administracion.procesar(texto, self.avisar)
            self.assertTrue(r is None or not (r.accion or r.ejecutar))
        self.lanzar.assert_not_called()
