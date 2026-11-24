"""Comunicación del panel con la configuración, el orbe y el hilo de voz."""
from __future__ import annotations

import logging
import re
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .ajustes import estado_servicios, guardar_ajustes
from .config import RAIZ, cargar
from .panel import Panel
from .texto import normalizar

log = logging.getLogger("panel")
_VOZ_EN_VIVO = {"velocidad", "tono_semitonos", "pausa_ms", "pronunciaciones"}


def icono() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#102830"))
    p.setPen(QPen(QColor("#69d9cb"), 4))
    p.drawEllipse(5, 5, 54, 54)
    p.setPen(QPen(QColor("#d6fffa"), 4, Qt.SolidLine, Qt.RoundCap))
    for x, alto in [(18, 9), (25, 18), (32, 28), (39, 18), (46, 9)]:
        p.drawLine(x, 32 - alto // 2, x, 32 + alto // 2)
    p.end()
    return QIcon(pix)


class Centro(QObject):
    abrir = Signal()
    encargo = Signal(str, str)
    inventario = Signal(int, dict, str)
    fin_muestra = Signal(str, bool)
    proyecto = Signal(dict)
    noticia_trabajo = Signal(str)

    def __init__(self, cfg, estado=None):
        super().__init__()
        self.cfg = cfg
        self.estado = estado
        self.orbe = None
        self.panel = None
        self.bandeja = None
        self.revision = 0
        self.probando = False
        self.trabajo_independiente = None
        self.abrir.connect(self.mostrar, Qt.QueuedConnection)
        self.encargo.connect(self._abrir_encargo, Qt.QueuedConnection)
        self.inventario.connect(self._inventario, Qt.QueuedConnection)
        self.fin_muestra.connect(self._fin_muestra, Qt.QueuedConnection)
        self.proyecto.connect(self._mostrar_proyecto, Qt.QueuedConnection)
        self.noticia_trabajo.connect(self._noticia, Qt.QueuedConnection)
        if estado is not None:
            from .ensayo_escucha import EnsayoEscucha
            estado.ensayo_escucha = EnsayoEscucha()
            estado.solicitar_panel = self.abrir.emit
            estado.solicitar_encargo = self.encargo.emit
            estado.mostrar_proyecto = self.proyecto.emit
            estado.noticia_trabajo = self.noticia_trabajo.emit

    @Slot(str, str)
    def _abrir_encargo(self, app, pedido):
        n = normalizar(pedido)
        tipo = "consulta"
        if re.search(r"\b(?:powerpoint|presentacion|diapositivas?)\b", n):
            tipo = "presentacion"
        elif (re.search(r"\b(?:revis\w+|modifi\w+|corr[ei]\w+|edit\w+|mejor\w+)\b", n)
              and re.search(r"\b(?:word|documento|informe|docx|archivo)\b", n)):
            tipo = "revision"
        elif re.search(r"\b(?:word|informe|documento|docx)\b", n):
            tipo = "informe"
        self.mostrar()
        self.panel.abrir_encargo(app, pedido, tipo)

    def conectar_orbe(self, orbe):
        self.orbe = orbe
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.bandeja = QSystemTrayIcon(QApplication.windowIcon(), self)
        self.bandeja.setToolTip("Jarvis · Configuración")
        self.menu_bandeja = QMenu()
        self.menu_bandeja.addAction("Configuración de Jarvis…", self.mostrar)
        self.menu_bandeja.addSeparator()
        self.menu_bandeja.addAction("Salir de Jarvis", QApplication.quit)
        self.bandeja.setContextMenu(self.menu_bandeja)
        self.bandeja.activated.connect(self._activar_bandeja)
        self.bandeja.show()

    def _activar_bandeja(self, motivo):
        if motivo == QSystemTrayIcon.Trigger:
            self.mostrar()

    @Slot()
    def mostrar(self):
        self.cfg = cargar()
        if self.panel is None:
            self.panel = Panel(self.cfg, {})
            self.panel.ensayo.conectar(self.estado, lambda: self.probando)
            from .correcciones import Correcciones
            from .store import Store
            trabajo = getattr(self.estado, "trabajo_office", None) or self.trabajo_independiente
            self.panel.correcciones.conectar(Correcciones(trabajo.proyectos.store if trabajo else Store()))
            self.panel.residente = self.estado is not None
            self.panel.guardar.connect(self.guardar)
            self.panel.actualizar.connect(self.refrescar)
            self.panel.probar_voz.connect(self.probar_voz)
            self.panel.editar_avanzados.connect(self.editar_avanzados)
            self.panel.consultar_proyecto.connect(self.consultar_proyecto)
            self.panel.iniciar_trabajo.connect(self._trabajar)
            self.panel.elegir_proyecto.connect(self._elegir_proyecto)
            self.panel.pausar_trabajo.connect(self._pausar_trabajo)
            self.panel.trabajando = lambda: bool(self.trabajo_independiente and self.trabajo_independiente.finalizando())
        else:
            self.panel.sincronizar(self.cfg)
        self.panel.showNormal()
        self.panel.raise_()
        self.panel.activateWindow()
        self.refrescar()

    def _motor_trabajo(self):
        if self.estado is not None:
            return getattr(self.estado, "trabajo_office", None)
        if self.trabajo_independiente is None:
            from .store import Store
            from .trabajo import TrabajoOffice
            from .brain import Cerebro
            from .conocimiento import Conocimiento
            t = TrabajoOffice(Store(), self.noticia_trabajo.emit, Conocimiento.desde_config(self.cfg))
            t.cerebro = Cerebro(self.cfg["llm"], self.cfg["usuario"]["nombre"])
            t.mostrar = self.proyecto.emit
            self.trabajo_independiente = t
        return self.trabajo_independiente

    @Slot(dict)
    def _mostrar_proyecto(self, ficha):
        self.mostrar()
        self.panel.ver_proyecto(ficha, enfocar=True)

    @Slot(str)
    def _noticia(self, mensaje):
        if self.panel:
            self.panel.estado_trabajo.setText(mensaje)
            self.consultar_proyecto()

    @Slot()
    def consultar_proyecto(self):
        trabajo = self._motor_trabajo()
        self.panel.ver_proyecto(trabajo.proyectos.ficha() if trabajo else None)

    @Slot(str)
    def _elegir_proyecto(self, nombre):
        t = self._motor_trabajo()
        if t and not t.ocupado():
            try:
                t.seleccionar_proyecto(nombre)
                self.panel.estado_trabajo.clear()
                self.consultar_proyecto()
            except ValueError as e:
                self.panel.estado_trabajo.setText(str(e))
        else:
            self.panel.estado_trabajo.setText("Espera a que termine el trabajo en curso.")

    @Slot(str, str, int, int)
    def _trabajar(self, ruta, modo, cantidad, minutos):
        t = self._motor_trabajo()
        if not t or t.cerebro is None:
            self.panel.estado_trabajo.setText("Jarvis está cargando el modelo local.")
            return
        try:
            mensaje = t.desde_archivo(ruta, modo, cantidad, minutos or None)
        except (ValueError, OSError) as e:
            mensaje = str(e)
        self.panel.estado_trabajo.setText(mensaje)

    @Slot()
    def _pausar_trabajo(self):
        t = self._motor_trabajo()
        if t:
            mensaje = "Trabajo en pausa." if t.cancelar(pausar=True) else t.reanudar()
            self.panel.estado_trabajo.setText(mensaje)

    @Slot()
    def editar_avanzados(self):
        import subprocess
        ruta = RAIZ / "config.toml"
        if not ruta.is_file():
            self.panel.mostrar_error("No encontré config.toml en la carpeta de Jarvis.")
            return
        try:
            subprocess.Popen(["notepad.exe", str(ruta)])
        except OSError as e:
            self.panel.mostrar_error(f"No pude abrir el Bloc de notas. {e}")

    @Slot()
    def refrescar(self):
        self.revision += 1
        revision, cfg = self.revision, self.cfg

        def leer():
            try:
                self.inventario.emit(revision, estado_servicios(cfg), "")
            except Exception as e:
                self.inventario.emit(revision, {}, str(e))
        threading.Thread(target=leer, daemon=True, name="inventario").start()

    @Slot(int, dict, str)
    def _inventario(self, revision, servicios, error):
        if revision == self.revision and self.panel is not None:
            if error:
                self.panel.mostrar_error(f"No se pudo leer el inventario. {error}")
            else:
                self.panel.actualizar_servicios(servicios)

    def ajustar_widget(self, clave, valor):
        self._guardar({"interfaz": {clave: valor}}, desde_panel=False)

    @Slot(dict)
    def guardar(self, cambios):
        self._guardar(cambios, desde_panel=True)

    def _guardar(self, cambios, desde_panel):
        try:
            self.cfg = guardar_ajustes(cambios)
        except (ValueError, OSError, TimeoutError) as e:
            mensaje = f"No se guardaron los cambios. {e}"
            if self.panel is not None and self.panel.isVisible():
                self.panel.mostrar_error(mensaje)
            else:
                QMessageBox.warning(self.orbe, "Configuración de Jarvis", mensaje)
            return
        if self.orbe is not None and "interfaz" in cambios:
            opciones = self.cfg["interfaz"]
            self.orbe.ajustar(opciones["tamano"], opciones["subtitulos"])
            self.orbe.setVisible(opciones["widget"])
        voz = {k: v for k, v in cambios.get("voz", {}).items() if k in _VOZ_EN_VIVO}
        if voz and self.estado is not None:
            self.estado.eventos_panel.put(("voz", voz, None))
        if self.panel is not None:
            if desde_panel:
                reinicio = any(s not in {"voz", "interfaz"} for s in cambios)
                reinicio |= bool(set(cambios.get("voz", {})) - _VOZ_EN_VIVO)
                mensaje = "Cambios guardados."
                if self.estado is None:
                    mensaje += " Jarvis los usará al iniciar."
                elif reinicio:
                    mensaje += " Reinicia Jarvis para aplicar los ajustes de escucha o modelos."
                self.panel.confirmar_guardado(self.cfg, mensaje)
                self.refrescar()
            else:
                self.panel.sincronizar(self.cfg)

    @Slot(dict)
    def probar_voz(self, opciones):
        if self.probando:
            return
        self.probando = True
        self.panel.muestra_en_curso(True)
        if self.estado is not None and not self.estado.fallo:
            self.estado.eventos_panel.put(("muestra", opciones, self.fin_muestra.emit))
            return

        def reproducir():
            from .muestra import reproducir_muestra
            try:
                reproducir_muestra(opciones)
            except Exception as e:
                self.fin_muestra.emit(f"No se pudo reproducir la muestra. {e}", True)
            else:
                self.fin_muestra.emit("Muestra reproducida.", False)
        threading.Thread(target=reproducir, name="muestra-voz", daemon=True).start()

    @Slot(str, bool)
    def _fin_muestra(self, mensaje, error):
        self.probando = False
        if self.panel is not None:
            self.panel.muestra_en_curso(False)
            self.panel.resultado_muestra(mensaje, error)
