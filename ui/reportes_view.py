"""
ui/reportes_view.py — Reportes de gestión (Inventario › Reportes y Dashboard › Reportes).

  · Mercancía quieta: qué no se vende hace N días (para promociones / liquidar).
  · Ventas por categoría, talla, color o marca: qué se vende y cuánto deja.
  · Comparativo mensual: ventas, tiquetes, unidades y variación contra el mes anterior.
  · Sugerido de compra: qué reponer según el ritmo de venta y los días que se quieren cubrir.
Todo se puede exportar a un solo Excel con formato ejecutivo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

import config as cfg
import excel_engine as xe
from database import rango_periodo
from ui.animations import DialogoAnimado
from ui.widgets import AlignRight, Contexto, boton, etiqueta, exportar_y_ofrecer_abrir

PERIODOS = [("Este mes", "mes"), ("Mes anterior", "mes_anterior"), ("Últimos 30 días", "30d"),
            ("Últimos 90 días", "90d"), ("Este año", "anio")]


def _rango(clave: str) -> tuple[str, str]:
    hoy = date.today()
    if clave == "mes_anterior":
        fin = hoy.replace(day=1) - timedelta(days=1)
        return f"{fin:%Y-%m}-01 00:00:00", f"{fin:%Y-%m-%d} 23:59:59"
    if clave == "90d":
        return f"{hoy - timedelta(days=89):%Y-%m-%d} 00:00:00", f"{hoy:%Y-%m-%d} 23:59:59"
    return rango_periodo(clave)


def _tabla(columnas: list[str], estirar: int = 1) -> QTableWidget:
    t = QTableWidget(0, len(columnas))
    t.setHorizontalHeaderLabels(columnas)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    h.setSectionResizeMode(estirar, QHeaderView.ResizeMode.Stretch)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setSortingEnabled(False)
    return t


def _llenar(t: QTableWidget, filas: list[list], derecha: set[int], colores: dict[tuple[int, int], str] | None = None):
    t.setRowCount(len(filas))
    for r, fila in enumerate(filas):
        for c, v in enumerate(fila):
            it = QTableWidgetItem("" if v is None else str(v))
            if c in derecha:
                it.setTextAlignment(int(AlignRight))
            if colores and (r, c) in colores:
                it.setForeground(QColor(colores[(r, c)]))
            t.setItem(r, c, it)


class ReportesDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Reportes de gestión")
        self.resize(1060, 700)
        lay = QVBoxLayout(self)
        cab = QHBoxLayout()
        cab.addWidget(etiqueta("Reportes de gestión", titulo=True))
        cab.addStretch()
        b_exp = boton("Exportar todo a Excel", "success")
        b_exp.clicked.connect(self._exportar)
        cab.addWidget(b_exp)
        lay.addLayout(cab)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_quieta(), "Mercancía quieta")
        self.tabs.addTab(self._tab_dimension(), "Ventas por categoría / talla / color")
        self.tabs.addTab(self._tab_comparativo(), "Comparativo mensual")
        self.tabs.addTab(self._tab_sugerido(), "Sugerido de compra")
        lay.addWidget(self.tabs, 1)
        cerrar = boton("Cerrar", "ghost", "lg")
        cerrar.clicked.connect(self.accept)
        fila = QHBoxLayout()
        fila.addStretch()
        fila.addWidget(cerrar)
        lay.addLayout(fila)
        self._quieta()
        self._dimension()
        self._comparativo()
        self._sugerido()

    # ------------------------------------------------------------------ mercancía quieta
    def _tab_quieta(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        f = QHBoxLayout()
        f.addWidget(etiqueta("Sin vender hace al menos"))
        self.dias_q = QSpinBox()
        self.dias_q.setRange(7, 720)
        self.dias_q.setValue(60)
        self.dias_q.setSuffix(" días")
        self.dias_q.valueChanged.connect(lambda _: self._quieta())
        f.addWidget(self.dias_q)
        f.addStretch()
        v.addLayout(f)
        self.t_q = _tabla(["Código", "Prenda", "Categoría", "Talla", "Color", "Stock", "Valor a costo",
                           "Última venta", "Días sin vender"])
        v.addWidget(self.t_q, 1)
        self.res_q = etiqueta("", muted=True)
        v.addWidget(self.res_q)
        return w

    def _quieta(self) -> None:
        filas = self.ctx.db.mercancia_quieta(self.dias_q.value())
        _llenar(self.t_q, [[f["codigo"], f["nombre"], f["categoria"], f["talla"], f["color"], f["stock"],
                            cfg.cop(f["valor_costo"]), f["ultima_venta"], f["dias_sin_venta"]] for f in filas],
                {5, 6, 8})
        self.res_q.setText(f"{len(filas)} referencias · {sum(f['stock'] for f in filas)} prendas quietas · "
                           f"{cfg.cop(sum(f['valor_costo'] for f in filas))} a costo "
                           f"({cfg.cop(sum(f['valor_venta'] for f in filas))} a precio de venta). "
                           "Buenas candidatas para promoción.")

    # ------------------------------------------------------------------ ventas por dimensión
    def _tab_dimension(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        f = QHBoxLayout()
        self.dim = QComboBox()
        for k, n in xe.DIMENSIONES.items():
            self.dim.addItem(f"Por {n.lower()}", k)
        self.periodo = QComboBox()
        for n, k in PERIODOS:
            self.periodo.addItem(n, k)
        for cb in (self.dim, self.periodo):
            cb.setMinimumHeight(40)
            cb.currentIndexChanged.connect(lambda _: self._dimension())
            f.addWidget(cb)
        f.addStretch()
        v.addLayout(f)
        self.t_d = _tabla(["", "Unidades", "Ventas netas", "Utilidad", "Margen", "Participación", "Tiquetes"], 0)
        v.addWidget(self.t_d, 1)
        self.res_d = etiqueta("", muted=True)
        v.addWidget(self.res_d)
        return w

    def _dimension(self) -> None:
        desde, hasta = _rango(self.periodo.currentData())
        filas = self.ctx.db.ventas_por_dimension(self.dim.currentData(), desde, hasta)
        self.t_d.setHorizontalHeaderItem(0, QTableWidgetItem(xe.DIMENSIONES[self.dim.currentData()]))
        _llenar(self.t_d, [[f["clave"], f["unidades"], cfg.cop(f["ingresos"]), cfg.cop(f["utilidad"]),
                            f"{f['margen']} %", f"{f['participacion']} %", f["tiquetes"]] for f in filas],
                {1, 2, 3, 4, 5, 6})
        if filas:
            top = filas[0]
            self.res_d.setText(f"Lo que más vende: {top['clave']} ({top['participacion']} % de las ventas, "
                               f"{top['unidades']} prendas). Total {cfg.cop(sum(f['ingresos'] for f in filas))}.")
        else:
            self.res_d.setText("Sin ventas en este periodo.")

    # ------------------------------------------------------------------ comparativo
    def _tab_comparativo(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        f = QHBoxLayout()
        f.addWidget(etiqueta("Meses a comparar"))
        self.meses = QSpinBox()
        self.meses.setRange(2, 36)
        self.meses.setValue(12)
        self.meses.valueChanged.connect(lambda _: self._comparativo())
        f.addWidget(self.meses)
        f.addStretch()
        v.addLayout(f)
        self.t_c = _tabla(["Mes", "Tiquetes", "Unidades", "Ventas netas", "Utilidad", "Tiquete promedio",
                           "Vs. mes anterior"], 0)
        v.addWidget(self.t_c, 1)
        return w

    def _comparativo(self) -> None:
        filas = self.ctx.db.comparativo_mensual(self.meses.value())
        colores = {}
        datos = []
        for r, f in enumerate(filas):
            var = f["variacion"]
            if var is not None:
                colores[(r, 6)] = "#22C55E" if var >= 0 else "#EF4444"
            datos.append([f["mes"], f["tiquetes"], f["unidades"], cfg.cop(f["ventas"]), cfg.cop(f["utilidad"]),
                          cfg.cop(f["ticket_promedio"]), "—" if var is None else f"{var:+.1f} %"])
        _llenar(self.t_c, datos, {1, 2, 3, 4, 5, 6}, colores)

    # ------------------------------------------------------------------ sugerido
    def _tab_sugerido(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        f = QHBoxLayout()
        f.addWidget(etiqueta("Analizar ventas de los últimos"))
        self.dias_s = QSpinBox()
        self.dias_s.setRange(7, 365)
        self.dias_s.setValue(30)
        self.dias_s.setSuffix(" días")
        f.addWidget(self.dias_s)
        f.addWidget(etiqueta("y tener mercancía para"))
        self.cobertura = QSpinBox()
        self.cobertura.setRange(7, 180)
        self.cobertura.setValue(30)
        self.cobertura.setSuffix(" días")
        f.addWidget(self.cobertura)
        for sp in (self.dias_s, self.cobertura):
            sp.valueChanged.connect(lambda _: self._sugerido())
        f.addStretch()
        v.addLayout(f)
        self.t_s = _tabla(["Código", "Prenda", "Talla", "Color", "Vendidas", "Stock", "Días de inventario",
                           "Pedir", "Costo estimado"])
        v.addWidget(self.t_s, 1)
        self.res_s = etiqueta("", muted=True)
        v.addWidget(self.res_s)
        return w

    def _sugerido(self) -> None:
        filas = self.ctx.db.sugerido_compra(self.dias_s.value(), self.cobertura.value())
        colores = {(r, 6): "#EF4444" for r, f in enumerate(filas) if (f["dias_inventario"] or 0) < 7}
        _llenar(self.t_s, [[f["codigo"], f["nombre"], f["talla"], f["color"], f["vendidas"], f["stock"],
                            f["dias_inventario"], f["sugerido"], cfg.cop(f["costo_estimado"])] for f in filas],
                {4, 5, 6, 7, 8}, colores)
        self.res_s.setText(f"Pedir {sum(f['sugerido'] for f in filas)} prendas de {len(filas)} referencias · "
                           f"inversión estimada {cfg.cop(sum(f['costo_estimado'] for f in filas))}. "
                           "En rojo: se agotan en menos de una semana.")

    # ------------------------------------------------------------------ exportar
    def _exportar(self) -> None:
        clave = self.periodo.currentData()
        desde, hasta = _rango(clave)
        nombre = self.periodo.currentText()
        params = dict(dias_quieta=self.dias_q.value(), dias_analisis=self.dias_s.value(),
                      cobertura=self.cobertura.value(), meses=self.meses.value())
        exportar_y_ofrecer_abrir(self, self.ctx, f"Reportes_Gestion_{datetime.now():%Y%m%d_%H%M}.xlsx",
                                 lambda ruta: xe.exportar_reportes(self.ctx.db, ruta, desde, hasta, nombre, **params))
