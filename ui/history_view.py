"""
ui/history_view.py — MÓDULO: Historial de Documentos y Trazabilidad.

* Tabla cronológica de todos los tiquetes emitidos (prefijo configurado, p. ej. SETP).
* Búsqueda por rango de fechas, número de tiquete, cliente (cédula o nombre), método y estado.
* Detalle: prendas, cantidades, subtotales, descuento, recibido, vuelto, fecha/hora exacta,
  cajero, cliente y la línea de tiempo de eventos (emitido, reimpreso, devoluciones, anulación).
* Reimprimir comprobante (no altera el consecutivo; queda registrado).
* Devolución parcial o anulación total con motivo: reingresa stock, descuenta del arqueo
  y de las métricas, y marca el tiquete como DEVOLUCION PARCIAL / ANULADO.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

import config as cfg
import excel_engine as xe
import printer_engine as pe
from database import POSError
from ui import theme
from ui.animations import DialogoAnimado, aparecer, destello
from ui.seguridad import autorizar, pedir_motivo
from ui.widgets import (AlignCenter, AlignRight, Contexto, boton, confirmar, en_segundo_plano, error, etiqueta,
                        exportar_y_ofrecer_abrir, refrescar_estilo, tarjeta)

ESTADOS = ["Todos", "COMPLETADA", "DEVOLUCION PARCIAL", "ANULADO"]
MOTIVOS = ["Talla no corresponde", "Defecto de fábrica", "Cambio de opinión del cliente",
           "Error en el cobro / digitación", "Cambio por otra prenda"]


def _fecha(iso: str | None, segundos: bool = False) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso
    return d.strftime("%d/%m/%Y %I:%M:%S %p" if segundos else "%d/%m/%Y %I:%M %p")


def _color_estado(estado: str) -> str:
    T = theme.T
    return {"COMPLETADA": T["success"], "ANULADO": T["danger"], "DEVOLUCION PARCIAL": T["warning"]}.get(
        estado, T["text"])


# =========================================================================== #
# Devolución / anulación
# =========================================================================== #
class DevolucionDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, venta: dict, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.venta = venta
        self.resultado: dict | None = None
        self.setWindowTitle(f"Devolución / Anulación — {venta['numero_tiquete']}")
        self.setMinimumSize(760, 560)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(etiqueta(f"Procesar devolución  ·  {venta['numero_tiquete']}", titulo=True))
        info = etiqueta(f"Venta del {_fecha(venta['fecha'])}  ·  {venta['metodo_pago']}  ·  "
                        f"Total {cfg.cop(venta['total'])}  ·  Ya devuelto {cfg.cop(venta['total_devuelto'])}",
                        muted=True)
        lay.addWidget(info)

        self.tabla = QTableWidget(len(venta["items"]), 5)
        self.tabla.setHorizontalHeaderLabels(["Prenda", "Vendidas", "Ya devueltas", "A devolver", "Valor"])
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.verticalHeader().setDefaultSectionSize(46)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.spins: dict[int, QSpinBox] = {}
        self.valores: dict[int, QLabel] = {}
        for i, it in enumerate(venta["items"]):
            pendiente = it["cantidad"] - it["cantidad_devuelta"]
            self.tabla.setItem(i, 0, QTableWidgetItem(it["nombre"]))
            for j, v in ((1, it["cantidad"]), (2, it["cantidad_devuelta"])):
                c = QTableWidgetItem(str(v))
                c.setTextAlignment(AlignCenter)
                self.tabla.setItem(i, j, c)
            sp = QSpinBox()
            sp.setRange(0, pendiente)
            sp.setEnabled(pendiente > 0)
            sp.setAlignment(AlignCenter)
            sp.valueChanged.connect(self._recalcular)
            self.spins[it["id"]] = sp
            self.tabla.setCellWidget(i, 3, sp)
            lbl = QLabel("$ 0")
            lbl.setAlignment(AlignRight)
            lbl.setContentsMargins(0, 0, 10, 0)
            self.valores[it["id"]] = lbl
            self.tabla.setCellWidget(i, 4, lbl)
        lay.addWidget(self.tabla, 1)

        self.chk_todo = QCheckBox("Anular el tiquete completo (devolver todas las prendas pendientes)")
        self.chk_todo.toggled.connect(self._todo)
        lay.addWidget(self.chk_todo)
        lay.addWidget(etiqueta("Motivo de la devolución (obligatorio)"))
        self.motivo = QComboBox()
        self.motivo.setEditable(True)
        self.motivo.addItems([""] + MOTIVOS)
        self.motivo.setMinimumHeight(42)
        lay.addWidget(self.motivo)

        fila_tot = QHBoxLayout()
        fila_tot.addWidget(etiqueta("TOTAL A DEVOLVER", titulo=True))
        self.lbl_total = QLabel("$ 0")
        self.lbl_total.setObjectName("totalLabel")
        self.lbl_total.setStyleSheet(f"color:{theme.T['danger']};")
        self.lbl_total.setAlignment(AlignRight)
        fila_tot.addWidget(self.lbl_total, 1)
        lay.addLayout(fila_tot)
        fila_re = QHBoxLayout()
        fila_re.addWidget(etiqueta("Devolver el dinero por:"))
        self.reintegro = QComboBox()
        self.reintegro.addItems(cfg.METODOS_PAGO)
        self.reintegro.setCurrentText(ctx.db.metodo_reintegro_sugerido(venta))
        self.reintegro.setMinimumHeight(40)
        fila_re.addWidget(self.reintegro)
        fila_re.addStretch()
        lay.addLayout(fila_re)
        nota = etiqueta(f"Pagó con: {venta['metodo_pago']}"
                        + (" (" + " + ".join(f"{m} {cfg.cop(v)}" for m, v in venta.get("pagos", [])) + ")"
                           if len(venta.get("pagos", [])) > 1 else "")
                        + ". Las prendas vuelven al inventario y el valor se descuenta del medio elegido en el "
                          "arqueo de la caja abierta y de las métricas.", muted=True)
        nota.setWordWrap(True)
        lay.addWidget(nota)

        fila = QHBoxLayout()
        cancelar = boton("Cancelar", "ghost", "lg")
        cancelar.clicked.connect(self.reject)
        self.btn_ok = boton("CONFIRMAR DEVOLUCIÓN", "danger", "lg")
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self.confirmar)
        fila.addWidget(cancelar)
        fila.addStretch()
        fila.addWidget(self.btn_ok)
        lay.addLayout(fila)

    def _todo(self, on: bool) -> None:
        for sp in self.spins.values():
            sp.setValue(sp.maximum() if on else 0)

    def _cantidades(self) -> dict[int, int]:
        return {did: sp.value() for did, sp in self.spins.items() if sp.value() > 0}

    def _recalcular(self) -> None:
        total = 0
        for it in self.venta["items"]:
            n = self.spins[it["id"]].value()
            pendiente = it["cantidad"] - it["cantidad_devuelta"]
            if n == pendiente and n > 0:
                monto = it["total"] - it["total_devuelto"]
            else:
                monto = min(int(round(it["total"] * n / it["cantidad"] / 50.0)) * 50,
                            it["total"] - it["total_devuelto"])
            self.valores[it["id"]].setText(cfg.cop(monto))
            total += monto
        self.lbl_total.setText(cfg.cop(total))
        self.btn_ok.setEnabled(total > 0 or bool(self._cantidades()))
        completo = all(sp.value() == sp.maximum() for sp in self.spins.values())
        self.btn_ok.setText("ANULAR TIQUETE" if completo and self._cantidades() else "CONFIRMAR DEVOLUCIÓN")

    def confirmar(self) -> None:
        cant = self._cantidades()
        motivo = self.motivo.currentText().strip()
        if not motivo:
            error(self, "Escriba o seleccione el motivo de la devolución.")
            self.motivo.setFocus()
            return
        completo = all(sp.value() == sp.maximum() for sp in self.spins.values())
        if completo and not autorizar(self, self.ctx, f"Anular el tiquete {self.venta['numero_tiquete']} "
                                                      f"por {self.lbl_total.text()}."):
            return
        if not completo and not confirmar(self, f"¿Registrar la devolución por {self.lbl_total.text()}?\n\n"
                                                "Esta operación queda en la auditoría y no se puede deshacer."):
            return
        try:
            self.resultado = self.ctx.db.registrar_devolucion(
                self.venta["numero_tiquete"], cant, motivo, self.ctx.db.get_config("cajero_actual"),
                self.reintegro.currentText())
        except POSError as e:
            error(self, str(e))
            return
        conf = self.ctx.conf
        from ui.cajon import despachar
        dev = self.resultado["devolucion"]
        despachar(self.ctx, pe.construir_devolucion(self.resultado, conf, motivo), "Devolucion", "DEVOLUCION",
                  dev["numero"], abrir=dev.get("metodo") == "Efectivo")
        import ticket_pdf
        resultado = self.resultado
        en_segundo_plano(lambda: ticket_pdf.guardar_devolucion(resultado, conf),
                         al_fallar=lambda m: self.ctx.notificar(f"No se pudo guardar el PDF: {m}", "warn"))
        self.accept()


# =========================================================================== #
# Vista principal
# =========================================================================== #
class HistorialView(QWidget):
    titulo = "Historial de Documentos"

    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        self.filas: list[dict] = []
        self.venta: dict | None = None
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 18, 20, 18)
        raiz.setSpacing(12)

        # ---------------- Filtros ----------------
        filtros = tarjeta()
        fl = QGridLayout(filtros)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setHorizontalSpacing(10)
        fl.setVerticalSpacing(6)
        hoy = QDate.currentDate()
        self.desde = QDateEdit(hoy.addDays(-6))
        self.hasta = QDateEdit(hoy)
        for d in (self.desde, self.hasta):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd/MM/yyyy")
            d.setMinimumHeight(40)
        self.numero = QLineEdit()
        self.numero.setPlaceholderText("Número de tiquete (escanee o digite)")
        self.cliente = QLineEdit()
        self.cliente.setPlaceholderText("Cédula o nombre del cliente")
        self.metodo = QComboBox()
        self.metodo.addItems(["Todos"] + cfg.METODOS_HISTORICOS)
        self.estado = QComboBox()
        self.estado.addItems(ESTADOS)
        for w in (self.numero, self.cliente, self.metodo, self.estado):
            w.setMinimumHeight(40)
        for j, (txt, w) in enumerate((("Desde", self.desde), ("Hasta", self.hasta), ("Tiquete", self.numero),
                                      ("Cliente", self.cliente), ("Método", self.metodo), ("Estado", self.estado))):
            fl.addWidget(etiqueta(txt, muted=True), 0, j)
            fl.addWidget(w, 1, j)
        fl.setColumnStretch(2, 2)
        fl.setColumnStretch(3, 2)
        rapidos = QHBoxLayout()
        rapidos.setSpacing(6)
        for txt, dias in (("Hoy", 0), ("7 días", 6), ("30 días", 29), ("Este año", -1), ("Todo", None)):
            b = boton(txt, size="sm", chip=True)
            b.clicked.connect(lambda _=False, d=dias: self._rapido(d))
            rapidos.addWidget(b)
        rapidos.addStretch()
        self.lbl_resumen = etiqueta("", muted=True)
        rapidos.addWidget(self.lbl_resumen)
        b_exp = boton("Exportar a Excel", "primary", size="sm")
        b_exp.clicked.connect(self.exportar)
        rapidos.addWidget(b_exp)
        fl.addLayout(rapidos, 2, 0, 1, 6)
        raiz.addWidget(filtros)

        # ---------------- Tabla + detalle ----------------
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(10)
        self.tabla = QTableWidget(0, 8)
        self.tabla.setHorizontalHeaderLabels(["Tiquete", "Fecha y hora", "Cliente", "Cajero", "Método",
                                              "Total", "Devuelto", "Estado"])
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.verticalHeader().setDefaultSectionSize(38)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.itemSelectionChanged.connect(self._seleccion)
        split.addWidget(self.tabla)

        self.detalle = tarjeta()
        self.detalle.setMinimumWidth(430)
        dl = QVBoxLayout(self.detalle)
        dl.setContentsMargins(18, 16, 18, 16)
        dl.setSpacing(8)
        cab = QHBoxLayout()
        self.lbl_num = etiqueta("Seleccione un tiquete", titulo=True)
        cab.addWidget(self.lbl_num)
        cab.addStretch()
        self.lbl_estado = QLabel("")
        cab.addWidget(self.lbl_estado)
        dl.addLayout(cab)
        self.lbl_meta = etiqueta("", muted=True)
        self.lbl_meta.setWordWrap(True)
        dl.addWidget(self.lbl_meta)
        self.items = QTableWidget(0, 5)
        self.items.setHorizontalHeaderLabels(["Prenda", "Cant.", "Dev.", "Precio", "Subtotal"])
        hi = self.items.horizontalHeader()
        hi.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hi.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.items.verticalHeader().setVisible(False)
        self.items.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.items.setMinimumHeight(140)
        dl.addWidget(self.items, 1)
        self.totales = QGridLayout()
        self.totales.setVerticalSpacing(2)
        dl.addLayout(self.totales)
        dl.addWidget(etiqueta("TRAZABILIDAD", kpiTitulo=True))
        self.eventos = QTableWidget(0, 3)
        self.eventos.setHorizontalHeaderLabels(["Fecha y hora", "Evento", "Detalle"])
        he = self.eventos.horizontalHeader()
        he.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        he.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.eventos.verticalHeader().setVisible(False)
        self.eventos.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.eventos.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.eventos.setWordWrap(True)
        self.eventos.setMinimumHeight(90)
        self.eventos.setMaximumHeight(170)
        dl.addWidget(self.eventos)
        botones = QHBoxLayout()
        self.btn_reimp = boton("Reimprimir comprobante", size="lg")
        self.btn_reimp.clicked.connect(self.reimprimir)
        self.btn_dev = boton("Procesar devolución / Anular", "danger", "lg")
        self.btn_dev.clicked.connect(self.devolver)
        botones.addWidget(self.btn_reimp, 1)
        botones.addWidget(self.btn_dev, 1)
        dl.addLayout(botones)
        self.btn_cambio = boton("Cambio de prenda (devuelve una y se lleva otra)", "primary", "lg")
        self.btn_cambio.clicked.connect(self.cambio)
        dl.addWidget(self.btn_cambio)
        fila_extra = QHBoxLayout()
        self.btn_pdf = boton("Abrir PDF del tiquete", "ghost")
        self.btn_pdf.clicked.connect(self.abrir_pdf)
        self.btn_metodo = boton("Corregir método de pago", "ghost")
        self.btn_metodo.setToolTip("Por ejemplo: se marcó efectivo pero pagó con débito")
        self.btn_metodo.clicked.connect(self.corregir_metodo)
        fila_extra.addWidget(self.btn_pdf, 1)
        fila_extra.addWidget(self.btn_metodo, 1)
        dl.addLayout(fila_extra)
        split.addWidget(self.detalle)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        raiz.addWidget(split, 1)
        self._habilitar(False)

        # ---------------- Conexiones ----------------
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self.buscar)
        for s in (self.numero.textChanged, self.cliente.textChanged):
            s.connect(lambda _: self._timer.start())
        for s in (self.desde.dateChanged, self.hasta.dateChanged, self.metodo.currentIndexChanged,
                  self.estado.currentIndexChanged):
            s.connect(lambda *_: self._timer.start())
        self.numero.returnPressed.connect(self._abrir_por_numero)
        ctx.bus.ventas_cambiadas.connect(lambda: self.buscar() if self.isVisible() else None)

    # ------------------------------------------------------------------ filtros
    def _rapido(self, dias: int | None) -> None:
        hoy = QDate.currentDate()
        self.desde.blockSignals(True)
        if dias is None:
            self.desde.setDate(QDate(2000, 1, 1))
        elif dias == -1:
            self.desde.setDate(QDate(hoy.year(), 1, 1))
        else:
            self.desde.setDate(hoy.addDays(-dias))
        self.desde.blockSignals(False)
        self.hasta.setDate(hoy)
        self.buscar()

    def _rango(self) -> tuple[str, str]:
        d1, d2 = self.desde.date().toPython(), self.hasta.date().toPython()
        if d1 > d2:
            d1, d2 = d2, d1
        return f"{d1:%Y-%m-%d} 00:00:00", f"{d2:%Y-%m-%d} 23:59:59"

    def buscar(self) -> None:
        desde, hasta = self._rango()
        numero = self.numero.text().strip()
        # Con número de tiquete se busca en todas las fechas (escanear un tiquete viejo)
        if numero:
            desde, hasta = None, None
        self.filas = [dict(r) for r in self.ctx.db.buscar_documentos(
            desde, hasta, numero, self.cliente.text(), self.metodo.currentText(), self.estado.currentText())]
        seleccion = self.venta["id"] if self.venta else None
        self.tabla.setRowCount(len(self.filas))
        fila_sel = -1
        for i, f in enumerate(self.filas):
            cliente = f["cliente_nombre"] or ""
            if f["cliente_documento"]:
                cliente = f"{cliente} ({f['cliente_documento']})".strip()
            vals = [f["numero_tiquete"], _fecha(f["fecha"]), cliente or "—", f["cajero"], f["metodo_pago"],
                    cfg.cop(f["total"]), cfg.cop(f["total_devuelto"]) if f["total_devuelto"] else "—", f["estado"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, f["id"])
                if j in (5, 6):
                    it.setTextAlignment(AlignRight)
                if j == 7:
                    it.setForeground(QColor(_color_estado(v)))
                self.tabla.setItem(i, j, it)
            if f["id"] == seleccion:
                fila_sel = i
        validos = [f for f in self.filas if f["estado"] != "ANULADO"]
        self.lbl_resumen.setText(
            f"{len(self.filas)} documentos  ·  Neto {cfg.cop(sum(f['neto'] for f in self.filas))}  ·  "
            f"{len(self.filas) - len(validos)} anulados")
        if fila_sel >= 0:
            self.tabla.selectRow(fila_sel)
        elif not self.filas:
            self._limpiar_detalle()

    def _abrir_por_numero(self) -> None:
        self.buscar()
        if len(self.filas) == 1:
            self.tabla.selectRow(0)

    # ------------------------------------------------------------------ detalle
    def _habilitar(self, on: bool) -> None:
        self.btn_reimp.setEnabled(on)
        self.btn_pdf.setEnabled(on)
        self.btn_metodo.setEnabled(on and bool(self.venta) and not self.venta["total_devuelto"])
        self.btn_dev.setEnabled(on and bool(self.venta) and self.venta["estado"] != "ANULADO")
        self.btn_cambio.setEnabled(on and bool(self.venta) and self.venta["estado"] != "ANULADO")

    def _limpiar_detalle(self) -> None:
        self.venta = None
        self.lbl_num.setText("Seleccione un tiquete")
        self.lbl_estado.setText("")
        self.lbl_meta.setText("")
        self.items.setRowCount(0)
        self.eventos.setRowCount(0)
        while self.totales.count():
            w = self.totales.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._habilitar(False)

    def _seleccion(self) -> None:
        filas = self.tabla.selectionModel().selectedRows()
        if not filas:
            return
        vid = self.tabla.item(filas[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        nuevo = self.venta is None or self.venta["id"] != vid
        self.mostrar(int(vid))
        if nuevo:
            aparecer(self.detalle, 240)

    def mostrar(self, venta_id: int) -> None:
        v = self.ctx.db.obtener_venta(venta_id)
        if v is None:
            return
        self.venta = v
        self.lbl_num.setText(v["numero_tiquete"])
        self.lbl_estado.setText(v["estado"])
        self.lbl_estado.setProperty("estado", v["estado"])
        refrescar_estilo(self.lbl_estado)
        cliente = v["cliente_nombre"] or "Consumidor final"
        if v["cliente_documento"]:
            cliente += f"  ·  C.C./NIT {v['cliente_documento']}"
        self.lbl_meta.setText(f"{_fecha(v['fecha'], True)}  ·  Cajero: {v['cajero'] or '—'}\n"
                              f"Cliente: {cliente}  ·  Pago: {v['metodo_pago']}"
                              + (" (" + " + ".join(f"{m} {cfg.cop(x)}" for m, x in v.get("pagos", [])) + ")"
                                 if len(v.get("pagos", [])) > 1 else "")
                              + (f"  ·  {v['separado']['numero']}" if v.get("separado") else ""))
        self.items.setRowCount(len(v["items"]))
        for i, it in enumerate(v["items"]):
            vals = [it["nombre"], str(it["cantidad"]), str(it["cantidad_devuelta"] or ""),
                    cfg.cop(it["precio_unitario"]), cfg.cop(it["subtotal"])]
            for j, val in enumerate(vals):
                c = QTableWidgetItem(val)
                if j >= 1:
                    c.setTextAlignment(AlignRight if j >= 3 else AlignCenter)
                if j == 2 and it["cantidad_devuelta"]:
                    c.setForeground(QColor(theme.T["warning"]))
                self.items.setItem(i, j, c)
        while self.totales.count():
            w = self.totales.takeAt(0).widget()
            if w:
                w.deleteLater()
        filas = [("Subtotal", v["subtotal"]), ("Descuento", -v["descuento"]), ("TOTAL", v["total"])]
        if v["metodo_pago"] == "Efectivo":
            filas += [("Recibido", v["monto_recibido"]), ("Vuelto entregado", v["cambio"])]
        if v["total_devuelto"]:
            filas += [("Devuelto", -v["total_devuelto"]), ("NETO VIGENTE", v["neto"])]
        for i, (k, val) in enumerate(filas):
            a, b = QLabel(k), QLabel(cfg.cop(val))
            b.setAlignment(AlignRight)
            if k.isupper():
                a.setStyleSheet("font-weight:800;")
                b.setStyleSheet(f"font-weight:900; font-size:17px; color:{theme.T['success']};")
            self.totales.addWidget(a, i, 0)
            self.totales.addWidget(b, i, 1)
        eventos = self.ctx.db.eventos_documento(v["id"])
        self.eventos.setRowCount(len(eventos))
        colores = {"EMITIDO": theme.T["success"], "REIMPRESO": theme.T["primary"],
                   "DEVOLUCION PARCIAL": theme.T["warning"], "ANULADO": theme.T["danger"]}
        for i, e in enumerate(eventos):
            c0 = QTableWidgetItem(_fecha(e["fecha"], True))
            c1 = QTableWidgetItem(e["evento"])
            c1.setForeground(QColor(colores.get(e["evento"], theme.T["text"])))
            c2 = QTableWidgetItem(e["descripcion"] + (f"  ({e['cajero']})" if e["cajero"] else ""))
            for j, c in enumerate((c0, c1, c2)):
                self.eventos.setItem(i, j, c)
        self.eventos.resizeRowsToContents()
        self._habilitar(True)

    # ------------------------------------------------------------------ acciones
    def reimprimir(self) -> None:
        if not self.venta:
            return
        v = self.venta
        self.ctx.db.registrar_reimpresion(v["id"], self.ctx.db.get_config("cajero_actual"))
        conf = self.ctx.conf
        datos = pe.construir_tiquete(v, conf, reimpresion=True).bytes()
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo=v["numero_tiquete"]),
                         lambda _: self.ctx.notificar(f"Copia de {v['numero_tiquete']} enviada a la impresora", "ok"),
                         lambda m: self.ctx.notificar(f"No se pudo imprimir: {m}", "error"))
        self.mostrar(v["id"])
        destello(self.eventos, theme.T["primary"])

    def devolver(self) -> None:
        if not self.venta:
            return
        dlg = DevolucionDialog(self.ctx, self.venta, self)
        if dlg.exec() and dlg.resultado:
            d = dlg.resultado["devolucion"]
            tipo = "Tiquete ANULADO" if d["tipo"] == "TOTAL" else "Devolución parcial"
            self.ctx.notificar(f"{tipo}: {d['numero']} por {cfg.cop(d['monto'])}", "ok")
            self.ctx.bus.ventas_cambiadas.emit()
            self.ctx.bus.productos_cambiados.emit()
            self.ctx.bus.caja_cambiada.emit()
            self.buscar()
            self.mostrar(self.venta["id"])
            destello(self.detalle, theme.T["danger"], 800, 16)

    def cambio(self) -> None:
        if not self.venta:
            return
        if not self.ctx.db.caja_abierta():
            error(self, "Abra la caja antes de hacer un cambio.")
            return
        from ui.cambio_view import CambioDialog
        dlg = CambioDialog(self.ctx, self.venta, self)
        if dlg.exec() and dlg.resultado:
            info = dlg.resultado["cambio_info"]
            extra = (f" · pagó {cfg.cop(info['diferencia'])}" if info["diferencia"] > 0 else
                     f" · se devolvió {cfg.cop(-info['diferencia'])}" if info["diferencia"] < 0 else "")
            self.ctx.notificar(f"Cambio hecho · tiquete nuevo {dlg.resultado['numero_tiquete']}{extra}", "ok")
            for s in (self.ctx.bus.ventas_cambiadas, self.ctx.bus.productos_cambiados, self.ctx.bus.caja_cambiada):
                s.emit()
            self.buscar()
            self.mostrar(self.venta["id"])

    def abrir_pdf(self) -> None:
        """Abre la copia PDF del tiquete; si no existe (ventas antiguas) la genera en el momento."""
        if not self.venta:
            return
        import ticket_pdf
        from ui.widgets import abrir_archivo
        conf = dict(self.ctx.conf, guardar_pdf_tiquetes="1")
        try:
            ruta = ticket_pdf.ruta_existente(self.venta, conf) or ticket_pdf.guardar_venta(self.venta, conf)
        except Exception as e:  # noqa: BLE001
            error(self, f"No se pudo generar el PDF:\n{e}")
            return
        abrir_archivo(str(ruta))

    def corregir_metodo(self) -> None:
        if not self.venta:
            return
        v = self.venta
        from PySide6.QtWidgets import QInputDialog
        opciones = [m for m in cfg.METODOS_PAGO if m != v["metodo_pago"]]
        nuevo, ok = QInputDialog.getItem(self, "Corregir método de pago",
                                         f"{v['numero_tiquete']} quedó como «{v['metodo_pago']}».\n"
                                         "¿Con qué pagó realmente el cliente?", opciones, 0, False)
        if not ok:
            return
        if not autorizar(self, self.ctx, f"Cambiar el método de pago de {v['numero_tiquete']}: "
                                         f"{v['metodo_pago']} → {nuevo}."):
            return
        motivo = pedir_motivo(self, "Motivo de la corrección", "", ["Se marcó el método equivocado",
                                                                   "Pagó con tarjeta débito", "Pagó por Nequi"])
        if not motivo:
            return
        try:
            self.ctx.db.cambiar_metodo_pago(v["id"], nuevo, motivo, self.ctx.db.get_config("cajero_actual"))
        except POSError as e:
            error(self, str(e))
            return
        self.ctx.notificar(f"{v['numero_tiquete']}: método corregido a {nuevo}", "ok")
        self.ctx.bus.ventas_cambiadas.emit()
        self.ctx.bus.caja_cambiada.emit()
        self.buscar()
        self.mostrar(v["id"])

    def exportar(self) -> None:
        desde, hasta = self._rango()
        sub = f"Periodo {desde[:10]} a {hasta[:10]}  ·  {len(self.filas)} documentos  ·  Generado {datetime.now():%d/%m/%Y %H:%M}"
        filas = list(self.filas)
        exportar_y_ofrecer_abrir(self, self.ctx, f"Historial_Documentos_{datetime.now():%Y%m%d_%H%M}.xlsx",
                                 lambda ruta: xe.exportar_historial(self.ctx.db, filas, ruta, sub))

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.buscar()
        QTimer.singleShot(0, self.numero.setFocus)


