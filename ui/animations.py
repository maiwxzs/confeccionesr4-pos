"""
ui/animations.py — Animaciones de la interfaz (Qt Animation Framework, sin dependencias extra).

* FadeStack ............ cambio de módulo con fundido + desplazamiento suave hacia arriba.
* contar(label, valor) .. números que "cuentan" hasta su valor (KPIs, totales).
* pulso(widget) ......... latido breve de un valor que cambió (total a pagar, vuelto).
* destello(widget) ...... resplandor de fondo que se desvanece (fila nueva del carrito, banner).
* aparecer(widget) ...... fundido de entrada de paneles y tarjetas (con retardo escalonado).
* DialogoAnimado ........ diálogos que aparecen con fundido y leve escala.
* IndicadorLateral ...... barra que se desliza en la barra lateral hasta el módulo activo.

Todas las animaciones son cortas (150-700 ms) y nunca bloquean al cajero: si se dispara
otra antes de terminar, la anterior se detiene en su estado final.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPoint,
                            QPropertyAnimation, QRect, QTimer, QVariantAnimation, Qt)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QFrame, QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget

_vivas: dict[int, QAbstractAnimation] = {}   # evita que el recolector borre animaciones en curso


def _guardar(clave: object, anim: QAbstractAnimation) -> None:
    previa = _vivas.get(id(clave))
    if previa is not None and previa is not anim:
        previa.stop()
    _vivas[id(clave)] = anim
    anim.finished.connect(lambda: _vivas.pop(id(clave), None) if _vivas.get(id(clave)) is anim else None)


# --------------------------------------------------------------------------- #
# Fundidos
# --------------------------------------------------------------------------- #
def aparecer(w: QWidget, ms: int = 320, retardo: int = 0, desplazar: int = 0) -> None:
    """Fundido de entrada (y desplazamiento vertical opcional). Al terminar retira el efecto
    gráfico para no penalizar el rendimiento del widget (tablas, gráficos)."""
    def iniciar():
        if not w.isVisible():
            return
        efecto = QGraphicsOpacityEffect(w)
        efecto.setOpacity(0.0)
        w.setGraphicsEffect(efecto)
        grupo = QParallelAnimationGroup(w)
        a = QPropertyAnimation(efecto, b"opacity", grupo)
        a.setDuration(ms)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        grupo.addAnimation(a)
        if desplazar:
            fin = w.pos()
            m = QPropertyAnimation(w, b"pos", grupo)
            m.setDuration(ms)
            m.setStartValue(fin + QPoint(0, desplazar))
            m.setEndValue(fin)
            m.setEasingCurve(QEasingCurve.Type.OutCubic)
            grupo.addAnimation(m)

        def limpiar():
            if w.graphicsEffect() is efecto:
                w.setGraphicsEffect(None)
        grupo.finished.connect(limpiar)
        _guardar(w, grupo)
        grupo.start()
    if retardo:
        QTimer.singleShot(retardo, iniciar)
    else:
        iniciar()


def escalonado(widgets: list[QWidget], ms: int = 360, paso: int = 70, desplazar: int = 14) -> None:
    """Entrada en cascada (tarjetas KPI, paneles)."""
    for i, w in enumerate(widgets):
        aparecer(w, ms, retardo=i * paso, desplazar=desplazar)


class FadeStack(QStackedWidget):
    """QStackedWidget con transición: la pantalla nueva entra con fundido y sube 18 px."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.duracion = 300

    def mostrar(self, w: QWidget) -> None:
        if self.currentWidget() is w:
            return
        self.setCurrentWidget(w)
        aparecer(w, self.duracion, desplazar=18)


# --------------------------------------------------------------------------- #
# Números que cuentan
# --------------------------------------------------------------------------- #
def contar(label: QLabel, hasta: int, formato: Callable[[int], str], ms: int = 650,
           desde: int | None = None) -> None:
    """Anima el texto de `label` desde su último valor hasta `hasta`."""
    inicio = label.property("_valor_anim")
    inicio = int(inicio) if inicio is not None else (desde if desde is not None else 0)
    label.setProperty("_valor_anim", int(hasta))
    if inicio == hasta:
        label.setText(formato(hasta))
        return
    anim = QVariantAnimation(label)
    anim.setDuration(ms)
    anim.setStartValue(float(inicio))
    anim.setEndValue(float(hasta))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda v: label.setText(formato(int(round(v)))))
    anim.finished.connect(lambda: label.setText(formato(int(hasta))))
    _guardar(label, anim)
    anim.start()


