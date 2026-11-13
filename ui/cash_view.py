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
from ui.seguridad import autorizar, pedir_monto, pedir_motivo
from ui.widgets import (AlignCenter, AlignRight, Contexto, KpiCard, MoneyEdit, abrir_archivo, boton, confirmar,
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
        if self.base.valor() > 1_000_000 and not confirmar(
                self, f"La base inicial es {cfg.cop(self.base.valor())}.\n¿Es correcto? (revise que no sobren ceros)"):
            return
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
        self.setMinimumSize(640, 480)
        self.resize(700, 720)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(etiqueta("Arqueo ciego", titulo=True))
        lay.addWidget(etiqueta("Cuente el efectivo (del cajón, la caja o donde lo guarde) e ingrese billetes y monedas. "
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
        # Conciliación de pagos digitales: ese dinero NO está en el cajón (va directo al banco)
        lay.addWidget(etiqueta("PAGOS DIGITALES (opcional) — escriba lo que reporta el cierre del datáfono "
                               "y las apps; el sistema lo compara con las ventas", kpiTitulo=True))
        conc = QGridLayout()
        conc.setHorizontalSpacing(10)
        self.declarados: dict[str, MoneyEdit] = {}
        textos = {"Datáfono": "Cierre del datáfono (débito/crédito)", "Nequi": "Recibido en Nequi",
                  "Bre-B": "Recibido por Bre-B (llaves)"}
        for j, (m, txt) in enumerate((m, textos.get(m, f"Recibido en {m}")) for m in cfg.METODOS_PAGO
                                     if m != "Efectivo"):
            conc.addWidget(etiqueta(txt, muted=True), 0, j)
            me = MoneyEdit(grande=False, placeholder="No verificar")
            self.declarados[m] = me
            conc.addWidget(me, 1, j)
        lay.addLayout(conc)
        self.obs = QLineEdit()
        self.obs.setPlaceholderText("Observaciones (obligatorias si hay diferencias)")
        lay.addWidget(self.obs)
        self._intentos = 0

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
        caja = self.ctx.db.caja_abierta()
        esperado = self.ctx.db.resumen_caja(caja["id"])["efectivo_esperado"] if caja else 0
        # Arqueo ciego con un reconteo: si no cuadra, se pide contar otra vez SIN revelar la cifra
        if total != esperado and self._intentos == 0:
            self._intentos = 1
            error(self, "El conteo no coincide con el sistema.\n\nPor favor cuente el dinero otra vez con calma "
                        "(billetes pegados, monedas en otro compartimiento…).\nLe queda 1 intento; después la caja "
                        "se cierra con la diferencia.", "Recuente por favor")
            return
        if total != esperado and len(self.obs.text().strip()) < 5:
            error(self, "Hay una diferencia en el efectivo. Escriba una observación que la explique "
                        "(queda en el reporte de cierre).")
            self.obs.setFocus()
            return
        if not confirmar(self, f"Efectivo contado: {cfg.cop(total)}\n\n"
                               "¿Confirmar el arqueo y CERRAR la caja?"):
            return
        denoms = {} if self.chk_manual.isChecked() else {d: sp.value() for d, sp in self.spins.items()}
        declarados = {m: (me.valor() if me.tiene_valor() else None) for m, me in self.declarados.items()}
        try:
            self.cierre = self.ctx.db.cerrar_caja(total, denoms, self.obs.text().strip(), declarados)
        except POSError as e:
            error(self, str(e))
            return
        self.accept()


# =========================================================================== #
# Resultado del cierre
# =========================================================================== #
def guardar_pdf_cierre(ctx: Contexto, cierre: dict):
    """Comprobante INTERNO del cierre en PDF (carpeta de tiquetes › año › mes › CIERRE-000012.pdf)."""
    try:
        import ticket_pdf
        conf = ctx.conf
        ruta = ticket_pdf.ruta_tiquete(conf, f"CIERRE-{int(cierre['id']):06d}", cierre.get("fecha_cierre"))
        if not ruta.exists():
            ticket_pdf.guardar(pe.construir_cierre(cierre, conf), ruta)
        return ruta
    except Exception:  # noqa: BLE001
        return None


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
            *[(f"{m} (neto)", cfg.cop(d.get("neto", d.get("total", 0))))
              for m, d in cierre["por_metodo"].items() if m in cfg.METODOS_PAGO or d.get("neto") or d.get("total")],
            ("TOTAL VENTAS", cfg.cop(cierre["total_ventas"])),
            ("Devoluciones", cfg.cop(cierre["total_devoluciones"])),
            *([("Abonos de separados", cfg.cop(cierre["total_abonos"]))] if cierre.get("total_abonos") else []),
            ("", ""),
            ("Base inicial", cfg.cop(cierre["base_inicial"])),
            ("+ Ventas en efectivo", cfg.cop(cierre["por_metodo"].get("Efectivo", {}).get("total", cierre["total_efectivo"]))),
            ("− Devoluciones en efectivo", cfg.cop(cierre["devoluciones_efectivo"])),
            *([("+ Abonos en efectivo", cfg.cop(cierre["por_metodo"]["Efectivo"]["abonos"]))]
              if cierre["por_metodo"].get("Efectivo", {}).get("abonos") else []),
            *([("− Separados devueltos", cfg.cop(cierre["por_metodo"]["Efectivo"]["reembolsos"]))]
              if cierre["por_metodo"].get("Efectivo", {}).get("reembolsos") else []),
            ("= Efectivo esperado", cfg.cop(cierre["efectivo_esperado"])),
            ("Efectivo contado", cfg.cop(cierre["efectivo_contado"])),
        ]
        if cierre.get("total_entradas") or cierre.get("total_salidas"):
            filas[-2:-2] = [("+ Entradas de efectivo", cfg.cop(cierre.get("total_entradas", 0))),
                            ("− Salidas (gastos / retiros)", cfg.cop(cierre.get("total_salidas", 0)))]
        cajon = cierre.get("cajon") or {}
        if cajon:
            filas += [("", ""), ("Aperturas del cajón", str(sum(cajon.values()))),
                      *[(f"   {o.title()}", str(n)) for o, n in sorted(cajon.items())]]
        for m, d in (cierre.get("conciliacion") or {}).items():
            filas += [("", ""), (f"{m}: sistema / reportado", f"{cfg.cop(d['sistema'])} / {cfg.cop(d['declarado'])}"),
                      (f"{m}: diferencia", "OK" if d["diferencia"] == 0 else cfg.cop(d["diferencia"]))]
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
        imp.setToolTip("Imprime una copia en papel (queda registrado en la auditoría)")
        imp.clicked.connect(self.imprimir)
        pdf = boton("Ver PDF", size="lg")
        pdf.setToolTip("El comprobante interno del cierre, guardado en la carpeta de tiquetes")
        pdf.clicked.connect(self.ver_pdf)
        exp = boton("Exportar a Excel", size="lg")
        exp.clicked.connect(lambda: exportar_y_ofrecer_abrir(
            self, ctx, f"Cierre_Caja_{cierre['id']}_{datetime.now():%Y%m%d}.xlsx",
            lambda ruta: xe.exportar_cierre_individual(ctx.db, cierre, ruta)))
        wa = boton("Enviar al celular (WhatsApp)", "success", "lg")
        wa.setToolTip("Abre WhatsApp con el resumen escrito para el número del dueño (Ajustes › Nube y celular)")
        wa.clicked.connect(self.whatsapp)
        cerrar = boton("Cerrar", "primary", "lg")
        cerrar.clicked.connect(self.accept)
        fila.addWidget(pdf)
        fila.addWidget(imp)
        fila.addWidget(exp)
        fila.addWidget(wa)
        fila.addStretch()
        fila.addWidget(cerrar)
        lay.addLayout(fila)
        self._pdf = guardar_pdf_cierre(ctx, cierre)
        if imprimir_auto:
            self.imprimir()

    def ver_pdf(self) -> None:
        ruta = self._pdf or guardar_pdf_cierre(self.ctx, self.cierre)
        if ruta:
            abrir_archivo(str(ruta))
        else:
            error(self, "No se pudo generar el PDF del cierre.")

    def whatsapp(self) -> None:
        import nube
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        numero = self.ctx.db.get_config("whatsapp_dueno")
        if len(nube.numero_whatsapp(numero)) < 11:
            error(self, "Escriba el celular del dueño en Ajustes › Nube y celular.")
            return
        QDesktopServices.openUrl(QUrl(nube.url_whatsapp(numero, nube.texto_cierre(self.cierre, self.ctx.conf))))

    def imprimir(self) -> None:
        conf = self.ctx.conf
        try:
            self.ctx.db.auditar("CIERRE_IMPRESO", "cierres_caja", self.cierre["id"],
                                {"cajero": self.ctx.db.get_config("cajero_actual")})
        except Exception:  # noqa: BLE001
            pass
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
        b_reabrir = boton("Reabrir último cierre", "ghost")
        b_reabrir.clicked.connect(self.reabrir)
        cab.addWidget(b_reabrir)
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
        self.btn_cajon = boton("Abrir cajón", size="lg")
        self.btn_cajon.clicked.connect(self.abrir_cajon)
        il.addWidget(self.btn_cajon)
        cerrar = boton("ARQUEO CIEGO Y CIERRE", "primary", "lg")
        cerrar.setMinimumWidth(280)
        cerrar.clicked.connect(self.arqueo)
        il.addWidget(cerrar)
        lay.addWidget(info)

        acciones = QHBoxLayout()
        acciones.setSpacing(8)
        for txt, fn, var in (("Entrada de efectivo", lambda: self.movimiento("ENTRADA"), None),
                             ("Salida / gasto", lambda: self.movimiento("SALIDA"), None),
                             ("Corregir base inicial", self.corregir_base, "ghost"),
                             ("Anular apertura (abierta por error)", self.anular_apertura, "danger")):
            b = boton(txt, var)
            b.clicked.connect(fn)
            acciones.addWidget(b)
        acciones.addStretch()
        lay.addLayout(acciones)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.kpi_metodo = {m: KpiCard("Datáfono / Tarjeta" if m == "Datáfono" else m,
                                      cfg.COLORES_METODO.get(m, "#3B82F6")) for m in cfg.METODOS_PAGO}
        for i, k in enumerate(self.kpi_metodo.values()):
            grid.addWidget(k, 0, i)
        self.kpi_total = KpiCard("Ventas netas del turno", "#3B82F6")
        self.kpi_dev = KpiCard("Devoluciones", "#EF4444")
        self.kpi_utl = KpiCard("Utilidad del turno", "#22C55E")
        self.kpi_esp = KpiCard("Efectivo esperado en cajón", "#F59E0B")
        for i, k in enumerate((self.kpi_total, self.kpi_dev, self.kpi_utl, self.kpi_esp)):
            grid.addWidget(k, 1, i)
        lay.addLayout(grid)

        movs = tarjeta()
        ml = QVBoxLayout(movs)
        ml.setContentsMargins(14, 10, 14, 10)
        cab = QHBoxLayout()
        cab.addWidget(etiqueta("Entradas y salidas de efectivo del turno", kpiTitulo=True))
        cab.addStretch()
        b_anular = boton("Anular movimiento seleccionado", "danger", size="sm")
        b_anular.clicked.connect(self.anular_movimiento)
        cab.addWidget(b_anular)
        ml.addLayout(cab)
        self.tabla_mov = QTableWidget(0, 5)
        self.tabla_mov.setHorizontalHeaderLabels(["Hora", "Tipo", "Concepto", "Valor", "Estado"])
        self.tabla_mov.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabla_mov.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabla_mov.verticalHeader().setVisible(False)
        self.tabla_mov.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_mov.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla_mov.setMaximumHeight(150)
        ml.addWidget(self.tabla_mov)
        lay.addWidget(movs)

        caj = tarjeta()
        cl = QVBoxLayout(caj)
        cl.setContentsMargins(14, 10, 14, 10)
        self.lbl_cajon = etiqueta("Aperturas del cajón en este turno", kpiTitulo=True)
        cl.addWidget(self.lbl_cajon)
        self.tabla_cajon = QTableWidget(0, 5)
        self.tabla_cajon.setHorizontalHeaderLabels(["Hora", "Por", "Documento", "Motivo", "Cajero"])
        self.tabla_cajon.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabla_cajon.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tabla_cajon.verticalHeader().setVisible(False)
        self.tabla_cajon.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_cajon.setMaximumHeight(150)
        cl.addWidget(self.tabla_cajon)
        lay.addWidget(caj)
        return w

    # ------------------------------------------------------------------ datos
    def refrescar(self) -> None:
        db = self.ctx.db
        caja = db.caja_abierta()
        self.stack.setCurrentIndex(1 if caja else 0)
        self.btn_cajon.setVisible(db.get_config("abrir_cajon_efectivo") == "1")
        if caja:
            r = db.resumen_caja(caja["id"])
            ciego = db.get_config("arqueo_ciego") == "1"
            self.lbl_info.setText(
                f"Desde {_fecha(caja['fecha_apertura'])}  ·  Cajero: {caja['cajero']}  ·  "
                f"Base inicial: {cfg.cop(caja['base_inicial'])}  ·  Entradas {cfg.cop(r['entradas'])}  ·  "
                f"Salidas {cfg.cop(r['salidas'])}")
            movs = self.ctx.db.movimientos_caja(caja["id"])
            self.tabla_mov.setRowCount(len(movs))
            for i, m in enumerate(movs):
                vals = [m["fecha"][11:16], m["tipo"], m["concepto"], cfg.cop(m["monto"]),
                        f"ANULADO: {m['anulado_motivo']}" if m["anulado"] else "Vigente"]
                for j, v in enumerate(vals):
                    it = QTableWidgetItem(v)
                    it.setData(Qt.ItemDataRole.UserRole, m["id"])
                    if j == 3:
                        it.setTextAlignment(AlignRight)
                    if m["anulado"]:
                        it.setForeground(QColor(theme.T["muted"]))
                    self.tabla_mov.setItem(i, j, it)
            ap = db.aperturas_cajon(caja["id"])
            self.lbl_cajon.setText(f"Aperturas del cajón en este turno: {len(ap)}"
                                   + (f" (manuales: {sum(1 for a in ap if a['origen'] == 'MANUAL')})" if ap else ""))
            self.tabla_cajon.setRowCount(len(ap))
            for i, a in enumerate(reversed(ap)):
                for j, v in enumerate([a["fecha"][11:16], a["origen"].title(), a["referencia"], a["motivo"], a["cajero"]]):
                    self.tabla_cajon.setItem(i, j, QTableWidgetItem(v))
            for m, k in self.kpi_metodo.items():
                dat = r["por_metodo"].get(m, {"cantidad": 0, "total": 0})
                dev = f" · devuelto {cfg.cop(dat['devuelto'])}" if dat.get("devuelto") else ""
                if dat.get("abonos"):
                    dev += f" · abonos {cfg.cop(dat['abonos'])}"
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
                self.kpi_esp.set_monto(r["efectivo_esperado"], "Base + efectivo − devol. ± movimientos")
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
        import nube
        db, cierre, conf = self.ctx.db, dlg.cierre, self.ctx.conf
        if (db.get_config("carpeta_respaldo_nube") or "").strip():
            en_segundo_plano(lambda: nube.respaldar_en_nube(db, "cierre"),
                             lambda r: r and self.ctx.notificar("Respaldo guardado en Google Drive", "ok"),
                             lambda m: self.ctx.notificar(f"No se pudo respaldar en la nube: {m}", "warn"))
        if db.get_config("telegram_token") and db.get_config("telegram_chat"):
            texto = nube.texto_cierre(cierre, conf)
            en_segundo_plano(lambda: nube.enviar_telegram(db.get_config("telegram_token"),
                                                          db.get_config("telegram_chat"), texto),
                             lambda _: self.ctx.notificar("Resumen enviado al celular (Telegram)", "ok"),
                             lambda m: self.ctx.notificar(f"No se pudo enviar el resumen: {m}", "warn"))
        self.ctx.bus.caja_cambiada.emit()
        ResultadoCierreDialog(self.ctx, cierre, self,
                              imprimir_auto=db.get_config("imprimir_cierre", "0") == "1").exec()

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
        from ui.cajon import abrir_manual
        abrir_manual(self, self.ctx)
        self.refrescar()

    # ------------------------------------------------------------------ correcciones
    def movimiento(self, tipo: str) -> None:
        conceptos = (["Pago domicilio", "Pago proveedor", "Servicios / arriendo", "Retiro del dueño",
                      "Consignación al banco", "Compra de insumos"] if tipo == "SALIDA"
                     else ["Cambio de sencillo", "Aporte del dueño", "Abono de cliente"])
        titulo = "Salida de efectivo (gasto / retiro)" if tipo == "SALIDA" else "Entrada de efectivo"
        r = pedir_monto(self, titulo, "Queda registrado en el turno y ajusta el efectivo esperado del arqueo.",
                        conceptos=conceptos)
        if not r:
            return
        monto, concepto = r
        if monto >= 500_000 and not confirmar(self, f"¿Confirma {titulo.lower()} por {cfg.cop(monto)}?"):
            return
        try:
            self.ctx.db.registrar_movimiento(tipo, monto, concepto, self.ctx.db.get_config("cajero_actual"))
        except POSError as e:
            error(self, str(e))
            return
        if tipo == "SALIDA" and self.ctx.db.get_config("abrir_cajon_efectivo") == "1":
            self.abrir_cajon()
        self.ctx.notificar(f"{titulo}: {cfg.cop(monto)} · {concepto}", "ok")
        self.ctx.bus.caja_cambiada.emit()

    def anular_movimiento(self) -> None:
        fila = self.tabla_mov.currentRow()
        if fila < 0:
            error(self, "Seleccione el movimiento a anular.")
            return
        mid = int(self.tabla_mov.item(fila, 0).data(Qt.ItemDataRole.UserRole))
        if not autorizar(self, self.ctx, "Anular un movimiento de efectivo registrado por error."):
            return
        motivo = pedir_motivo(self, "Anular movimiento", "¿Por qué se anula?",
                              ["Registrado por error", "Valor equivocado", "Registrado dos veces"])
        if not motivo:
            return
        try:
            self.ctx.db.anular_movimiento(mid, motivo)
        except POSError as e:
            error(self, str(e))
            return
        self.ctx.notificar("Movimiento anulado", "ok")
        self.ctx.bus.caja_cambiada.emit()

    def corregir_base(self) -> None:
        caja = self.ctx.db.caja_abierta()
        if not caja or not autorizar(self, self.ctx, "Corregir la base inicial de la caja abierta."):
            return
        r = pedir_monto(self, "Corregir base inicial", f"Base registrada: {cfg.cop(caja['base_inicial'])}",
                        valor=caja["base_inicial"])
        if not r:
            return
        motivo = pedir_motivo(self, "Motivo de la corrección", "", ["Error al digitar la base", "Se agregó sencillo"])
        if not motivo:
            return
        try:
            self.ctx.db.corregir_base(r[0], motivo)
        except POSError as e:
            error(self, str(e))
            return
        self.ctx.notificar(f"Base corregida a {cfg.cop(r[0])}", "ok")
        self.ctx.bus.caja_cambiada.emit()

    def anular_apertura(self) -> None:
        if not autorizar(self, self.ctx, "Anular la apertura de caja (solo si se abrió por error y no tiene ventas)."):
            return
        motivo = pedir_motivo(self, "Anular apertura", "", ["Abierta por error", "Abierta dos veces"])
        if not motivo:
            return
        try:
            self.ctx.db.anular_apertura(motivo)
        except POSError as e:
            error(self, str(e))
            return
        self.ctx.notificar("Apertura anulada", "ok")
        self.ctx.bus.caja_cambiada.emit()

    def reabrir(self) -> None:
        ultimo = self.ctx.db.q1("SELECT * FROM cierres_caja ORDER BY id DESC LIMIT 1")
        if ultimo is None or ultimo["estado"] != "CERRADA":
            error(self, "No hay un cierre para reabrir (o ya hay una caja abierta).")
            return
        if not autorizar(self, self.ctx, f"Reabrir el cierre N.º {ultimo['id']} del {_fecha(ultimo['fecha_cierre'])}."):
            return
        motivo = pedir_motivo(self, "Reabrir caja", "Se volverá a hacer el arqueo al cerrar.",
                              ["Se cerró por error", "Faltó registrar un gasto", "Faltó registrar una venta"])
        if not motivo:
            return
        try:
            self.ctx.db.reabrir_caja(ultimo["id"], motivo)
        except POSError as e:
            error(self, str(e))
            return
        self.ctx.notificar(f"Caja N.º {ultimo['id']} reabierta", "ok")
        self.ctx.bus.caja_cambiada.emit()
