"""
ui/widgets.py — Componentes reutilizables: bus de eventos, botones táctiles, campo de dinero COP,
tarjetas KPI, notificaciones tipo "toast" y ejecución en segundo plano (impresión sin congelar la UI).
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import (QEasingCurve, QObject, QPropertyAnimation, QRunnable, Qt, QThreadPool,
                            QTimer, Signal, Slot)
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import config as cfg

log = logging.getLogger(__name__)
AlignRight = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
AlignCenter = Qt.AlignmentFlag.AlignCenter


# --------------------------------------------------------------------------- #
# Contexto compartido
# --------------------------------------------------------------------------- #
class Bus(QObject):
    """Eventos globales para que los módulos se refresquen entre sí."""
    productos_cambiados = Signal()
    ventas_cambiadas = Signal()
    caja_cambiada = Signal()
    config_cambiada = Signal()
    toast = Signal(str, str)  # mensaje, tipo (ok|error|info)


class Contexto:
    """Objeto que reciben todas las vistas: base de datos + bus de eventos."""

    def __init__(self, db, bus: Bus):
        self.db = db
        self.bus = bus

    @property
    def conf(self) -> dict[str, str]:
        return self.db.get_all_config()

    def notificar(self, mensaje: str, tipo: str = "ok") -> None:
        self.bus.toast.emit(mensaje, tipo)


# --------------------------------------------------------------------------- #
# Hilos de trabajo
# --------------------------------------------------------------------------- #
class _Senales(QObject):
    ok = Signal(object)
    error = Signal(str)


class _Receptor(QObject):
    """Vive en el hilo de la UI: garantiza que los callbacks se ejecuten ahí (conexión en cola)."""

    def __init__(self, al_terminar, al_fallar, fin):
        super().__init__()
        self._ok, self._mal, self._fin = al_terminar, al_fallar, fin

    @Slot(object)
    def recibir_ok(self, r) -> None:
        try:
            if self._ok:
                self._ok(r)
        finally:
            self._fin()

    @Slot(str)
    def recibir_error(self, m) -> None:
        try:
            if self._mal:
                self._mal(m)
        finally:
            self._fin()


class _Tarea(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.senales = _Senales()

    def run(self) -> None:
        try:
            r = self.fn()
        except Exception as e:  # noqa: BLE001
            log.error("Error en segundo plano: %s", traceback.format_exc())
            self.senales.error.emit(str(e))
        else:
            self.senales.ok.emit(r)


_tareas_vivas: set = set()


def en_segundo_plano(fn: Callable[[], Any], al_terminar: Callable[[Any], None] | None = None,
                     al_fallar: Callable[[str], None] | None = None) -> None:
    """Ejecuta `fn` en el pool de hilos (impresión, Excel) y entrega el resultado en el hilo de la UI."""
    t = _Tarea(fn)
    t.setAutoDelete(False)
    par = (t, None)

    def _fin():
        _tareas_vivas.discard(par_ref[0])

    receptor = _Receptor(al_terminar, al_fallar, _fin)
    par = (t, receptor)
    par_ref = [par]
    _tareas_vivas.add(par)
    cola = Qt.ConnectionType.QueuedConnection
    t.senales.ok.connect(receptor.recibir_ok, cola)
    t.senales.error.connect(receptor.recibir_error, cola)
    QThreadPool.globalInstance().start(t)


# --------------------------------------------------------------------------- #
# Fábricas de widgets
# --------------------------------------------------------------------------- #
def boton(texto: str, variant: str | None = None, size: str | None = None, **props) -> QPushButton:
    b = QPushButton(texto)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if variant:
        b.setProperty("variant", variant)
    if size:
        b.setProperty("size", size)
    for k, v in props.items():
        b.setProperty(k, v)
    return b


def etiqueta(texto: str = "", **props) -> QLabel:
    l = QLabel(texto)
    for k, v in props.items():
        l.setProperty(k, v)
    return l


def tarjeta(panel: bool = False) -> QFrame:
    f = QFrame()
    f.setProperty("panel" if panel else "card", True)
    return f


def refrescar_estilo(w: QWidget) -> None:
    """Re-aplica el QSS tras cambiar una propiedad dinámica."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def pil_a_pixmap(img) -> QPixmap:
    im = img.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    qimg = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