# --------------------------------------------------------------------------- #
# Pulso y destello
# --------------------------------------------------------------------------- #
def pulso(label: QLabel, crecer: float = 0.16, ms: int = 260) -> None:
    """Agranda y regresa la fuente del label (latido) sin mover el layout."""
    f = label.font()
    base = label.property("_pt_base")
    if base is None:
        base = f.pointSizeF() if f.pointSizeF() > 0 else 24.0
        label.setProperty("_pt_base", base)
    base = float(base)
    anim = QVariantAnimation(label)
    anim.setDuration(ms)
    anim.setKeyValueAt(0.0, base)
    anim.setKeyValueAt(0.45, base * (1 + crecer))
    anim.setKeyValueAt(1.0, base)
    anim.setEasingCurve(QEasingCurve.Type.OutQuad)

    def aplicar(v):
        fuente = label.font()
        fuente.setPointSizeF(float(v))
        label.setFont(fuente)
    anim.valueChanged.connect(aplicar)
    _guardar(label, anim)
    anim.start()


def destello(w: QWidget, color: str = "#22C55E", ms: int = 650, radio: int = 10) -> None:
    """Superpone un resplandor de color que se desvanece (no altera el estilo del widget)."""
    capa = QFrame(w)
    capa.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    capa.setGeometry(w.rect())
    capa.show()
    c = QColor(color)
    anim = QVariantAnimation(capa)
    anim.setDuration(ms)
    anim.setStartValue(0.42)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda a: capa.setStyleSheet(
        f"background: rgba({c.red()},{c.green()},{c.blue()},{float(a):.3f}); border-radius:{radio}px;"))
    anim.finished.connect(capa.deleteLater)
    _guardar(capa, anim)
    anim.start()


# --------------------------------------------------------------------------- #
# Diálogos
# --------------------------------------------------------------------------- #
class DialogoAnimado(QDialog):
    """QDialog que aparece con fundido + leve crecimiento y se cierra con fundido."""

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self.property("_animado"):
            return
        self.setProperty("_animado", True)
        self.setWindowOpacity(0.0)
        fin = self.geometry()
        ini = QRect(fin.x() + fin.width() // 40, fin.y() + fin.height() // 40,
                    fin.width() - fin.width() // 20, fin.height() - fin.height() // 20)
        grupo = QParallelAnimationGroup(self)
        o = QPropertyAnimation(self, b"windowOpacity", grupo)
        o.setDuration(200)
        o.setStartValue(0.0)
        o.setEndValue(1.0)
        g = QPropertyAnimation(self, b"geometry", grupo)
        g.setDuration(220)
        g.setStartValue(ini)
        g.setEndValue(fin)
        g.setEasingCurve(QEasingCurve.Type.OutCubic)
        grupo.addAnimation(o)
        grupo.addAnimation(g)
        _guardar(self, grupo)
        grupo.start()

    def done(self, r: int) -> None:
        if self.property("_cerrando"):
            return super().done(r)
        self.setProperty("_cerrando", True)
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(140)
        a.setStartValue(self.windowOpacity())
        a.setEndValue(0.0)
        a.finished.connect(lambda: QDialog.done(self, r))
        _guardar(self, a)
        a.start()


# --------------------------------------------------------------------------- #
# Indicador deslizante de la barra lateral
# --------------------------------------------------------------------------- #
class IndicadorLateral(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("navIndicador")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.lower()

    def ir_a(self, destino: QWidget, animar: bool = True) -> None:
        geo = QRect(destino.mapTo(self.parentWidget(), QPoint(0, 0)), destino.size())
        if not animar or not self.isVisible():
            self.setGeometry(geo)
            self.show()
            return
        a = QPropertyAnimation(self, b"geometry", self)
        a.setDuration(320)
        a.setStartValue(self.geometry())
        a.setEndValue(geo)
        a.setEasingCurve(QEasingCurve.Type.OutBack)
        _guardar(self, a)
        a.start()
