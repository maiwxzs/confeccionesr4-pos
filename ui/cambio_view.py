"""
ui/cambio_view.py — Cambio de prenda en UN solo paso (Documentos › Cambio de prenda).

1. Se marca qué prendas del tiquete original devuelve el cliente.
2. Se escanean (o buscan) las prendas que se lleva.
3. El programa calcula la diferencia: si debe pagar, se cobra (cualquier medio o mixto); si queda a
   favor del cliente, se le devuelve por el medio elegido.
Se emite un tiquete nuevo con la nota «Cambio del tiquete X» y ambos documentos quedan trazados.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QHBoxLayout, QHeaderView, QLabel,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

import config as cfg
import printer_engine as pe
from database import ItemCarrito, POSError
from ui.animations import DialogoAnimado
from ui.cajon import despachar
from ui.widgets import AlignCenter, AlignRight, Contexto, boton, confirmar, en_segundo_plano, error, etiqueta


class CambioDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, venta: dict[str, Any], parent=None):
        super().__init__(parent)
        self.ctx, self.venta = ctx, venta
        self.nuevos: list[dict[str, Any]] = []
        self.resultado: dict[str, Any] | None = None
        self.setWindowTitle(f"Cambio de prenda — {venta['numero_tiquete']}")
        self.setMinimumSize(820, 600)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(etiqueta(f"Cambio de prenda · tiquete {venta['numero_tiquete']}", titulo=True))

        # 1. Lo que devuelve
        lay.addWidget(etiqueta("1. ¿QUÉ DEVUELVE?", kpiTitulo=True))
        self.dev = QTableWidget(len(venta["items"]), 4)
        self.dev.setHorizontalHeaderLabels(["Prenda", "Compró", "Devuelve", "Valor"])
        h = self.dev.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.dev.verticalHeader().setVisible(False)
        self.dev.verticalHeader().setDefaultSectionSize(42)
        self.dev.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.dev.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.spins: dict[int, QSpinBox] = {}
        self.lbl_val: dict[int, QLabel] = {}
        for i, it in enumerate(venta["items"]):
            pendiente = it["cantidad"] - it["cantidad_devuelta"]
            self.dev.setItem(i, 0, QTableWidgetItem(it["nombre"]))
            c = QTableWidgetItem(str(pendiente))
            c.setTextAlignment(AlignCenter)
            self.dev.setItem(i, 1, c)
            sp = QSpinBox()
            sp.setRange(0, pendiente)
            sp.setEnabled(pendiente > 0)
            sp.setValue(pendiente if len(venta["items"]) == 1 else 0)
            sp.valueChanged.connect(self._recalcular)
            self.spins[it["id"]] = sp
            self.dev.setCellWidget(i, 2, sp)
            lbl = QLabel("$ 0")
            lbl.setAlignment(AlignRight)
            lbl.setContentsMargins(0, 0, 8, 0)
            self.lbl_val[it["id"]] = lbl
            self.dev.setCellWidget(i, 3, lbl)
        self.dev.setMinimumHeight(110)
        lay.addWidget(self.dev, 1)

        # 2. Lo que se lleva
        lay.addWidget(etiqueta("2. ¿QUÉ SE LLEVA? (escanee o busque)", kpiTitulo=True))
        fila_b = QHBoxLayout()
        self.prod = QComboBox()
        self.prod.setEditable(True)
        self.prod.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.prod.setMinimumHeight(42)
        self.prod.lineEdit().setPlaceholderText("Código de barras o nombre de la prenda")
        for p in ctx.db.listar_productos():
            self.prod.addItem(f"{p['nombre']}  ·  {p['codigo']}  ·  {cfg.cop(p['precio'])}  ·  stock {p['stock']}",
                              p["id"])
        self.prod.setCurrentIndex(-1)
        comp = self.prod.completer()
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.prod.lineEdit().returnPressed.connect(self._agregar)
        fila_b.addWidget(self.prod, 1)
        b_add = boton("Agregar", "primary")
        b_add.clicked.connect(self._agregar)
        fila_b.addWidget(b_add)
        lay.addLayout(fila_b)
        self.lleva = QTableWidget(0, 4)
        self.lleva.setHorizontalHeaderLabels(["Prenda", "Cant.", "Valor", ""])
        h2 = self.lleva.horizontalHeader()
        h2.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h2.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.lleva.verticalHeader().setVisible(False)
        self.lleva.verticalHeader().setDefaultSectionSize(42)
        self.lleva.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lleva.setMinimumHeight(100)
        lay.addWidget(self.lleva, 1)

        # 3. Diferencia
        self.lbl_resumen = etiqueta("", muted=True)
        lay.addWidget(self.lbl_resumen)
        fila_t = QHBoxLayout()
        self.lbl_dif_t = etiqueta("DIFERENCIA", titulo=True)
        fila_t.addWidget(self.lbl_dif_t)
        self.lbl_dif = QLabel("$ 0")
        self.lbl_dif.setObjectName("totalLabel")
        self.lbl_dif.setAlignment(AlignRight)
        fila_t.addWidget(self.lbl_dif, 1)
        lay.addLayout(fila_t)
        fila_r = QHBoxLayout()
        self.lbl_re = etiqueta("Devolver la diferencia por:")
        fila_r.addWidget(self.lbl_re)
        self.reintegro = QComboBox()
        self.reintegro.addItems(cfg.METODOS_PAGO)
        fila_r.addWidget(self.reintegro)
        fila_r.addStretch()
        lay.addLayout(fila_r)
        self.motivo = QComboBox()
        self.motivo.setEditable(True)
        self.motivo.addItems(["Talla", "Color", "No le gustó", "Defecto de fábrica", "Regalo"])
        self.motivo.setMinimumHeight(40)
        fila_m = QHBoxLayout()
        fila_m.addWidget(etiqueta("Motivo:"))
        fila_m.addWidget(self.motivo, 1)
        lay.addLayout(fila_m)

        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        self.ok = boton("HACER EL CAMBIO", "success", "lg")
        self.ok.clicked.connect(self._confirmar)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(self.ok)
        lay.addLayout(fila)
        self._recalcular()
        self.prod.setFocus()

    # ------------------------------------------------------------------ cálculo
    def _credito(self) -> int:
        total = 0
        for it in self.venta["items"]:
            n = self.spins[it["id"]].value()
            pendiente = it["cantidad"] - it["cantidad_devuelta"]
            if n == pendiente and n > 0:
                monto = it["total"] - it["total_devuelto"]
            else:
                monto = min(int(round(it["total"] * n / it["cantidad"] / 50.0)) * 50, it["total"] - it["total_devuelto"])
            self.lbl_val[it["id"]].setText(cfg.cop(monto))
            total += monto
        return total

    def _nuevo_total(self) -> int:
        return sum(n["precio"] * n["cantidad"] for n in self.nuevos)

    def _recalcular(self, *_a) -> None:
        credito, nuevo = self._credito(), self._nuevo_total()
        dif = nuevo - credito
        self.lbl_resumen.setText(f"Devuelve {cfg.cop(credito)}  ·  Se lleva {cfg.cop(nuevo)}")
        if dif > 0:
            self.lbl_dif_t.setText("EL CLIENTE PAGA")
            self.lbl_dif.setStyleSheet("color:#22C55E;")
        elif dif < 0:
            self.lbl_dif_t.setText("SE LE DEVUELVE")
            self.lbl_dif.setStyleSheet("color:#F59E0B;")
        else:
            self.lbl_dif_t.setText("SIN DIFERENCIA")
            self.lbl_dif.setStyleSheet("")
        self.lbl_dif.setText(cfg.cop(abs(dif)))
        self.lbl_re.setVisible(dif < 0)
        self.reintegro.setVisible(dif < 0)
        self.ok.setEnabled(credito > 0 and bool(self.nuevos))

    # ------------------------------------------------------------------ prendas nuevas
    def _agregar(self) -> None:
        texto = self.prod.currentText().strip()
        p = None
        if texto:
            p = self.ctx.db.producto_por_codigo(texto)
        if p is None:
            pid = self.prod.currentData()
            if pid is not None and self.prod.currentIndex() >= 0 and self.prod.itemText(self.prod.currentIndex()) == texto:
                p = self.ctx.db.producto_por_id(int(pid))
        if p is None:
            error(self, "No encontré esa prenda. Escanee el código o elíjala de la lista.")
            return
        for n in self.nuevos:
            if n["producto_id"] == p["id"]:
                n["cantidad"] += 1
                break
        else:
            self.nuevos.append({"producto_id": p["id"], "nombre": p["nombre"], "precio": p["precio"], "cantidad": 1})
        self.prod.setCurrentIndex(-1)
        self.prod.clearEditText()
        self._pintar_nuevos()

    def _pintar_nuevos(self) -> None:
        self.lleva.setRowCount(len(self.nuevos))
        for r, n in enumerate(self.nuevos):
            self.lleva.setItem(r, 0, QTableWidgetItem(n["nombre"]))
            sp = QSpinBox()
            sp.setRange(1, 99)
            sp.setValue(n["cantidad"])
            sp.valueChanged.connect(lambda v, i=r: self._cant(i, v))
            self.lleva.setCellWidget(r, 1, sp)
            v = QTableWidgetItem(cfg.cop(n["precio"] * n["cantidad"]))
            v.setTextAlignment(int(AlignRight))
            self.lleva.setItem(r, 2, v)
            x = boton("Quitar", **{"del": True})
            x.clicked.connect(lambda _=False, i=r: self._quitar(i))
            self.lleva.setCellWidget(r, 3, x)
        self._recalcular()

    def _cant(self, i: int, v: int) -> None:
        self.nuevos[i]["cantidad"] = v
        self.lleva.setItem(i, 2, QTableWidgetItem(cfg.cop(self.nuevos[i]["precio"] * v)))
        self._recalcular()

    def _quitar(self, i: int) -> None:
        del self.nuevos[i]
        self._pintar_nuevos()

    # ------------------------------------------------------------------ confirmar
    def _confirmar(self) -> None:
        cantidades = {did: sp.value() for did, sp in self.spins.items() if sp.value() > 0}
        credito, nuevo = self._credito(), self._nuevo_total()
        dif = nuevo - credito
        pagos = None
        if dif > 0:
            from ui.pagos import pedir_pagos
            pagos = pedir_pagos(self, "Cobrar la diferencia", dif,
                                texto=f"Devuelve {cfg.cop(credito)} y se lleva {cfg.cop(nuevo)}.")
            if pagos is None:
                return
        elif not confirmar(self, f"¿Hacer el cambio?\n\nDevuelve {cfg.cop(credito)} · se lleva {cfg.cop(nuevo)}"
                                 + (f"\nSe le devuelven {cfg.cop(-dif)} por {self.reintegro.currentText()}." if dif < 0 else "")):
            return
        try:
            self.resultado = self.ctx.db.registrar_cambio(
                self.venta["numero_tiquete"], cantidades,
                [ItemCarrito(n["producto_id"], n["cantidad"], n["precio"]) for n in self.nuevos],
                self.motivo.currentText(), self.ctx.db.get_config("cajero_actual"), pagos,
                self.reintegro.currentText())
        except POSError as e:
            error(self, str(e))
            return
        conf = self.ctx.conf
        nueva = self.resultado
        info = nueva["cambio_info"]
        mueve_efectivo = any(m == "Efectivo" for m, _ in info["pagos_diferencia"]) or \
            (info["diferencia"] < 0 and info["metodo_reintegro"] == "Efectivo")
        despachar(self.ctx, pe.construir_tiquete(nueva, conf, abrir_cajon=False), nueva["numero_tiquete"], "CAMBIO",
                  nueva["numero_tiquete"], abrir=mueve_efectivo)
        import ticket_pdf
        en_segundo_plano(lambda: ticket_pdf.guardar_venta(nueva, conf))
        self.accept()
