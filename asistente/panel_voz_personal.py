"""Controles del panel para escuchar dos lecturas y editar nombres propios."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .pronunciacion import validar_pronunciaciones


class ComparacionLecturas(QWidget):
    comparar = Signal()
    elegir = Signal(dict)
    claves = ("velocidad", "tono_semitonos", "pausa_ms", "pronunciaciones")

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.detalle = QLabel("A usa tus ajustes guardados. B usa los que estés editando o un ritmo alternativo.")
        self.detalle.setWordWrap(True)
        layout.addWidget(self.detalle)
        fila = QHBoxLayout()
        self.escuchar = QPushButton("Escuchar A y B")
        self.escuchar.clicked.connect(self.comparar.emit)
        self.usar_a = QPushButton("Usar A")
        self.usar_b = QPushButton("Usar B")
        self.usar_a.clicked.connect(lambda: self.elegir.emit(deepcopy(self.perfiles[0])))
        self.usar_b.clicked.connect(lambda: self.elegir.emit(deepcopy(self.perfiles[1])))
        for boton in (self.escuchar, self.usar_a, self.usar_b):
            boton.setObjectName("accion")
            fila.addWidget(boton)
        fila.addStretch()
        layout.addLayout(fila)
        self.olvidar()

    def olvidar(self, *_):
        self.perfiles = []
        self.pendiente = False
        self.usar_a.setEnabled(False)
        self.usar_b.setEnabled(False)

    def preparar(self, guardada, borrador):
        perfiles = [{k: deepcopy(voz[k]) for k in self.claves} for voz in (guardada, borrador)]
        if perfiles[0] == perfiles[1]:
            ritmo = perfiles[0]["velocidad"]
            delta = .15 if ritmo < 1.85 else -.15
            perfiles[1]["velocidad"] = round(ritmo + delta, 2)
        self.olvidar()
        self.perfiles = perfiles
        self.pendiente = True
        self.detalle.setText(f"A · {perfiles[0]['velocidad']:g} ×   /   B · {perfiles[1]['velocidad']:g} ×. "
                             "Escucharás A primero y B después de una pausa.")
        return deepcopy(perfiles)

    def reproduciendo(self, activa):
        self.escuchar.setEnabled(not activa)
        if activa:
            self.usar_a.setEnabled(False)
            self.usar_b.setEnabled(False)

    def resultado(self, error):
        if self.pendiente and error:
            self.olvidar()
        self.pendiente = False
        self.usar_a.setEnabled(bool(self.perfiles))
        self.usar_b.setEnabled(bool(self.perfiles))


class EditorPronunciaciones(QWidget):
    cambiado = Signal()

    def __init__(self):
        super().__init__()
        self._entradas = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.lista = QTableWidget(0, 2)
        self.lista.setHorizontalHeaderLabels(["Se escribe", "Se pronuncia"])
        self.lista.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.lista.verticalHeader().hide()
        self.lista.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lista.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lista.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.lista.setAccessibleName("Pronunciaciones personales")
        self.lista.setFixedHeight(105)
        layout.addWidget(self.lista)
        campos = QHBoxLayout()
        self.escrita = QLineEdit()
        self.hablada = QLineEdit()
        for campo, etiqueta in ((self.escrita, "Se escribe"), (self.hablada, "Se pronuncia")):
            campo.setAccessibleName(etiqueta)
            campo.setPlaceholderText(etiqueta)
            campo.setMaxLength(80)
            campos.addWidget(campo)
        layout.addLayout(campos)
        botones = QHBoxLayout()
        aplicar = QPushButton("Añadir o actualizar")
        aplicar.setObjectName("accion")
        aplicar.clicked.connect(self._aplicar)
        self.hablada.returnPressed.connect(self._aplicar)
        self.quitar = QPushButton("Quitar seleccionada")
        self.quitar.setObjectName("accion")
        self.quitar.setEnabled(False)
        self.quitar.clicked.connect(self._quitar)
        botones.addWidget(aplicar)
        botones.addWidget(self.quitar)
        botones.addStretch()
        layout.addLayout(botones)
        self.aviso = QLabel("Escribe la lectura como debería sonar y pulsa «Añadir o actualizar».")
        self.aviso.setWordWrap(True)
        layout.addWidget(self.aviso)
        self.lista.itemSelectionChanged.connect(self._seleccion)

    def value(self):
        return dict(self._entradas)

    def setValue(self, entradas):
        self._entradas = dict(entradas or {})
        self.lista.clearSelection()
        filas = sorted(self._entradas.items(), key=lambda par: par[0].casefold())
        self.lista.setRowCount(len(filas))
        for fila, valores in enumerate(filas):
            for columna, valor in enumerate(valores):
                self.lista.setItem(fila, columna, QTableWidgetItem(valor))
        self.quitar.setEnabled(False)
        self.escrita.clear()
        self.hablada.clear()

    def _seleccion(self):
        celdas = self.lista.selectedItems()
        self.quitar.setEnabled(bool(celdas))
        if celdas:
            fila = celdas[0].row()
            self.escrita.setText(self.lista.item(fila, 0).text())
            self.hablada.setText(self.lista.item(fila, 1).text())

    def _aplicar(self):
        try:
            nuevas = validar_pronunciaciones({self.escrita.text(): self.hablada.text()})
            escrita = next(iter(nuevas))
            anteriores = {k: v for k, v in self._entradas.items() if k.casefold() != escrita.casefold()}
            entradas = validar_pronunciaciones({**anteriores, **nuevas})
        except ValueError as e:
            self.aviso.setText(str(e))
            return
        self.setValue(entradas)
        self.aviso.setText(f"«{escrita}» está en la lista. Pulsa «Guardar cambios» para aplicarla.")
        self.cambiado.emit()

    def _quitar(self):
        celdas = self.lista.selectedItems()
        if not celdas:
            return
        escrita = self.lista.item(celdas[0].row(), 0).text()
        entradas = self.value()
        del entradas[escrita]
        self.setValue(entradas)
        self.aviso.setText(f"Quitaste «{escrita}» de la lista. Pulsa «Guardar cambios» para aplicarlo.")
        self.cambiado.emit()
