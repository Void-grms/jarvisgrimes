"""Pruebas del recorrido de voz sin micrófono, cuentas ni aplicaciones reales."""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from asistente.app import Asistente
from asistente.acciones.pestana_propia import Apertura
from asistente.actividad import ResultadoAccion, ResultadoDeshacer
from asistente.config import DEFAULTS
from asistente.motor import Motor
from asistente.respuesta import Respuesta
from asistente.store import Store
from asistente.stt import Transcripcion


class Turnos(unittest.TestCase):
    def setUp(self):
        carpeta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["documentos"]["carpeta"] = str(carpeta)
        self.cfg["escucha"]["resumen_matutino"] = False
        # Estas pruebas cubren el modo cauteloso (pregunta "¿Abro X?"); por defecto ya no pregunta.
        self.cfg["escucha"]["confirmar_aperturas"] = True
        self.enterContext(patch("asistente.motor.Store", side_effect=lambda: self.abrir_db(carpeta)))
        self.enterContext(patch("asistente.comandos.Recordatorios"))
        self.enterContext(patch("asistente.comandos.Comandos.iniciar"))
        self.enterContext(patch("asistente.comandos.sistema.Apps"))
        self.enterContext(patch("asistente.comandos.ventanas.listar", return_value=[]))
        self.enterContext(patch("asistente.comandos.ventanas.abrir_y_enfocar",
                               side_effect=lambda lanzar, *args, **kwargs: lanzar()))
        self.motor = Motor(self.cfg)
        self.cerebro = Mock()
        self.cerebro.frases.return_value = iter(["¿Puedes aclararlo?"])
        self.motor.preparar(self.cerebro)
        self.nav = self.motor.comandos.nav = Mock(wraps=self.motor.comandos.nav)
        self.nav.abrir = Mock()
        self.motor.comandos.apps.buscar.side_effect = lambda q: (
            {"Name": "Word", "AppID": "prueba"} if q.casefold() == "word" else None)
        self.motor.aplicar(Respuesta())

    def abrir_db(self, carpeta):
        store = Store(carpeta / "prueba.db")
        self.addCleanup(store._con.close)
        return store

    def voz(self, texto):
        return self.motor.entender(texto, por_voz=True)

    def test_recibo_conserva_la_orden_confirmada_y_deshace_solo_una_vez(self):
        cierre = Mock(return_value=ResultadoDeshacer("Cerré la pestaña de prueba."))
        self.nav.abrir.return_value = Apertura.para("https://www.youtube.com/", "chrome")
        with patch.object(Apertura, "identificar", return_value=ResultadoAccion("Pestaña verificada.", cierre)):
            preparar = self.voz("Abre YouTube.")
            self.motor.aplicar(preparar)
            self.assertIn("Todavía", self.motor.actividad.detalle())
            r = self.voz("Sí.")
            self.assertIn("Pendiente", self.motor.actividad.detalle())
            r.accion()
        detalle = self.voz("¿Por qué abriste eso?").texto
        self.assertIn("Abre YouTube.", detalle)
        self.assertIn("verificada", detalle)
        self.assertNotIn("Sí.", detalle)
        for _ in range(2):
            deshacer = self.voz("Deshazlo por favor.")
            self.motor.ejecutar(deshacer)
        cierre.assert_called_once_with()
        self.assertIn("no tiene", deshacer.texto)
        self.assertIn("Cerré", self.motor.actividad.detalle())

    def test_tres_frases_de_youtube_se_distinguen_sin_abrir_pestanas(self):
        self.motor.esc["confirmar_aperturas"] = False
        negacion = self.voz("Jarvis, no abras YouTube.")
        self.assertFalse(negacion.accion or negacion.ejecutar)
        with patch.object(self.motor.pantalla, "preparar", return_value=lambda: "Texto escrito.") as escritura:
            escribir = self.voz("Jarvis, escribe: abre YouTube.")
            self.motor.ejecutar(escribir)
            escritura.assert_called_once_with("escribir", "abre YouTube.", "")
        self.nav.abrir.assert_not_called()
        self.voz("Jarvis, abre YouTube.").accion()
        self.nav.abrir.assert_called_once_with("https://www.youtube.com/", None, False)
        self.assertIn("navegador", self.motor.actividad.detalle())
        deshacer = self.voz("Deshazlo.")
        self.motor.ejecutar(deshacer)
        self.assertIn("no tiene", deshacer.texto)

    def test_dictado_explicito_tiene_prioridad_sobre_preferencias_y_memorias(self):
        nombre = self.motor.nombre
        with patch.object(self.motor.pantalla, "preparar", return_value=lambda: "Texto escrito.") as escribir:
            for texto in ("Ya no me digas patrón, mi nombre es Ana", "recuerda que mi nombre es Ana"):
                self.motor.ejecutar(self.voz("Jarvis, escribe: " + texto))
                escribir.assert_called_with("escribir", texto, "")
        self.assertEqual(self.motor.nombre, nombre)

    def test_el_encargo_por_voz_termina_en_revision(self):
        revisar = self.motor.encargos.abrir_revision = Mock()
        r = self.voz("Usa Claude para crear un informe con mis notas.")
        revisar.assert_not_called()
        self.motor.ejecutar(r)
        revisar.assert_called_once()
        destino, instruccion = revisar.call_args.args
        self.assertEqual(destino, "Claude")
        self.assertIn("informe", instruccion)
        self.nav.abrir.assert_not_called()
        self.motor.comandos.apps.abrir.assert_not_called()

    def test_muletillas_del_registro_respetan_el_verbo(self):
        for frase, esperado in (
            ("No ver, quiero de qué abras el navegador.", "¿Abro el navegador?"),
            ("A ver, este abre de Word.", "¿Abro Word?"),
        ):
            r = self.voz(frase)
            self.assertEqual(r.texto, esperado)
        self.cerebro.interpretar.assert_not_called()

    def test_nota_con_otra_orden_espera_aclaracion(self):
        bloc = self.enterContext(patch("asistente.comandos.ventanas.abrir_bloc", return_value="Abrí el Bloc."))
        notas = self.motor.comandos.notas
        r = self.voz("Toma nota que falta el informe. Este abre el blog de notas.")
        self.assertEqual(notas.ultimas(), [])
        self.assertIsNotNone(r.siguiente)
        self.motor.aplicar(r)
        confirmada = self.voz("Sí.")
        self.motor.ejecutar(confirmada)
        self.assertEqual(notas.ultimas(), ["falta el informe."])
        bloc.assert_called_once()

    def test_interprete_no_cambia_abrir_por_crear(self):
        self.cerebro.interpretar.return_value = "hazme un documento de Word sobre historia"
        with patch.object(self.motor.comandos, "procesar") as comandos:
            r = self.motor._interpretar("abre Word por ahí")
            self.assertFalse(r.accion or r.ejecutar or r.siguiente)
            self.cerebro.interpretar.return_value = "abre Word"
            r = self.motor._interpretar("abre Word por ahí")
            comandos.assert_not_called()
            r.siguiente("sí")
            comandos.assert_called_once_with("abre Word")

    def test_audio_atrasado_no_se_transcribe_ni_ejecuta(self):
        with patch("asistente.app.Motor", return_value=self.motor):
            app = Asistente()
        app.stt = Mock()
        app.responder = Mock()
        app._procesar_audio(np.zeros(16000, dtype=np.float32), time.perf_counter() - 80)
        app.stt.reconocer.assert_not_called()
        app.responder.assert_not_called()

    def test_una_transcripcion_no_abre_youtube(self):
        r = self.voz("Abre YouTube.")
        self.assertEqual(r.texto, "¿Abro YouTube?")
        self.assertIsNone(r.accion)
        self.motor.aplicar(r)
        si = self.voz("Sí, por favor.")
        self.assertIsNone(si.siguiente)
        self.nav.abrir.assert_not_called()
        si.accion()
        self.nav.abrir.assert_called_once()
        self.assertIsNone(self.voz("Sí").accion)

    def test_negativa_y_cambio_de_pedido_descartan_apertura(self):
        for cambio in ("No.", "Sí, pero no lo hagas.", "¿Qué hora es?"):
            with self.subTest(cambio=cambio):
                self.motor.aplicar(self.voz("Abre YouTube"))
                self.assertIsNone(self.voz(cambio).accion)
                self.assertIsNone(self.voz("Sí").accion)
        self.nav.abrir.assert_not_called()

    def test_apertura_con_clave_o_teclado_es_directa(self):
        for r in (self.voz("Jarvis, abre Word"), self.motor.entender("Abre Word", forzar=True)):
            self.assertIsNotNone(r.ejecutar)
            self.assertIsNone(r.siguiente)
        self.assertFalse(self.motor.quitar_clave("Mi amigo dice Jarvis todo el día")[0])

    def test_nombre_del_registro_sobrevive_reinicio(self):
        r = self.voz("Yo digo a de que hay que cambiar el nombre porque me está diciendo Patrick, "
                     "quiero que me digas Fernando.")
        self.assertEqual(r.texto, "Te llamaré Fernando.")
        self.cerebro.cambiar_nombre.assert_called_once_with("Fernando")
        otro = Motor(self.cfg)
        self.assertEqual(otro.entender("Jarvis, ¿cómo me llamo?").texto, "Te llamas Fernando.")

    def test_aclarar_nombre_sin_extraerlo_del_ruido(self):
        r = self.voz("Abre, para empezar, cambié el nombre. Ya no me digas patrón y siguen afrenando.")
        self.assertIsNotNone(r.siguiente)
        self.assertIsNone(r.accion)
        self.motor.aplicar(r)
        self.assertEqual(self.voz("María José").texto, "Te llamaré María José.")

    def test_confirmacion_caduca_aunque_transcribir_tarde(self):
        self.motor.aplicar(self.voz("Abre YouTube."))
        self.motor.pendiente.hasta = time.time() - 1
        self.assertIsNone(self.voz("Sí").accion)

    def test_confirmar_escritura_y_repetir_solo_su_respuesta(self):
        operacion = Mock(return_value="Añadí el texto al campo.")
        self.motor.pantalla = Mock()
        self.motor.pantalla.preparar.return_value = operacion
        r = self.voz("Escribe aquí ¡Mañana!")
        self.assertTrue(r.siguiente)
        self.motor.aplicar(r)
        self.motor.pantalla.preparar.assert_called_once_with("escribir", "¡Mañana!", "")
        operacion.assert_not_called()
        si = self.voz("Sí.")
        self.motor.ejecutar(si)
        self.motor.ejecutar(si)
        repetir = self.voz("Repite.")
        self.motor.ejecutar(repetir)
        self.assertEqual(repetir.texto, "Añadí el texto al campo.")
        operacion.assert_called_once()

    def test_preguntar_por_un_control_ausente_es_inmediato(self):
        self.motor.pantalla = Mock()
        self.motor.pantalla.preparar.return_value = "No encontré ese botón."
        r = self.voz("Haz clic en Revisar")
        self.assertEqual(r.texto, "No encontré ese botón.")
        self.assertIsNone(r.siguiente)
        self.assertIsNone(r.ejecutar)

    def test_dictado_literal_conserva_puntuacion_con_clave(self):
        self.motor.pantalla = Mock()
        self.voz("Jarvis, escribe aquí ¿Cómo estás?")
        self.motor.pantalla.preparar.assert_called_once_with("escribir", "¿Cómo estás?", "")

    def test_saludo_en_silencio_se_duerme_sin_insistir(self):
        self.motor.sync = Mock()
        r = self.motor.saludo(forzar=True)
        self.motor.aplicar(r)
        self.motor.pendiente.hasta = time.time() - 1
        fin = self.motor.expirado()
        self.assertTrue(fin.dormir)
        self.assertFalse(fin.texto)
        self.motor.aplicar(fin)
        self.assertIsNone(self.voz("Abre Word"))

    def test_audio_dudoso_silencio_eco_y_audio_previo_no_ejecutan(self):
        with patch("asistente.app.Motor", return_value=self.motor):
            app = Asistente()
        app.stt = Mock()
        app.decir = Mock()
        app.responder = Mock()
        audio = np.zeros(16000, dtype=np.float32)
        fin = time.perf_counter()
        self.motor.aplicar(self.voz("Abre YouTube"))
        app._procesar_audio(audio, fin - 2)
        app.stt.reconocer.assert_not_called()
        for estado in ("dudosa", "dudosa", "sin_voz"):
            app.stt.reconocer.return_value = Transcripcion(estado)
            app._procesar_audio(audio, fin + 2)
        app.decir.assert_called_once()
        app._inicio_voz, app._fin_voz = fin, fin + 2
        app._dicho = ["También entendí abre YouTube."]
        app.stt.reconocer.return_value = Transcripcion("aceptada", "Abre YouTube")
        app._procesar_audio(audio, fin + 2)
        app.responder.assert_not_called()
        self.assertIsNotNone(self.motor.pendiente)

    def test_interrumpir_pregunta_descarta_la_accion(self):
        with patch("asistente.app.Motor", return_value=self.motor):
            app = Asistente()
        app.decir = Mock(return_value=False)
        app.responder(self.voz("Abre YouTube"))
        self.assertIsNone(self.motor.pendiente)
        self.assertIsNone(self.voz("Sí").accion)

    def test_espera_al_hablar_pausa_el_archivo_y_actualiza_el_panel(self):
        estado = Mock(mic_activo=True)
        with patch("asistente.app.Motor", return_value=self.motor):
            app = Asistente(estado)
        app.stt = Mock()
        self.motor.trabajo.cancelar = Mock(return_value=True)
        app.stt.reconocer.return_value = Transcripcion("aceptada", "Espera")
        fin = time.perf_counter()
        app._interrumpido = fin - .2
        app._procesar_audio(np.zeros(16000, dtype=np.float32), fin)
        self.motor.trabajo.cancelar.assert_called_once_with(pausar=True)
        estado.noticia_trabajo.assert_called_once_with("Trabajo en pausa.")
        self.motor.trabajo.avisar("Copia terminada.")
        estado.noticia_trabajo.assert_called_with("Copia terminada.")
        self.assertEqual(self.motor.avisos.get_nowait().texto, "Copia terminada.")
