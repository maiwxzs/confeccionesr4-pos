"""
ui/dashboard_view.py — MÓDULO 4: Dashboard Ejecutivo y Métricas de Rentabilidad.

KPIs: Ventas Totales, Utilidad Neta Real (precio − costo), Cash en Caja Esperado, Devoluciones.
Gráficos interactivos (Qt Charts, con tooltips al pasar el mouse):
  1. Barras: ventas por hora del día.
  2. Circular: % de ingresos por método de pago.
  3. Top 5 prendas con mayor rotación.
Exportación contable completa a Excel del periodo seleccionado.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QEasingCurve, QMargins, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QDateEdit, QGridLayout, QHBoxLayout, QLabel, QToolTip,
                               QVBoxLayout, QWidget)

import config as cfg
from database import rango_periodo
from ui import theme
from ui.animations import escalonado
from ui.widgets import Contexto, KpiCard, boton, etiqueta, exportar_y_ofrecer_abrir, tarjeta

try:
    from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView,
                                  QHorizontalBarSeries, QPieSeries, QValueAxis)
    HAY_CHARTS = True
except ImportError:  # pragma: no cover
    HAY_CHARTS = False

COLORES_METODO = cfg.COLORES_METODO
PERIODOS = [("Hoy", "hoy"), ("Ayer", "ayer"), ("Últimos 7 días", "7d"), ("Últimos 30 días", "30d"),
            ("Este mes", "mes"), ("Este año", "anio"), ("Personalizado…", "custom")]


class DashboardView(QWidget):
    titulo = "Dashboard Ejecutivo"

    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 20)
        raiz.setSpacing(14)

        # --- Barra de periodo ---
        barra = QHBoxLayout()
        barra.addWidget(etiqueta("Periodo:", titulo=True))
        self.periodo = QComboBox()
        self.periodo.setMinimumHeight(42)
        self.periodo.setMinimumWidth(190)
        for txt, clave in PERIODOS:
            self.periodo.addItem(txt, clave)
        barra.addWidget(self.periodo)
        self.desde = QDateEdit(QDate.currentDate())
        self.hasta = QDateEdit(QDate.currentDate())
        for d in (self.desde, self.hasta):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd/MM/yyyy")
            d.setMinimumHeight(42)
            d.setVisible(False)
            d.dateChanged.connect(lambda *_: self.refrescar())
        barra.addWidget(self.desde)
        barra.addWidget(self.hasta)
        self.lbl_rango = etiqueta("", muted=True)
        barra.addWidget(self.lbl_rango)
        barra.addStretch()
        b_act = boton("Actualizar")
        b_act.clicked.connect(self.refrescar)
        b_exp = boton("Exportación contable a Excel", "primary")
        b_exp.clicked.connect(self.exportar)
        b_rep = boton("Reportes")
        b_rep.setToolTip("Mercancía quieta, ventas por talla/color/categoría, comparativo mensual, sugerido de compra")
        b_rep.clicked.connect(self._reportes)
        barra.addWidget(b_act)
        barra.addWidget(b_rep)
        barra.addWidget(b_exp)
        raiz.addLayout(barra)

        # --- KPIs ---
        kpis = QHBoxLayout()
        kpis.setSpacing(14)
        T = theme.T
        self.k_ventas = KpiCard("Ventas totales", T["primary"])
        self.k_util = KpiCard("Utilidad neta real", T["success"])
        self.k_cash = KpiCard("Cash en caja esperado", T["warning"])
        self.k_dev = KpiCard("Devoluciones", T["danger"])
        for k in (self.k_ventas, self.k_util, self.k_cash, self.k_dev):
            kpis.addWidget(k)
        raiz.addLayout(kpis)

        # --- Gráficos ---
        grid = QGridLayout()
        grid.setSpacing(14)
        if HAY_CHARTS:
            self.v_horas = self._chart_view("Ventas por hora del día")
            self.v_metodos = self._chart_view("Ingresos por método de pago")
            self.v_top = self._chart_view("Top 5 prendas de mayor rotación")
            grid.addWidget(self._envolver(self.v_horas), 0, 0, 1, 2)
            grid.addWidget(self._envolver(self.v_metodos), 1, 0)
            grid.addWidget(self._envolver(self.v_top), 1, 1)
        else:
            grid.addWidget(QLabel("Qt Charts no está disponible en esta instalación."), 0, 0)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        raiz.addLayout(grid, 1)

        self.periodo.currentIndexChanged.connect(self._al_cambiar_periodo)
        for s in (ctx.bus.ventas_cambiadas, ctx.bus.caja_cambiada, ctx.bus.config_cambiada):
            s.connect(self._refrescar_si_visible)

    # ------------------------------------------------------------------ helpers
    def _chart_view(self, titulo: str):
        chart = QChart()
        chart.setTitle(titulo)
        chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        chart.setAnimationDuration(700)
        chart.setAnimationEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        chart.setMargins(QMargins(6, 6, 6, 6))
        v = QChartView(chart)
        v.setRenderHint(QPainter.RenderHint.Antialiasing)
        v.setMinimumHeight(240)
        return v

    @staticmethod
    def _envolver(w):
        t = tarjeta()
        l = QVBoxLayout(t)
        l.setContentsMargins(6, 6, 6, 6)
        l.addWidget(w)
        return t

    def _estilizar(self, chart) -> None:
        T = theme.T
        chart.setBackgroundBrush(QColor(T["card"]))
        chart.setBackgroundRoundness(12)
        chart.setPlotAreaBackgroundVisible(False)
        f = QFont("Segoe UI", 11)
        f.setBold(True)
        chart.setTitleFont(f)
        chart.setTitleBrush(QColor(T["text"]))
        chart.legend().setLabelColor(QColor(T["text"]))
        for eje in chart.axes():
            eje.setLabelsColor(QColor(T["muted"]))
            eje.setGridLineColor(QColor(T["border"]))
            eje.setLinePen(QPen(QColor(T["border"])))

    def _rango(self) -> tuple[str, str]:
        clave = self.periodo.currentData()
        if clave == "custom":
            d1, d2 = self.desde.date().toPython(), self.hasta.date().toPython()
            if d1 > d2:
                d1, d2 = d2, d1
            return f"{d1:%Y-%m-%d} 00:00:00", f"{d2:%Y-%m-%d} 23:59:59"
        return rango_periodo(clave)

    def _al_cambiar_periodo(self) -> None:
        custom = self.periodo.currentData() == "custom"
        self.desde.setVisible(custom)
        self.hasta.setVisible(custom)
        self.refrescar()

    def _refrescar_si_visible(self) -> None:
        if self.isVisible():
            self.refrescar()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        for k in (self.k_ventas, self.k_util, self.k_cash, self.k_dev):
            k.lbl_valor.setProperty("_valor_anim", 0)   # cada visita: los números cuentan desde 0
        self.refrescar()
        escalonado([self.k_ventas, self.k_util, self.k_cash, self.k_dev], paso=60)

    # ------------------------------------------------------------------ datos
    def refrescar(self) -> None:
        db = self.ctx.db
        desde, hasta = self._rango()
        self.lbl_rango.setText(f"{desde[8:10]}/{desde[5:7]}/{desde[:4]} – {hasta[8:10]}/{hasta[5:7]}/{hasta[:4]}")
        k = db.kpis(desde, hasta)
        anul = f" · {k['num_anuladas']} anulados" if k["num_anuladas"] else ""
        self.k_ventas.set_monto(k["ventas_netas"], f"{k['num_ventas']} ventas · {k['unidades']} prendas{anul}")
        margen = (k["utilidad_neta"] / k["ventas_netas"] * 100) if k["ventas_netas"] else 0
        self.k_util.set_monto(k["utilidad_neta"], f"Margen {margen:.1f} % · Tiquete prom. "
                                                  f"{cfg.cop(k['ticket_promedio'])}")
        if k["caja_abierta"]:
            self.k_cash.set_monto(k["efectivo_esperado"], "Caja abierta (base + efectivo − devol.)")
        else:
            self.k_cash.set_valor("—", "Caja cerrada")
        self.k_dev.set_monto(k["devoluciones"], f"{k['num_devoluciones']} devoluciones / anulaciones")
        if HAY_CHARTS:
            self._grafico_horas(db.ventas_por_hora(desde, hasta))
            self._grafico_metodos(db.ventas_por_metodo(desde, hasta))
            self._grafico_top(db.top_productos(desde, hasta, 5))

    def _grafico_horas(self, horas: dict[int, int]) -> None:
        chart = self.v_horas.chart()
        chart.removeAllSeries()
        for eje in chart.axes():
            chart.removeAxis(eje)
        con_venta = [h for h, v in horas.items() if v]
        ini = min([8] + con_venta)
        fin = max([20] + con_venta)
        rango = list(range(ini, fin + 1))
        s = QBarSet("Ventas")
        s.setColor(QColor(theme.T["primary"]))
        s.setBorderColor(QColor(theme.T["primary"]))
        for h in rango:
            s.append(horas[h] / 1000)  # eje en miles de pesos (Qt no admite separador de miles)
        serie = QBarSeries()
        serie.append(s)
        serie.setBarWidth(0.7)
        chart.addSeries(serie)
        etiquetas = [f"{h % 12 or 12}{'am' if h < 12 else 'pm'}" for h in rango]
        ax = QBarCategoryAxis()
        ax.append(etiquetas)
        ay = QValueAxis()
        maximo = max([horas[h] / 1000 for h in rango] + [1])
        ay.setRange(0, maximo * 1.15)
        ay.setLabelFormat("%.0f")
        ay.setTickCount(5)
        chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
        serie.attachAxis(ax)
        serie.attachAxis(ay)
        chart.legend().setVisible(False)

        def tip(estado: bool, idx: int):
            if estado:
                h = rango[idx]
                QToolTip.showText(QCursor.pos(), f"{h:02d}:00 – {h:02d}:59\n{cfg.cop(horas[h])}")
            else:
                QToolTip.hideText()
        s.hovered.connect(tip)
        ay.setTitleText("miles de $")
        self._estilizar(chart)
        ay.setTitleBrush(QColor(theme.T["muted"]))

    def _grafico_metodos(self, metodos: dict[str, int]) -> None:
        chart = self.v_metodos.chart()
        chart.removeAllSeries()
        total = sum(metodos.values())
        serie = QPieSeries()
        serie.setHoleSize(0.42)
        if total == 0:
            sl = serie.append("Sin ventas", 1)
            sl.setColor(QColor(theme.T["border"]))
            sl.setLabelVisible(True)
            sl.setLabelColor(QColor(theme.T["muted"]))
        else:
            for m, v in metodos.items():
                if v <= 0:
                    continue
                sl = serie.append(f"{m} {v / total * 100:.0f}%", v)
                sl.setColor(QColor(COLORES_METODO.get(m, "#64748B")))
                sl.setBorderColor(QColor(theme.T["card"]))
                sl.setBorderWidth(2)
                sl.setLabelVisible(True)
                sl.setLabelColor(QColor(theme.T["text"]))

                def tip(estado: bool, sl=sl, m=m, v=v):
                    sl.setExploded(estado)
                    if estado:
                        QToolTip.showText(QCursor.pos(), f"{m}\n{cfg.cop(v)} ({v / total * 100:.1f} %)")
                    else:
                        QToolTip.hideText()
                sl.hovered.connect(tip)
        chart.addSeries(serie)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
        self._estilizar(chart)

    def _grafico_top(self, top) -> None:
        chart = self.v_top.chart()
        chart.removeAllSeries()
        for eje in chart.axes():
            chart.removeAxis(eje)
        filas = list(reversed([dict(r) for r in top]))  # el más vendido arriba
        s = QBarSet("Unidades")
        s.setColor(QColor(theme.T["success"]))
        s.setBorderColor(QColor(theme.T["success"]))
        nombres = []
        for r in filas:
            s.append(r["unidades"])
            n = r["nombre"]
            nombres.append(n if len(n) <= 26 else n[:25] + "…")
        if not filas:
            nombres = ["Sin ventas en el periodo"]
            s.append(0)
        serie = QHorizontalBarSeries()
        serie.append(s)
        serie.setBarWidth(0.65)
        serie.setLabelsVisible(True)
        serie.setLabelsFormat("@value und")
        chart.addSeries(serie)
        ay = QBarCategoryAxis()
        ay.append(nombres)
        ax = QValueAxis()
        ax.setRange(0, max([r["unidades"] for r in filas] + [1]) * 1.25)
        ax.setLabelFormat("%.0f")
        chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
        chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        serie.attachAxis(ay)
        serie.attachAxis(ax)
        chart.legend().setVisible(False)

        def tip(estado: bool, idx: int):
            if estado and filas:
                r = filas[idx]
                QToolTip.showText(QCursor.pos(), f"{r['nombre']}\n{r['unidades']} und · {cfg.cop(r['ingresos'])}"
                                                 f"\nUtilidad {cfg.cop(r['utilidad'])}")
            else:
                QToolTip.hideText()
        s.hovered.connect(tip)
        self._estilizar(chart)

    # ------------------------------------------------------------------ export
    def _reportes(self) -> None:
        from ui.reportes_view import ReportesDialog
        ReportesDialog(self.ctx, self).exec()

    def exportar(self) -> None:
        desde, hasta = self._rango()
        nombre = f"Reporte_Ventas_{desde[:10]}_a_{hasta[:10]}.xlsx"
        import excel_engine as xe   # carga diferida: el Dashboard abre sin cargar openpyxl/numpy
        exportar_y_ofrecer_abrir(self, self.ctx, nombre,
                                 lambda ruta: xe.exportar_ventas(self.ctx.db, desde, hasta, ruta))
