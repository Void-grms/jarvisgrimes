"""Comparación guiada de lo dicho con el texto que entrega Whisper."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .ensayo_escucha import FRASES


class PanelEnsayo(QWidget):
    def __init__(self):
        super().__init__()
        self.estado = None
        self.ensayo = None
        self.muestra_activa = lambda: False
        self._vista = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.indicacion = QLabel()
        self.indicacion.setWordWrap(True)
        layout.addWidget(self.indicacion)
        self.resultado = QPlainTextEdit()
        self.resultado.setReadOnly(True)
        self.resultado.setAccessibleName("Resultados del ensayo de escucha")
        self.resultado.setMaximumHeight(180)
        layout.addWidget(self.resultado)
        controles = QHBoxLayout()
        self.inicio = QPushButton("Iniciar ensayo")
        self.saltar = QPushButton("No detectó mi voz")
        self.salida = QPushButton("Terminar ensayo")
        for boton in (self.inicio, self.saltar, self.salida):
            controles.addWidget(boton)
        controles.addStretch()
        layout.addLayout(controles)
        self.inicio.clicked.connect(self._iniciar)
        self.saltar.clicked.connect(self._saltar)
        self.salida.clicked.connect(self._detener)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.actualizar)
        self.actualizar()

    def conectar(self, estado, muestra_activa):
        self.estado = estado
        self.ensayo = getattr(estado, "ensayo_escucha", None)
        self.muestra_activa = muestra_activa
        self.actualizar()

    def _impedimento(self):
        if self.ensayo is None or self.estado.fallo:
            return "Inicia Jarvis y abre Configuración desde el widget o su icono en la bandeja para probar tu micrófono."
        if not self.estado.mic_activo:
            return "El micrófono está silenciado. Puedes activarlo en el widget cuando quieras hacer el ensayo."
        if self.muestra_activa() or self.estado.modo not in ("escuchando", "dormido"):
            return "Espera a que Jarvis termine y esté disponible para escuchar."
        return ""

    def _iniciar(self):
        if not self._impedimento():
            self.ensayo.iniciar()
        self.actualizar()

    def _saltar(self):
        if self.ensayo and self._vista:
            self.ensayo.registrar(self._vista[0], "", "sin captura", 0.0, 0.0)
        self.actualizar()

    def _detener(self):
        if self.ensayo:
            self.ensayo.detener()
        self.actualizar()

    def showEvent(self, evento):
        super().showEvent(evento)
        self.actualizar()
        self.timer.start()

    def hideEvent(self, evento):
        self.timer.stop()
        self._detener()
        super().hideEvent(evento)

    def actualizar(self):
        if self.ensayo and (self.estado.fallo or not self.estado.mic_activo):
            self.ensayo.detener("El ensayo se detuvo.")
        turno, lecturas, motivo = self.ensayo.vista() if self.ensayo else ((0, False), (), "")
        activo = turno[1]
        impedimento = "" if activo else self._impedimento()
        vista = (turno, lecturas, motivo, impedimento)
        if self._vista == vista:
            return
        self._vista = vista
        n = len(lecturas)
        total = len(FRASES)
        if activo and n < total:
            guia = f"Frase {n + 1} de {total}. Lee en voz alta «{FRASES[n]}»"
        elif activo:
            guia = "Has leído todas las frases. Pulsa Terminar ensayo para volver a dar órdenes."
        else:
            guia = impedimento or f"Lee {total} frases con tu voz habitual. Durante el ensayo Jarvis solo transcribe."
        self.indicacion.setText(" ".join(filter(None, (motivo, guia))))
        self.inicio.setVisible(not activo)
        self.inicio.setEnabled(not impedimento)
        self.saltar.setVisible(activo and n < total)
        self.salida.setVisible(activo)
        self.resultado.setVisible(bool(lecturas))
        if lecturas:
            aciertos = sum(fila.coincide for fila in lecturas)
            lineas = [f"{aciertos} de {n} coincidencias de texto · {n - aciertos} por revisar"]
            for indice in range(n - 1, -1, -1):
                fila = lecturas[indice]
                estado = "Coincide" if fila.coincide else {
                    "aceptada": "Texto distinto", "sin_voz": "Sin voz", "dudosa": "Dudosa",
                    "sin captura": "Sin captura", "error": "Error de reconocimiento",
                }.get(fila.estado, fila.estado)
                lectura = f"«{fila.texto}»" if fila.texto else "Sin transcripción"
                tiempo = f" · audio {fila.audio:.1f} s · reconocimiento {fila.demora:.2f} s" if fila.audio else ""
                lineas.extend(("", f"{indice + 1}. {fila.esperada}", f"{estado} · {lectura}{tiempo}"))
            self.resultado.setPlainText("\n".join(lineas))
