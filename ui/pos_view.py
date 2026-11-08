"""
ui/pos_view.py — MÓDULO 1: Punto de Venta (pantalla de cobro rápido).

Izquierda (65 %): buscador con autofoco permanente (lector de código de barras), filtros por
categoría y grilla de tarjetas táctiles pintada con un delegado (miles de productos sin lag).
Derecha (35 %): carrito, descuentos, método de pago, calculadora de cambio y cobro.

Atajos: F2 buscar · F12 pagar · F5 nueva venta · F9 reimprimir último · F8 quitar último ítem · Esc limpiar.
Las devoluciones y anulaciones se procesan en el módulo Historial de Documentos.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate, QTableWidget, QVBoxLayout, QWidget)

import config as cfg
import printer_engine as pe
from database import ItemCarrito, POSError
from ui import theme
from ui.animations import destello, pulso
from ui.cash_view import AperturaDialog
from ui.widgets import (AlignCenter, AlignRight, Contexto, MoneyEdit, boton, confirmar, en_segundo_plano,
                        error, etiqueta, refrescar_estilo)

UserRole = Qt.ItemDataRole.UserRole
LIMITE_GRILLA = 240


# =========================================================================== #
# Grilla de productos (modelo + delegado)
# =========================================================================== #
class ProductosModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items: list[dict[str, Any]] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        p = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return p["nombre"]
        if role == UserRole:
            return p
        if role == Qt.ItemDataRole.ToolTipRole:
            return (f"{p['nombre']}\nCód: {p['codigo']}  ·  {p.get('marca') or ''}\n"
                    f"Precio: {cfg.cop(p['precio'])}  ·  Stock: {p['stock']}")
        return None

    def set_items(self, items: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class TarjetaDelegate(QStyledItemDelegate):
    ANCHO, ALTO = 192, 132

    def __init__(self, parent=None):
        super().__init__(parent)
        self.f_nombre = QFont("Segoe UI", 10)
        self.f_nombre.setBold(True)
        self.f_ref = QFont("Segoe UI", 8)
        self.f_precio = QFont("Segoe UI", 15)
        self.f_precio.setWeight(QFont.Weight.Black)
        self.f_stock = QFont("Segoe UI", 8)
        self.f_stock.setBold(True)

        self.destello_fila = -1      # tarjeta recién tocada (animación de confirmación)
        self.destello_alfa = 0.0

    def set_compacto(self, on: bool) -> None:
        self.ANCHO, self.ALTO = (172, 112) if on else (192, 132)
        self.f_nombre.setPointSize(9 if on else 10)
        self.f_precio.setPointSize(13 if on else 15)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(self.ANCHO, self.ALTO)

    def paint(self, painter: QPainter, option, index) -> None:
        p = index.data(UserRole)
        if not p:
            return
        T = theme.T
        r = QRectF(option.rect.adjusted(5, 5, -5, -5))
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        camino = QPainterPath()
        camino.addRoundedRect(r, 14, 14)
        painter.fillPath(camino, QBrush(QColor(T["hover"] if hover else T["card"])))
        painter.setPen(QPen(QColor(T["primary"] if hover else T["border"]), 2 if hover else 1))
        painter.drawPath(camino)

        # Franja de color por categoría
        painter.setClipPath(camino)
        color_cat = QColor(theme.color_categoria(p["categoria"]))
        painter.fillRect(QRectF(r.left(), r.top(), r.width(), 6), color_cat)
        if index.row() == self.destello_fila and self.destello_alfa > 0:
            brillo = QColor(T["success"])
            brillo.setAlphaF(self.destello_alfa)
            painter.fillRect(r, brillo)
        painter.setClipping(False)

        x, w = r.left() + 12, r.width() - 24
        # Nombre (máximo 2 líneas)
        painter.setFont(self.f_nombre)
        painter.setPen(QColor(T["text"]))
        fm = QFontMetrics(self.f_nombre)
        l1, l2 = self._dos_lineas(p["nombre"], fm, int(w))
        y = r.top() + 14
        painter.drawText(QRectF(x, y, w, fm.height()), Qt.AlignmentFlag.AlignLeft, l1)
        if l2:
            painter.drawText(QRectF(x, y + fm.height(), w, fm.height()), Qt.AlignmentFlag.AlignLeft, l2)
        # Referencia
        painter.setFont(self.f_ref)
        painter.setPen(QColor(T["muted"]))
        y_ref = y + fm.height() * 2 + 2
        ref = p["codigo"] + (f" · Ref {p['referencia']}" if p["referencia"] else "")
        if p.get("marca"):
            ref += f" · {p['marca'].title()}"
        painter.drawText(QRectF(x, y_ref, w, 16), Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(self.f_ref).elidedText(ref, Qt.TextElideMode.ElideRight, int(w)))
        # Precio
        painter.setFont(self.f_precio)
        painter.setPen(QColor(T["success"]))
        painter.drawText(QRectF(x, r.bottom() - 36, w, 28),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cfg.cop(p["precio"]))
        # Insignia de stock
        stock, critico = p["stock"], p.get("_critico", 3)
        if stock <= 0:
            bg, fg, txt = T["crit_bg"], T["crit_fg"], "Agotado"
        elif stock <= critico:
            bg, fg, txt = T["warn_bg"], T["warn_fg"], f"{stock} und"
        else:
            bg, fg, txt = T["card2"], T["muted"], f"{stock} und"
        painter.setFont(self.f_stock)
        tw = QFontMetrics(self.f_stock).horizontalAdvance(txt) + 14
        badge = QRectF(r.right() - tw - 10, r.top() + 14 + fm.height() * 2 + 2, tw, 18)
        b = QPainterPath()
        b.addRoundedRect(badge, 9, 9)
        painter.fillPath(b, QBrush(QColor(bg)))
        painter.setPen(QColor(fg))
        painter.drawText(badge, AlignCenter, txt)
        painter.restore()

    @staticmethod
    def _dos_lineas(texto: str, fm: QFontMetrics, ancho: int) -> tuple[str, str]:
        palabras, l1 = texto.split(), ""
        i = 0
        while i < len(palabras) and fm.horizontalAdvance((l1 + " " + palabras[i]).strip()) <= ancho:
            l1 = (l1 + " " + palabras[i]).strip()
            i += 1
        if not l1:  # una sola palabra larguísima
            return fm.elidedText(texto, Qt.TextElideMode.ElideRight, ancho), ""
        resto = " ".join(palabras[i:]).strip(" ·")
        return l1.strip(" ·"), fm.elidedText(resto, Qt.TextElideMode.ElideRight, ancho) if resto else ""


# =========================================================================== #
# Vista principal del POS
# =========================================================================== #
class _BarraDeslizable(QScrollArea):
    """Fila de categorías que se desliza de lado (rueda del mouse o arrastrando la barra)
    en lugar de aplastar los botones cuando no caben."""

    def __init__(self, contenido: QWidget):
        super().__init__()
        self.setObjectName("transparente")
        self.setWidget(contenido)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
                           "QScrollBar:horizontal { height: 6px; margin: 0; }")
        self.ajustar_alto()

    def ajustar_alto(self) -> None:
        w = self.widget()
        alto = w.sizeHint().height() if w is not None else 40
        self.setFixedHeight(max(34, alto) + 8)

    def wheelEvent(self, e) -> None:  # noqa: N802
        barra = self.horizontalScrollBar()
        if barra.maximum() > 0:
            d = e.angleDelta().y() or e.angleDelta().x()
            barra.setValue(barra.value() - d)
            e.accept()
        else:
            super().wheelEvent(e)


class POSView(QWidget):
    titulo = "Punto de Venta"

    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        self.carrito: list[dict[str, Any]] = []
        self.categoria = "Todos"
        self._desc_pct: int | None = None
        self._construir()
        self._conectar()
        self.refrescar_categorias()
        self.refrescar_productos()
        self._actualizar_totales()

    # ------------------------------------------------------------------ UI
    def _construir(self) -> None:
        raiz = QHBoxLayout(self)
        raiz.setContentsMargins(16, 16, 16, 16)
        raiz.setSpacing(16)
        self._raiz = raiz
        self._compacto = None

        # ---------------- Izquierda (65 %) ----------------
        izq = QVBoxLayout()
        izq.setSpacing(12)
        self._izq = izq
        fila_busq = QHBoxLayout()
        self.buscar = QLineEdit()
        self.buscar.setObjectName("search")
        self.buscar.setPlaceholderText("Escanee el código o busque por nombre, código o talla   (F2)")
        self.buscar.setClearButtonEnabled(True)
        fila_busq.addWidget(self.buscar, 1)
        izq.addLayout(fila_busq)

        self.barra_cat = QHBoxLayout()
        self.barra_cat.setSpacing(8)
        self.grupo_cat = QButtonGroup(self)
        self.grupo_cat.setExclusive(True)
        cont_cat = QWidget()
        cont_cat.setObjectName("transparente")
        cont_cat.setLayout(self.barra_cat)
        self.barra_cat.setContentsMargins(0, 0, 0, 0)
        self.scroll_cat = _BarraDeslizable(cont_cat)
        izq.addWidget(self.scroll_cat)

        self.modelo = ProductosModel()
        self.grilla = QListView()
        self.grilla.setObjectName("grid")
        self.grilla.setModel(self.modelo)
        self._delegado = TarjetaDelegate(self.grilla)  # referencia viva (override en Python)
        self.grilla.setItemDelegate(self._delegado)
        self.grilla.setViewMode(QListView.ViewMode.IconMode)
        self.grilla.setResizeMode(QListView.ResizeMode.Adjust)
        self.grilla.setMovement(QListView.Movement.Static)
        self.grilla.setUniformItemSizes(True)
        self.grilla.setSpacing(2)
        self.grilla.setGridSize(QSize(TarjetaDelegate.ANCHO + 4, TarjetaDelegate.ALTO + 4))
        self.grilla.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.grilla.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grilla.setMouseTracking(True)
        self.grilla.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.grilla.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.grilla.verticalScrollBar().setSingleStep(24)
        izq.addWidget(self.grilla, 1)
        self.lbl_conteo = etiqueta("", muted=True)
        izq.addWidget(self.lbl_conteo)

        # ---------------- Derecha (35 %) ----------------
        der_frame = QFrame()
        der_frame.setProperty("panel", True)
        der = QVBoxLayout(der_frame)
        der.setContentsMargins(14, 14, 14, 14)
        der.setSpacing(10)
        self._der = der

        cab = QHBoxLayout()
        cab.addWidget(etiqueta("Venta actual", titulo=True))
        cab.addStretch()
        self.lbl_proximo = etiqueta("", muted=True)
        cab.addWidget(self.lbl_proximo)
        der.addLayout(cab)

        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setAlignment(AlignCenter)
        self.banner.setWordWrap(True)
        self.banner.hide()
        der.addWidget(self.banner)

        fila_cli = QHBoxLayout()
        fila_cli.setSpacing(6)
        self.cli_doc = QLineEdit()
        self.cli_doc.setPlaceholderText("Cédula / NIT cliente (opcional)")
        self.cli_nombre = QLineEdit()
        self.cli_nombre.setPlaceholderText("Nombre del cliente")
        fila_cli.addWidget(self.cli_doc, 2)
        fila_cli.addWidget(self.cli_nombre, 3)
        der.addLayout(fila_cli)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(["Producto", "Cant.", "Valor", ""])
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.tabla.setColumnWidth(1, 128)
        self.tabla.setColumnWidth(3, 72)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.verticalHeader().setDefaultSectionSize(58)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabla.setShowGrid(False)
        self.tabla.setMinimumHeight(110)
        der.addWidget(self.tabla, 1)

        # Totales + descuento
        tot = QGridLayout()
        tot.setVerticalSpacing(4)
        tot.setHorizontalSpacing(8)
        tot.addWidget(etiqueta("Subtotal", muted=True), 0, 0)
        self.lbl_subtotal = QLabel("$ 0")
        self.lbl_subtotal.setAlignment(AlignRight)
        tot.addWidget(self.lbl_subtotal, 0, 1, 1, 2)
        tot.addWidget(etiqueta("Descuento", muted=True), 1, 0)
        fila_desc = QHBoxLayout()
        fila_desc.setSpacing(6)
        for pct in (5, 10, 15):
            b = boton(f"{pct}%", size="sm", chip=True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, p=pct: self._aplicar_pct(p))
            fila_desc.addWidget(b)
        self.desc = MoneyEdit(grande=False)
        self.desc.setMaximumWidth(150)
        fila_desc.addWidget(self.desc)
        tot.addLayout(fila_desc, 1, 1, 1, 2, Qt.AlignmentFlag.AlignRight)
        lbl_total_t = etiqueta("TOTAL A PAGAR", titulo=True)
        tot.addWidget(lbl_total_t, 2, 0)
        self.lbl_total = QLabel("$ 0")
        self.lbl_total.setObjectName("totalLabel")
        self.lbl_total.setAlignment(AlignRight)
        tot.addWidget(self.lbl_total, 2, 1, 1, 2)
        tot.setColumnStretch(1, 1)
        der.addLayout(tot)

        # Métodos de pago
        metodos = QHBoxLayout()
        metodos.setSpacing(8)
        self.grupo_pago = QButtonGroup(self)
        for m in cfg.METODOS_PAGO:
            b = boton(m, pago=True)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.grupo_pago.addButton(b)
            b.setProperty("metodo", m)
            metodos.addWidget(b)
        self.grupo_pago.buttons()[0].setChecked(True)
        der.addLayout(metodos)

        # Calculadora de cambio
        self.panel_efectivo = QWidget()
        self.panel_efectivo.setObjectName("transparente")
        pe_lay = QVBoxLayout(self.panel_efectivo)
        pe_lay.setContentsMargins(0, 0, 0, 0)
        pe_lay.setSpacing(8)
        self._pe_lay = pe_lay
        fila_rec = QHBoxLayout()
        col_rec = QVBoxLayout()
        col_rec.setSpacing(2)
        col_rec.addWidget(etiqueta("Monto recibido", muted=True))
        self.recibido = MoneyEdit()
        col_rec.addWidget(self.recibido)
        fila_rec.addLayout(col_rec, 1)
        self.caja_cambio = QFrame()
        self.caja_cambio.setObjectName("cambioBox")
        cc = QVBoxLayout(self.caja_cambio)
        cc.setContentsMargins(12, 4, 12, 4)
        cc.setSpacing(0)
        self.lbl_cambio_t = etiqueta("VUELTO / CAMBIO", muted=True)
        self.lbl_cambio_t.setAlignment(AlignCenter)
        self.lbl_cambio = QLabel("$ 0")
        self.lbl_cambio.setObjectName("cambioLabel")
        self.lbl_cambio.setAlignment(AlignCenter)
        cc.addWidget(self.lbl_cambio_t)
        cc.addWidget(self.lbl_cambio)
        fila_rec.addWidget(self.caja_cambio, 1)
        pe_lay.addLayout(fila_rec)
        atajos = QHBoxLayout()
        atajos.setSpacing(8)
        exacto = boton("Exacto", size="lg")
        exacto.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        exacto.clicked.connect(lambda: self.recibido.setValor(self._totales()[2]))
        atajos.addWidget(exacto)
        for v in (20_000, 50_000, 100_000):
            b = boton(cfg.cop(v), size="lg")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, val=v: self.recibido.setValor(val))
            atajos.addWidget(b)
        pe_lay.addLayout(atajos)
        der.addWidget(self.panel_efectivo)

        # Cobro
        fila_cobro = QHBoxLayout()
        fila_cobro.setSpacing(8)
        self.btn_nueva = boton("NUEVA VENTA\n(F5)", size="xl")
        self.btn_nueva.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_nueva.setMinimumWidth(130)
        self.btn_pagar = boton("PAGAR E IMPRIMIR   (F12)", "success", "xl")
        self.btn_pagar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        fila_cobro.addWidget(self.btn_nueva)
        fila_cobro.addWidget(self.btn_pagar, 1)
        der.addLayout(fila_cobro)

        raiz.addLayout(izq, 65)
        raiz.addWidget(der_frame, 35)
        self._der_frame = der_frame
        der_frame.setMinimumWidth(400)

    # ------------------------------------------------------------ modo compacto
    _QSS_COMPACTO = """
        QPushButton[size="xl"] { font-size: 16px; min-height: 46px; border-radius: 12px; }
        QPushButton[size="lg"] { font-size: 14px; min-height: 34px; border-radius: 10px; padding: 4px 8px; }
        QPushButton[pago="true"] { min-height: 38px; font-size: 14px; padding: 4px 6px; }
        QPushButton[chip="true"] { min-height: 30px; padding: 3px 14px; border-radius: 15px; }
        QPushButton[size="sm"] { min-height: 28px; padding: 2px 8px; }
        QPushButton[qty="true"] { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
                                  font-size: 17px; border-radius: 15px; }
        QPushButton[del="true"] { min-height: 26px; max-height: 26px; padding: 0 6px; }
        QLineEdit { padding: 5px 8px; }
        QLineEdit#search { font-size: 16px; min-height: 38px; }
        QLineEdit[money="true"] { font-size: 18px; min-height: 34px; }
        QLabel#totalLabel { font-size: 28px; }
        QLabel#cambioLabel { font-size: 24px; }
        QLabel[titulo="true"] { font-size: 15px; }
        QHeaderView::section { padding: 5px 6px; }
    """

    def _aplicar_compacto(self) -> None:
        # Pantallas pequeñas (portátiles 1366×768, zoom de Windows 125 %): todo un poco más chico
        compacto = self.height() < 820 or self.width() < 1250
        if compacto == self._compacto:
            return
        self._compacto = compacto
        self.setStyleSheet(self._QSS_COMPACTO if compacto else "")
        m = 10 if compacto else 16
        self._raiz.setContentsMargins(m, m, m, m)
        self._raiz.setSpacing(10 if compacto else 16)
        self._izq.setSpacing(8 if compacto else 12)
        self._der.setContentsMargins(*([10] * 4 if compacto else [14] * 4))
        self._der.setSpacing(6 if compacto else 10)
        self._pe_lay.setSpacing(6 if compacto else 8)
        self.tabla.verticalHeader().setDefaultSectionSize(46 if compacto else 58)
        self.tabla.setColumnWidth(1, 104 if compacto else 128)
        self.tabla.setColumnWidth(3, 60 if compacto else 72)
        self.btn_nueva.setText("NUEVA\n(F5)" if compacto else "NUEVA VENTA\n(F5)")
        self.btn_nueva.setMinimumWidth(90 if compacto else 130)
        self.btn_pagar.setText("COBRAR  (F12)" if compacto else "PAGAR E IMPRIMIR   (F12)")
        self._der_frame.setMinimumWidth(370 if compacto else 400)
        self._delegado.set_compacto(compacto)
        self.grilla.setGridSize(QSize(self._delegado.ANCHO + 4, self._delegado.ALTO + 4))
        self.grilla.doItemsLayout()
        self.scroll_cat.ajustar_alto()
        self._render_carrito()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._aplicar_compacto()

    def _conectar(self) -> None:
        self._timer_busq = QTimer(self)
        self._timer_busq.setSingleShot(True)
        self._timer_busq.setInterval(140)
        self._timer_busq.timeout.connect(self.refrescar_productos)
        self.buscar.textChanged.connect(lambda _: self._timer_busq.start())
        self.buscar.returnPressed.connect(self._al_enter_busqueda)
        self.grilla.clicked.connect(self._tocar_tarjeta)
        self.desc.valorCambiado.connect(self._al_cambiar_desc_manual)
        self.recibido.valorCambiado.connect(lambda _: self._actualizar_cambio())
        self.recibido.returnPressed.connect(self.pagar)
        self.grupo_pago.buttonClicked.connect(lambda _: self._al_cambiar_metodo())
        self.btn_pagar.clicked.connect(self.pagar)
        self.btn_nueva.clicked.connect(self.nueva_venta)
        self.cli_doc.editingFinished.connect(self._autocompletar_cliente)
        self._anim_tarjeta = None

        for tecla, fn in (("F2", self.enfocar_busqueda), ("F12", self.pagar), ("F5", self.nueva_venta),
                          ("F9", self.reimprimir), ("F8", self.quitar_ultimo),
                          ("Escape", lambda: self.buscar.clear())):
            sc = QShortcut(QKeySequence(tecla), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        bus = self.ctx.bus
        bus.productos_cambiados.connect(self._al_cambiar_productos)
        bus.config_cambiada.connect(self._al_cambiar_productos)
        bus.ventas_cambiadas.connect(self._actualizar_proximo)

    # ------------------------------------------------------------------ Productos
    def _tocar_tarjeta(self, idx) -> None:
        """Agrega al carrito y hace 'brillar' la tarjeta tocada."""
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        if self._anim_tarjeta is not None:
            self._anim_tarjeta.stop()
        self._delegado.destello_fila = idx.row()
        anim = QVariantAnimation(self)
        anim.setDuration(420)
        anim.setStartValue(0.45)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def paso(v):
            self._delegado.destello_alfa = float(v)
            self.grilla.viewport().update()
        anim.valueChanged.connect(paso)
        self._anim_tarjeta = anim
        anim.start()
        self.agregar(idx.data(UserRole))

    def _autocompletar_cliente(self) -> None:
        if self.cli_doc.text().strip() and not self.cli_nombre.text().strip():
            nombre = self.ctx.db.cliente_por_documento(self.cli_doc.text())
            if nombre:
                self.cli_nombre.setText(nombre)
                destello(self.cli_nombre, theme.T["primary"])

    def refrescar_categorias(self) -> None:
        while self.barra_cat.count():
            it = self.barra_cat.takeAt(0)
            if it.widget():
                self.grupo_cat.removeButton(it.widget())
                it.widget().deleteLater()
        for cat in ["Todos"] + self.ctx.db.categorias():
            b = boton(cat, chip=True)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setChecked(cat == self.categoria)
            b.clicked.connect(lambda _=False, c=cat: self._filtrar_categoria(c))
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.grupo_cat.addButton(b)
            self.barra_cat.addWidget(b)
        self.barra_cat.addStretch()
        if hasattr(self, "scroll_cat"):
            self.scroll_cat.ajustar_alto()

    def _filtrar_categoria(self, cat: str) -> None:
        self.categoria = cat
        self.refrescar_productos()
        self.enfocar_busqueda()

    def refrescar_productos(self) -> None:
        texto = self.buscar.text()
        filas = self.ctx.db.listar_productos(texto, self.categoria, limite=LIMITE_GRILLA + 1)
        critico = self.ctx.db.get_config_int("stock_critico", 3)
        items = []
        for r in filas[:LIMITE_GRILLA]:
            d = dict(r)
            d["_critico"] = critico
            items.append(d)
        self.modelo.set_items(items)
        extra = " (refine la búsqueda para ver más)" if len(filas) > LIMITE_GRILLA else ""
        self.lbl_conteo.setText(f"{len(items)} productos{extra}")
        self._actualizar_proximo()

    def _al_cambiar_productos(self) -> None:
        self.refrescar_categorias()
        self.refrescar_productos()
        # sincronizar stock de los ítems del carrito
        for it in self.carrito:
            p = self.ctx.db.producto_por_id(it["producto_id"])
            if p:
                it["stock"] = p["stock"]

    def _al_enter_busqueda(self) -> None:
        """Lógica del escáner: código exacto → al carrito; si hay un único resultado también."""
        texto = self.buscar.text().strip()
        if not texto:
            return
        p = self.ctx.db.producto_por_codigo(texto)
        if p is None:
            res = self.ctx.db.listar_productos(texto, None, limite=2)
            p = res[0] if len(res) == 1 else None
        if p is None:
            QApplication.beep()
            self.ctx.notificar(f"No se encontró «{texto}»", "warn")
            self.buscar.selectAll()
            return
        self.agregar(dict(p))
        self.buscar.clear()

    def enfocar_busqueda(self) -> None:
        self.buscar.setFocus()
        self.buscar.selectAll()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(0, self.enfocar_busqueda)
        self._actualizar_proximo()

    # ------------------------------------------------------------------ Carrito
    def _permitir_negativo(self) -> bool:
        return self.ctx.db.get_config("permitir_stock_negativo") == "1"

    def agregar(self, p: dict[str, Any] | None) -> None:
        if not p:
            return
        if int(p.get("precio") or 0) <= 0:
            QApplication.beep()
            self.ctx.notificar(f"«{p['nombre']}» no tiene precio. Asígnelo en Inventario antes de venderlo.", "warn")
            return
        self.banner.hide()
        for it in self.carrito:
            if it["producto_id"] == p["id"]:
                if not self._permitir_negativo() and it["cantidad"] + 1 > it["stock"]:
                    QApplication.beep()
                    self.ctx.notificar(f"Sin stock suficiente de «{p['nombre']}» ({it['stock']} disp.)", "warn")
                    return
                it["cantidad"] += 1
                self.carrito.remove(it)
                self.carrito.append(it)  # el último modificado queda al final
                break
        else:
            if not self._permitir_negativo() and p["stock"] < 1:
                QApplication.beep()
                self.ctx.notificar(f"«{p['nombre']}» está AGOTADO", "warn")
                return
            self.carrito.append({"producto_id": p["id"], "codigo": p["codigo"], "nombre": p["nombre"],
                                 "referencia": p["referencia"], "precio": p["precio"], "cantidad": 1,
                                 "stock": p["stock"]})
        self._render_carrito()
        self._destacar_ultima_fila()
        self.enfocar_busqueda()

    def cambiar_cantidad(self, pid: int, delta: int) -> None:
        for it in self.carrito:
            if it["producto_id"] == pid:
                nueva = it["cantidad"] + delta
                if delta > 0 and not self._permitir_negativo() and nueva > it["stock"]:
                    QApplication.beep()
                    self.ctx.notificar(f"Solo hay {it['stock']} unidades disponibles", "warn")
                    return
                if nueva <= 0:
                    self.carrito.remove(it)
                else:
                    it["cantidad"] = nueva
                break
        self._render_carrito()
        self.enfocar_busqueda()

    def quitar(self, pid: int) -> None:
        self.carrito = [it for it in self.carrito if it["producto_id"] != pid]
        self._render_carrito()
        self.enfocar_busqueda()

    def quitar_ultimo(self) -> None:
        if self.isVisible() and self.carrito:
            self.quitar(self.carrito[-1]["producto_id"])

    def _render_carrito(self) -> None:
        T = theme.T
        self.tabla.setRowCount(len(self.carrito))
        for fila, it in enumerate(self.carrito):
            ref = f"{it['codigo']} · "
            lbl = QLabel(f"<div style='font-weight:700'>{it['nombre']}</div>"
                         f"<div style='color:{T['muted']};font-size:12px'>{ref}{cfg.cop(it['precio'])} c/u</div>")
            lbl.setContentsMargins(8, 0, 4, 0)
            lbl.setWordWrap(True)
            self.tabla.setCellWidget(fila, 0, lbl)

            cant = QWidget()
            cl = QHBoxLayout(cant)
            cl.setContentsMargins(2, 0, 2, 0)
            cl.setSpacing(4)
            menos = boton("−", qty=True)
            mas = boton("+", qty=True)
            for b in (menos, mas):
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            menos.clicked.connect(lambda _=False, pid=it["producto_id"]: self.cambiar_cantidad(pid, -1))
            mas.clicked.connect(lambda _=False, pid=it["producto_id"]: self.cambiar_cantidad(pid, +1))
            n = QLabel(str(it["cantidad"]))
            n.setAlignment(AlignCenter)
            n.setMinimumWidth(28)
            n.setStyleSheet("font-size:17px;font-weight:800;")
            cl.addWidget(menos)
            cl.addWidget(n)
            cl.addWidget(mas)
            self.tabla.setCellWidget(fila, 1, cant)

            val = QLabel(cfg.cop(it["precio"] * it["cantidad"]))
            val.setAlignment(AlignRight)
            val.setContentsMargins(4, 0, 8, 0)
            val.setStyleSheet("font-weight:800;font-size:15px;")
            self.tabla.setCellWidget(fila, 2, val)

            x = boton("Quitar", **{"del": True})
            x.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            x.setToolTip("Quitar del carrito")
            x.clicked.connect(lambda _=False, pid=it["producto_id"]: self.quitar(pid))
            self.tabla.setCellWidget(fila, 3, x)
        if self.carrito:
            self.tabla.scrollToBottom()
        self._actualizar_totales()

    def _destacar_ultima_fila(self) -> None:
        fila = len(self.carrito) - 1
        if fila >= 0:
            for col in (0, 2):
                w = self.tabla.cellWidget(fila, col)
                if w is not None:
                    destello(w, theme.T["success"], 700, 6)

    # ------------------------------------------------------------------ Totales
    def _totales(self) -> tuple[int, int, int]:
        subtotal = sum(it["precio"] * it["cantidad"] for it in self.carrito)
        desc = min(self.desc.valor(), subtotal)
        return subtotal, desc, subtotal - desc

    def _aplicar_pct(self, pct: int) -> None:
        self._desc_pct = pct
        self._actualizar_totales()
        self.enfocar_busqueda()

    def _al_cambiar_desc_manual(self, _v: int) -> None:
        if self.desc.hasFocus():
            self._desc_pct = None
        self._actualizar_totales(recalcular_pct=False)

    def _actualizar_totales(self, recalcular_pct: bool = True) -> None:
        if recalcular_pct and self._desc_pct is not None:
            subtotal = sum(it["precio"] * it["cantidad"] for it in self.carrito)
            # redondeo a la centena para no manejar monedas de $50
            self.desc.blockSignals(True)
            self.desc.setValor(int(round(subtotal * self._desc_pct / 100 / 100.0)) * 100, emitir=False)
            self.desc.blockSignals(False)
        subtotal, desc, total = self._totales()
        self.lbl_subtotal.setText(cfg.cop(subtotal))
        nuevo = cfg.cop(total)
        if self.lbl_total.text() != nuevo:
            self.lbl_total.setText(nuevo)
            if total:
                pulso(self.lbl_total, 0.12)
        self.btn_pagar.setEnabled(bool(self.carrito))
        unidades = sum(it["cantidad"] for it in self.carrito)
        self.tabla.horizontalHeaderItem(0).setText(f"Producto ({unidades} und)" if unidades else "Producto")
        self._actualizar_cambio()

    def _metodo(self) -> str:
        b = self.grupo_pago.checkedButton()
        return b.property("metodo") if b else "Efectivo"

    def _al_cambiar_metodo(self) -> None:
        self.panel_efectivo.setVisible(self._metodo() == "Efectivo")
        if self._metodo() == "Efectivo":
            self.recibido.setFocus()
        else:
            self.enfocar_busqueda()

    def _actualizar_cambio(self) -> None:
        total = self._totales()[2]
        falta = False
        if not self.recibido.tiene_valor():
            self.lbl_cambio_t.setText("VUELTO / CAMBIO")
            self.lbl_cambio.setText("$ 0")
        elif self.recibido.valor() < total:
            falta = True
            self.lbl_cambio_t.setText("FALTA")
            self.lbl_cambio.setText(cfg.cop(total - self.recibido.valor()))
        else:
            self.lbl_cambio_t.setText("VUELTO / CAMBIO")
            nuevo = cfg.cop(self.recibido.valor() - total)
            if self.lbl_cambio.text() != nuevo:
                self.lbl_cambio.setText(nuevo)
                pulso(self.lbl_cambio, 0.14)
        for w in (self.lbl_cambio, self.caja_cambio):
            if w.property("falta") != falta:
                w.setProperty("falta", falta)
                refrescar_estilo(w)

    def _actualizar_proximo(self) -> None:
        conf = self.ctx.db.get_all_config()
        self.lbl_proximo.setText(f"Próximo: {conf['prefijo']}{conf['consecutivo_siguiente']}")

    # ------------------------------------------------------------------ Acciones
    def nueva_venta(self) -> None:
        if not self.isVisible():
            return
        if self.carrito and not confirmar(self, "¿Descartar la venta actual?"):
            return
        self._limpiar()

    def _limpiar(self) -> None:
        self.carrito.clear()
        self._desc_pct = None
        self.desc.limpiar()
        self.recibido.limpiar()
        self.cli_doc.clear()
        self.cli_nombre.clear()
        self.grupo_pago.buttons()[0].setChecked(True)
        self.panel_efectivo.setVisible(True)
        self._render_carrito()
        self.enfocar_busqueda()

    def _asegurar_caja(self) -> bool:
        if self.ctx.db.caja_abierta():
            return True
        dlg = AperturaDialog(self.ctx, self)
        return dlg.exec() == QDialog.DialogCode.Accepted

    def pagar(self) -> None:
        if not self.isVisible() or not self.carrito:
            return
        subtotal, desc, total = self._totales()
        metodo = self._metodo()
        recibido = self.recibido.valor() if (metodo == "Efectivo" and self.recibido.tiene_valor()) else 0
        if metodo == "Efectivo" and recibido and recibido < total:
            QApplication.beep()
            error(self, f"El monto recibido ({cfg.cop(recibido)}) es menor que el total ({cfg.cop(total)}).")
            self.recibido.setFocus()
            return
        # Validaciones contra errores de digitación
        if metodo == "Efectivo" and recibido and recibido > max(total * 5, total + 500_000):
            if not confirmar(self, f"Monto recibido: {cfg.cop(recibido)} para un total de {cfg.cop(total)}.\n"
                                   f"Vuelto: {cfg.cop(recibido - total)}\n\n¿Es correcto? (revise que no sobren ceros)"):
                self.recibido.setFocus()
                return
        if subtotal and desc > subtotal * 0.30:
            from ui.seguridad import autorizar
            if not autorizar(self, self.ctx, f"Descuento de {cfg.cop(desc)} ({desc / subtotal * 100:.0f} % de la venta). "
                                             "Los descuentos mayores al 30 % requieren autorización."):
                return
        if not self._asegurar_caja():
            return
        items = [ItemCarrito(it["producto_id"], it["cantidad"], it["precio"]) for it in self.carrito]
        try:
            venta = self.ctx.db.registrar_venta(items, metodo, desc, recibido,
                                                self.ctx.db.get_config("cajero_actual"),
                                                self.cli_doc.text(), self.cli_nombre.text())
        except POSError as e:
            QApplication.beep()
            error(self, str(e))
            return
        self._imprimir_venta(venta)
        self._limpiar()
        cambio = f" · CAMBIO {cfg.cop(venta['cambio'])}" if metodo == "Efectivo" else f" · {metodo}"
        self.banner.setText(f"Venta {venta['numero_tiquete']} por {cfg.cop(venta['total'])}{cambio}")
        self.banner.show()
        from ui.animations import aparecer
        aparecer(self.banner, 300, desplazar=-10)
        self.ctx.bus.ventas_cambiadas.emit()
        self.ctx.bus.productos_cambiados.emit()
        restantes = self.ctx.db.numeros_restantes()
        if restantes <= 1000:
            self.ctx.notificar(f"Quedan {restantes} números de la resolución DIAN. Gestione una nueva.", "warn")

    def _imprimir_venta(self, venta: dict, reimpresion: bool = False) -> None:
        conf = self.ctx.conf
        tk = pe.construir_tiquete(venta, conf, reimpresion=reimpresion)
        datos = tk.bytes()
        en_segundo_plano(
            lambda: pe.enviar(datos, conf, titulo=venta["numero_tiquete"]),
            al_fallar=lambda m: self.ctx.notificar(
                f"La venta quedó guardada, pero no se pudo imprimir:\n{m}", "error"))
        if not reimpresion:  # copia PDF del tiquete en Documentos\ConfeccionesR4 Tiquetes
            import ticket_pdf
            en_segundo_plano(lambda: ticket_pdf.guardar_venta(venta, conf),
                             al_fallar=lambda m: self.ctx.notificar(f"No se pudo guardar el PDF: {m}", "warn"))

    def reimprimir(self) -> None:
        if not self.isVisible():
            return
        v = self.ctx.db.ultima_venta()
        if not v:
            self.ctx.notificar("Aún no hay ventas registradas", "info")
            return
        self.ctx.db.registrar_reimpresion(v["id"], self.ctx.db.get_config("cajero_actual"))
        self._imprimir_venta(v, reimpresion=True)
        self.ctx.notificar(f"Reimprimiendo {v['numero_tiquete']}", "info")
        self.enfocar_busqueda()
