"""
ui/separados_view.py — Separados (apartados) con abonos.

Flujo:
  1. En Vender se arman las prendas y se pulsa SEPARAR (F7): cédula, nombre, teléfono y abono inicial.
     Las prendas quedan RESERVADAS (salen del stock disponible) y se imprime el comprobante.
  2. En el módulo Separados se registran los abonos (cualquier medio de pago).
  3. Cuando paga el saldo se ENTREGA: se emite el tiquete de venta (método «Separado»).
  4. Si el cliente desiste se ANULA: la ropa vuelve al inventario y se devuelve todo, parte o nada
     de lo abonado (queda registrado cuánto se retuvo y por qué).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import config as cfg
import printer_engine as pe
from database import ItemCarrito, POSError
from ui.animations import DialogoAnimado
from ui.cajon import despachar
from ui.widgets import (AlignRight, Contexto, MoneyEdit, boton, confirmar, en_segundo_plano, error, etiqueta,
                        tarjeta)


def _fecha(iso: str) -> str:
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %I:%M %p")
    except (TypeError, ValueError):
        return str(iso or "")


def _fecha_corta(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(iso or "")


def aplicar_cambio(pagos: list[tuple[str, int]], total: int) -> tuple[list[tuple[str, int]], int]:
    """El efectivo entregado puede traer cambio: deja solo lo que se aplica al saldo."""
    otros = sum(v for m, v in pagos if m != "Efectivo")
    efectivo = sum(v for m, v in pagos if m == "Efectivo")
    aplicado = max(0, min(efectivo, total - otros))
    salida = [(m, v) for m, v in pagos if m != "Efectivo"] + ([("Efectivo", aplicado)] if aplicado else [])
    return salida, efectivo - aplicado


def imprimir_separado(ctx: Contexto, sep: dict[str, Any], titulo: str, abono: dict | None = None,
                      abrir: bool = False) -> None:
    conf = ctx.conf
    tk = pe.construir_separado(sep, conf, titulo, abono)
    despachar(ctx, tk, sep["numero"], "ABONO", sep["numero"], imprimir=True, abrir=abrir)
    if conf.get("guardar_pdf_tiquetes", "1") == "1":
        import ticket_pdf
        sufijo = "_abono_" + datetime.now().strftime("%H%M%S") if abono else ""
        tk_pdf = pe.construir_separado(sep, conf, titulo, abono)
        en_segundo_plano(lambda: ticket_pdf.guardar(tk_pdf, ticket_pdf.ruta_tiquete(
            conf, sep["numero"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sufijo)),
            al_fallar=lambda m: ctx.notificar(f"No se pudo guardar el PDF: {m}", "warn"))


# =========================================================================== #
# Crear separado (desde Vender)
# =========================================================================== #
class NuevoSeparadoDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, items: list[ItemCarrito], total: int, descuento: int,
                 documento: str = "", nombre: str = "", parent=None):
        super().__init__(parent)
        self.ctx, self.items, self.total, self.descuento = ctx, items, int(total), int(descuento)
        self.separado: dict[str, Any] | None = None
        self.setWindowTitle("Separar prendas")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.addWidget(etiqueta("Separar prendas", titulo=True))
        cab = QHBoxLayout()
        cab.addWidget(etiqueta(f"{sum(i.cantidad for i in items)} prenda(s) · TOTAL", kpiTitulo=True))
        cab.addStretch()
        t = QLabel(cfg.cop(self.total))
        t.setObjectName("totalLabel")
        cab.addWidget(t)
        lay.addLayout(cab)
        form = QFormLayout()
        self.doc = QLineEdit(documento)
        self.doc.setPlaceholderText("Cédula (obligatoria)")
        self.nombre = QLineEdit(nombre)
        self.nombre.setPlaceholderText("Nombre completo (obligatorio)")
        self.tel = QLineEdit()
        self.tel.setPlaceholderText("Celular para avisarle")
        self.dias = QSpinBox()
        self.dias.setRange(1, 180)
        self.dias.setSuffix(" días para pagar")
        self.dias.setValue(ctx.db.get_config_int("separado_dias_limite", 30))
        self.abono = MoneyEdit()
        self.metodo = QComboBox()
        self.metodo.addItems(cfg.METODOS_PAGO)
        self.obs = QLineEdit()
        self.obs.setPlaceholderText("Observaciones (opcional)")
        form.addRow("Cédula", self.doc)
        form.addRow("Nombre", self.nombre)
        form.addRow("Celular", self.tel)
        form.addRow("Plazo", self.dias)
        fila_ab = QHBoxLayout()
        fila_ab.addWidget(self.abono, 2)
        fila_ab.addWidget(self.metodo, 1)
        form.addRow("Abono inicial", fila_ab)
        form.addRow("", self.obs)
        lay.addLayout(form)
        self.saldo = etiqueta("", muted=True)
        self.saldo.setAlignment(AlignRight)
        lay.addWidget(self.saldo)
        self.abono.valorCambiado.connect(self._actualizar)
        self.doc.editingFinished.connect(self._autocompletar)
        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        ok = boton("Separar e imprimir", "success", "lg")
        ok.clicked.connect(self._guardar)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)
        self._actualizar()
        (self.nombre if documento else self.doc).setFocus()

    def _autocompletar(self) -> None:
        if not self.nombre.text().strip():
            n = self.ctx.db.cliente_por_documento(self.doc.text())
            if n:
                self.nombre.setText(n)

    def _actualizar(self, *_a) -> None:
        ab = self.abono.valor()
        if ab > self.total:
            self.saldo.setText(f"El abono supera el total en {cfg.cop(ab - self.total)}")
        else:
            self.saldo.setText(f"Queda debiendo: {cfg.cop(self.total - ab)}")

    def _guardar(self) -> None:
        ab = self.abono.valor()
        if ab >= self.total and ab:
            error(self, "Si paga todo de una vez, haga una venta normal en lugar de un separado.")
            return
        if ab == 0 and not confirmar(self, "¿Separar sin abono inicial?"):
            return
        try:
            sep = self.ctx.db.crear_separado(
                self.items, self.doc.text(), self.nombre.text(), self.tel.text(), self.descuento,
                [(self.metodo.currentText(), ab)] if ab else [], self.ctx.db.get_config("cajero_actual"),
                self.dias.value(), self.obs.text())
        except POSError as e:
            error(self, str(e))
            return
        abono = {"pagos": [(self.metodo.currentText(), ab)]} if ab else None
        imprimir_separado(self.ctx, sep, "SEPARADO", abono, abrir=bool(ab) and self.metodo.currentText() == "Efectivo")
        self.separado = sep
        self.accept()


# =========================================================================== #
# Anular separado
# =========================================================================== #
class AnularSeparadoDialog(DialogoAnimado):
    def __init__(self, sep: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Anular {sep['numero']}")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.addWidget(etiqueta(f"Anular separado {sep['numero']}", titulo=True))
        t = etiqueta(f"Las prendas vuelven al inventario. El cliente abonó {cfg.cop(sep['abonado'])}: "
                     "indique cuánto se le devuelve (puede ser todo, una parte o nada).", muted=True)
        t.setWordWrap(True)
        lay.addWidget(t)
        form = QFormLayout()
        self.motivo = QComboBox()
        self.motivo.setEditable(True)
        self.motivo.addItems(["", "El cliente desistió", "Venció el plazo sin pagar", "Separado por error"])
        self.motivo.lineEdit().setPlaceholderText("Motivo (obligatorio)")
        self.reintegro = MoneyEdit()
        self.reintegro.setValor(sep["abonado"], emitir=False)
        self.metodo = QComboBox()
        self.metodo.addItems(cfg.METODOS_PAGO)
        form.addRow("Motivo", self.motivo)
        fila = QHBoxLayout()
        fila.addWidget(self.reintegro, 2)
        fila.addWidget(self.metodo, 1)
        form.addRow("Se le devuelve", fila)
        lay.addLayout(form)
        b = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        ok = boton("Anular separado", "danger", "lg")
        ok.clicked.connect(self._ok)
        b.addWidget(c)
        b.addStretch()
        b.addWidget(ok)
        lay.addLayout(b)
        self.abonado = sep["abonado"]

    def _ok(self) -> None:
        if len(self.motivo.currentText().strip()) < 3:
            error(self, "Escriba el motivo de la anulación.")
            return
        if self.reintegro.valor() > self.abonado:
            error(self, f"No se puede devolver más de lo abonado ({cfg.cop(self.abonado)}).")
            return
        self.accept()


# =========================================================================== #
# Módulo Separados
# =========================================================================== #
class SeparadosView(QWidget):
    titulo = "Separados y abonos"
    COLS = ["Número", "Fecha", "Cliente", "Celular", "Prendas", "Total", "Abonado", "Saldo", "Vence", "Estado"]

    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        self.filas: list[dict[str, Any]] = []
        self.actual: dict[str, Any] | None = None
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 20)
        raiz.setSpacing(12)

        barra = QHBoxLayout()
        self.buscar = QLineEdit()
        self.buscar.setObjectName("search")
        self.buscar.setPlaceholderText("Buscar por número SEP, cédula, nombre o celular (o escanee el comprobante)")
        self.buscar.setClearButtonEnabled(True)
        barra.addWidget(self.buscar, 1)
        self.estado = QComboBox()
        for txt, val in (("Activos", "ACTIVO"), ("Entregados", "ENTREGADO"), ("Anulados", "ANULADO"),
                         ("Todos", "Todos")):
            self.estado.addItem(txt, val)
        self.estado.setMinimumHeight(44)
        barra.addWidget(self.estado)
        raiz.addLayout(barra)
        self.lbl_resumen = etiqueta("", muted=True)
        raiz.addWidget(self.lbl_resumen)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tabla = QTableWidget(0, len(self.COLS))
        self.tabla.setHorizontalHeaderLabels(self.COLS)
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.setAlternatingRowColors(True)
        split.addWidget(self.tabla)

        det = tarjeta(panel=True)
        det.setMinimumWidth(400)
        dl = QVBoxLayout(det)
        dl.setContentsMargins(16, 14, 16, 14)
        self.lbl_num = etiqueta("Seleccione un separado", titulo=True)
        dl.addWidget(self.lbl_num)
        self.lbl_meta = etiqueta("", muted=True)
        self.lbl_meta.setWordWrap(True)
        dl.addWidget(self.lbl_meta)
        self.items = QTableWidget(0, 3)
        self.items.setHorizontalHeaderLabels(["Prenda", "Cant.", "Valor"])
        self.items.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.items.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.items.verticalHeader().setVisible(False)
        self.items.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items.setMinimumHeight(110)
        dl.addWidget(self.items, 1)
        dl.addWidget(etiqueta("ABONOS", kpiTitulo=True))
        self.abonos = QTableWidget(0, 3)
        self.abonos.setHorizontalHeaderLabels(["Fecha", "Medio", "Valor"])
        self.abonos.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.abonos.verticalHeader().setVisible(False)
        self.abonos.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.abonos.setMinimumHeight(90)
        dl.addWidget(self.abonos, 1)
        self.lbl_saldo = QLabel("")
        self.lbl_saldo.setObjectName("totalLabel")
        self.lbl_saldo.setAlignment(AlignRight)
        dl.addWidget(self.lbl_saldo)
        f1 = QHBoxLayout()
        self.b_abonar = boton("Registrar abono", "primary", "lg")
        self.b_entregar = boton("Entregar (pagar saldo)", "success", "lg")
        f1.addWidget(self.b_abonar, 1)
        f1.addWidget(self.b_entregar, 1)
        dl.addLayout(f1)
        f2 = QHBoxLayout()
        self.b_reimp = boton("Reimprimir comprobante", "ghost")
        self.b_anular = boton("Anular separado", "danger")
        f2.addWidget(self.b_reimp, 1)
        f2.addWidget(self.b_anular, 1)
        dl.addLayout(f2)
        split.addWidget(det)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        raiz.addWidget(split, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self.refrescar)
        self.buscar.textChanged.connect(lambda _: self._timer.start())
        self.estado.currentIndexChanged.connect(lambda _: self.refrescar())
        self.tabla.itemSelectionChanged.connect(self._al_seleccionar)
        self.b_abonar.clicked.connect(self.abonar)
        self.b_entregar.clicked.connect(self.entregar)
        self.b_reimp.clicked.connect(self.reimprimir)
        self.b_anular.clicked.connect(self.anular)
        ctx.bus.ventas_cambiadas.connect(self.refrescar)
        self._habilitar()
        self.refrescar()

    # ---------------------------------------------------------------- datos
    def refrescar(self) -> None:
        sel = self.actual["id"] if self.actual else None
        self.filas = self.ctx.db.listar_separados(self.estado.currentData(), self.buscar.text())
        self.tabla.setRowCount(len(self.filas))
        rojo, ambar = QColor("#EF4444"), QColor("#F59E0B")
        for r, f in enumerate(self.filas):
            vals = [f["numero"], _fecha(f["fecha"]), f"{f['cliente_nombre']} · {f['cliente_documento']}",
                    f["telefono"], str(f["unidades"] or 0), cfg.cop(f["total"]), cfg.cop(f["abonado"]),
                    cfg.cop(f["saldo"]), _fecha_corta(f["fecha_limite"]),
                    "VENCIDO" if f["vencido"] else f["estado"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c >= 5 and c <= 7:
                    it.setTextAlignment(int(AlignRight))
                if f["vencido"] and c in (8, 9):
                    it.setForeground(rojo)
                elif c == 7 and f["estado"] == "ACTIVO" and f["saldo"]:
                    it.setForeground(ambar)
                self.tabla.setItem(r, c, it)
        activos = [f for f in self.filas if f["estado"] == "ACTIVO"]
        self.lbl_resumen.setText(
            f"{len(self.filas)} separado(s) · por cobrar {cfg.cop(sum(f['saldo'] for f in activos))}"
            f" · vencidos {sum(1 for f in activos if f['vencido'])}")
        if sel:
            for r, f in enumerate(self.filas):
                if f["id"] == sel:
                    self.tabla.selectRow(r)
                    break
            else:
                self.actual = None
        self._mostrar()

    def _al_seleccionar(self) -> None:
        r = self.tabla.currentRow()
        self.actual = self.ctx.db.obtener_separado(self.filas[r]["id"]) if 0 <= r < len(self.filas) else None
        self._mostrar()

    def _mostrar(self) -> None:
        s = self.actual
        if s:
            s = self.actual = self.ctx.db.obtener_separado(s["id"])
        self.items.setRowCount(0)
        self.abonos.setRowCount(0)
        if not s:
            self.lbl_num.setText("Seleccione un separado")
            self.lbl_meta.setText("")
            self.lbl_saldo.setText("")
            self._habilitar()
            return
        self.lbl_num.setText(f"{s['numero']} · {'VENCIDO' if s['vencido'] else s['estado']}")
        self.lbl_meta.setText(f"{s['cliente_nombre']} · C.C. {s['cliente_documento']}"
                              + (f" · Cel. {s['telefono']}" if s["telefono"] else "")
                              + f"\nSeparado {_fecha(s['fecha'])} · vence {_fecha_corta(s['fecha_limite'])}"
                              + (f"\n{s['observaciones']}" if s["observaciones"] else "")
                              + (f"\nMotivo de anulación: {s['cierre_motivo']}" if s["cierre_motivo"] else ""))
        self.items.setRowCount(len(s["items"]))
        for r, it in enumerate(s["items"]):
            for c, v in enumerate([it["nombre"], str(it["cantidad"]), cfg.cop(it["precio_unitario"] * it["cantidad"])]):
                self.items.setItem(r, c, QTableWidgetItem(v))
        self.abonos.setRowCount(len(s["abonos"]))
        for r, a in enumerate(s["abonos"]):
            etiqueta_m = a["metodo"] + (" (devuelto)" if a["origen"] == "REEMBOLSO_SEPARADO" else "")
            for c, v in enumerate([_fecha(a["fecha"]), etiqueta_m, cfg.cop(a["monto"])]):
                self.abonos.setItem(r, c, QTableWidgetItem(v))
        self.lbl_saldo.setText(f"Saldo {cfg.cop(s['saldo'])}" if s["estado"] == "ACTIVO"
                               else f"Abonado {cfg.cop(s['abonado'])}")
        self._habilitar()

    def _habilitar(self) -> None:
        activo = bool(self.actual and self.actual["estado"] == "ACTIVO")
        for b in (self.b_abonar, self.b_entregar, self.b_anular):
            b.setEnabled(activo)
        self.b_reimp.setEnabled(bool(self.actual))

    # ---------------------------------------------------------------- acciones
    def _caja(self) -> bool:
        if self.ctx.db.caja_abierta():
            return True
        from ui.cash_view import AperturaDialog
        from PySide6.QtWidgets import QDialog
        return AperturaDialog(self.ctx, self).exec() == QDialog.DialogCode.Accepted

    def abonar(self) -> None:
        s = self.actual
        if not s or not self._caja():
            return
        from ui.pagos import pedir_pagos
        pagos = pedir_pagos(self, f"Abono a {s['numero']}", s["saldo"], parcial=True,
                            texto=f"{s['cliente_nombre']} · saldo actual {cfg.cop(s['saldo'])}")
        if not pagos:
            return
        try:
            nuevo = self.ctx.db.abonar_separado(s["id"], pagos, self.ctx.db.get_config("cajero_actual"))
        except POSError as e:
            error(self, str(e))
            return
        imprimir_separado(self.ctx, nuevo, "ABONO A SEPARADO", nuevo["ultimo_abono"],
                          abrir=any(m == "Efectivo" for m, _ in pagos))
        self.ctx.notificar(f"Abono de {cfg.cop(nuevo['ultimo_abono']['monto'])} · saldo {cfg.cop(nuevo['saldo'])}", "ok")
        self.ctx.bus.ventas_cambiadas.emit()
        if nuevo["saldo"] == 0 and confirmar(self, "Ya pagó todo. ¿Entregar las prendas ahora?"):
            self.actual = nuevo
            self.entregar()

    def entregar(self) -> None:
        s = self.actual
        if not s or not self._caja():
            return
        pagos, cambio = [], 0
        if s["saldo"] > 0:
            from ui.pagos import pedir_pagos
            entregados = pedir_pagos(self, f"Pagar saldo y entregar {s['numero']}", s["saldo"],
                                     texto="El cliente paga lo que falta y se lleva sus prendas.")
            if entregados is None:
                return
            pagos, cambio = aplicar_cambio(entregados, s["saldo"])
        elif not confirmar(self, f"¿Entregar las prendas del separado {s['numero']}?"):
            return
        try:
            venta = self.ctx.db.entregar_separado(s["id"], pagos, self.ctx.db.get_config("cajero_actual"))
        except POSError as e:
            error(self, str(e))
            return
        conf = self.ctx.conf
        tk = pe.construir_tiquete(venta, conf, abrir_cajon=False)
        despachar(self.ctx, tk, venta["numero_tiquete"], "ABONO", s["numero"],
                  abrir=any(m == "Efectivo" for m, _ in pagos))
        import ticket_pdf
        en_segundo_plano(lambda: ticket_pdf.guardar_venta(venta, conf))
        self.ctx.notificar(f"Entregado · tiquete {venta['numero_tiquete']}"
                           + (f" · CAMBIO {cfg.cop(cambio)}" if cambio else ""), "ok")
        self.ctx.bus.ventas_cambiadas.emit()
        self.ctx.bus.productos_cambiados.emit()

    def reimprimir(self) -> None:
        if self.actual:
            imprimir_separado(self.ctx, self.actual, "SEPARADO (COPIA)")

    def anular(self) -> None:
        s = self.actual
        if not s or not self._caja():
            return
        from ui.seguridad import autorizar
        dlg = AnularSeparadoDialog(s, self)
        if not dlg.exec():
            return
        if not autorizar(self, self.ctx, f"Anular el separado {s['numero']}"):
            return
        try:
            self.ctx.db.anular_separado(s["id"], dlg.motivo.currentText(), dlg.reintegro.valor(),
                                        dlg.metodo.currentText(), self.ctx.db.get_config("cajero_actual"))
        except POSError as e:
            error(self, str(e))
            return
        if dlg.reintegro.valor() and dlg.metodo.currentText() == "Efectivo":
            despachar(self.ctx, None, s["numero"], "DEVOLUCION", s["numero"], imprimir=False, abrir=True)
        self.ctx.notificar(f"Separado {s['numero']} anulado · las prendas volvieron al inventario", "ok")
        self.ctx.bus.ventas_cambiadas.emit()
        self.ctx.bus.productos_cambiados.emit()