# --------------------------------------------------------------------------- #
# Campo de dinero COP con formato en vivo
# --------------------------------------------------------------------------- #
class MoneyEdit(QLineEdit):
    """Campo que muestra '$ 100.000' mientras se digita y entrega un entero."""
    valorCambiado = Signal(int)

    def __init__(self, parent: QWidget | None = None, grande: bool = True, placeholder: str = "$ 0"):
        super().__init__(parent)
        self.setAlignment(AlignRight)
        self.setPlaceholderText(placeholder)
        if grande:
            self.setProperty("money", True)
        self._valor = 0
        self.textEdited.connect(self._al_editar)
        self.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)

    def _al_editar(self, texto: str) -> None:
        digitos = "".join(ch for ch in texto if ch.isdigit())[:12]
        self._valor = int(digitos) if digitos else 0
        self.setText(cfg.cop(self._valor) if digitos else "")
        self.setCursorPosition(len(self.text()))
        self.valorCambiado.emit(self._valor)

    def valor(self) -> int:
        return self._valor

    def tiene_valor(self) -> bool:
        return bool(self.text().strip())

    def setValor(self, v: int | None, emitir: bool = True) -> None:
        self._valor = int(v or 0)
        self.setText(cfg.cop(self._valor) if v is not None else "")
        if emitir:
            self.valorCambiado.emit(self._valor)

    def limpiar(self) -> None:
        self._valor = 0
        self.clear()
        self.valorCambiado.emit(0)


# --------------------------------------------------------------------------- #
# Tarjeta KPI
# --------------------------------------------------------------------------- #
class KpiCard(QFrame):
    """Tarjeta KPI con franja de color y valor que "cuenta" hasta la cifra (animado)."""

    def __init__(self, titulo: str, color: str | None = None):
        super().__init__()
        self.setProperty("card", True)
        self.setMinimumHeight(112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._color = color or "#3B82F6"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 14)
        lay.setSpacing(4)
        franja = QFrame()
        franja.setFixedHeight(4)
        franja.setFixedWidth(42)
        franja.setStyleSheet(f"background:{self._color}; border-radius:2px;")
        lay.addWidget(franja)
        self.lbl_titulo = etiqueta(titulo.upper(), kpiTitulo=True)
        lay.addWidget(self.lbl_titulo)
        self.lbl_valor = etiqueta("$ 0", kpiValor=True)
        if color:
            self.lbl_valor.setStyleSheet(f"color: {color};")
        lay.addWidget(self.lbl_valor)
        self.lbl_sub = etiqueta("", kpiSub=True)
        lay.addWidget(self.lbl_sub)

    def set_valor(self, valor: str, sub: str = "") -> None:
        self.lbl_valor.setProperty("_valor_anim", None)
        self.lbl_valor.setText(valor)
        self.lbl_sub.setText(sub)

    def set_monto(self, monto: int, sub: str = "", animar: bool = True) -> None:
        """Muestra un monto en pesos con animación de conteo."""
        from ui.animations import contar
        self.lbl_sub.setText(sub)
        if animar:
            contar(self.lbl_valor, int(monto), cfg.cop)
        else:
            self.lbl_valor.setProperty("_valor_anim", int(monto))
            self.lbl_valor.setText(cfg.cop(monto))


