"""Widget flotante de Jarvis: una esfera de partículas en la esquina inferior derecha,
siempre encima de las demás ventanas y sin robar el foco.

- Escuchando: gira despacio.  Oyéndote: el borde vibra con tu voz (celeste).
- Pensando: se arremolina (violeta).  Hablando: late con su voz (blanco).  Dormido: tenue.
- Subtítulos: lo que entendió de ti y lo que responde (para ver si te escuchó bien).
- Clic: "te escucho".  Clic derecho: silenciar micrófono, subtítulos, salir.  Arrastrar: mover.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import random
import threading
import time
from collections.abc import Callable

import numpy as np

from .config import DATOS

log = logging.getLogger("interfaz")
POSICION = DATOS / "widget.json"

COLORES = {  # (r, g, b, brillo)
    "cargando": (170, 170, 185, 0.55), "escuchando": (225, 228, 235, 0.85),
    "oyendo": (140, 215, 255, 1.0), "pensando": (185, 155, 255, 1.0),
    "hablando": (255, 255, 255, 1.0), "dormido": (120, 120, 135, 0.45),
}


class Estado:
    """Lo comparten el asistente (hilo de trabajo) y el widget (hilo de la interfaz)."""

    def __init__(self):
        self.modo = "cargando"
        self.nivel = 0.0            # volumen 0..1 (micrófono o voz de Jarvis)
        self.usuario = ""
        self.jarvis = ""
        self.t_texto = 0.0
        self.mic_activo = True
        self.subtitulos = True
        self.solicitar_panel = None
        self.eventos_panel = queue.Queue()
        self.fallo = False
        self.actividad = None

    def decir_usuario(self, texto: str) -> None:
        self.usuario, self.jarvis, self.t_texto = texto, "", time.time()

    def decir_jarvis(self, texto: str, agregar: bool = False) -> None:
        self.jarvis = (self.jarvis + " " + texto).strip() if agregar else texto
        self.t_texto = time.time()


def esfera(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    y = 1 - 2 * i / n
    r = np.sqrt(1 - y * y)
    phi = i * math.pi * (3 - math.sqrt(5))
    return np.stack([np.cos(phi) * r, y, np.sin(phi) * r], axis=1)


def crear_orbe(estado: Estado, acciones: dict | None = None, tamano: int = 200, *, padre_muestra=None):
    """Crea la ventana flotante o un hijo estático para dibujar la muestra del panel."""
    from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
    from PySide6.QtGui import QAction, QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

    acciones = acciones or {}

    class Orbe(QWidget):
        def __init__(self):
            banderas = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                        if padre_muestra is None else Qt.Widget)
            super().__init__(padre_muestra, banderas)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.ajustar(tamano, estado.subtitulos, inicial=True)
            self.puntos = esfera(1500)
            rng = np.random.default_rng(7)
            self.dispersion = rng.random(len(self.puntos)) ** 3  # algunos puntos "escapan" del borde
            self.fase = rng.random(len(self.puntos)) * math.tau
            self.angulo, self.amp, self.nivel, self.t0 = 0.0, 0.3, 0.0, time.time()
            self._arrastre = None
            if padre_muestra is None:
                self._ubicar()
                self.timer = QTimer(self)
                self.timer.timeout.connect(self.update)
                self.timer.start(33)

        # ---------- posición ----------
        def _mover_dentro(self, x, y, pantalla):
            geo = pantalla.availableGeometry()
            self.move(max(geo.left(), min(x, geo.right() - self.width() + 1)),
                      max(geo.top(), min(y, geo.bottom() - self.height() + 1)))

        def ajustar(self, tamano, subtitulos, inicial=False):
            """Conserva la esquina del orbe y deja espacio para subtítulos legibles."""
            pantalla = self.screen() or QApplication.primaryScreen()
            esquina = self.geometry().bottomRight()
            self.ORBE = max(120, min(320, int(tamano)))
            self.TEXTO = 260 if subtitulos else 0
            estado.subtitulos = subtitulos
            self.resize(self.TEXTO + self.ORBE, self.ORBE + 10)
            if not inicial:
                self._mover_dentro(esquina.x() - self.width() + 1,
                                   esquina.y() - self.height() + 1, pantalla)
                self.update()

        def _ubicar(self):
            from PySide6.QtCore import QPoint
            pantalla = QApplication.primaryScreen()
            geo = pantalla.availableGeometry()
            x, y = geo.right() - self.width() - 12, geo.bottom() - self.height() - 8
            try:
                guardada = json.loads(POSICION.read_text(encoding="utf-8"))
                derecha = int(guardada["x"]) + int(guardada.get("ancho", 460))
                abajo = int(guardada["y"]) + int(guardada.get("alto", 210))
                destino = QApplication.screenAt(QPoint(derecha - 40, abajo - 40))
                if destino:
                    x, y, pantalla = derecha - self.width(), abajo - self.height(), destino
            except (OSError, ValueError, KeyError, TypeError):
                pass
            self._mover_dentro(x, y, pantalla)

        def _guardar_posicion(self):
            try:
                POSICION.write_text(json.dumps({"x": self.x(), "y": self.y(),
                                                "ancho": self.width(), "alto": self.height()}),
                                    encoding="utf-8")
            except OSError:
                pass

        # ---------- ratón ----------
        def mousePressEvent(self, e):
            if e.button() == Qt.LeftButton:
                self._arrastre = (e.globalPosition().toPoint(), self.pos(), False)
            elif e.button() == Qt.RightButton:
                self._menu(e.globalPosition().toPoint())

        def mouseMoveEvent(self, e):
            if self._arrastre:
                inicio, pos, _ = self._arrastre
                delta = e.globalPosition().toPoint() - inicio
                if delta.manhattanLength() > 4:
                    self.move(pos + delta)
                    self._arrastre = (inicio, pos, True)

        def mouseReleaseEvent(self, e):
            if e.button() == Qt.LeftButton and self._arrastre:
                movido = self._arrastre[2]
                self._arrastre = None
                if movido:
                    self._guardar_posicion()
                elif acciones.get("clic"):
                    threading.Thread(target=acciones["clic"], daemon=True).start()

        def _ultima_accion(self):
            aviso = QMessageBox(self)
            aviso.setWindowTitle("Última acción de Jarvis")
            aviso.setTextFormat(Qt.PlainText)
            aviso.setText(estado.actividad.detalle())
            aviso.exec()

        def _menu(self, punto):
            m = QMenu()
            if estado.actividad is not None:
                m.addAction("Última acción…", self._ultima_accion)
            if "panel" in acciones:
                m.addAction("Configuración de Jarvis…", acciones["panel"])
                m.addSeparator()
            mic = QAction("Activar micrófono" if not estado.mic_activo else "Silenciar micrófono", m)
            mic.triggered.connect(lambda: setattr(estado, "mic_activo", not estado.mic_activo))
            sub = QAction("Ocultar subtítulos" if estado.subtitulos else "Mostrar subtítulos", m)
            def ajustar_widget(clave, valor):
                if "ajustar_widget" in acciones:
                    acciones["ajustar_widget"](clave, valor)
                else:
                    self.ajustar(valor if clave == "tamano" else self.ORBE,
                                 valor if clave == "subtitulos" else estado.subtitulos)
            sub.triggered.connect(lambda: ajustar_widget("subtitulos", not estado.subtitulos))
            salir = QAction("Salir de Jarvis", m)
            salir.triggered.connect(QApplication.quit)
            for a in (mic, sub):
                m.addAction(a)
            for titulo, cambio in (("Hacer más grande", 20), ("Hacer más pequeño", -20)):
                destino = max(120, min(320, self.ORBE + cambio))
                accion = m.addAction(titulo)
                accion.setEnabled(destino != self.ORBE)
                accion.triggered.connect(lambda _, n=destino: ajustar_widget("tamano", n))
            if acciones.get("clic"):
                hablar = QAction("Te escucho (despertar)", m)
                hablar.triggered.connect(lambda: threading.Thread(target=acciones["clic"], daemon=True).start())
                m.addAction(hablar)
            m.addSeparator()
            m.addAction(salir)
            m.exec(punto)

        # ---------- dibujo ----------
        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            self._subtitulos(p)
            self._orbe(p)
            p.end()

        def _subtitulos(self, p):
            edad = time.time() - estado.t_texto
            if not estado.subtitulos or edad > 14 or not (estado.usuario or estado.jarvis):
                return
            alfa = 1.0 if edad < 12 else max(0.0, (14 - edad) / 2)
            p.setFont(QFont("Segoe UI", 9))
            ancho = self.TEXTO - 40
            lineas = [(f"Tú: {estado.usuario[-200:]}", QColor(150, 200, 255)) if estado.usuario else None,
                      (f"Jarvis: {estado.jarvis[-260:]}", QColor(235, 235, 240)) if estado.jarvis else None]
            lineas = [l for l in lineas if l]
            alturas = [p.boundingRect(QRectF(0, 0, ancho, 400), Qt.TextWordWrap, t).height() for t, _ in lineas]
            alto = min(self.ORBE - 30, sum(alturas) + 6 * (len(lineas) - 1) + 22)
            caja = QRectF(4, (self.height() - alto) / 2, self.TEXTO - 16, alto)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(12, 12, 18, int(220 * alfa)))
            p.drawRoundedRect(caja, 14, 14)
            y = caja.top() + 11
            for (texto, color), h in zip(lineas, alturas):
                color.setAlphaF(alfa)
                p.setPen(color)
                rect = QRectF(caja.left() + 12, y, ancho, min(h, caja.bottom() - 8 - y))
                p.drawText(rect, Qt.TextWordWrap, texto)
                y += h + 6
                if y > caja.bottom() - 12:
                    break

        def _orbe(self, p):
            t = time.time() - self.t0
            modo = estado.modo
            r_, g_, b_, brillo = COLORES.get(modo, COLORES["escuchando"])
            self.nivel += (estado.nivel - self.nivel) * 0.35
            objetivo = {"oyendo": 0.55 + 2.4 * self.nivel, "hablando": 0.45 + 2.8 * self.nivel,
                        "pensando": 0.9 + 0.25 * math.sin(t * 4), "dormido": 0.12,
                        "cargando": 0.35 + 0.2 * math.sin(t * 2)}.get(modo, 0.35)
            self.amp += (objetivo - self.amp) * 0.12
            velocidad = {"pensando": 1.6, "hablando": 0.55, "oyendo": 0.45, "dormido": 0.12}.get(modo, 0.25)
            self.angulo += velocidad * 0.033

            cx, cy = self.TEXTO + self.ORBE / 2, self.height() / 2
            radio = self.ORBE * 0.36
            fondo = QRadialGradient(QPointF(cx, cy), radio * 1.38)
            fondo.setColorAt(0.0, QColor(0, 0, 0, 238))
            fondo.setColorAt(0.80, QColor(0, 0, 0, 225))
            fondo.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(fondo))
            p.drawEllipse(QPointF(cx, cy), radio * 1.38, radio * 1.38)

            a, inclinacion = self.angulo, 0.38 + 0.06 * math.sin(t * 0.3)
            x, y, z = self.puntos[:, 0], self.puntos[:, 1], self.puntos[:, 2]
            x1 = x * math.cos(a) + z * math.sin(a)
            z1 = -x * math.sin(a) + z * math.cos(a)
            y2 = y * math.cos(inclinacion) - z1 * math.sin(inclinacion)
            z2 = y * math.sin(inclinacion) + z1 * math.cos(inclinacion)
            borde = (1 - np.abs(z2)) ** 3
            theta = np.arctan2(y2, x1)
            ruido = (np.sin(3 * theta + 1.3 * t + self.fase * 0.2) * np.cos(5 * theta - 0.9 * t)
                     + 0.5 * np.sin(7 * theta - 2.1 * t + self.fase))
            if modo == "pensando":
                ruido += 0.6 * np.sin(2 * theta + 3.0 * t)
            d = 1 + self.amp * borde * (0.10 * ruido + 0.22 * self.dispersion)
            px = cx + radio * x1 * d
            py = cy + radio * y2 * d
            luz = (0.08 + 0.92 * (0.25 * (z2 + 1) / 2 + 0.75 * borde ** 0.8)) * brillo
            luz = np.where(z2 < 0, luz * 0.55, luz)
            niveles = np.clip((luz * 9).astype(int), 0, 9)
            for nivel in range(1, 10):
                sel = niveles == nivel
                if not sel.any():
                    continue
                color = QColor(r_, g_, b_, int(255 * min(1.0, nivel / 9 + 0.08)))
                p.setPen(QPen(color, max(1.0, (1.4 + nivel * 0.09) * self.ORBE / 200)))
                p.drawPoints([QPointF(float(a_), float(b2)) for a_, b2 in zip(px[sel], py[sel])])

    return Orbe()


def ejecutar_con_widget(trabajo: Callable[[Estado], None], acciones: dict | None = None,
                        cfg: dict | None = None, oculto: bool = False) -> None:
    """Mantiene accesible el panel incluso cuando el usuario oculta la esfera."""
    from PySide6.QtWidgets import QApplication
    from .centro import Centro, icono
    from .config import cargar
    cfg = cfg or cargar()
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Jarvis")
    app.setWindowIcon(icono())
    app.setQuitOnLastWindowClosed(False)
    estado = Estado()
    opciones = cfg["interfaz"]
    estado.subtitulos = opciones["subtitulos"]
    centro = Centro(cfg, estado)
    acciones = {**(acciones or {}), "panel": centro.mostrar, "ajustar_widget": centro.ajustar_widget}
    orbe = crear_orbe(estado, acciones, opciones["tamano"])
    centro.conectar_orbe(orbe)
    orbe.setVisible(opciones["widget"] and not oculto)
    hilo = threading.Thread(target=lambda: _proteger(trabajo, estado), daemon=True, name="asistente")
    hilo.start()
    app.exec()
    os._exit(0)


def _proteger(trabajo, estado: Estado) -> None:
    try:
        trabajo(estado)
    except Exception:
        log.exception("El asistente se detuvo")
        estado.fallo = True
        while not estado.eventos_panel.empty():
            _, _, terminado = estado.eventos_panel.get_nowait()
            if terminado:
                terminado("Se detuvo el asistente. Puedes volver a probar la muestra desde el panel.", True)
        estado.modo = "dormido"
        estado.decir_jarvis("Me detuve por un error. Revisa datos\\asistente.log.")


def demo_cuadros(ruta_base: str, modos=("escuchando", "oyendo", "pensando", "hablando", "dormido")) -> list[str]:
    """Para pruebas: dibuja el widget fuera de pantalla y guarda un PNG por modo."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    estado = Estado()
    estado.decir_usuario("¿Qué hora es?")
    estado.decir_jarvis("Son las 10 y 15 de la mañana, jueves 19 de noviembre.")
    orbe = crear_orbe(estado)
    orbe.show()
    rutas: list[str] = []
    pasos = list(modos)

    def siguiente():
        if rutas or pasos != list(modos):
            ruta = f"{ruta_base}_{estado.modo}.png"
            orbe.grab().save(ruta)
            rutas.append(ruta)
        if not pasos:
            app.quit()
            return
        estado.modo = pasos.pop(0)
        estado.nivel = 0.45
        QTimer.singleShot(1500, siguiente)

    QTimer.singleShot(100, siguiente)
    app.exec()
    return rutas
