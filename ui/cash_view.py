"""
ui/cash_view.py — MÓDULO 3: Arqueo y Cierre de Caja.

* Apertura con base inicial en efectivo (fondo de sencillo).
* Control de turno con acumulados por método de pago.
* Arqueo CIEGO: el cajero cuenta billetes y monedas sin ver el valor esperado; el sistema
  compara y emite el resultado: CUADRE PERFECTO, SOBRANTE o FALTANTE (impreso + Excel).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QScrollArea, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import config as cfg
import excel_engine as xe
import printer_engine as pe
from database import POSError
from ui import theme
from ui.animations import DialogoAnimado, escalonado
from ui.widgets import (AlignCenter, AlignRight, Contexto, KpiCard, MoneyEdit, boton, confirmar,
                        en_segundo_plano, error, etiqueta, exportar_y_ofrecer_abrir, tarjeta)


def _fecha(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %I:%M %p")
    except ValueError:
        return iso


# =========================================================================== #
# Apertura
# =========================================================================== #
class AperturaDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Apertura de caja")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(etiqueta("Apertura de caja", titulo=True))
        lay.addWidget(etiqueta("Registre el dinero en efectivo con el que inicia el turno (fondo de sencillo).",
                               muted=True))
        lay.addWidget(etiqueta("Cajero"))
        self.cajero = QLineEdit(ctx.db.get_config("cajero_actual"))
        lay.addWidget(self.cajero)
        lay.addWidget(etiqueta("Base inicial en efectivo"))
        self.base = MoneyEdit()
        lay.addWidget(self.base)
        rap = QHBoxLayout()
        for v in (0, 50_000, 100_000, 200_000):
            b = boton(cfg.cop(v), size="lg")
            b.clicked.connect(lambda _=False, val=v: self.base.setValor(val))
            rap.addWidget(b)
        lay.addLayout(rap)
        fila = QHBoxLayout()
        cancelar = boton("Cancelar", "ghost", "lg")
        cancelar.clicked.connect(self.reject)
        ok = boton("ABRIR CAJA", "success", "lg")
        ok.clicked.connect(self.abrir)
        fila.addWidget(cancelar)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)
        self.base.returnPressed.connect(self.abrir)
        self.base.setFocus()

    def abrir(self) -> None:
        cajero = self.cajero.text().strip() or "Cajero"
        try:
            self.ctx.db.abrir_caja(self.base.valor(), cajero)
        except POSError as e:
            error(self, str(e))
            return
        self.ctx.db.set_config({"cajero_actual": cajero})
        self.ctx.notificar(f"Caja abierta con base {cfg.cop(self.base.valor())}", "ok")
        self.ctx.bus.caja_cambiada.emit()
        self.accept()


# =========================================================================== #
# Arqueo ciego
# =========================================================================== #
class ArqueoDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.cierre: dict | None = None
        self.setWindowTitle("Arqueo ciego y cierre de caja")
        self.setMinimumSize(620, 640)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(etiqueta("Arqueo ciego", titulo=True))
        lay.addWidget(etiqueta("Cuente el dinero físico del cajón e ingrese la cantidad de billetes y monedas. "
                               "El sistema NO muestra el valor esperado hasta confirmar.", muted=True))
        lay.itemAt(1).widget().setWordWrap(True)

        grid_w = QWidget()
        grid_w.setObjectName("transparente")
        grid = QGridLayout(grid_w)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.addWidget(etiqueta("Denominación", muted=True), 0, 0)
        grid.addWidget(etiqueta("Cantidad", muted=True), 0, 1)
        grid.addWidget(etiqueta("Subtotal", muted=True), 0, 2)
        self.spins: dict[int, QSpinBox] = {}
        self.subs: dict[int, QLabel] = {}
        for i, d in enumerate(cfg.DENOMINACIONES_COP, start=1):
            tipo = "Moneda" if d <= 1000 else "Billete"
            grid.addWidget(QLabel(f"{tipo}  {cfg.cop(d)}"), i, 0)
            sp = QSpinBox()
            sp.setRange(0, 9999)
            sp.setMinimumHeight(38)
            sp.setAlignment(AlignRight)
            sp.valueChanged.connect(self._recalcular)
            self.spins[d] = sp
            grid.addWidget(sp, i, 1)
            lbl = QLabel("$ 0")
            lbl.setAlignment(AlignRight)
            self.subs[d] = lbl
            grid.addWidget(lbl, i, 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_w)
        lay.addWidget(scroll, 1)

        self.chk_manual = QCheckBox("Prefiero digitar el total contado directamente")
        self.chk_manual.toggled.connect(self._modo_manual)
        lay.addWidget(self.chk_manual)
        self.manual = MoneyEdit()
        self.manual.setVisible(False)
        self.manual.valorCambiado.connect(lambda _: self._recalcular())
        lay.addWidget(self.manual)

        fila_tot = QHBoxLayout()
        fila_tot.addWidget(etiqueta("TOTAL CONTADO", titulo=True))
        self.lbl_total = QLabel("$ 0")
        self.lbl_total.setObjectName("totalLabel")
        self.lbl_total.setAlignment(AlignRight)
        fila_tot.addWidget(self.lbl_total, 1)
        lay.addLayout(fila_tot)
        self.obs = QLineEdit()
        self.obs.setPlaceholderText("Observaciones (opcional)")
        lay.addWidget(self.obs)

        fila = QHBoxLayout()
        cancelar = boton("Cancelar", "ghost", "lg")
        cancelar.clicked.connect(self.reject)
        ok = boton("CONFIRMAR ARQUEO Y CERRAR CAJA", "primary", "lg")
        ok.clicked.connect(self.cerrar)
        fila.addWidget(cancelar)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)

    def _modo_manual(self, on: bool) -> None:
        self.manual.setVisible(on)
        for sp in self.spins.values():
            sp.setEnabled(not on)
        if on:
            self.manual.setFocus()
        self._recalcular()

    def total(self) -> int:
        if self.chk_manual.isChecked():
            return self.manual.valor()
        return sum(d * sp.value() for d, sp in self.spins.items())

    def _recalcular(self) -> None:
        for d, sp in self.spins.items():
            self.subs[d].setText(cfg.cop(d * sp.value()))
        self.lbl_total.setText(cfg.cop(self.total()))

    def cerrar(self) -> None:
        total = self.total()
        if not confirmar(self, f"Efectivo contado: {cfg.cop(total)}\n\n"
                               "¿Confirmar el arqueo y CERRAR la caja?\nEsta acción no se puede deshacer."):
            return
        denoms = {} if self.chk_manual.isChecked() else {d: sp.value() for d, sp in self.spins.items()}
        try:
            self.cierre = self.ctx.db.cerrar_caja(total, denoms, self.obs.text().strip())
        except POSError as e:
            error(self, str(e))
            return
        self.accept()


# =========================================================================== #
# Resultado del cierre
# =========================================================================== #
class ResultadoCierreDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, cierre: dict, parent=None, imprimir_auto: bool = True):
        super().__init__(parent)
        self.ctx = ctx
        self.cierre = cierre
        self.setWindowTitle(f"Cierre de caja N.º {cierre['id']}")
        self.setMinimumWidth(560)
        T = theme.T
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        color = {"CUADRE PERFECTO": T["success"], "SOBRANTE": T["warning"], "FALTANTE": T["danger"]}.get(
            cierre["resultado"], T["text"])
        res = QLabel(cierre["resultado"])
        res.setAlignment(AlignCenter)
        res.setStyleSheet(f"font-size:34px;font-weight:900;color:{color};padding:10px;"
                          f"border:2px solid {color};border-radius:16px;")
        lay.addWidget(res)
        if cierre["diferencia"]:
            d = QLabel(f"Diferencia: {cfg.cop(cierre['diferencia'])}")
            d.setAlignment(AlignCenter)
            d.setStyleSheet(f"font-size:20px;font-weight:800;color:{color};")
            lay.addWidget(d)

        filas = [
            ("Apertura", _fecha(cierre["fecha_apertura"])), ("Cierre", _fecha(cierre["fecha_cierre"])),
            ("Cajero", cierre.get("cajero") or "—"), ("N.º de ventas", str(cierre["num_ventas"])),
            ("", ""),
            ("Efectivo", cfg.cop(cierre["por_metodo"]["Efectivo"]["total"])),
            ("Nequi", cfg.cop(cierre["por_metodo"]["Nequi"]["total"])),
            ("Daviplata", cfg.cop(cierre["por_metodo"]["Daviplata"]["total"])),
            ("Datáfono", cfg.cop(cierre["por_metodo"]["Datáfono"]["total"])),
            ("TOTAL VENTAS", cfg.cop(cierre["total_ventas"])),
            ("Devoluciones", cfg.cop(cierre["total_devoluciones"])),
            ("", ""),
            ("Base inicial", cfg.cop(cierre["base_inicial"])),
            ("+ Ventas en efectivo", cfg.cop(cierre["total_efectivo"])),
            ("− Devoluciones en efectivo", cfg.cop(cierre["devoluciones_efectivo"])),
            ("= Efectivo esperado", cfg.cop(cierre["efectivo_esperado"])),
            ("Efectivo contado", cfg.cop(cierre["efectivo_contado"])),
        ]
        g = QGridLayout()
        g.setVerticalSpacing(4)
        for i, (k, v) in enumerate(filas):
            a, b = QLabel(k), QLabel(v)
            b.setAlignment(AlignRight)
            if k.startswith(("TOTAL", "=")):
                a.setStyleSheet("font-weight:800;")
                b.setStyleSheet("font-weight:800;")
            g.addWidget(a, i, 0)
            g.addWidget(b, i, 1)
        lay.addLayout(g)

        fila = QHBoxLayout()
        imp = boton("Imprimir", size="lg")
        imp.clicked.connect(self.imprimir)
        exp = boton("Exportar a Excel", size="lg")
        exp.clicked.connect(lambda: exportar_y_ofrecer_abrir(
            self, ctx, f"Cierre_Caja_{cierre['id']}_{datetime.now():%Y%m%d}.xlsx",
            lambda ruta: xe.exportar_cierre_individual(ctx.db, cierre, ruta)))
        cerrar = boton("Cerrar", "primary", "lg")
        cerrar.clicked.connect(self.accept)
        fila.addWidget(imp)
        fila.addWidget(exp)
        fila.addStretch()
        fila.addWidget(cerrar)
        lay.addLayout(fila)
        if imprimir_auto:
            self.imprimir()

    def imprimir(self) -> None:
        conf = self.ctx.conf
        datos = pe.construir_cierre(self.cierre, conf).bytes()
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo=f"Cierre_{self.cierre['id']}"),
                         al_fallar=lambda m: self.ctx.notificar(f"No se pudo imprimir el cierre: {m}", "error"))


# =========================================================================== #
# Vista principal
# =========================================================================== #
class CajaView(QWidget):
    titulo = "Arqueo y Cierre de Caja"

    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 20)
        raiz.setSpacing(16)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._pagina_cerrada())
        self.stack.addWidget(self._pagina_abierta())
        raiz.addWidget(self.stack)

        # Historial
        hist = tarjeta()
        hl = QVBoxLayout(hist)
        hl.setContentsMargins(16, 14, 16, 14)
        cab = QHBoxLayout()
        cab.addWidget(etiqueta("Historial de cierres", titulo=True))
        cab.addStretch()
        b_ver = boton("Ver / reimprimir")
        b_ver.clicked.connect(self.ver_cierre)
        b_exp = boton("Exportar historial a Excel")
        b_exp.clicked.connect(lambda: exportar_y_ofrecer_abrir(
            self, ctx, f"Historial_Cierres_{datetime.now():%Y%m%d}.xlsx",
            lambda ruta: xe.exportar_cierres(ctx.db, ruta)))
        cab.addWidget(b_ver)
        cab.addWidget(b_exp)
        hl.addLayout(cab)
        self.tabla = QTableWidget(0, 10)
        self.tabla.setHorizontalHeaderLabels(["N.º", "Apertura", "Cierre", "Cajero", "Ventas", "Total ventas",
                                              "Esperado", "Contado", "Diferencia", "Resultado"])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.doubleClicked.connect(lambda _: self.ver_cierre())
        hl.addWidget(self.tabla)
        raiz.addWidget(hist, 1)

        for s in (ctx.bus.caja_cambiada, ctx.bus.ventas_cambiadas, ctx.bus.config_cambiada):
            s.connect(self.refrescar)
        self.refrescar()

    # ------------------------------------------------------------------ páginas
    def _pagina_cerrada(self) -> QWidget:
        w = tarjeta()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(28, 24, 28, 24)
        col = QVBoxLayout()
        col.addWidget(etiqueta("La caja está CERRADA", titulo=True))
        col.addWidget(etiqueta("Abra la caja registrando la base inicial en efectivo para comenzar a vender.",
                               muted=True))
        lay.addLayout(col, 1)
        b = boton("ABRIR CAJA", "success", "xl")
        b.setMinimumWidth(280)
        b.clicked.connect(lambda: AperturaDialog(self.ctx, self).exec())
        lay.addWidget(b)
        return w

    def _pagina_abierta(self) -> QWidget:
        w = QWidget()
        w.setObjectName("transparente")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        info = tarjeta()
        il = QHBoxLayout(info)
        il.setContentsMargins(22, 16, 22, 16)
        col = QVBoxLayout()
        col.addWidget(etiqueta("Caja ABIERTA", titulo=True))
        self.lbl_info = etiqueta("", muted=True)
        col.addWidget(self.lbl_info)
        il.addLayout(col, 1)
        cajon = boton("Abrir cajón", size="lg")
        cajon.clicked.connect(self.abrir_cajon)
        il.addWidget(cajon)
        cerrar = boton("ARQUEO CIEGO Y CIERRE", "primary", "lg")
        cerrar.setMinimumWidth(280)
        cerrar.clicked.connect(self.arqueo)
        il.addWidget(cerrar)
        lay.addWidget(info)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.kpi_metodo = {
            "Efectivo": KpiCard("Efectivo", "#22C55E"), "Nequi": KpiCard("Nequi", "#A855F7"),
            "Daviplata": KpiCard("Daviplata", "#EF4444"), "Datáfono": KpiCard("Datáfono / Tarjeta", "#3B82F6"),
        }
        for i, k in enumerate(self.kpi_metodo.values()):
            grid.addWidget(k, 0, i)
        self.kpi_total = KpiCard("Ventas netas del turno", "#3B82F6")
        self.kpi_dev = KpiCard("Devoluciones", "#EF4444")
        self.kpi_utl = KpiCard("Utilidad del turno", "#22C55E")
        self.kpi_esp = KpiCard("Efectivo esperado en cajón", "#F59E0B")
        for i, k in enumerate((self.kpi_total, self.kpi_dev, self.kpi_utl, self.kpi_esp)):
            grid.addWidget(k, 1, i)
        lay.addLayout(grid)
        return w

    # ------------------------------------------------------------------ datos
    def refrescar(self) -> None:
        db = self.ctx.db
        caja = db.caja_abierta()
        self.stack.setCurrentIndex(1 if caja else 0)
        if caja:
            r = db.resumen_caja(caja["id"])
            ciego = db.get_config("arqueo_ciego") == "1"
            self.lbl_info.setText(
                f"Desde {_fecha(caja['fecha_apertura'])}  ·  Cajero: {caja['cajero']}  ·  "
                f"Base inicial: {cfg.cop(caja['base_inicial'])}")
            for m, k in self.kpi_metodo.items():
                dat = r["por_metodo"].get(m, {"cantidad": 0, "total": 0})
                dev = f" · devuelto {cfg.cop(dat['devuelto'])}" if dat.get("devuelto") else ""
                if m == "Efectivo" and ciego:
                    k.set_valor("$ ••••••", f"{dat['cantidad']} ventas · oculto por arqueo ciego")
                else:
                    k.set_monto(dat.get("neto", dat["total"]), f"{dat['cantidad']} ventas{dev}")
            if ciego:
                self.kpi_total.set_valor("$ ••••••", f"{r['num_ventas']} ventas")
                self.kpi_utl.set_valor("$ ••••••", "Precio venta − costo")
            else:
                self.kpi_total.set_monto(r["ventas_netas"], f"{r['num_ventas']} ventas")
                self.kpi_utl.set_monto(r["utilidad"], "Precio venta − costo")
            self.kpi_dev.set_monto(r["total_devoluciones"], f"{r['num_devoluciones']} devoluciones")
            if ciego:
                self.kpi_esp.set_valor("$ ••••••", "Se revela al cerrar")
            else:
                self.kpi_esp.set_monto(r["efectivo_esperado"], "Base + efectivo − devoluciones")
        self._llenar_historial()

    def _llenar_historial(self) -> None:
        T = theme.T
        filas = [r for r in self.ctx.db.historial_cierres(200) if r["estado"] == "CERRADA"]
        self.tabla.setRowCount(len(filas))
        for i, r in enumerate(filas):
            vals = [str(r["id"]), _fecha(r["fecha_apertura"]), _fecha(r["fecha_cierre"]), r["cajero"],
                    str(r["num_ventas"]), cfg.cop(r["total_ventas"]), cfg.cop(r["efectivo_esperado"]),
                    cfg.cop(r["efectivo_contado"]), cfg.cop(r["diferencia"]), r["resultado"] or ""]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, r["id"])
                if j >= 4:
                    it.setTextAlignment(AlignRight)
                if j == 9:
                    it.setForeground(QColor({"CUADRE PERFECTO": T["success"], "SOBRANTE": T["warning"],
                                             "FALTANTE": T["danger"]}.get(v, T["text"])))
                self.tabla.setItem(i, j, it)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.refrescar()
        if self.stack.currentIndex() == 1:
            escalonado(list(self.kpi_metodo.values()) + [self.kpi_total, self.kpi_dev, self.kpi_utl, self.kpi_esp],
                       paso=45)

    # ------------------------------------------------------------------ acciones
    def arqueo(self) -> None:
        dlg = ArqueoDialog(self.ctx, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.cierre:
            return
        try:
            self.ctx.db.respaldar()  # copia de seguridad automática tras cada cierre
        except Exception:  # noqa: BLE001
            pass
        self.ctx.bus.caja_cambiada.emit()
        ResultadoCierreDialog(self.ctx, dlg.cierre, self).exec()

    def ver_cierre(self) -> None:
        fila = self.tabla.currentRow()
        if fila < 0:
            error(self, "Seleccione un cierre de la tabla.")
            return
        cid = self.tabla.item(fila, 0).data(Qt.ItemDataRole.UserRole)
        cierre = self.ctx.db.obtener_cierre(int(cid))
        if cierre:
            ResultadoCierreDialog(self.ctx, cierre, self, imprimir_auto=False).exec()

    def abrir_cajon(self) -> None:
        conf = self.ctx.conf
        datos = pe.construir_apertura_cajon(conf).bytes()
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo="Cajon"),
                         al_fallar=lambda m: self.ctx.notificar(f"No se pudo abrir el cajón: {m}", "error"))
