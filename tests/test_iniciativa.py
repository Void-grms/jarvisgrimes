import tempfile
import time
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from docx import Document

from asistente.config import DEFAULTS
from asistente.motor import Motor
from asistente.store import Item, Store


class Iniciativa(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cfg = deepcopy(DEFAULTS)
        cfg["documentos"]["carpeta"] = str(self.tmp / "Mis documentos")
        cfg["escucha"]["resumen_matutino"] = False
        self.store = Store(self.tmp / "prueba.sqlite")
        self.addCleanup(self.store._con.close)
        with patch("asistente.motor.Store", return_value=self.store):
            self.motor = Motor(cfg)
        self.motor.sync = Mock()
        self.ahora = datetime(2026, 4, 6, 9, 0, tzinfo=self.motor.router.tz)
        referencia = self.ahora.timestamp()

        class Reloj(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(referencia, tz)

        self.enterContext(patch("time.time", return_value=referencia))
        self.enterContext(patch("asistente.iniciativa.datetime", Reloj))
        self.enterContext(patch("asistente.router.datetime", Reloj))
        self.examen = Item("clementina", "e1", "examen", "Evaluación de estadística",
                           curso="Análisis de datos", detalle="Repasar las muestras.",
                           vence_ts=time.time() + 3600)
        self.entrega = Item("clementina", "t1", "tarea", "Informe de campo",
                            vence_ts=time.time() + 7200)
        self.store.reemplazar_fuente("clementina", [self.entrega, self.examen])

    def decir(self, frase):
        r = self.motor.entender(frase, forzar=True, por_voz=True)
        self.motor.ejecutar(r)
        self.motor.aplicar(r)
        return r

    def archivos(self):
        return list(self.tmp.rglob("*.docx"))

    def test_consulta_detalles_y_aceptacion_por_voz_crean_un_solo_word(self):
        r = self.decir("¿En qué avanzo?")
        self.assertIn("Evaluación de estadística", r.texto)
        self.assertIn("plan de estudio en Word", r.texto)
        self.assertEqual(self.archivos(), [])
        detalles = self.decir("Dame los detalles")
        self.assertIn("Análisis de datos", detalles.texto)
        self.assertIn("Repasar las muestras.", detalles.texto)
        self.assertEqual(self.archivos(), [])
        hecho = self.decir("Sí, prepáralo.")
        self.assertIn("Guardé", hecho.texto)
        self.motor.ejecutar(hecho)
        rutas = self.archivos()
        self.assertEqual(len(rutas), 1)
        parrafos = [p.text for p in Document(rutas[0]).paragraphs]
        self.assertIn("Plan de estudio — Evaluación de estadística", parrafos)
        self.assertIn("Práctica", parrafos)
        self.assertNotIn("Revisión y entrega", parrafos)

    def test_consulta_entregas_se_centra_en_tarea_y_rechazo_no_se_repite(self):
        r = self.decir("¿Qué entregas tengo?")
        self.assertIn("esquema de entrega en Word para «Informe de campo»", r.texto)
        self.decir("No, gracias.")
        misma = self.decir("Dime mis tareas pendientes")
        self.assertIsNone(misma.siguiente)
        self.assertEqual(self.archivos(), [])
        self.assertIsNotNone(self.decir("¿Qué hacemos ahora?").siguiente)

    def test_los_saludos_la_inactividad_y_otros_temas_no_inician_propuestas(self):
        self.assertNotIn("Word", self.motor.saludo(forzar=True).texto)
        self.assertIsNone(self.motor.entender("¿Qué tareas tengo?", por_voz=True))
        self.assertIsNone(self.decir("¿Qué clases tengo?").siguiente)
        self.assertIsNone(self.decir("¿Qué hora es?").siguiente)
        self.assertIsNone(self.store.get_meta("propuestas_agenda"))

    def test_descarta_cache_antigua_incluso_al_pedir_sugerencia_directa(self):
        self.store.set_meta("sync_clementina", str(time.time() - 86401))
        r = self.decir("¿Por dónde empiezo?")
        self.assertIn("actualizada", r.texto)
        self.assertIsNone(r.siguiente)
        self.assertIsNone(self.decir("¿Qué pendientes tengo?").siguiente)
        self.assertEqual(self.archivos(), [])

    def test_respeta_fecha_de_la_consulta_opcion_desactivada_y_vencimiento_lejano(self):
        ahora = self.ahora
        self.entrega.vence_ts = (ahora + timedelta(days=1)).replace(hour=10, minute=0).timestamp()
        self.store.reemplazar_fuente("clementina", [self.entrega])
        self.assertIsNone(self.decir("¿Qué entregas tengo para hoy?").siguiente)
        self.motor.esc["sugerir_pendientes"] = False
        self.assertIsNone(self.decir("¿Qué tareas tengo para mañana?").siguiente)
        self.motor.esc["sugerir_pendientes"] = True
        self.entrega.vence_ts = time.time() + 86400 * 4
        self.store.reemplazar_fuente("clementina", [self.entrega])
        self.assertIsNone(self.decir("¿Qué hacemos ahora?").siguiente)

    def test_cambio_de_tema_y_objecion_no_autorizan_el_documento(self):
        self.decir("¿Qué hacemos ahora?")
        self.decir("¿Qué hora es?")
        self.decir("Sí")
        self.assertEqual(self.archivos(), [])
        r = self.decir("¿En qué avanzo?")
        self.assertIsNone(r.siguiente("Sí, pero espera"))
        self.store.reemplazar_fuente("clementina", [])
        self.assertIn("cambió", self.decir("Sí").texto)
        self.assertEqual(self.archivos(), [])
