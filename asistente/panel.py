"""Panel de configuración de Jarvis. El controlador se encarga del disco y del audio."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QLocale, QPointF, QRectF, QSize, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QColor, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QPlainTextEdit, QFileDialog,
    QSizePolicy, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .config import DEFAULTS

TEXTO_MUESTRA = "Hoy publico el informe. Ayer se publicó y mañana lo verá el público."
_ESTILO = """
QWidget#panel { background: #f6f8f6; color: #172c2c; }
QWidget#panel QWidget { font-size: 14px; }
QWidget#panel QLabel, QWidget#panel QCheckBox { color: #172c2c; }
QWidget#panel QLabel#detalle { color: #526662; }
QFrame#barra { background: #152e30; }
QFrame#barra QLabel { color: #f3f8f6; }
QWidget#panel QFrame#barra QLabel#marca { font-size: 22px; font-weight: 650; }
QFrame#barra QLabel#descripcionMarca { color: #afc6bf; }
QFrame#barra QLabel#simbolo { background: #316355; border-radius: 9px; }
QPushButton#navegar { background: transparent; border: 1px solid transparent; color: #d8e5df;
    border-radius: 7px; padding: 9px 10px; text-align: left; }
QPushButton#navegar:hover { background: #213e40; }
QPushButton#navegar:checked { background: #30534e; color: white; font-weight: 600; }
QPushButton#navegar:focus { border-color: #98d3b8; }
QWidget#panel QLabel#titulo { font-size: 29px; font-weight: 650; }
QWidget#panel QLabel#seccion { font-size: 16px; font-weight: 650; }
QWidget#panel QLabel#campo { font-size: 14px; font-weight: 550; }
QFrame#tarjeta, QFrame#prueba { background: #ffffff; border: 1px solid #d8e2dc; border-radius: 12px; }
QFrame#prueba { background: #e8f3ec; border-color: #c5dccd; }
QFrame#linea { background: #e2e9e3; max-height: 1px; }
QFrame#pie { background: #ffffff; border-top: 1px solid #d8e2dc; }
QScrollArea, QStackedWidget, QWidget#pagina, QWidget#contenido { background: transparent; border: 0; }
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #ffffff; color: #1a302b;
    border: 1px solid #baccc1; border-radius: 5px; padding: 5px 7px; min-height: 20px; }
