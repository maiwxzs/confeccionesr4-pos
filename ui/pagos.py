"""
ui/pagos.py — Ventana de cobro reutilizable: pago mixto, diferencia de un cambio, abonos de separados.

Una fila por medio de pago (Efectivo, Nequi, Bre-B, Datáfono). En el efectivo se escribe lo que
ENTREGÓ el cliente (el programa calcula el cambio); los demás medios deben ser exactos.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

import config as cfg
from ui.animations import DialogoAnimado
from ui.widgets import AlignRight, MoneyEdit, boton, error, etiqueta


class PagoDialog(DialogoAnimado):
    """
    total: lo que hay que pagar.  parcial=True (abonos): se puede pagar menos del total y no hay cambio.
    Resultado: self.pagos = [(método, valor)]  (efectivo = lo recibido, con cambio incluido).
    """

    def __init__(self, titulo: str, total: int, parent=None, parcial: bool = False, texto: str = "",
                 metodo_inicial: str | None = None):
        super().__init__(parent)
        self.total = int(total)
        self.parcial = parcial
        self.pagos: list[tuple[str, int]] = []
        self.setWindowTitle(titulo)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(etiqueta(titulo, titulo=True))
        cab = QHBoxLayout()
        cab.addWidget(etiqueta("SALDO A PAGAR" if parcial else "TOTAL A PAGAR", kpiTitulo=True))
        cab.addStretch()
        t = QLabel(cfg.cop(self.total))
        t.setObjectName("totalLabel")
        cab.addWidget(t)
        lay.addLayout(cab)
        if texto:
            n = etiqueta(texto, muted=True)
            n.setWordWrap(True)
            lay.addWidget(n)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.campos: dict[str, MoneyEdit] = {}
        for fila, m in enumerate(cfg.METODOS_PAGO):
            grid.addWidget(QLabel(m + (" (lo que entrega)" if m == "Efectivo" and not parcial else "")), fila, 0)
            campo = MoneyEdit(grande=False)
            campo.setMinimumHeight(40)
            campo.valorCambiado.connect(lambda _v: self._actualizar())
            self.campos[m] = campo
            grid.addWidget(campo, fila, 1)
            resto = boton("El resto", size="sm")
            resto.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            resto.clicked.connect(lambda _=False, mm=m: self._poner_resto(mm))
            grid.addWidget(resto, fila, 2)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        self.estado = QLabel("")
        self.estado.setAlignment(AlignRight)
        self.estado.setStyleSheet("font-size:20px;font-weight:900;")
        lay.addWidget(self.estado)

        fila_b = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        self.ok = boton("Registrar abono" if parcial else "COBRAR", "success", "lg")
        self.ok.clicked.connect(self._aceptar)
        fila_b.addWidget(c)
        fila_b.addStretch()
        fila_b.addWidget(self.ok)
        lay.addLayout(fila_b)
        inicial = metodo_inicial if metodo_inicial in self.campos else "Efectivo"
        if not parcial:
            self._poner_resto(inicial)
        self.campos[inicial].setFocus()
        self.campos[inicial].selectAll()
        self._actualizar()

    def _suma(self, sin: str | None = None) -> int:
        return sum(c.valor() for m, c in self.campos.items() if m != sin)

    def _poner_resto(self, metodo: str) -> None:
        resto = max(0, self.total - self._suma(sin=metodo))
        self.campos[metodo].setValor(resto)
        self.campos[metodo].setFocus()

    def _actualizar(self) -> None:
        efectivo = self.campos["Efectivo"].valor()
        otros = self._suma(sin="Efectivo")
        suma = efectivo + otros
        color = "#22C55E"
        if self.parcial:
            if suma > self.total:
                txt, color = f"Supera el saldo por {cfg.cop(suma - self.total)}", "#EF4444"
            elif suma == 0:
                txt, color = "Escriba el valor del abono", "#94A3B8"
            else:
                txt = f"Abono {cfg.cop(suma)} · queda debiendo {cfg.cop(self.total - suma)}"
        elif otros > self.total:
            txt, color = f"Nequi/Bre-B/Datáfono superan el total por {cfg.cop(otros - self.total)}", "#EF4444"
        elif suma < self.total:
            txt, color = f"FALTA {cfg.cop(self.total - suma)}", "#EF4444"
        else:
            txt = f"CAMBIO {cfg.cop(suma - self.total)}" if suma > self.total else "Pago completo"
        self.estado.setText(txt)
        self.estado.setStyleSheet(f"font-size:20px;font-weight:900;color:{color};")

    def _aceptar(self) -> None:
        pagos = [(m, c.valor()) for m, c in self.campos.items() if c.valor() > 0]
        suma = sum(v for _, v in pagos)
        otros = sum(v for m, v in pagos if m != "Efectivo")
        if self.parcial:
            if suma <= 0:
                error(self, "Escriba el valor del abono.")
                return
            if suma > self.total:
                error(self, f"El abono supera el saldo ({cfg.cop(self.total)}).")
                return
        else:
            if otros > self.total:
                error(self, "Lo pagado con Nequi, Bre-B o datáfono no puede superar el total.")
                return
            if suma < self.total:
                error(self, f"Faltan {cfg.cop(self.total - suma)} por pagar.")
                return
            if dict(pagos).get("Efectivo") and otros == self.total:
                error(self, "Con los otros medios ya se completó el total: borre el efectivo.")
                return
        self.pagos = pagos
        self.accept()


def pedir_pagos(parent, titulo: str, total: int, parcial: bool = False, texto: str = "",
                metodo_inicial: str | None = None) -> list[tuple[str, int]] | None:
    dlg = PagoDialog(titulo, total, parent, parcial, texto, metodo_inicial)
    return dlg.pagos if dlg.exec() else None
