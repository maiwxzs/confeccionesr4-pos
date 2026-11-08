"""
ui/inventory_view.py — MÓDULO 2: Gestión de Inventario y Creador de Etiquetas.

* Tabla administrable (modelo/vista de Qt) con búsqueda instantánea, orden por columna y
  edición en línea de código, referencia, nombre, categoría, costo, precio y stock.
* Alertas visuales: amarillo = stock crítico (≤ umbral, por defecto 3) · rojo = agotado.
* Importación masiva desde Excel con vista previa y exportación de inventario valorizado.
* Generador de etiquetas adhesivas con código de barras, nombre y precio.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QCompleter, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QRadioButton, QSizePolicy, QSpinBox, QStyledItemDelegate, QTableView,
                               QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

import barcode_engine as be
import config as cfg
import excel_engine as xe
import printer_engine as pe
from database import POSError
from ui import theme
from ui.animations import DialogoAnimado
from ui.seguridad import autorizar
from ui.widgets import (AlignCenter, AlignRight, Contexto, FlujoLayout, MoneyEdit, boton, confirmar,
                        en_segundo_plano, error, etiqueta, exportar_y_ofrecer_abrir, info, pil_a_pixmap, tarjeta)

UserRole = Qt.ItemDataRole.UserRole
EditRole = Qt.ItemDataRole.EditRole
DisplayRole = Qt.ItemDataRole.DisplayRole


# =========================================================================== #
# Modelo de tabla
# =========================================================================== #
class InventarioModel(QAbstractTableModel):
    COLS = [("codigo", "Código"), ("referencia", "Referencia"), ("nombre", "Producto"),
            ("categoria", "Categoría"), ("costo", "Costo compra"), ("precio", "Precio venta"),
            ("stock", "Stock"), ("margen", "Margen"), ("valor", "Valor (costo)"),
            ("talla", "Talla"), ("color", "Color"), ("marca", "Marca")]
    EDITABLES = {"codigo", "referencia", "nombre", "categoria", "costo", "precio", "stock", "talla", "color", "marca"}
    DINERO = {"costo", "precio", "valor"}

    def __init__(self, ctx: Contexto, al_error, al_confirmar=None):
        super().__init__()
        self.ctx = ctx
        self.filas: list[dict[str, Any]] = []
        self.critico = 3
        self.al_error = al_error
        self.al_confirmar = al_confirmar or (lambda _m: True)
        self.ver_inactivos = False

    def cargar(self) -> None:
        self.beginResetModel()
        self.critico = self.ctx.db.get_config_int("stock_critico", 3)
        self.filas = [dict(r) for r in self.ctx.db.listar_productos(incluir_inactivos=self.ver_inactivos)]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.filas)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLS)

    def headerData(self, section, orientation, role=DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == DisplayRole:
            return self.COLS[section][1]
        return None

    @staticmethod
    def _valor(f: dict, clave: str):
        if clave == "margen":
            return ((f["precio"] - f["costo"]) / f["precio"] * 100) if f["precio"] else 0.0
        if clave == "valor":
            return max(f["stock"], 0) * f["costo"]
        return f[clave]

    def data(self, index: QModelIndex, role=DisplayRole):
        if not index.isValid():
            return None
        f = self.filas[index.row()]
        clave = self.COLS[index.column()][0]
        v = self._valor(f, clave)
        T = theme.T
        if role == DisplayRole:
            if clave in self.DINERO:
                return cfg.cop(v)
            if clave == "margen":
                return f"{v:.0f} %"
            return str(v)
        if role == EditRole:
            return v
        if role == UserRole:  # para ordenar numéricamente
            return v if not isinstance(v, str) else v.lower()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if clave in self.DINERO or clave == "margen":
                return int(AlignRight)
            if clave == "stock":
                return int(AlignCenter)
            return None
        if not f.get("activo", 1):
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(T["muted"])
            if role == Qt.ItemDataRole.ToolTipRole:
                return "Producto DESACTIVADO (no aparece en el punto de venta)"
            if role == Qt.ItemDataRole.FontRole:
                fnt = QFont()
                fnt.setItalic(True)
                return fnt
            if role == Qt.ItemDataRole.BackgroundRole:
                return None
        if role == Qt.ItemDataRole.BackgroundRole:
            if f["stock"] <= 0:
                return QColor(T["crit_bg"])
            if f["stock"] <= self.critico:
                return QColor(T["warn_bg"])
        if role == Qt.ItemDataRole.ForegroundRole:
            if f["stock"] <= 0:
                return QColor(T["crit_fg"])
            if f["stock"] <= self.critico:
                return QColor(T["warn_fg"])
            if clave == "margen" and v < 20:
                return QColor(T["warning"])
        if role == Qt.ItemDataRole.FontRole and clave == "stock":
            fnt = QFont()
            fnt.setBold(True)
            return fnt
        if role == Qt.ItemDataRole.ToolTipRole and f["stock"] <= self.critico:
            return "AGOTADO" if f["stock"] <= 0 else f"Stock crítico: quedan {f['stock']}"
        return None

    def flags(self, index: QModelIndex):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.COLS[index.column()][0] in self.EDITABLES:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role=EditRole) -> bool:  # noqa: N802
        if role != EditRole or not index.isValid():
            return False
        f = self.filas[index.row()]
        clave = self.COLS[index.column()][0]
        if clave in ("costo", "precio", "stock"):
            value = cfg.parse_cop(value)
        else:
            value = str(value).strip()
            if clave == "nombre" and not value:
                return False
            if clave in ("codigo", "talla", "color", "marca"):
                value = value.upper()
        if f[clave] == value:
            return False
        # Validaciones contra errores de digitación
        if clave == "stock" and value < 0:
            self.al_error("El stock no puede quedar negativo.")
            return False
        if clave == "stock" and value >= 500 and not self.al_confirmar(
                f"¿Seguro que hay {value} unidades de «{f['nombre']}»?"):
            return False
        if clave == "precio":
            if value <= 0 and not self.al_confirmar("¿Dejar el precio en $ 0? No se podrá vender."):
                return False
            if f["costo"] and 0 < value < f["costo"] and not self.al_confirmar(
                    f"El precio ({cfg.cop(value)}) queda por DEBAJO del costo ({cfg.cop(f['costo'])}). ¿Continuar?"):
                return False
            if f["precio"] and value and (value > f["precio"] * 3 or value < f["precio"] / 3) and not self.al_confirmar(
                    f"El precio cambia de {cfg.cop(f['precio'])} a {cfg.cop(value)}. ¿Es correcto?"):
                return False
        if clave == "costo" and f["precio"] and value > f["precio"] and not self.al_confirmar(
                f"El costo ({cfg.cop(value)}) queda por ENCIMA del precio ({cfg.cop(f['precio'])}). ¿Continuar?"):
            return False
        try:
            self.ctx.db.actualizar_producto(f["id"], {clave: value})
        except POSError as e:
            self.al_error(str(e))
            return False
        f[clave] = value
        izq = self.index(index.row(), 0)
        der = self.index(index.row(), self.columnCount() - 1)
        self.dataChanged.emit(izq, der)
        return True


class FiltroProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.texto = ""
        self.categoria = "Todas"
        self.solo_criticos = False
        self.setSortRole(UserRole)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def actualizar(self, texto: str, categoria: str, solo_criticos: bool) -> None:
        self.texto = texto.lower().split()
        self.categoria = categoria
        self.solo_criticos = solo_criticos
        self.invalidateFilter()

    def filterAcceptsRow(self, fila: int, parent: QModelIndex) -> bool:  # noqa: N802
        m: InventarioModel = self.sourceModel()
        f = m.filas[fila]
        if self.categoria != "Todas" and f["categoria"] != self.categoria:
            return False
        if self.solo_criticos and f["stock"] > m.critico:
            return False
        if self.texto:
            pajar = f"{f['codigo']} {f['referencia']} {f['nombre']} {f['categoria']} {f['marca']}".lower()
            return all(p in pajar for p in self.texto)
        return True


# =========================================================================== #
# Delegados de edición
# =========================================================================== #
class DineroDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # noqa: N802
        return MoneyEdit(parent, grande=False)

    def setEditorData(self, editor: MoneyEdit, index):  # noqa: N802
        editor.setValor(int(index.data(EditRole) or 0), emitir=False)
        editor.selectAll()

    def setModelData(self, editor: MoneyEdit, model, index):  # noqa: N802
        model.setData(index, editor.valor(), EditRole)


class StockDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # noqa: N802
        sp = QSpinBox(parent)
        sp.setRange(-99_999, 999_999)
        sp.setAlignment(AlignCenter)
        return sp

    def setEditorData(self, editor: QSpinBox, index):  # noqa: N802
        editor.setValue(int(index.data(EditRole) or 0))
        editor.selectAll()

    def setModelData(self, editor: QSpinBox, model, index):  # noqa: N802
        editor.interpretText()
        model.setData(index, editor.value(), EditRole)


class CategoriaDelegate(QStyledItemDelegate):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx

    def createEditor(self, parent, option, index):  # noqa: N802
        cb = QComboBox(parent)
        cb.setEditable(True)
        cb.addItems(self.ctx.db.categorias())
        return cb

    def setEditorData(self, editor: QComboBox, index):  # noqa: N802
        editor.setCurrentText(str(index.data(EditRole)))

    def setModelData(self, editor: QComboBox, model, index):  # noqa: N802
        model.setData(index, editor.currentText(), EditRole)


# =========================================================================== #
# Diálogos
# =========================================================================== #
class ProductoDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.nuevo_id: int | None = None
        self.setWindowTitle("Nuevo producto")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.addWidget(etiqueta("Nuevo producto", titulo=True))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        fila_cod = QHBoxLayout()
        self.codigo = QLineEdit()
        self.codigo.setPlaceholderText("Escanee o deje vacío para generar EAN-13")
        gen = boton("Generar")
        gen.clicked.connect(lambda: self.codigo.setText(ctx.db.generar_codigo()))
        fila_cod.addWidget(self.codigo, 1)
        fila_cod.addWidget(gen)
        form.addRow("Código de barras", fila_cod)
        self.ref = QLineEdit()
        form.addRow("Referencia", self.ref)
        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Ej. Jean Clásico Hombre T32")
        form.addRow("Nombre *", self.nombre)
        self.cat = QComboBox()
        self.cat.setEditable(True)
        self.cat.addItems(ctx.db.categorias())
        form.addRow("Categoría", self.cat)
        fila_var = QHBoxLayout()
        self.talla = QLineEdit()
        self.talla.setPlaceholderText("Talla (8, 32, M, XL…)")
        self.color = QLineEdit()
        self.color.setPlaceholderText("Color")
        self.marca = QLineEdit()
        self.marca.setPlaceholderText("Marca / modelo")
        for w in (self.talla, self.color, self.marca):
            fila_var.addWidget(w)
        form.addRow("Variante", fila_var)
        self.costo = MoneyEdit(grande=False)
        form.addRow("Costo de compra", self.costo)
        self.precio = MoneyEdit(grande=False)
        form.addRow("Precio de venta", self.precio)
        self.stock = QSpinBox()
        self.stock.setRange(0, 999_999)
        form.addRow("Stock inicial", self.stock)
        lay.addLayout(form)
        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        ok = boton("Guardar producto", "success", "lg")
        ok.clicked.connect(self.guardar)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)
        self.codigo.setFocus()

    def guardar(self) -> None:
        if not self.nombre.text().strip():
            error(self, "Escriba el nombre del producto.")
            return
        if self.precio.valor() and self.costo.valor() > self.precio.valor():
            if not confirmar(self, "El costo es mayor que el precio de venta. ¿Guardar de todas formas?"):
                return
        try:
            self.nuevo_id = self.ctx.db.crear_producto({
                "codigo": self.codigo.text().strip().upper(), "referencia": self.ref.text().strip().upper(),
                "nombre": xe.componer_nombre(self.nombre.text(), self.color.text(), self.talla.text()),
                "categoria": self.cat.currentText().strip(), "costo": self.costo.valor(),
                "precio": self.precio.valor(), "stock": self.stock.value(),
                "talla": xe.limpiar_codigo(self.talla.text()), "color": self.color.text().strip().upper(),
                "marca": self.marca.text().strip().upper()})
        except POSError as e:
            error(self, str(e))
            return
        self.accept()


class ImportarDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, ruta: str, resultado: xe.ResultadoImportacion, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.ruta = ruta
        self.res = resultado
        self.setWindowTitle("Importar inventario desde Excel")
        self.resize(1080, 680)
        lay = QVBoxLayout(self)
        cab = QHBoxLayout()
        nombre = ruta.replace("\\", "/").split("/")[-1]
        cab.addWidget(etiqueta(f"Vista previa: {nombre}", titulo=True))
        cab.addStretch()
        self.hoja = QComboBox()
        self.hoja.addItems(resultado.hojas)
        self.hoja.setCurrentText(resultado.hoja)
        self.hoja.setMinimumWidth(220)
        self.hoja.currentTextChanged.connect(self._cambiar_hoja)
        if len(resultado.hojas) > 1:
            cab.addWidget(QLabel("Hoja:"))
            cab.addWidget(self.hoja)
        lay.addLayout(cab)
        self.resumen = QTextEdit()
        self.resumen.setReadOnly(True)
        self.resumen.setMaximumHeight(150)
        lay.addWidget(self.resumen)
        self.tabla = QTableWidget(0, 10)
        self.tabla.setHorizontalHeaderLabels(["Código", "Producto (como se verá en el POS)", "Categoría", "Talla",
                                              "Color", "Marca", "Costo", "Precio", "Stock", "Margen"])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabla.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.tabla, 1)
        fila_modo = QHBoxLayout()
        fila_modo.addWidget(QLabel("Productos existentes:"))
        self.rb_reemplazar = QRadioButton("Reemplazar stock con el del archivo (conteo físico)")
        self.rb_sumar = QRadioButton("Sumar al stock actual (entrada de mercancía)")
        self.rb_reemplazar.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_reemplazar)
        grp.addButton(self.rb_sumar)
        fila_modo.addWidget(self.rb_reemplazar)
        fila_modo.addWidget(self.rb_sumar)
        fila_modo.addStretch()
        lay.addLayout(fila_modo)
        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        self.ok = boton("", "success", "lg")
        self.ok.clicked.connect(self.importar)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(self.ok)
        lay.addLayout(fila)
        self._mostrar()

    def _cambiar_hoja(self, hoja: str) -> None:
        try:
            self.res = xe.importar_inventario(self.ruta, hoja)
        except Exception as e:  # noqa: BLE001
            self.res = xe.ResultadoImportacion(hojas=self.res.hojas, hoja=hoja)
            self.res.advertencias.append(f"No se pudo leer la hoja: {e}")
        self._mostrar()

    def _mostrar(self) -> None:
        r = self.res
        txt = r.resumen
        if r.omitidas:
            txt += "\n\nFilas omitidas:\n" + "\n".join(f"  Fila {n}: {m}" for n, m in r.omitidas[:50])
        self.resumen.setPlainText(txt)
        self.tabla.setRowCount(min(len(r.filas), 500))
        for i, f in enumerate(r.filas[:500]):
            margen = f"{(f['precio'] - f['costo']) / f['precio'] * 100:.0f} %" if f["precio"] else "—"
            vals = [f["codigo"] or "(auto)", f["nombre"], f["categoria"], f["talla"], f["color"], f["marca"],
                    cfg.cop(f["costo"]), cfg.cop(f["precio"]), str(f["stock"]), margen]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j >= 6:
                    it.setTextAlignment(AlignRight)
                self.tabla.setItem(i, j, it)
        und = sum(f["stock"] for f in r.filas)
        self.ok.setText(f"Importar {len(r.filas)} productos ({und:,} unidades)".replace(",", "."))
        self.ok.setEnabled(bool(r.filas))

    def importar(self) -> None:
        modo = "sumar" if self.rb_sumar.isChecked() else "reemplazar"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.ctx.db.respaldar()
            r = self.ctx.db.importar_productos(self.res.filas, modo, self.res.encabezados)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            error(self, f"No se pudo importar:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        info(self, f"Importación completada.\n\nNuevos: {r['nuevos']}\nActualizados: {r['actualizados']}")
        self.accept()


class EtiquetasDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, producto_id: int | None = None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.img = None
        conf = ctx.conf
        self.setWindowTitle("Generador de etiquetas con código de barras")
        self.resize(820, 560)
        lay = QHBoxLayout(self)
        lay.setSpacing(18)

        izq = QVBoxLayout()
        izq.addWidget(etiqueta("Etiquetas adhesivas", titulo=True))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.producto = QComboBox()
        self.producto.setEditable(True)
        self.producto.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for p in ctx.db.listar_productos():
            self.producto.addItem(f"{p['nombre']}  ·  {p['referencia'] or p['codigo']}  ·  {cfg.cop(p['precio'])}",
                                  p["id"])
        comp = self.producto.completer()
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        if producto_id is not None:
            i = self.producto.findData(producto_id)
            if i >= 0:
                self.producto.setCurrentIndex(i)
        form.addRow("Producto", self.producto)
        self.copias = QSpinBox()
        self.copias.setRange(1, 999)
        form.addRow("Copias", self.copias)
        self.ancho = QSpinBox()
        self.ancho.setRange(25, 104)
        self.ancho.setSuffix(" mm")
        self.ancho.setValue(int(float(conf.get("etiqueta_ancho_mm", 50))))
        self.alto = QSpinBox()
        self.alto.setRange(15, 100)
        self.alto.setSuffix(" mm")
        self.alto.setValue(int(float(conf.get("etiqueta_alto_mm", 30))))
        tam = QHBoxLayout()
        tam.addWidget(self.ancho)
        tam.addWidget(QLabel("×"))
        tam.addWidget(self.alto)
        form.addRow("Tamaño", tam)
        self.simb = QComboBox()
        self.simb.addItem("Automática (EAN-13 si es válido)", "auto")
        self.simb.addItem("Code128", "code128")
        self.simb.addItem("EAN-13", "ean13")
        self.simb.setCurrentIndex(max(0, self.simb.findData(conf.get("etiqueta_simbologia", "auto"))))
        form.addRow("Simbología", self.simb)
        self.chk_tienda = QCheckBox("Mostrar nombre de la tienda")
        self.chk_tienda.setChecked(conf.get("etiqueta_mostrar_tienda", "1") == "1")
        form.addRow("", self.chk_tienda)
        self.letra = QComboBox()
        self.letra.addItem("Normal (letra de imprenta)", "normal")
        for nombre in be.FUENTES_CURSIVAS:
            self.letra.addItem(f"Cursiva · {nombre}", nombre)
        actual = conf.get("etiqueta_fuente", "Dancing Script") if conf.get("etiqueta_estilo", "cursiva") == "cursiva" \
            else "normal"
        self.letra.setCurrentIndex(max(0, self.letra.findData(actual)))
        self.letra.setToolTip("Solo cambia el nombre de la tienda. Las barras y el código no se tocan: "
                              "así el lector las sigue leyendo igual.")
        form.addRow("Letra del nombre", self.letra)
        izq.addLayout(form)
        izq.addStretch()
        b_png = boton("Guardar PNG", size="lg")
        b_png.clicked.connect(self.guardar_png)
        b_imp = boton("IMPRIMIR ETIQUETAS", "success", "lg")
        b_imp.clicked.connect(self.imprimir)
        izq.addWidget(b_png)
        izq.addWidget(b_imp)
        lay.addLayout(izq, 1)

        der = tarjeta()
        dl = QVBoxLayout(der)
        dl.addWidget(etiqueta("Vista previa (203 dpi)", muted=True))
        self.preview = QLabel()
        self.preview.setAlignment(AlignCenter)
        self.preview.setMinimumSize(380, 260)
        self.preview.setStyleSheet("background:white;border-radius:8px;color:#B91C1C;")
        dl.addWidget(self.preview, 1)
        lay.addWidget(der, 1)

        for s in (self.producto.currentIndexChanged, self.ancho.valueChanged, self.alto.valueChanged,
                  self.simb.currentIndexChanged, self.chk_tienda.toggled, self.letra.currentIndexChanged):
            s.connect(lambda *_: self._timer.start())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.actualizar)
        self._ajustar_copias()
        self.producto.currentIndexChanged.connect(lambda *_: self._ajustar_copias())
        self.actualizar()

    def _estilo(self) -> tuple[str, str]:
        dato = self.letra.currentData()
        return ("normal", "Dancing Script") if dato == "normal" else ("cursiva", dato)

    def _producto(self):
        pid = self.producto.currentData()
        return self.ctx.db.producto_por_id(int(pid)) if pid is not None else None

    def _ajustar_copias(self) -> None:
        p = self._producto()
        self.copias.setValue(max(1, p["stock"]) if p else 1)

    def actualizar(self) -> None:
        p = self._producto()
        if not p:
            self.preview.setText("Seleccione un producto")
            self.img = None
            return
        try:
            self.img = be.generar_etiqueta(p, self.ancho.value(), self.alto.value(), self.simb.currentData(),
                                           self.ctx.db.get_config("razon_social"), self.chk_tienda.isChecked(),
                                           *self._estilo())
        except Exception as e:  # noqa: BLE001
            self.img = None
            self.preview.setText(f"No se pudo generar el código:\n{e}")
            return
        pm = pil_a_pixmap(self.img)
        self.preview.setPixmap(pm.scaled(self.preview.width() - 20, self.preview.height() - 20,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.FastTransformation))

    def guardar_png(self) -> None:
        if self.img is None:
            return
        p = self._producto()
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar etiqueta",
                                              str(cfg.exports_dir() / f"Etiqueta_{p['codigo']}.png"), "PNG (*.png)")
        if ruta:
            be.guardar_etiqueta_png(self.img, ruta)
            self.ctx.notificar("Etiqueta guardada", "ok")

    def imprimir(self) -> None:
        if self.img is None:
            return
        conf = dict(self.ctx.conf)
        conf.update({"etiqueta_ancho_mm": str(self.ancho.value()), "etiqueta_alto_mm": str(self.alto.value())})
        if conf.get("modo_etiquetas", "windows") != "escpos" and not conf.get("impresora_etiquetas"):
            from ui.widgets import error
            error(self, "Aún no se ha elegido la impresora de ETIQUETAS (SAT TT460).\n\n"
                        "Vaya a Ajustes › Etiquetas, pulse «Detectar» y luego «Imprimir etiqueta de prueba».")
            return
        self.ctx.db.set_config({"etiqueta_ancho_mm": self.ancho.value(), "etiqueta_alto_mm": self.alto.value(),
                                "etiqueta_simbologia": self.simb.currentData(),
                                "etiqueta_mostrar_tienda": "1" if self.chk_tienda.isChecked() else "0",
                                "etiqueta_estilo": self._estilo()[0], "etiqueta_fuente": self._estilo()[1]})
        img, n = self.img, self.copias.value()
        en_segundo_plano(lambda: pe.imprimir_etiquetas(img, n, conf),
                         lambda total: self.ctx.notificar(
                             f"{total} etiqueta(s) enviadas a la impresora"
                             + (f" (filas completas del rollo; pidió {n})" if total and total != n else ""), "ok"),
                         lambda m: error(self, m, "Error imprimiendo etiquetas"))


# =========================================================================== #
# Vista principal
# =========================================================================== #
class InventarioView(QWidget):
    titulo = "Inventario y Etiquetas"

    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        self._emitiendo = False
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 20)
        raiz.setSpacing(12)

        barra = QHBoxLayout()
        barra.setSpacing(8)
        self.buscar = QLineEdit()
        self.buscar.setObjectName("search")
        self.buscar.setPlaceholderText("Buscar por código, nombre, talla, color, marca o categoría…")
        self.buscar.setClearButtonEnabled(True)
        barra.addWidget(self.buscar, 1)
        self.cat = QComboBox()
        self.cat.setMinimumWidth(140)
        self.cat.setMinimumHeight(48)
        barra.addWidget(self.cat)
        self.chk_crit = QCheckBox("Solo stock crítico")
        barra.addWidget(self.chk_crit)
        raiz.addLayout(barra)

        self.b_nuevo = boton("Nuevo producto", "primary")
        self.b_import = boton("Importar Excel")
        self.b_export = boton("Inventario valorizado")
        self.b_formato = boton("Exportar en formato original")
        self.b_formato.setToolTip("Mismas columnas de 'Inventario General 01.xlsx' con el stock actual,\n"
                                  "listo para subir al software de facturación")
        self.b_plant = boton("Plantilla de Excel", "ghost")
        self.b_etq = boton("Etiquetas código de barras", "success")
        self.b_desact = boton("Desactivar", "danger")
        self.b_eliminar = boton("Eliminar (creado por error)", "danger")
        self.b_deshacer = boton("Deshacer última importación", "ghost")
        self.b_reactivar = boton("Reactivar", "ghost")
        self.chk_inactivos = QCheckBox("Ver desactivados")
        self.b_conteo = boton("Conteo físico (escáner)", "primary")
        self.b_conteo.setToolTip("Contar la ropa con el lector y ajustar diferencias (queda registrado)")
        self.b_reportes = boton("Reportes de gestión")
        self.b_reportes.setToolTip("Mercancía quieta, ventas por talla/color/categoría, comparativo, sugerido de compra")
        # Los botones saltan a otra línea si no caben (pantallas de 1366 px)
        cont = QWidget()
        cont.setObjectName("transparente")
        flujo = FlujoLayout(cont, 8)
        for w in (self.b_nuevo, self.b_import, self.b_etq, self.b_conteo, self.b_reportes, self.b_export,
                  self.b_formato, self.b_plant, self.b_deshacer, self.b_desact, self.b_eliminar,
                  self.chk_inactivos, self.b_reactivar):
            flujo.addWidget(w)
        cont.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        raiz.addWidget(cont)

        self.modelo = InventarioModel(ctx, lambda m: error(self, m), lambda m: confirmar(self, m))
        self.proxy = FiltroProxy()
        self.proxy.setSourceModel(self.modelo)
        self.tabla = QTableView()
        self.tabla.setModel(self.proxy)
        self.tabla.setSortingEnabled(True)
        self.tabla.sortByColumn(2, Qt.SortOrder.AscendingOrder)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.verticalHeader().setDefaultSectionSize(40)
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabla.setColumnHidden(1, True)  # Referencia: el archivo de la tienda no la usa (se ve si se importa)
        self._d_cat = CategoriaDelegate(ctx, self.tabla)
        self.tabla.setItemDelegateForColumn(3, self._d_cat)
        self._d_dinero = DineroDelegate(self.tabla)
        self.tabla.setItemDelegateForColumn(4, self._d_dinero)
        self.tabla.setItemDelegateForColumn(5, self._d_dinero)
        self._d_stock = StockDelegate(self.tabla)
        self.tabla.setItemDelegateForColumn(6, self._d_stock)
        raiz.addWidget(self.tabla, 1)

        self.lbl_resumen = etiqueta("", muted=True)
        raiz.addWidget(self.lbl_resumen)
        raiz.addWidget(etiqueta("Doble clic (o empezar a escribir) sobre una celda para editarla. "
                                "Amarillo = stock crítico · Rojo = agotado.", muted=True))

        # Conexiones
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._filtrar)
        self.buscar.textChanged.connect(lambda _: self._timer.start())
        self.cat.currentTextChanged.connect(lambda _: self._filtrar())
        self.chk_crit.toggled.connect(lambda _: self._filtrar())
        self.b_nuevo.clicked.connect(self.nuevo)
        self.b_import.clicked.connect(self.importar)
        self.b_export.clicked.connect(lambda: exportar_y_ofrecer_abrir(
            self, ctx, f"Inventario_Valorizado_{datetime.now():%Y%m%d_%H%M}.xlsx",
            lambda ruta: xe.exportar_inventario(ctx.db, ruta)))
        self.b_formato.clicked.connect(lambda: exportar_y_ofrecer_abrir(
            self, ctx, f"Inventario_General_{datetime.now():%Y%m%d_%H%M}.xlsx",
            lambda ruta: xe.exportar_formato_carga(ctx.db, ruta)))
        self.b_plant.clicked.connect(self.plantilla)
        self.b_etq.clicked.connect(self.etiquetas)
        self.b_desact.clicked.connect(self.desactivar)
        self.b_eliminar.clicked.connect(self.eliminar)
        self.b_deshacer.clicked.connect(self.deshacer_importacion)
        self.b_conteo.clicked.connect(self.conteo)
        self.b_reportes.clicked.connect(self.reportes)
        self.b_reactivar.clicked.connect(self.reactivar)
        self.chk_inactivos.toggled.connect(self._ver_inactivos)
        self.modelo.dataChanged.connect(lambda *_: self._cambio_propio())
        ctx.bus.productos_cambiados.connect(self._cambio_externo)
        ctx.bus.config_cambiada.connect(self._cambio_externo)
        self.recargar()

    # ------------------------------------------------------------------ datos
    def recargar(self) -> None:
        actual = self.cat.currentText() or "Todas"
        self.cat.blockSignals(True)
        self.cat.clear()
        self.cat.addItems(["Todas"] + self.ctx.db.categorias())
        i = self.cat.findText(actual)
        self.cat.setCurrentIndex(i if i >= 0 else 0)
        self.cat.blockSignals(False)
        self.modelo.cargar()
        self.tabla.setColumnHidden(1, not any(f["referencia"] for f in self.modelo.filas))
        self._filtrar()

    def _filtrar(self) -> None:
        self.proxy.actualizar(self.buscar.text(), self.cat.currentText() or "Todas", self.chk_crit.isChecked())
        self._resumen()

    def _resumen(self) -> None:
        f = self.modelo.filas
        crit = sum(1 for x in f if 0 < x["stock"] <= self.modelo.critico)
        agot = sum(1 for x in f if x["stock"] <= 0)
        und = sum(max(x["stock"], 0) for x in f)
        vc = sum(max(x["stock"], 0) * x["costo"] for x in f)
        vv = sum(max(x["stock"], 0) * x["precio"] for x in f)
        self.lbl_resumen.setText(
            f"{self.proxy.rowCount()} de {len(f)} referencias  ·  {und:,} unidades  ·  Valor al costo "
            f"{cfg.cop(vc)}  ·  Valor a precio venta {cfg.cop(vv)}  ·  {crit} críticos  ·  {agot} agotados"
            .replace(",", "."))

    def _cambio_propio(self) -> None:
        self._resumen()
        self._emitiendo = True
        self.ctx.bus.productos_cambiados.emit()
        self._emitiendo = False

    def _cambio_externo(self) -> None:
        if not self._emitiendo:
            self.recargar()

    def _seleccionados(self) -> list[dict]:
        filas = {self.proxy.mapToSource(i).row() for i in self.tabla.selectionModel().selectedRows()}
        return [self.modelo.filas[r] for r in sorted(filas)]

    # ------------------------------------------------------------------ acciones
    def nuevo(self) -> None:
        dlg = ProductoDialog(self.ctx, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ctx.bus.productos_cambiados.emit()
            self.ctx.notificar("Producto creado", "ok")
            if confirmar(self, "¿Desea imprimir las etiquetas de código de barras de este producto?",
                         "Etiquetas"):
                EtiquetasDialog(self.ctx, dlg.nuevo_id, self).exec()

    def importar(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(self, "Seleccione el archivo de inventario", str(cfg.exports_dir()),
                                              "Excel / CSV (*.xlsx *.xlsm *.xls *.ods *.csv)")
        if not ruta:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = xe.importar_inventario(ruta)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            error(self, f"No se pudo leer el archivo:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        if ImportarDialog(self.ctx, ruta, res, self).exec() == QDialog.DialogCode.Accepted:
            self.ctx.bus.productos_cambiados.emit()

    def plantilla(self) -> None:
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar plantilla",
                                              str(cfg.exports_dir() / "Plantilla_Inventario_R4.xlsx"),
                                              "Excel (*.xlsx)")
        if ruta:
            xe.generar_plantilla_inventario(ruta)
            self.ctx.notificar("Plantilla guardada", "ok")

    def conteo(self) -> None:
        from ui.conteo_view import ConteoDialog
        ConteoDialog(self.ctx, self).exec()

    def reportes(self) -> None:
        from ui.reportes_view import ReportesDialog
        ReportesDialog(self.ctx, self).exec()

    def etiquetas(self) -> None:
        sel = self._seleccionados()
        EtiquetasDialog(self.ctx, sel[0]["id"] if sel else None, self).exec()

    def desactivar(self) -> None:
        sel = self._seleccionados()
        if not sel:
            error(self, "Seleccione uno o más productos.")
            return
        nombres = "\n".join(f"• {p['nombre']}" for p in sel[:10]) + ("\n…" if len(sel) > 10 else "")
        if not confirmar(self, f"¿Desactivar {len(sel)} producto(s)?\n\n{nombres}\n\n"
                               "Dejarán de aparecer en el POS; el historial de ventas se conserva."):
            return
        for p in sel:
            self.ctx.db.desactivar_producto(p["id"])
        self.ctx.bus.productos_cambiados.emit()

    def _ver_inactivos(self, on: bool) -> None:
        self.modelo.ver_inactivos = on
        self.recargar()

    def eliminar(self) -> None:
        sel = self._seleccionados()
        if not sel:
            error(self, "Seleccione el producto creado por error.")
            return
        nombres = "\n".join(f"• {p['nombre']}" for p in sel[:10])
        if not autorizar(self, self.ctx, f"Eliminar definitivamente {len(sel)} producto(s):\n{nombres}"):
            return
        borrados, con_ventas = 0, []
        for p in sel:
            try:
                self.ctx.db.eliminar_producto(p["id"])
                borrados += 1
            except POSError:
                con_ventas.append(p)
        if con_ventas and confirmar(self, f"{len(con_ventas)} producto(s) ya tienen ventas y no se pueden borrar "
                                          "(se perdería el historial).\n\n¿Desactivarlos en su lugar?"):
            for p in con_ventas:
                self.ctx.db.desactivar_producto(p["id"])
        self.ctx.notificar(f"{borrados} producto(s) eliminados", "ok")
        self.ctx.bus.productos_cambiados.emit()

    def deshacer_importacion(self) -> None:
        imp = self.ctx.db.ultima_importacion()
        if imp is None or imp["deshecha"]:
            error(self, "No hay ninguna importación para deshacer.")
            return
        if not autorizar(self, self.ctx, f"Deshacer la importación del {imp['fecha']} "
                                         f"({imp['nuevos']} nuevos, {imp['actualizados']} actualizados). Los productos "
                                         "vuelven a como estaban antes y se quitan los que se crearon."):
            return
        try:
            self.ctx.db.respaldar()
            r = self.ctx.db.deshacer_importacion()
        except POSError as e:
            error(self, str(e))
            return
        info(self, f"Importación deshecha.\n\nRestaurados: {r['restaurados']}\nEliminados: {r['eliminados']}\n"
                   f"Desactivados (ya tenían ventas): {r['desactivados']}")
        self.ctx.bus.productos_cambiados.emit()

    def reactivar(self) -> None:
        sel = [p for p in self._seleccionados() if not p.get("activo", 1)]
        if not sel:
            error(self, "Marque «Ver desactivados» y seleccione los productos a reactivar.")
            return
        for p in sel:
            self.ctx.db.actualizar_producto(p["id"], {"activo": 1})
        self.ctx.notificar(f"{len(sel)} producto(s) reactivados", "ok")
        self.ctx.bus.productos_cambiados.emit()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.buscar.setFocus()
