import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from asistente.acciones import word_activo as office

_RESOLVER_ORIGINAL = office._ventana_office


@unittest.skipUnless(os.name == "nt", "El puente de Office utiliza pywin32.")
class DocumentoEnPrimerPlano(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ruta = base / "Informe.docx"
        self.otro = base / "Proyecto anterior" / self.ruta.name
        self.otro.parent.mkdir()
        self.ruta.touch()
        self.otro.touch()
        self.frente = self.enterContext(patch.object(office, "_frente", return_value=(123, "winword")))
        self.enterContext(patch.object(office, "_com", side_effect=nullcontext))
        self.doc = SimpleNamespace(Path=str(base), Saved=True, FullName=str(self.ruta))
        self.w = Mock(Hwnd=123, HWND=123, Document=self.doc)
        self.ventana = self.enterContext(patch.object(office, "_ventana_office", return_value=self.w))
        rango = self.w.Selection.Range.Duplicate
        rango.Start, rango.End, rango.Text = 3, 9, "público"
        p = rango.Paragraphs.Item.return_value
        p.Range.Text = "Es de interés público.\r"
        p.Previous.return_value.Range.Text = "Alcance oficial.\r"

    def test_fuente_y_seleccion_del_hwnd_correcto(self):
        c = office.capturar_documento("word")
        self.assertEqual(c.ruta, self.ruta)
        self.assertEqual(c.seleccion, "público")
        self.assertEqual(c.parrafo, "Es de interés público.")
        self.assertEqual(c.parrafo_anterior, "Alcance oficial.")
        self.assertEqual(c.hwnd, 123)
        self.ventana.assert_called_once_with(123, "word")

    def test_archivo_nuevo_o_sucio_pide_guardar(self):
        for carpeta, guardado in (("", True), (str(self.ruta.parent), False)):
            with self.subTest(carpeta=carpeta, guardado=guardado):
                self.doc.Path, self.doc.Saved = carpeta, guardado
                with self.assertRaisesRegex(office.ErrorDocumento, "Guarda los cambios") as fallo:
                    office.capturar_documento()
                self.assertNotIsInstance(fallo.exception, office.SinOfficeActivo)

    def test_cambio_de_foco_documento_o_edicion_durante_captura(self):
        self.frente.side_effect = [(123, "winword"), (124, "winword")]
        with self.assertRaisesRegex(office.ErrorDocumento, "activo cambió"):
            office.capturar_documento()
        self.frente.side_effect = None
        for campo, cambio in (("FullName", str(self.otro)), ("Saved", False)):
            with self.subTest(campo=campo):
                self.doc.FullName, self.doc.Saved = str(self.ruta), True
                def editar(ventana):
                    setattr(self.doc, campo, cambio)
                    return "", "", ""
                with patch.object(office, "_contexto_word", side_effect=editar), self.assertRaises(office.ErrorDocumento):
                    office.capturar_documento()

    def test_ventana_ajena_y_dialogo_office_se_distinguen(self):
        self.frente.return_value = (44, "chrome")
        with self.assertRaises(office.SinOfficeActivo):
            office.capturar_documento()
        for identificacion in (None, (225, ""), (300, "powerpnt")):
            self.frente.return_value = identificacion
            with self.subTest(identificacion=identificacion), self.assertRaises(office.ErrorDocumento) as fallo:
                office.capturar_documento("word")
            self.assertNotIsInstance(fallo.exception, office.SinOfficeActivo)

    def test_presentacion_y_diapositiva_pertenecen_al_mismo_archivo(self):
        presentacion = self.ruta.with_suffix(".pptx")
        presentacion.touch()
        self.frente.return_value = (123, "powerpnt")
        self.doc.FullName = str(presentacion)
        self.w.Presentation = self.doc
        hoja = self.w.View.Slide
        hoja.Parent, hoja.SlideIndex = self.doc, 3
        self.w.Selection.Type = 3
        self.w.Selection.TextRange.Text = "Texto seleccionado"
        c = office.capturar_documento("powerpoint")
        self.assertEqual((c.ruta, c.diapositiva, c.seleccion), (presentacion, 3, "Texto seleccionado"))
        hoja.Parent = SimpleNamespace(FullName=str(self.otro.with_suffix(".pptx")))
        c = office.capturar_documento()
        self.assertIsNone(c.diapositiva)
        self.assertEqual(c.seleccion, "")

    def test_verificar_apertura_rechaza_un_homonimo_y_ventana_ajena(self):
        self.enterContext(patch.object(office, "_abrir_office", return_value=self.w))
        enfocar = self.enterContext(patch.object(office.ventanas, "enfocar"))
        self.doc.FullName = str(self.otro)
        self.assertFalse(office.abrir_verificado(self.ruta, espera=0))
        self.doc.FullName = str(self.ruta)
        self.assertTrue(office.abrir_verificado(self.ruta, espera=0))
        self.frente.return_value = (222, "winword")
        self.assertFalse(office.abrir_verificado(self.ruta, espera=0))
        self.assertEqual(enfocar.call_args.args, (123,))
        self.assertFalse(office.abrir_verificado(self.ruta.with_suffix(".pptx"), espera=0))

    def test_si_esta_abierto_no_intenta_recargar_documento(self):
        app = MagicMock()
        actual = MagicMock(FullName=str(self.ruta))
        actual.Windows.Item.return_value = self.w
        app.Documents.__iter__.return_value = iter([SimpleNamespace(FullName=str(self.otro)), actual])
        with patch("win32com.client.Dispatch", return_value=app):
            self.assertIs(office._abrir_office(self.ruta, "word"), self.w)
        self.w.Activate.assert_called_once()
        app.Documents.Open.assert_not_called()

    def test_diapositiva_mdi_se_vincula_al_marco_de_su_documento(self):
        activa = SimpleNamespace(HWND=778)
        otra = SimpleNamespace(HWND=779)
        app = SimpleNamespace(ActiveWindow=activa, Windows=[activa, otra])
        with (patch.object(office, "_powerpoint", return_value=app),
              patch("win32gui.IsWindow", return_value=True),
              patch("win32gui.GetAncestor", side_effect=lambda hwnd, _: {778: 88, 779: 99}[hwnd])):
            self.assertIs(_RESOLVER_ORIGINAL(88, "powerpoint"), activa)
            self.assertIs(_RESOLVER_ORIGINAL(99, "powerpoint"), otra)
            with self.assertRaises(office.ErrorDocumento):
                _RESOLVER_ORIGINAL(100, "powerpoint")

    def test_hwnd_de_otra_instancia_se_resuelve_desde_el_panel_hijo(self):
        import win32con
        import pythoncom
        auxiliar = SimpleNamespace(ActiveWindow=SimpleNamespace(Hwnd=224))
        def hijos(hwnd, callback, extra):
            self.assertEqual(hwnd, 123)
            callback(55, extra)
        # Se ejercita el resolvedor real, sustituyendo únicamente los enlaces de Windows.
        with (patch("win32com.client.GetActiveObject", return_value=auxiliar),
              patch("win32gui.EnumChildWindows", side_effect=hijos),
              patch("win32gui.GetClassName", return_value="_WwG"),
              patch("win32gui.IsWindowVisible", return_value=True),
              patch("win32gui.SendMessageTimeout", return_value=(1, 78)) as mensaje,
              patch.object(pythoncom, "ObjectFromLresult", return_value=object()),
              patch("win32com.client.Dispatch", return_value=self.w)):
            self.assertIs(_RESOLVER_ORIGINAL(123, "word"), self.w)
            mensaje.assert_called_once_with(55, win32con.WM_GETOBJECT, 0, -16, win32con.SMTO_ABORTIFHUNG, 500)
