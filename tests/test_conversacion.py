import json
import unittest
from unittest.mock import Mock, patch

from asistente.brain import Cerebro


def respuesta_ollama(*trozos):
    lineas = [json.dumps({"message": {"content": trozo}}).encode() for trozo in trozos]
    lineas.append(b'{"done": true, "eval_count": 12}')
    return Mock(status_code=200, iter_lines=Mock(return_value=iter(lineas)))


class ConversacionTest(unittest.TestCase):
    def setUp(self):
        self.cerebro = Cerebro({
            "url": "http://localhost", "modelo": "prueba", "num_ctx": 1024,
            "max_tokens": 160, "temperatura": 0, "turnos_memoria": 6,
        }, nombre="Patrick")

    @patch("asistente.brain.requests.post")
    def test_dos_oraciones_sin_eco_y_solo_lo_dicho_en_memoria(self, post):
        respuesta = respuesta_ollama("En Lima. ", "¡En Lima! ", "Queda en la costa. ",
                                     "En Lima, en efecto.")
        post.return_value = respuesta
        frases = list(self.cerebro.frases("¿Dónde?", "hora anterior"))
        self.assertEqual(frases, ["En Lima.", "Queda en la costa."])
        self.assertEqual([m["content"] for m in self.cerebro.historial],
                         ["¿Dónde?", "En Lima. Queda en la costa."])
        self.assertEqual(next(respuesta.iter_lines.return_value),
                         json.dumps({"message": {"content": "En Lima, en efecto."}}).encode())
        respuesta.close.assert_called_once_with()

    @patch("asistente.brain.requests.post")
    def test_respuesta_de_una_frase_mide_el_primer_audio(self, post):
        post.return_value = respuesta_ollama("Son ", "cuatro.")
        self.assertEqual(list(self.cerebro.frases("¿Dos más dos?", "")), ["Son cuatro."])
        self.assertIsNotNone(self.cerebro.medicion["primera"])
        self.assertEqual(self.cerebro.medicion["tokens"], 12)

    @patch("asistente.brain.requests.post")
    def test_omite_ofrecimiento_de_ayuda_tras_responder(self, post):
        post.return_value = respuesta_ollama("Te llamas Fernando. ¿En qué puedo ayudarte hoy?")
        self.assertEqual(list(self.cerebro.frases("¿Cómo me llamo?", "")), ["Te llamas Fernando."])

    @patch("asistente.brain.requests.post")
    def test_filtra_razonamiento_y_respeta_abreviatura_y_decimal(self, post):
        post.return_value = respuesta_ollama(
            "<thi", "nk>Debo pensarlo. ", "Otro cálculo.</th", "ink>",
            "La Dra. ", "Lozano calculó 2.5 litros. ", "¿Te sirve?")
        frases = list(self.cerebro.frases("¿Cuánto calculó?", ""))
        self.assertEqual(frases, ["La Dra. Lozano calculó 2.5 litros.", "¿Te sirve?"])

    @patch("asistente.brain.requests.post")
    def test_cancelar_libera_el_stream(self, post):
        respuesta = respuesta_ollama("Primera respuesta. ", "Segunda.")
        post.return_value = respuesta
        frases = self.cerebro.frases("Dime algo", "")
        self.assertEqual(next(frases), "Primera respuesta.")
        frases.close()
        respuesta.close.assert_called_once_with()
        self.assertEqual(self.cerebro.historial, [])

    @patch("asistente.brain.requests.post")
    def test_cambio_de_nombre_descarta_incluso_un_turno_anterior_en_curso(self, post):
        post.side_effect = [respuesta_ollama("Hola, Patrick. ", "Aquí estoy."),
                            respuesta_ollama("Fernando.")]
        anterior = self.cerebro.frases("Hola", "")
        next(anterior)
        self.cerebro.cambiar_nombre("Fernando")
        list(anterior)
        self.assertEqual(self.cerebro.historial, [])
        self.assertEqual(list(self.cerebro.frases("¿Cómo me llamo?", "")), ["Fernando."])
        mensajes = post.call_args.kwargs["json"]["messages"]
        self.assertIn("Fernando", mensajes[0]["content"])
        self.assertNotIn("Patrick", str(mensajes))

    @patch("asistente.brain.requests.get")
    @patch("asistente.brain.requests.post")
    def test_precalentar_no_borra_una_conversacion(self, post, get):
        self.cerebro.historial = [{"role": "user", "content": "Pregunta previa"},
                                  {"role": "assistant", "content": "Respuesta previa"}]
        post.side_effect = [Mock(), respuesta_ollama("Hola.")]
        self.cerebro.precalentar()
        self.assertNotIn("Pregunta previa", str(post.call_args.kwargs["json"]["messages"]))
        self.assertEqual(self.cerebro.historial[-1]["content"], "Respuesta previa")

    @patch("asistente.brain.requests.post")
    def test_cero_turnos_no_conserva_las_preguntas(self, post):
        self.cerebro.turnos = 0
        post.return_value = respuesta_ollama("Una respuesta.")
        list(self.cerebro.frases("Hola", ""))
        self.assertEqual(self.cerebro.historial, [])


if __name__ == "__main__":
    unittest.main()
