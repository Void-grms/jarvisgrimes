"""Lista editable de nombres de aplicaciones que Jarvis aprendió por voz."""
import sqlite3

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class PanelCorrecciones(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.correcciones = None
        self._filas = None
        contenido = QVBoxLayout(self)
        contenido.setContentsMargins(0, 0, 0, 0)
        instruccion = QLabel("Di «Cuando te pida abrir World, me refiero a Microsoft Word».")
        instruccion.setWordWrap(True)
        instruccion.setObjectName("detalle")
        contenido.addWidget(instruccion)
        fila = QHBoxLayout()
        self.lista = QComboBox()
        self.lista.setAccessibleName("Correcciones aprendidas al abrir aplicaciones")
        self.lista.setPlaceholderText("Aún no hay correcciones")
        self.quitar = QPushButton("Quitar corrección")
        self.quitar.setObjectName("accion")
        self.quitar.setEnabled(False)
        self.quitar.clicked.connect(self._borrar)
        fila.addWidget(self.lista, 1)
        fila.addWidget(self.quitar)
        contenido.addLayout(fila)
        self.aviso = QLabel()
        self.aviso.setObjectName("detalle")
        self.aviso.hide()
        contenido.addWidget(self.aviso)
        self.reloj = QTimer(self)
        self.reloj.setInterval(2500)
        self.reloj.timeout.connect(self.refrescar)

    def conectar(self, correcciones):
        self.correcciones = correcciones
        self._filas = None
        self.refrescar()

    def refrescar(self):
        if self.correcciones is None:
            return
        try:
            filas = self.correcciones.listar()
        except sqlite3.Error:
            self.aviso.setText("No pude consultar las correcciones.")
            self.aviso.show()
            self.quitar.setEnabled(False)
            return
        self.quitar.setEnabled(bool(filas))
        if filas == self._filas:
            return
        seleccion = self.lista.currentData()
        self._filas = filas
        self.lista.clear()
        for indice, c in enumerate(filas):
            self.lista.addItem(f"{c.alias} → {c.nombre}", c)
            if seleccion == c:
                self.lista.setCurrentIndex(indice)
        if filas and self.lista.currentIndex() < 0:
            self.lista.setCurrentIndex(0)
        self.aviso.hide()

    def _borrar(self):
        c = self.lista.currentData()
        if not c or self.correcciones is None:
            return
        try:
            existia = self.correcciones.borrar(c)
        except sqlite3.Error:
            self.aviso.setText("No pude quitar esa corrección.")
            self.aviso.show()
            return
        self.refrescar()
        self.aviso.setText(f"Quité la corrección de «{c.alias}»." if existia
                           else "La corrección cambió desde que la seleccionaste.")
        self.aviso.show()

    def showEvent(self, event):
        super().showEvent(event)
        self.refrescar()
        self.reloj.start()

    def hideEvent(self, event):
        self.reloj.stop()
        super().hideEvent(event)
