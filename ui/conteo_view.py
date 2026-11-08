"""
ui/conteo_view.py — Conteo físico con escáner (Inventario › Conteo físico).

Se pasa el lector por cada prenda (cada lectura suma 1). El programa compara lo contado con el
sistema y muestra faltantes y sobrantes. Al terminar se aplican los ajustes con un motivo; cada
cambio queda en la tabla de ajustes (antes / después / quién / cuándo) y en la auditoría.
El conteo se guarda solo mientras se hace: si se cierra la ventana, se retoma donde iba.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout)

import config as cfg
import excel_engine as xe
from database import POSError
from ui.animations import DialogoAnimado
from ui.widgets import AlignCenter, Contexto, boton, confirmar, error, etiqueta, exportar_y_ofrecer_abrir

ARCHIVO = "conteo_en_curso.json"


class ConteoDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.contados: dict[int, int] = {}
        self.ruta = cfg.data_dir() / ARCHIVO
        self.setWindowTitle("Conteo físico de inventario")
        self.resize(980, 700)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(etiqueta("Conteo físico con escáner", titulo=True))
        ayuda = etiqueta("Pase el lector por cada prenda: cada lectura suma 1. También puede escribir el código y "
                         "pulsar Enter, o corregir la cantidad con doble clic en «Contado».", muted=True)
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)

        fila = QHBoxLayout()
        self.lector = QLineEdit()
        self.lector.setObjectName("search")
        self.lector.setPlaceholderText("Escanee aquí…")
        self.lector.returnPressed.connect(self._leer)
        fila.addWidget(self.lector, 2)
        self.alcance = QComboBox()
        self.alcance.addItem("Comparar solo lo escaneado", "escaneado")
        self.alcance.addItem("Todo el inventario (lo no escaneado cuenta 0)", "todo")
        for c in ctx.db.categorias():
            self.alcance.addItem(f"Solo la categoría {c} (lo no escaneado cuenta 0)", f"cat:{c}")
        self.alcance.setMinimumHeight(44)
        self.alcance.currentIndexChanged.connect(lambda _: self._pintar())
        fila.addWidget(self.alcance, 2)
        lay.addLayout(fila)
        self.ultimo = QLabel("")
        self.ultimo.setStyleSheet("font-size:18px;font-weight:800;")
        lay.addWidget(self.ultimo)

        self.tabla = QTableWidget(0, 6)
        self.tabla.setHorizontalHeaderLabels(["Código", "Prenda", "Categoría", "Sistema", "Contado", "Diferencia"])
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.tabla.itemChanged.connect(self._editado)
        lay.addWidget(self.tabla, 1)
        self.resumen = etiqueta("", muted=True)
        lay.addWidget(self.resumen)

        fila_b = QHBoxLayout()
        b_desc = boton("Descartar conteo", "ghost")
        b_desc.clicked.connect(self._descartar)
        b_exp = boton("Exportar diferencias a Excel")
        b_exp.clicked.connect(self._exportar)
        b_cerrar = boton("Cerrar (se guarda)", size="lg")
        b_cerrar.clicked.connect(self.accept)
        self.b_aplicar = boton("Aplicar ajustes al inventario", "success", "lg")
        self.b_aplicar.clicked.connect(self._aplicar)
        fila_b.addWidget(b_desc)
        fila_b.addWidget(b_exp)
        fila_b.addStretch()
        fila_b.addWidget(b_cerrar)
        fila_b.addWidget(self.b_aplicar)
        lay.addLayout(fila_b)
        self._cargar()
        self._pintar()
        self.lector.setFocus()

    # ------------------------------------------------------------------ persistencia
    def _cargar(self) -> None:
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
            self.contados = {int(k): int(v) for k, v in datos.get("contados", {}).items()}
            i = self.alcance.findData(datos.get("alcance", "escaneado"))
            if i >= 0:
                self.alcance.setCurrentIndex(i)
            if self.contados:
                self.ultimo.setText(f"Conteo retomado: {sum(self.contados.values())} prendas leídas "
                                    f"(iniciado {datos.get('inicio', '')})")
        except (OSError, ValueError):
            self.contados = {}

    def _guardar(self) -> None:
        try:
            previo = json.loads(self.ruta.read_text(encoding="utf-8")) if self.ruta.exists() else {}
        except (OSError, ValueError):
            previo = {}
        datos = {"inicio": previo.get("inicio") or datetime.now().strftime("%d/%m/%Y %H:%M"),
                 "alcance": self.alcance.currentData(), "contados": self.contados}
        try:
            self.ruta.write_text(json.dumps(datos), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ lectura
    def _leer(self) -> None:
        codigo = self.lector.text().strip()
        self.lector.clear()
        if not codigo:
            return
        p = self.ctx.db.producto_por_codigo(codigo)
        if p is None:
            QApplication.beep()
            self.ultimo.setText(f"NO EXISTE: el código {codigo} no está en el inventario")
            self.ultimo.setStyleSheet("font-size:18px;font-weight:800;color:#EF4444;")
            return
        self.contados[p["id"]] = self.contados.get(p["id"], 0) + 1
        self.ultimo.setText(f"LEÍDO: {p['nombre']} · contadas {self.contados[p['id']]} · sistema {p['stock']}")
        self.ultimo.setStyleSheet("font-size:18px;font-weight:800;color:#22C55E;")
        self._guardar()
        self._pintar(p["id"])

    def _filas(self) -> list[dict[str, Any]]:
        alcance = self.alcance.currentData()
        productos = {p["id"]: p for p in self.ctx.db.listar_productos()}
        if alcance == "escaneado":
            ids = [pid for pid in self.contados if pid in productos]
        elif alcance == "todo":
            ids = list(productos)
        else:
            cat = alcance[4:]
            ids = [pid for pid, p in productos.items() if p["categoria"] == cat] + \
                [pid for pid in self.contados if pid in productos and productos[pid]["categoria"] != cat]
        filas = []
        for pid in ids:
            p = productos[pid]
            contado = self.contados.get(pid, 0)
            filas.append({"id": pid, "codigo": p["codigo"], "nombre": p["nombre"], "categoria": p["categoria"],
                          "sistema": p["stock"], "contado": contado, "diferencia": contado - p["stock"],
                          "valor": (contado - p["stock"]) * p["costo"]})
        filas.sort(key=lambda f: (f["diferencia"] == 0, f["diferencia"], f["nombre"]))
        return filas

    def _pintar(self, resaltar: int | None = None) -> None:
        filas = self._filas()
        self._filas_actuales = filas
        self.tabla.blockSignals(True)
        self.tabla.setRowCount(len(filas))
        rojo, verde, gris = QColor("#EF4444"), QColor("#22C55E"), QColor("#94A3B8")
        fila_res = None
        for r, f in enumerate(filas):
            for c, v in enumerate([f["codigo"], f["nombre"], f["categoria"], str(f["sistema"]), str(f["contado"]),
                                   f"{f['diferencia']:+d}" if f["diferencia"] else "OK"]):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, f["id"])
                if c != 4:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c >= 3:
                    it.setTextAlignment(AlignCenter)
                if c == 5:
                    it.setForeground(rojo if f["diferencia"] < 0 else verde if f["diferencia"] > 0 else gris)
                self.tabla.setItem(r, c, it)
            if f["id"] == resaltar:
                fila_res = r
        self.tabla.blockSignals(False)
        if fila_res is not None:
            self.tabla.selectRow(fila_res)
            self.tabla.scrollToItem(self.tabla.item(fila_res, 0))
        falt = sum(-f["diferencia"] for f in filas if f["diferencia"] < 0)
        sobr = sum(f["diferencia"] for f in filas if f["diferencia"] > 0)
        valor = sum(f["valor"] for f in filas)
        self.resumen.setText(f"{sum(self.contados.values())} prendas leídas · {len(filas)} referencias comparadas · "
                             f"faltan {falt} · sobran {sobr} · diferencia a costo {cfg.cop(valor)}")
        self.b_aplicar.setEnabled(any(f["diferencia"] for f in filas))

    def _editado(self, it: QTableWidgetItem) -> None:
        if it.column() != 4:
            return
        pid = it.data(Qt.ItemDataRole.UserRole)
        try:
            n = int(it.text().strip())
            if n < 0:
                raise ValueError
        except ValueError:
            error(self, "Escriba una cantidad válida (0 o más).")
            self._pintar()
            return
        self.contados[int(pid)] = n
        self._guardar()
        self._pintar(int(pid))

    # ------------------------------------------------------------------ acciones
    def _exportar(self) -> None:
        filas = [f for f in self._filas_actuales if f["diferencia"]]
        exportar_y_ofrecer_abrir(self, self.ctx, f"Conteo_Fisico_{datetime.now():%Y%m%d_%H%M}.xlsx",
                                 lambda ruta: xe.exportar_conteo(self.ctx.db, filas, ruta,
                                                                 "Conteo físico · diferencias"))

    def _descartar(self) -> None:
        if self.contados and not confirmar(self, "¿Borrar lo contado y empezar de nuevo?"):
            return
        self.contados = {}
        try:
            self.ruta.unlink(missing_ok=True)
        except OSError:
            pass
        self.ultimo.setText("")
        self._pintar()

    def _aplicar(self) -> None:
        filas = [f for f in self._filas_actuales if f["diferencia"]]
        if not filas:
            return
        from ui.seguridad import autorizar, pedir_motivo
        motivo = pedir_motivo(self, "Aplicar conteo físico",
                              f"Se ajustarán {len(filas)} referencias al valor contado. Queda registrado quién, "
                              "cuándo, el antes y el después.",
                              [f"Conteo físico {datetime.now():%d/%m/%Y}", "Conteo mensual", "Conteo por categoría"])
        if not motivo or not autorizar(self, self.ctx, f"Ajustar inventario por conteo ({len(filas)} referencias)"):
            return
        try:
            cambios = self.ctx.db.aplicar_conteo({f["id"]: f["contado"] for f in filas}, motivo,
                                                 self.ctx.db.get_config("cajero_actual"))
        except POSError as e:
            error(self, str(e))
            return
        try:
            self.ruta.unlink(missing_ok=True)
        except OSError:
            pass
        self.contados = {}
        self.ctx.bus.productos_cambiados.emit()
        self.ctx.notificar(f"Inventario ajustado: {len(cambios)} referencias", "ok")
        self.accept()