# --------------------------------------------------------------------------- #
# Notificación flotante (entra deslizándose desde la derecha)
# --------------------------------------------------------------------------- #
class Toast(QLabel):
    COLORES = {"ok": "#22C55E", "error": "#EF4444", "warn": "#F59E0B", "info": "#3B82F6"}

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setWordWrap(True)
        self.setMaximumWidth(520)
        self.hide()
        self._efecto = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._efecto)
        self._grupo = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._ocultar)

    def _animar(self, op_ini: float, op_fin: float, x_ini: int, x_fin: int, al_terminar=None) -> None:
        from PySide6.QtCore import QParallelAnimationGroup, QPoint
        if self._grupo is not None:
            self._grupo.stop()
        g = QParallelAnimationGroup(self)
        o = QPropertyAnimation(self._efecto, b"opacity", g)
        o.setDuration(260)
        o.setStartValue(op_ini)
        o.setEndValue(op_fin)
        m = QPropertyAnimation(self, b"pos", g)
        m.setDuration(300)
        m.setStartValue(QPoint(x_ini, self.y()))
        m.setEndValue(QPoint(x_fin, self.y()))
        m.setEasingCurve(QEasingCurve.Type.OutCubic)
        g.addAnimation(o)
        g.addAnimation(m)
        if al_terminar:
            g.finished.connect(al_terminar)
        self._grupo = g
        g.start()

    def mostrar(self, texto: str, tipo: str = "ok", ms: int = 3200) -> None:
        self.setText(texto)
        self.setStyleSheet(f"border-left: 6px solid {self.COLORES.get(tipo, '#3B82F6')};")
        self.adjustSize()
        p = self.parentWidget()
        x_fin = p.width() - self.width() - 28
        self.move(x_fin, p.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._animar(0.0, 1.0, x_fin + 60, x_fin)
        self._timer.start(ms if tipo != "error" else ms + 2500)

    def _ocultar(self) -> None:
        self._animar(1.0, 0.0, self.x(), self.x() + 60, self.hide)


# --------------------------------------------------------------------------- #
# Diálogos rápidos
# --------------------------------------------------------------------------- #
def error(parent: QWidget | None, texto: str, titulo: str = "Atención") -> None:
    QMessageBox.warning(parent, titulo, texto)


def info(parent: QWidget | None, texto: str, titulo: str = cfg.APP_NAME) -> None:
    QMessageBox.information(parent, titulo, texto)


def confirmar(parent: QWidget | None, texto: str, titulo: str = "Confirmar") -> bool:
    caja = QMessageBox(parent)
    caja.setIcon(QMessageBox.Icon.Question)
    caja.setWindowTitle(titulo)
    caja.setText(texto)
    si = caja.addButton("Sí, continuar", QMessageBox.ButtonRole.YesRole)
    caja.addButton("Cancelar", QMessageBox.ButtonRole.NoRole)
    caja.setDefaultButton(si)
    caja.exec()
    return caja.clickedButton() is si


# --------------------------------------------------------------------------- #
# Archivos
# --------------------------------------------------------------------------- #
def pedir_ruta_excel(parent: QWidget, nombre_sugerido: str) -> str:
    from PySide6.QtWidgets import QFileDialog
    ruta, _ = QFileDialog.getSaveFileName(parent, "Guardar reporte de Excel",
                                          str(cfg.exports_dir() / nombre_sugerido), "Libro de Excel (*.xlsx)")
    if ruta and not ruta.lower().endswith(".xlsx"):
        ruta += ".xlsx"
    return ruta


def abrir_archivo(ruta: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(ruta)))


def exportar_y_ofrecer_abrir(parent: QWidget, ctx: "Contexto", nombre: str, fn: Callable[[str], Any]) -> None:
    """Pide la ruta, exporta en segundo plano y ofrece abrir el archivo."""
    ruta = pedir_ruta_excel(parent, nombre)
    if not ruta:
        return
    from PySide6.QtWidgets import QApplication

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

    def ok(_):
        QApplication.restoreOverrideCursor()
        caja = QMessageBox(parent)
        caja.setWindowTitle("Reporte generado")
        caja.setText(f"Reporte guardado en:\n{ruta}")
        abrir = caja.addButton("Abrir ahora", QMessageBox.ButtonRole.AcceptRole)
        caja.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
        caja.exec()
        if caja.clickedButton() is abrir:
            abrir_archivo(ruta)

    def mal(msg):
        QApplication.restoreOverrideCursor()
        error(parent, f"No se pudo generar el reporte:\n{msg}\n\n¿El archivo está abierto en Excel?")

    en_segundo_plano(lambda: fn(ruta), ok, mal)


class Desplazable(QScrollArea):
    """Envuelve una pantalla para que, en monitores pequeños (1366×768, zoom 125 %), aparezca una
    barra para bajar en lugar de amontonar tarjetas, tablas y totales unos encima de otros.
    `alto_minimo`: altura por debajo de la cual el contenido deja de encogerse y se desplaza."""

    def __init__(self, contenido: QWidget, alto_minimo: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("desplazable")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if alto_minimo:
            contenido.setMinimumHeight(alto_minimo)
        self.setWidget(contenido)
        self.verticalScrollBar().setSingleStep(30)
        self.contenido = contenido


def desplazable(contenido: QWidget, alto_minimo: int = 0) -> "Desplazable":
    return Desplazable(contenido, alto_minimo)
