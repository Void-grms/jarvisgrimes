"""Preparar el contenido localmente, revisarlo y recoger el resultado de la app elegida."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton, QStackedLayout,
                               QVBoxLayout, QWidget)


def _texto(valor):
    etiqueta = QLabel(valor)
    etiqueta.setWordWrap(True)
    etiqueta.setTextFormat(Qt.PlainText)
    etiqueta.setObjectName("detalle")
    return etiqueta


def _boton(valor, funcion, principal=False):
    boton = QPushButton(valor)
    boton.setObjectName("guardar" if principal else "accion")
    boton.clicked.connect(funcion)
    return boton


class EditorEncargo(QWidget):
    terminado = Signal(bool, str)
    seleccion = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.rutas = []
        self.material = None
        self.prompt = ""
        self.enviando = False
        self.leyendo = False
        self.enviado = False
        self.ultimo_archivo = None
        self.respuesta_guardada = None
        self.paginas = QStackedLayout()
        self.aviso = _texto("")
        self.aviso.setAccessibleName("Estado del encargo")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.paginas)
        layout.addWidget(self.aviso)
        self._edicion()
        self._revision()
        self._resultado()
        self.terminado.connect(self._terminado, Qt.QueuedConnection)
        self.seleccion.connect(self._seleccion, Qt.QueuedConnection)

    def _pagina(self):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self.paginas.addWidget(pagina)
        return layout

    @staticmethod
    def _campo(layout, titulo, control):
        etiqueta = _texto(titulo)
        etiqueta.setObjectName("campo")
        etiqueta.setBuddy(control)
        control.setAccessibleName(titulo)
        layout.addWidget(etiqueta)
        layout.addWidget(control)

    def _edicion(self):
        layout = self._pagina()
        self.tipo = QComboBox()
        for titulo, clave in (("Informe de Word", "informe"), ("Revisión de un Word", "revision"),
                              ("Presentación de PowerPoint", "presentacion"), ("Otro encargo", "consulta")):
            self.tipo.addItem(titulo, clave)
        self._campo(layout, "Trabajo", self.tipo)
        self.app = QComboBox()
        self.app.addItems(["ChatGPT", "Claude"])
        self._campo(layout, "Aplicación de escritorio", self.app)
        self.pedido = QPlainTextEdit()
        self.pedido.setPlaceholderText("Por ejemplo, resume mis notas en un informe para el equipo docente.")
        self.pedido.setMaximumHeight(95)
        self._campo(layout, "Instrucciones", self.pedido)
        self.archivos = _texto("")
        self.archivos.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.archivos)
        self.elegir = _boton("Elegir archivos…", self._archivos)
        botones = QHBoxLayout()
        botones.addWidget(self.elegir)
        self.quitar = _boton("Quitar archivos", self._limpiar_fuentes)
        botones.addWidget(self.quitar)
        botones.addStretch()
        layout.addLayout(botones)
        self.tipo.currentIndexChanged.connect(self._cambiar_tipo)
        self._cambiar_tipo()
        layout.addWidget(_boton("Revisar encargo", self.revisar, True), alignment=Qt.AlignRight)

    def _revision(self):
        layout = self._pagina()
        self.cabecera = _texto("")
        self.cabecera.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.cabecera)
        self.vista = QPlainTextEdit()
        self.vista.setReadOnly(True)
        self.vista.setMinimumHeight(220)
        self._campo(layout, "Texto completo que recibirá la aplicación", self.vista)
        fila = QHBoxLayout()
        self.volver = _boton("Editar", self._volver)
        self.enviar = _boton("Autorizar y enviar", self._enviar, True)
        fila.addWidget(self.volver)
        fila.addStretch()
        fila.addWidget(self.enviar)
        layout.addLayout(fila)
        fila = QHBoxLayout()
        self.copiar = _boton("Copiar encargo", self._copiar)
        self.importar = _boton("Ya tengo la respuesta", self._abrir_resultado)
        fila.addWidget(self.copiar)
        fila.addWidget(self.importar)
        fila.addStretch()
        layout.addLayout(fila)

    def _resultado(self):
        layout = self._pagina()
        self.respuesta = QPlainTextEdit()
        self.respuesta.setPlaceholderText("Pega aquí la respuesta completa de la aplicación.")
        self.respuesta.setMinimumHeight(210)
        self._campo(layout, "Respuesta para el archivo", self.respuesta)
        self.tomar = _boton("Importar el texto seleccionado en la aplicación", self._leer_seleccion)
        layout.addWidget(self.tomar, alignment=Qt.AlignLeft)
        fila = QHBoxLayout()
        self.ver = _boton("Ver encargo", self._ver_revision)
        fila.addWidget(self.ver)
        fila.addStretch()
        self.crear_archivo = _boton("Guardar archivo nuevo…", self._guardar, True)
        fila.addWidget(self.crear_archivo)
        layout.addLayout(fila)
        self.abrir_archivo = _boton("Abrir archivo", self._abrir_archivo)
        self.abrir_archivo.hide()
        layout.addWidget(self.abrir_archivo, alignment=Qt.AlignRight)

    def _volver(self):
        self.paginas.setCurrentIndex(0)
        self.aviso.clear()

    def _ver_revision(self):
        self.paginas.setCurrentIndex(1)
        self.aviso.clear()

    def _abrir_resultado(self):
        self.paginas.setCurrentIndex(2)
        self.aviso.clear()

    def _cambiar_tipo(self):
        self.elegir.setVisible(self.tipo.currentData() != "consulta")
        self._limpiar_fuentes()

    def _limpiar_fuentes(self):
        self.rutas = []
        self._listar()

    def _listar(self):
        self.quitar.setVisible(bool(self.rutas))
        self.archivos.setText("Archivos de origen\n" + ("\n".join(map(str, self.rutas)) or "Ninguno"))

    def _archivos(self):
        if self.tipo.currentData() == "revision":
            ruta, _ = QFileDialog.getOpenFileName(self, "Elige el documento a revisar", "", "Word (*.docx)")
            rutas = [ruta] if ruta else []
        else:
            rutas, _ = QFileDialog.getOpenFileNames(self, "Elige las fuentes", "", "Notas y Word (*.md *.txt *.docx)")
        if rutas:
            self.rutas = [Path(r) for r in rutas]
            self._listar()

    def nuevo(self, app, pedido, tipo):
        if self.enviando or self.leyendo:
            self.aviso.setText("Espera a que termine la operación en la aplicación.")
            return
        if self.pedido.toPlainText().strip() or self.rutas or self.respuesta.toPlainText():
            pregunta = QMessageBox.question(self, "Cambiar de encargo",
                "¿Quieres reemplazar el encargo que tienes abierto?", QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No)
            if pregunta != QMessageBox.Yes:
                return
        self.tipo.setCurrentIndex(max(0, self.tipo.findData(tipo)))
        self.app.setCurrentIndex(max(0, self.app.findText(app, Qt.MatchFixedString)))
        self._cambiar_tipo()
        self.material, self.prompt = None, ""
        self._firma, self.enviado = None, False
        self.ultimo_archivo = None
        self.abrir_archivo.hide()
        self.respuesta.clear()
        self.pedido.setPlainText(pedido)
        self._volver()

    def revisar(self):
        from .materiales import preparar_material
        pedido, tipo = self.pedido.toPlainText().strip(), self.tipo.currentData()
        try:
            if not pedido:
                raise ValueError("Escribe qué quieres que haga la aplicación.")
            material = None if tipo == "consulta" else preparar_material(tipo, pedido, self.rutas)
        except (OSError, ValueError) as e:
            self.aviso.setText(str(e))
            return
        prompt = material.prompt if material else pedido
        firma = (self.app.currentText(), tuple(self.rutas), prompt)
        if firma != getattr(self, "_firma", None):
            self.enviado = False
            self.ultimo_archivo = None
            self.abrir_archivo.hide()
            self.respuesta.clear()
        self._firma = firma
        self._campos_revisados = (tipo, self.app.currentText(), pedido, tuple(self.rutas))
        self.material, self.prompt = material, prompt
        detalle = "\n".join(map(str, self.rutas)) or "Ninguno"
        self.cabecera.setText(f"Destino · {self.app.currentText()}\nArchivos de origen\n{detalle}")
        self.vista.setPlainText(prompt)
        self.enviar.setText(f"Autorizar y enviar a {self.app.currentText()}")
        self.enviar.setEnabled(not self.enviado)
        self.importar.setVisible(material is not None)
        self._ver_revision()

    def _copiar(self):
        QApplication.clipboard().setText(self.prompt)
        self.aviso.setText("Encargo copiado al portapapeles.")

    def _enviar(self):
        if self.enviando or self.enviado or self.paginas.currentIndex() != 1:
            return
        destino, _, prompt = self._firma
        self.enviando = True
        self.volver.setEnabled(False)
        self.enviar.setEnabled(False)
        self.importar.setEnabled(False)
        self.aviso.setText(f"Buscando el cuadro de mensaje de {destino}…")

        def trabajo():
            try:
                from .acciones.encargos import enviar_revisado
                ok, mensaje = enviar_revisado(destino, prompt)
            except Exception as e:
                ok, mensaje = False, f"No pude completar el envío. {e}"
            self.terminado.emit(ok, mensaje)
        threading.Thread(target=trabajo, name="encargo-revisado", daemon=True).start()

    @Slot(bool, str)
    def _terminado(self, ok, mensaje):
        self.enviando = False
        self.enviado = ok
        self.volver.setEnabled(True)
        self.enviar.setEnabled(not ok)
        self.importar.setEnabled(True)
        self.aviso.setText(mensaje)

    def _guardar(self):
        from .materiales import guardar_resultado
        respuesta = self.respuesta.toPlainText().strip()
        if not respuesta:
            self.aviso.setText("Pega la respuesta antes de guardar.")
            return
        destino, _ = QFileDialog.getSaveFileName(self, "Guardar como archivo nuevo",
            self.material.nombre_sugerido, f"Documento (*{self.material.extension})")
        if destino:
            try:
                ruta = guardar_resultado(self.material, respuesta, Path(destino))
            except (OSError, ValueError) as e:
                self.aviso.setText(str(e))
                return
            self.aviso.setText(f"Archivo guardado en {ruta}")
            self.ultimo_archivo = ruta
            self.respuesta_guardada = respuesta
            self.abrir_archivo.show()

    def tiene_trabajo_pendiente(self):
        campos = (self.tipo.currentData(), self.app.currentText(), self.pedido.toPlainText().strip(), tuple(self.rutas))
        if (campos[2] or self.rutas) and campos != getattr(self, "_campos_revisados", None):
            return True
        if self.material is None and self.enviado:
            return False
        if self.ultimo_archivo and self.respuesta.toPlainText().strip() == self.respuesta_guardada:
            return False
        return bool(self.pedido.toPlainText().strip() or self.rutas or self.respuesta.toPlainText().strip())

    def _abrir_archivo(self):
        if self.ultimo_archivo is not None:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ultimo_archivo))):
                self.aviso.setText(f"Windows no pudo abrir {self.ultimo_archivo}")

    def _leer_seleccion(self):
        if self.leyendo:
            return
        if self.respuesta.toPlainText().strip():
            if QMessageBox.question(self, "Reemplazar la respuesta",
                    "¿Quieres reemplazar este texto por la selección de la aplicación?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        destino = self.app.currentText()
        self.leyendo = True
        self.respuesta.setReadOnly(True)
        for boton in (self.ver, self.crear_archivo, self.tomar):
            boton.setEnabled(False)
        self.aviso.setText(f"Leyendo la selección de {destino}…")
        def leer():
            try:
                from .acciones.encargos import leer_seleccion
                ok, texto = leer_seleccion(destino)
            except Exception as e:
                ok, texto = False, f"No pude leer la selección. {e}"
            self.seleccion.emit(ok, texto)
        threading.Thread(target=leer, name="respuesta-seleccionada", daemon=True).start()

    @Slot(bool, str)
    def _seleccion(self, ok, texto):
        self.leyendo = False
        self.respuesta.setReadOnly(False)
        for boton in (self.ver, self.crear_archivo, self.tomar):
            boton.setEnabled(True)
        if ok:
            self.respuesta.setPlainText(texto)
        self.aviso.setText("Respuesta importada. Puedes revisarla antes de guardar." if ok else texto)
        if self.window().isVisible():
            self.window().raise_()
            self.window().activateWindow()