QComboBox { padding-right: 16px; }
QComboBox QAbstractItemView { background: white; color: #17342c; selection-background-color: #d6ecdf;
    selection-color: #17342c; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #287252; }
QPushButton#accion, QPushButton#guardar { background: #edf3ef; color: #174734;
    border: 1px solid #b8cfc2; border-radius: 6px; padding: 7px 13px; font-weight: 600; }
QPushButton#accion:hover { background: #dcebe1; }
QPushButton#guardar { background: #246248; color: #ffffff; border-color: #246248; }
QPushButton#guardar:hover { background: #174b35; }
QPushButton#guardar:disabled, QPushButton#accion:disabled { background: #e6ece8; color: #596c62; border-color: #d4ded8; }
QPushButton#accion:focus, QPushButton#guardar:focus { border: 2px solid #3c906c; padding: 6px 12px; }
QCheckBox { spacing: 9px; padding: 4px 0; }
QSlider { background: transparent; min-height: 22px; }
QSlider::groove:horizontal { height: 5px; background: #d9e4dc; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #468261; border-radius: 2px; }
QSlider::add-page:horizontal { background: #d9e4dc; border-radius: 2px; }
QSlider::handle:horizontal { width: 15px; margin: -5px 0; border-radius: 7px; background: #27694e; }
QSlider:focus::handle:horizontal { background: #19482f; }
QWidget#panel QLabel#estado { color: #2f614e; }
QWidget#panel QLabel#error { color: #99352d; }
"""


def _etiqueta(texto, nombre="detalle"):
    label = QLabel(texto)
    label.setObjectName(nombre)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


def _icono(tipo, color):
    """Iconos de navegación legibles también a escalas altas de Windows."""
    pix = QPixmap(48, 48)
    pix.fill(Qt.transparent)
    pix.setDevicePixelRatio(2)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if tipo == "voz":
        for x, y in [(3, 10), (7, 6), (11, 3), (15, 7), (19, 9)]:
            p.drawLine(QPointF(x, y), QPointF(x, 24 - y))
    elif tipo == "widget":
        p.drawRoundedRect(2, 4, 20, 15, 3, 3)
        p.drawEllipse(13, 10, 6, 6)
    elif tipo == "escucha":
        p.drawRoundedRect(9, 2, 6, 13, 3, 3)
        p.drawArc(5, 6, 14, 12, 180 * 16, 180 * 16)
        p.drawLine(12, 18, 12, 21)
        p.drawLine(9, 21, 15, 21)
    elif tipo == "sistema":
        for x in (3, 13):
            for y in (3, 13):
                p.drawRoundedRect(x, y, 7, 7, 1.5, 1.5)
    elif tipo in {"encargos", "proyectos"}:
        p.drawRoundedRect(5, 2, 14, 19, 2, 2)
        for y in (7, 11, 15):
            p.drawLine(8, y, 16, y)
    elif tipo == "play":
        camino = QPainterPath(QPointF(7, 4))
        camino.lineTo(19, 12)
        camino.lineTo(7, 20)
        camino.closeSubpath()
        p.fillPath(camino, QColor(color))
    p.end()
    return QIcon(pix)


class _VistaWidget(QWidget):
    def __init__(self):
        super().__init__()
        from .interfaz import Estado, crear_orbe
        self.setFixedHeight(184)
        self.setAccessibleName("Vista previa del widget a la mitad de su tamaño")
        self.estado = Estado()
        self.estado.modo = "oyendo"
        self.estado.nivel = 0.25
        self.estado.decir_usuario("Jarvis, ¿qué hora es?")
        self.orbe = crear_orbe(self.estado, padre_muestra=self)
        self.orbe.hide()
        self.imagen = QPixmap()

    def mostrar(self, tamano, subtitulos):
        self.estado.decir_jarvis("Son las diez.")
        self.orbe.ajustar(tamano, subtitulos, inicial=True)
        self.imagen = self.orbe.grab()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        fondo = QLinearGradient(0, self.height(), self.width(), 0)
        fondo.setColorAt(0, QColor("#45625e"))
        fondo.setColorAt(0.6, QColor("#2f4b4b"))
        fondo.setColorAt(1, QColor("#20393d"))
        p.setPen(Qt.NoPen)
        p.setBrush(fondo)
        p.drawRoundedRect(QRectF(self.rect()), 7, 7)
        ancho, alto = self.orbe.width() / 2, self.orbe.height() / 2
        if not self.imagen.isNull():
            destino = QRectF((self.width() - ancho) / 2, (self.height() - alto) / 2, ancho, alto)
            p.drawPixmap(destino, self.imagen, QRectF(self.imagen.rect()))
        p.end()


class Panel(QWidget):
    guardar = Signal(dict)
    probar_voz = Signal(dict)
    actualizar = Signal()
    editar_avanzados = Signal()
    consultar_proyecto = Signal()
    iniciar_trabajo = Signal(str, str, int, int)
    elegir_proyecto = Signal(str)
    pausar_trabajo = Signal()

    def __init__(self, cfg, estado_servicios, parent=None):
        super().__init__(parent, Qt.Window)
        self.residente = False
        self.setObjectName("panel")
        self.setWindowTitle("Jarvis · Configuración")
        self.setWindowIcon(_icono("voz", "#317255"))
        self.setLocale(QLocale(QLocale.Spanish, QLocale.Peru))
        self.setStyleSheet(_ESTILO)
        self.campos = {}
        self._base = {}
        self._voz = {}
        self._leyendo = True
        self._botones = QButtonGroup(self)
        self._botones.setExclusive(True)
        self.paginas = QStackedWidget()

        principal = QHBoxLayout(self)
        principal.setContentsMargins(0, 0, 0, 0)
        principal.setSpacing(0)
        principal.addWidget(self._barra())
        columna = QVBoxLayout()
        columna.setSpacing(0)
        columna.addWidget(self.paginas, 1)
        columna.addWidget(self._pie())
        principal.addLayout(columna, 1)

        self._pagina_voz()
        self._pagina_widget()
        self._pagina_escucha()
        self._pagina_sistema()
        from .panel_encargos import EditorEncargo
        pagina = self._pagina("Encargos", "Trabaja con ChatGPT o Claude y guarda el resultado en tu equipo.")
        self.encargo = EditorEncargo()
        self._tarjeta(pagina).addWidget(self.encargo)
        self._pagina_proyectos()
        self._botones.idClicked.connect(self.paginas.setCurrentIndex)
        self._botones.button(5).clicked.connect(self.consultar_proyecto.emit)
        self._botones.button(0).setChecked(True)
        self._atajo_guardar = QShortcut(QKeySequence.Save, self)
        self._atajo_guardar.activated.connect(self._guardar)
        self.confirmar_guardado(cfg, "")
        self.actualizar_servicios(estado_servicios)
        self.setMinimumSize(640, 440)
        screen = self.screen()
        disponible = screen.availableGeometry().size() if screen else QSize(960, 780)
        self.resize(min(940, disponible.width() - 26), min(740, disponible.height() - 56))

    def _barra(self):
        barra = QFrame()
        barra.setObjectName("barra")
        barra.setFixedWidth(172)
        interior = QVBoxLayout(barra)
        interior.setContentsMargins(13, 25, 13, 18)
        interior.setSpacing(5)
        marca = QHBoxLayout()
        marca.setSpacing(9)
        icono = QLabel()
        icono.setObjectName("simbolo")
        icono.setFixedSize(35, 35)
        icono.setAlignment(Qt.AlignCenter)
        icono.setPixmap(_icono("voz", "#e4f9e5").pixmap(23, 23))
        marca.addWidget(icono)
        marca.addWidget(_etiqueta("Jarvis", "marca"))
        marca.addStretch()
        interior.addLayout(marca)
        interior.addWidget(_etiqueta("Configuración", "descripcionMarca"))
        interior.addSpacing(24)
        for i, (clave, nombre) in enumerate([("voz", "Voz"), ("widget", "Widget"),
                                              ("escucha", "Escucha"), ("sistema", "Sistema"),
                                              ("encargos", "Encargos"), ("proyectos", "Proyectos")]):
            boton = QPushButton(_icono(clave, "#d3eadb"), nombre)
            boton.setObjectName("navegar")
            boton.setCheckable(True)
            boton.setIconSize(QSize(18, 18))
            boton.setCursor(Qt.PointingHandCursor)
            self._botones.addButton(boton, i)
            interior.addWidget(boton)
        interior.addStretch()
        return barra

    def abrir_encargo(self, app, pedido, tipo):
        self._botones.button(4).click()
        self.encargo.nuevo(app, pedido, tipo)

    def _pagina_proyectos(self):
        pagina = self._pagina("Proyectos", "Consulta la versión de trabajo, las decisiones y los cambios de tus documentos.")
        proyecto = self._tarjeta(pagina, "Proyecto de trabajo")
        fila = QHBoxLayout()
        self.nombre_proyecto = QLineEdit()
        self.nombre_proyecto.setAccessibleName("Nombre del proyecto")
        self.nombre_proyecto.setPlaceholderText("Nombre del proyecto")
        self.nombre_proyecto.setMaxLength(100)
        cargar_proyecto = QPushButton("Usar proyecto")
        cargar_proyecto.setObjectName("accion")
        cargar_proyecto.clicked.connect(lambda: self.elegir_proyecto.emit(self.nombre_proyecto.text()))
        fila.addWidget(self.nombre_proyecto, 1)
        fila.addWidget(cargar_proyecto)
        proyecto.addLayout(fila)
        self.proyecto_actual = _etiqueta("", "campo")
        proyecto.addWidget(self.proyecto_actual)
        self.detalle_proyecto = QPlainTextEdit()
        self.detalle_proyecto.setAccessibleName("Archivo, cambios y apuntes")
        self.detalle_proyecto.setReadOnly(True)
        self.detalle_proyecto.setMinimumHeight(155)
        proyecto.addWidget(self.detalle_proyecto)
        opciones = self._tarjeta(pagina, "Trabajar con un Word")
        self.tipo_trabajo = QComboBox()
        self.tipo_trabajo.addItem("Resumir en una copia y conservar tablas", "resumen")
        self.tipo_trabajo.addItem("Preparar una exposición", "presentacion")
        opciones.addWidget(self.tipo_trabajo)
        self.cantidad_diapositivas = QSpinBox()
        self.cantidad_diapositivas.setRange(2, 20)
        self.cantidad_diapositivas.setValue(6)
        self.minutos_exposicion = QSpinBox()
        self.minutos_exposicion.setRange(0, 180)
        self.minutos_exposicion.setSpecialValueText("Sin indicar")
        self.minutos_exposicion.setSuffix(" min")
        self._fila(opciones, "Diapositivas con portada", self.cantidad_diapositivas)
        self._fila(opciones, "Duración de la exposición", self.minutos_exposicion)
        for control in (self.cantidad_diapositivas, self.minutos_exposicion):
            control.setEnabled(False)
            self.tipo_trabajo.currentIndexChanged.connect(control.setEnabled)
        comandos = QHBoxLayout()
        iniciar = QPushButton("Elegir Word y empezar…")
        iniciar.setObjectName("guardar")
        iniciar.clicked.connect(self._seleccionar_word)
        parar = QPushButton("Pausar o reanudar")
        parar.setObjectName("accion")
        parar.clicked.connect(self.pausar_trabajo.emit)
        comandos.addWidget(iniciar)
        comandos.addWidget(parar)
        opciones.addLayout(comandos)
        self.estado_trabajo = _etiqueta("")
        opciones.addWidget(self.estado_trabajo)

    def _seleccionar_word(self):
        archivo, _ = QFileDialog.getOpenFileName(self, "Elige el Word guardado", "", "Documento de Word (*.docx)")
        if archivo:
            self.iniciar_trabajo.emit(archivo, self.tipo_trabajo.currentData(),
                                      self.cantidad_diapositivas.value(), self.minutos_exposicion.value())

    def ver_proyecto(self, ficha, enfocar=False):
        from .trabajo import detalle_ficha
        if enfocar:
            self._botones.button(5).setChecked(True)
            self.paginas.setCurrentIndex(5)
        self.proyecto_actual.setText(ficha["nombre"] if ficha else "Aún no hay un proyecto activo")
        self.detalle_proyecto.setPlainText(detalle_ficha(ficha).partition("\n")[2] if ficha else "")

    def _pie(self):
        pie = QFrame()
        pie.setObjectName("pie")
        fila = QHBoxLayout(pie)
        fila.setContentsMargins(23, 11, 23, 11)
        self.aviso = _etiqueta("", "estado")
        fila.addWidget(self.aviso, 1)
        self.boton_guardar = QPushButton("Guardar cambios")
        self.boton_guardar.setObjectName("guardar")
        self.boton_guardar.setToolTip("Guardar cambios (Ctrl+S)")
        self.boton_guardar.clicked.connect(self._guardar)
        fila.addWidget(self.boton_guardar)
        return pie

    def _pagina(self, titulo, detalle):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        pagina = QWidget()
        pagina.setObjectName("pagina")
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)
        layout.addWidget(_etiqueta(titulo, "titulo"))
        layout.addWidget(_etiqueta(detalle))
        layout.addSpacing(4)
        area.setWidget(pagina)
        self.paginas.addWidget(area)
        return layout

    @staticmethod
    def _tarjeta(layout, titulo="", prueba=False):
        tarjeta = QFrame()
        tarjeta.setObjectName("prueba" if prueba else "tarjeta")
        interior = QVBoxLayout(tarjeta)
        interior.setContentsMargins(15, 12, 15, 12)
        interior.setSpacing(7)
        if titulo:
            interior.addWidget(_etiqueta(titulo, "seccion"))
        layout.addWidget(tarjeta)
        return interior

    @staticmethod
    def _linea(layout):
        linea = QFrame()
        linea.setObjectName("linea")
        linea.setFixedHeight(1)
        layout.addWidget(linea)

    def _registro(self, clave, control, señal):
        control.setObjectName(clave)
        self.campos[clave] = control
        señal.connect(self._edicion)
        return control

    @staticmethod
    def _fila(layout, titulo, control, detalle=""):
        fila = QHBoxLayout()
        fila.setSpacing(12)
        columna = QVBoxLayout()
        columna.setSpacing(2)
        nombre = _etiqueta(titulo, "campo")
        nombre.setBuddy(control)
        columna.addWidget(nombre)
        control.setAccessibleName(titulo)
        if detalle:
            columna.addWidget(_etiqueta(detalle))
            control.setAccessibleDescription(detalle)
        fila.addLayout(columna, 1)
        fila.addWidget(control)
        layout.addLayout(fila)

    def _numero(self, clave, minimo, maximo, paso, unidad="", decimales=0):
        control = QDoubleSpinBox() if decimales else QSpinBox()
        if decimales:
            control.setDecimals(decimales)
        control.setRange(minimo, maximo)
        control.setSingleStep(paso)
        control.setSuffix(unidad)
        control.setFixedWidth(118)
        return self._registro(clave, control, control.valueChanged)

    def _check(self, layout, clave, etiqueta):
        control = QCheckBox(etiqueta)
        self._registro(clave, control, control.toggled)
        layout.addWidget(control)

    def _selector(self, layout, clave, etiqueta):
        combo = QComboBox()
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        combo.setAccessibleName(etiqueta)
        nombre = _etiqueta(etiqueta, "campo")
        nombre.setBuddy(combo)
        layout.addWidget(nombre)
        layout.addWidget(combo)
        self._registro(clave, combo, combo.currentIndexChanged)

    def _pagina_voz(self):
        from .panel_voz_personal import ComparacionLecturas, EditorPronunciaciones
        layout = self._pagina("Voz", "Escucha cómo suenan los acentos y ajusta el ritmo de Jarvis.")
        previo = self._tarjeta(layout, "Prueba de voz", prueba=True)
        self.texto_muestra = QLineEdit(TEXTO_MUESTRA)
        self.texto_muestra.setObjectName("textoMuestra")
        self.texto_muestra.setMaxLength(420)
        self.texto_muestra.setAccessibleName("Texto de la muestra de voz")
        self.texto_muestra.setPlaceholderText("Escribe una frase para escucharla")
        previo.addWidget(self.texto_muestra)
        fila = QHBoxLayout()
        self.boton_muestra = QPushButton(_icono("play", "#246248"), "Escuchar muestra")
        self.boton_muestra.setIconSize(QSize(15, 15))
        self.boton_muestra.setObjectName("accion")
        self.boton_muestra.clicked.connect(self._probar)
        self.texto_muestra.returnPressed.connect(self._probar)
        fila.addWidget(self.boton_muestra)
        self.aviso_muestra = _etiqueta("")
        fila.addWidget(self.aviso_muestra, 1)
        previo.addLayout(fila)
        self.comparacion = ComparacionLecturas()
        self.comparacion.comparar.connect(lambda: self._probar(comparar=True))
        self.comparacion.elegir.connect(self._elegir_lectura)
        self.texto_muestra.textChanged.connect(self.comparacion.olvidar)
        acentos = QPushButton("Usar frase de acentos")
        acentos.setObjectName("accion")
        acentos.clicked.connect(lambda: self.texto_muestra.setText(
            "Es práctico. Yo practico cada día. Ayer practicó con Fernando."))
        previo.addWidget(acentos, alignment=Qt.AlignLeft)
        previo.addWidget(self.comparacion)

        voz = self._tarjeta(layout, "Lectura en voz alta")
        self._selector(voz, "voz.modelo_piper", "Voz instalada")
        self._linea(voz)
        self._fila(voz, "Velocidad", self._numero("voz.velocidad", 0.50, 2.00, 0.05, " ×", 2),
                   "1 × corresponde al ritmo original.")
        self._linea(voz)
        self._fila(voz, "Tono", self._numero("voz.tono_semitonos", -3, 3, 0.1, " st", 1),
                   "Sube o baja la voz en semitonos.")
        self._linea(voz)
        self._fila(voz, "Pausa entre frases", self._numero("voz.pausa_ms", 0, 500, 10, " ms"))
        self._linea(voz)
        self._check(voz, "voz.relleno", "Decir una frase breve mientras prepara la respuesta")
        layout.addWidget(_etiqueta("Al reiniciar se cargan la voz instalada y las frases de espera."))
        nombres = self._tarjeta(layout, "Pronunciaciones personales")
        diccionario = EditorPronunciaciones()
        nombres.addWidget(self._registro("voz.pronunciaciones", diccionario, diccionario.cambiado))
        diccionario.cambiado.connect(self.comparacion.olvidar)
        self.campos["voz.modelo_piper"].currentIndexChanged.connect(self.comparacion.olvidar)
        for clave in ("velocidad", "tono_semitonos", "pausa_ms"):
            self.campos["voz." + clave].valueChanged.connect(self.comparacion.olvidar)

    def _pagina_widget(self):
        layout = self._pagina("Widget", "Cambia el tamaño de la esfera que aparece sobre el escritorio.")
        muestra = self._tarjeta(layout, "Vista previa al 50 %")
        self.vista_widget = _VistaWidget()
        muestra.addWidget(self.vista_widget)
        aspecto = self._tarjeta(layout, "Apariencia")
        numero = self._numero("interfaz.tamano", 120, 320, 10, " px")
        self._fila(aspecto, "Tamaño de la esfera", numero)
        self.deslizador = QSlider(Qt.Horizontal)
        self.deslizador.setAccessibleName("Tamaño de la esfera en píxeles")
        self.deslizador.setRange(numero.minimum(), numero.maximum())
        self.deslizador.setSingleStep(1)
        self.deslizador.setPageStep(10)
        self.deslizador.valueChanged.connect(numero.setValue)
        numero.valueChanged.connect(self.deslizador.setValue)
        aspecto.addWidget(self.deslizador)
        extremos = QHBoxLayout()
        extremos.addWidget(_etiqueta("120 px"))
        extremos.addStretch()
        extremos.addWidget(_etiqueta("320 px"))
        aspecto.addLayout(extremos)
        self._linea(aspecto)
        self._check(aspecto, "interfaz.subtitulos", "Mostrar lo que escuchó y respondió Jarvis")
        self.deslizador.valueChanged.connect(self._actualizar_vista_widget)
        self.campos["interfaz.subtitulos"].toggled.connect(self._actualizar_vista_widget)
        opciones = self._tarjeta(layout, "Escritorio")
        self._check(opciones, "interfaz.widget", "Mostrar la esfera en el escritorio")

    def _actualizar_vista_widget(self, *_):
        self.vista_widget.mostrar(self.deslizador.value(), self.campos["interfaz.subtitulos"].isChecked())

    def _pagina_escucha(self):
        from .panel_correcciones import PanelCorrecciones
        from .panel_ensayo import PanelEnsayo
        layout = self._pagina("Escucha", "Configura cómo se activa Jarvis y cuándo da por terminada tu frase.")
        prueba = self._tarjeta(layout, "Ensayo con tu voz")
        self.ensayo = PanelEnsayo()
        prueba.addWidget(self.ensayo)
        activa = self._tarjeta(layout, "Conversación")
        palabra = QLineEdit()
        palabra.setMaxLength(40)
        palabra.setMaximumWidth(170)
        self._registro("escucha.palabra_clave", palabra, palabra.textChanged)
        self._fila(activa, "Palabra de activación", palabra)
        self._linea(activa)
        self._fila(activa, "Duración de la conversación",
                   self._numero("escucha.minutos_conversacion", 1, 60, 1, " min"),
                   "Tiempo durante el que puedes hablar sin repetir su nombre.")
        self._linea(activa)
        self._check(activa, "escucha.interrumpir", "Permitir que mi voz interrumpa a Jarvis")
        self._check(activa, "escucha.confirmar_aperturas", "Confirmar aperturas si omito la palabra de activación")
        self._check(activa, "escucha.saludar_al_iniciar", "Saludar al iniciar el asistente")
        self._check(activa, "escucha.sugerir_pendientes", "Sugerir un siguiente paso al hablar de mis pendientes")
        alias = self._tarjeta(layout, "Correcciones al abrir aplicaciones")
        self.correcciones = PanelCorrecciones()
        alias.addWidget(self.correcciones)
        det = self._tarjeta(layout, "Detección de voz")
        self._fila(det, "Silencio al final de la frase",
                   self._numero("escucha.silencio_ms", 240, 2000, 20, " ms"),
                   "Auméntalo si te corta cuando haces una pausa.")
        self._linea(det)
        self._fila(det, "Filtro de ruido", self._numero("escucha.agresividad_vad", 0, 3, 1),
                   "0 conserva más sonido y 3 exige una voz más clara.")
        layout.addWidget(_etiqueta("Los cambios de escucha se cargan al iniciar el asistente."))

    def _pagina_sistema(self):
        layout = self._pagina("Sistema", "Consulta los modelos, las herramientas y las integraciones de este equipo.")
        modelos = self._tarjeta(layout, "Modelos locales")
        self._selector(modelos, "stt.modelo", "Reconocimiento de voz · Whisper")
        self._selector(modelos, "llm.modelo", "Conversación · Ollama")
        self._linea(modelos)
        self._check(modelos, "conocimiento.busqueda_semantica", "Buscar notas también por su significado")
        modelos.addWidget(_etiqueta("Reinicia Jarvis para aplicar los cambios de modelos y búsqueda."))
        servicios = self._tarjeta(layout)
        cabecera = QHBoxLayout()
        cabecera.addWidget(_etiqueta("Servicios y herramientas", "seccion"), 1)
        actualizar = QPushButton("Actualizar")
        actualizar.setObjectName("accion")
        actualizar.setAccessibleName("Actualizar inventario de servicios y herramientas")
        actualizar.clicked.connect(self.actualizar.emit)
        cabecera.addWidget(actualizar)
        servicios.addLayout(cabecera)
        self.lista_servicios = QVBoxLayout()
        self.lista_servicios.setSpacing(8)
        servicios.addLayout(self.lista_servicios)
        avanzados = self._tarjeta(layout, "Configuración avanzada")
        avanzados.addWidget(_etiqueta("Edita las carpetas, los sitios y las integraciones en el archivo local. Reinicia Jarvis para cargar los cambios manuales."))
        editar = QPushButton("Abrir config.toml en el Bloc de notas")
        editar.setObjectName("accion")
        editar.clicked.connect(self.editar_avanzados.emit)
        avanzados.addWidget(editar, alignment=Qt.AlignLeft)

    def _valor(self, clave):
        c = self.campos[clave]
        if isinstance(c, QCheckBox):
            return c.isChecked()
        if isinstance(c, QLineEdit):
            return c.text().strip()
        if isinstance(c, QComboBox):
            return c.currentData()
        return c.value()

    def _poner(self, clave, valor):
        c = self.campos[clave]
        with QSignalBlocker(c):
            if isinstance(c, QCheckBox):
                c.setChecked(valor)
            elif isinstance(c, QLineEdit):
                c.setText(valor)
            elif isinstance(c, QComboBox):
                if c.findData(valor) < 0:
                    c.addItem(self._nombre_opcion(clave, valor), valor)
                c.setCurrentIndex(c.findData(valor))
            else:
                c.setValue(valor)
        if clave == "interfaz.tamano":
            self.deslizador.setValue(c.value())
        elif clave == "interfaz.subtitulos":
            self._actualizar_vista_widget()

    @staticmethod
    def _nombre_opcion(clave, valor):
        if clave != "voz.modelo_piper":
            return str(valor)
        nombre = Path(valor).stem
        partes = nombre.split("-")
        idiomas = {"es_MX": "Español de México", "es_ES": "Español de España",
                   "es_AR": "Español de Argentina"}
        if len(partes) >= 3:
            return f"{partes[1].capitalize()} · {idiomas.get(partes[0], partes[0])} ({partes[-1]})"
        return nombre

    def actualizar_servicios(self, estado):
        for clave, fuente in (("voz.modelo_piper", "voces"), ("stt.modelo", "modelos_stt"),
                              ("llm.modelo", "modelos_llm")):
            c = self.campos[clave]
            elegido = self._valor(clave)
            opciones = list(dict.fromkeys(estado.get(fuente, [])))
            # Conservar el modelo activo incluso si está en una carpeta personalizada o desconectada.
            for valor in (elegido, self._base[clave]):
                if valor and valor not in opciones:
                    opciones.append(valor)
            with QSignalBlocker(c):
                c.clear()
                for valor in opciones:
                    c.addItem(self._nombre_opcion(clave, valor), valor)
                c.setCurrentIndex(c.findData(elegido))
        while self.lista_servicios.count():
            item = self.lista_servicios.takeAt(0)
            item.widget().deleteLater()
        filas = estado.get("servicios", [])
        if not filas:
            self.lista_servicios.addWidget(_etiqueta("El inventario está pendiente de actualización."))
        for servicio in filas:
            self._linea(self.lista_servicios)
            item = QWidget()
            fila = QVBoxLayout(item)
            fila.setContentsMargins(0, 0, 0, 0)
            fila.setSpacing(3)
            titulo = QHBoxLayout()
            titulo.addWidget(_etiqueta(servicio["nombre"], "campo"), 1)
            titulo.addWidget(_etiqueta(servicio["estado"]))
            fila.addLayout(titulo)
            if servicio.get("detalle"):
                fila.addWidget(_etiqueta(servicio["detalle"]))
            self.lista_servicios.addWidget(item)

    def cambios(self):
        cambios = {}
        for clave, base in self._base.items():
            valor = self._valor(clave)
            if valor != base:
                seccion, campo = clave.split(".")
                cambios.setdefault(seccion, {})[campo] = valor
        return cambios

    @staticmethod
    def _mensaje(label, mensaje, error=False):
        label.setObjectName("error" if error else "estado")
        label.setText(mensaje)
        label.style().unpolish(label)
        label.style().polish(label)

    def _edicion(self, *_):
        if self._leyendo:
            return
        sucio = bool(self.cambios())
        self.boton_guardar.setEnabled(sucio)
        self._mensaje(self.aviso, "Cambios sin guardar" if sucio else "")

    def _guardar(self):
        cambios = self.cambios()
        if cambios:
            if cambios.get("escucha", {}).get("palabra_clave") == "":
                self.mostrar_error("Escribe una palabra de activación.")
                return
            self.guardar.emit(cambios)

    def sincronizar(self, cfg):
        """Refleja cambios del widget o del asistente sin descartar lo que el usuario está editando."""
        editados = {k for k, base in self._base.items() if self._valor(k) != base}
        self._cargar(cfg, editados)
        self._edicion()

    def _cargar(self, cfg, editados):
        self._leyendo = True
        try:
            self._voz = {**DEFAULTS["voz"], **deepcopy(cfg.get("voz", {}))}
            for clave in self.campos:
                seccion, campo = clave.split(".")
                valor = cfg.get(seccion, {}).get(campo, DEFAULTS.get(seccion, {}).get(campo))
                if clave not in editados:
                    self._poner(clave, valor)
                    valor = self._valor(clave)
                self._base[clave] = valor
        finally:
            self._leyendo = False

    def confirmar_guardado(self, cfg, mensaje="Cambios guardados."):
        self._cargar(cfg, set())
        self.boton_guardar.setEnabled(False)
        self._mensaje(self.aviso, mensaje)

    def mostrar_error(self, mensaje):
        self._mensaje(self.aviso, mensaje, error=True)

    def _elegir_lectura(self, perfil):
        for clave, valor in perfil.items():
            self._poner("voz." + clave, valor)
        self._edicion()
        self.comparacion.olvidar()
        self._mensaje(self.aviso_muestra, "Lectura seleccionada.")

    def _probar(self, *, comparar=False):
        if not self.boton_muestra.isEnabled():
            return
        texto = self.texto_muestra.text().strip()
        if not texto:
            self.resultado_muestra("Escribe una frase para escucharla.", True)
            self.texto_muestra.setFocus()
            return
        voz = dict(self._voz)
        for clave in self.campos:
            if clave.startswith("voz."):
                voz[clave.split(".")[1]] = self._valor(clave)
        if comparar:
            voz["lecturas"] = self.comparacion.preparar(self._voz, voz)
        self.probar_voz.emit({**voz, "texto_muestra": texto})

    def muestra_en_curso(self, activa):
        self.boton_muestra.setEnabled(not activa)
        self.comparacion.reproduciendo(activa)
        self._mensaje(self.aviso_muestra, "Preparando la muestra…" if activa else "")

    def resultado_muestra(self, mensaje="", error=False):
        self.boton_muestra.setEnabled(True)
        self.comparacion.resultado(error)
        self._mensaje(self.aviso_muestra, mensaje, error)

    def closeEvent(self, evento):
        if not self.residente and getattr(self, "trabajando", lambda: False)():
            self.estado_trabajo.setText("Hay un trabajo en curso. Puedes pausarlo antes de cerrar.")
            self.paginas.setCurrentIndex(5)
            self._botones.button(5).setChecked(True)
            evento.ignore()
            return
        pendientes = not self.residente and self.encargo.tiene_trabajo_pendiente()
        if not self.residente and (self.encargo.enviando or self.encargo.leyendo):
            self.mostrar_error("Espera a que termine la operación en la aplicación antes de cerrar.")
            evento.ignore()
            return
        if self.cambios() or pendientes:
            pregunta = QMessageBox(self)
            pregunta.setWindowTitle("Cambios sin guardar")
            pregunta.setText("¿Quieres cerrar y descartar el trabajo pendiente?" if pendientes
                             else "¿Quieres descartar los cambios pendientes?")
            seguir = pregunta.addButton("Seguir editando", QMessageBox.RejectRole)
            descartar = pregunta.addButton("Descartar", QMessageBox.DestructiveRole)
            pregunta.setDefaultButton(seguir)
            pregunta.exec()
            if pregunta.clickedButton() != descartar:
                evento.ignore()
                return
            for clave, valor in self._base.items():
                self._poner(clave, valor)
            self._edicion()
        super().closeEvent(evento)
