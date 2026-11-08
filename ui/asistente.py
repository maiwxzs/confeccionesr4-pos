"""
ui/asistente.py — Asistente de configuración inicial (primer uso / programa recién entregado).

Aparece solo cuando la base de datos está en blanco (sin productos ni ventas) y nunca se
configuró. Pasos:
  1. Bienvenida
  2. Datos del negocio (razón social, NIT, régimen, dirección, ciudad, teléfono)
  3. Numeración de tiquetes (resolución DIAN propia o comprobante interno)
  4. Operación (cajero, cajón monedero, stock crítico, clave de administrador)
  5. Impresora térmica (detección automática + página de prueba)
  6. Inventario (importar Excel ahora o después)
"""
from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

import config as cfg
import printer_engine as pe
from ui.animations import DialogoAnimado, aparecer
from ui.widgets import Contexto, boton, en_segundo_plano, error, etiqueta


def necesita_asistente(db) -> bool:
    if db.get_config("configurado") == "1":
        return False
    hay_datos = db.q1("SELECT (SELECT COUNT(*) FROM productos) + (SELECT COUNT(*) FROM ventas)")[0]
    if hay_datos:                               # instalación previa que ya se está usando
        db.set_config({"configurado": "1"}, auditar=False)
        return False
    return True


def _pagina(titulo: str, texto: str = "") -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("transparente")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(10)
    lay.addWidget(etiqueta(titulo, titulo=True))
    if texto:
        t = etiqueta(texto, muted=True)
        t.setWordWrap(True)
        lay.addWidget(t)
    return w, lay


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setVerticalSpacing(10)
    f.setHorizontalSpacing(14)
    return f


class AsistenteInicial(DialogoAnimado):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.importar_al_final = False
        c = ctx.conf
        self.setWindowTitle("Configuración inicial — ConfeccionesR4 POS")
        self.setMinimumSize(680, 540)
        raiz = QVBoxLayout(self)
        raiz.setSpacing(12)
        self.lbl_paso = etiqueta("", kpiTitulo=True)
        raiz.addWidget(self.lbl_paso)
        self.stack = QStackedWidget()
        raiz.addWidget(self.stack, 1)

        # 1. Bienvenida
        w, l = _pagina("Bienvenido a su punto de venta",
                       "En 5 pasos dejamos el programa listo: datos del negocio, numeración de los tiquetes, "
                       "cajero, impresora e inventario. Todo se puede cambiar después en Ajustes.")
        l.addStretch()
        self.stack.addWidget(w)

        # 2. Negocio
        w, l = _pagina("Datos del negocio", "Aparecen en el encabezado de los tiquetes y de los reportes.")
        f = _form()
        self.campos: dict[str, QLineEdit] = {}
        for clave, txt in (("razon_social", "Nombre / Razón social *"), ("nit", "NIT o cédula *"),
                           ("regimen", "Régimen tributario"), ("direccion", "Dirección"), ("ciudad", "Ciudad"),
                           ("telefono", "Teléfono")):
            e = QLineEdit(c.get(clave, ""))
            e.setMinimumHeight(38)
            self.campos[clave] = e
            f.addRow(txt, e)
        l.addLayout(f)
        l.addStretch()
        self.stack.addWidget(w)

        # 3. Numeración
        w, l = _pagina("Numeración de los tiquetes",
                       "Si la DIAN le autorizó una numeración (resolución), escríbala. Si no, el programa imprime "
                       "un «Comprobante interno de venta» numerado (no es factura electrónica).")
        self.rb_sin = QRadioButton("No tengo resolución: imprimir comprobante interno")
        self.rb_con = QRadioButton("Tengo resolución DIAN propia")
        g = QButtonGroup(self)
        g.addButton(self.rb_sin)
        g.addButton(self.rb_con)
        tiene = not cfg.es_resolucion_de_prueba(c)
        (self.rb_con if tiene else self.rb_sin).setChecked(True)
        l.addWidget(self.rb_sin)
        l.addWidget(self.rb_con)
        self.form_res = QWidget()
        f = _form()
        self.form_res.setLayout(f)
        self.res = QLineEdit(c.get("resolucion_dian", "") if tiene else "")
        self.prefijo = QLineEdit(c.get("prefijo", "") if tiene else "")
        self.desde = QLineEdit(c.get("rango_desde", "") if tiene else "")
        self.hasta = QLineEdit(c.get("rango_hasta", "") if tiene else "")
        for e, txt in ((self.res, "Resolución No."), (self.prefijo, "Prefijo"), (self.desde, "Desde el número"),
                       (self.hasta, "Hasta el número")):
            e.setMinimumHeight(38)
            f.addRow(txt, e)
        l.addWidget(self.form_res)
        self.rb_con.toggled.connect(self.form_res.setVisible)
        self.form_res.setVisible(tiene)
        l.addStretch()
        self.stack.addWidget(w)

        # 4. Operación
        w, l = _pagina("Operación del día a día")
        f = _form()
        self.cajero = QLineEdit(c.get("cajero_actual", "Cajero"))
        f.addRow("Nombre del cajero", self.cajero)
        self.stock = QSpinBox()
        self.stock.setRange(0, 1000)
        self.stock.setValue(int(c.get("stock_critico", 3) or 3))
        self.stock.setSuffix(" und")
        f.addRow("Avisar stock bajo cuando queden", self.stock)
        self.chk_cajon = QCheckBox("Tengo un CAJÓN MONEDERO conectado a la impresora (se abre solo al cobrar en efectivo)")
        self.chk_cajon.setChecked(c.get("abrir_cajon_efectivo") == "1")
        f.addRow("", self.chk_cajon)
        nota = etiqueta("Si guarda el efectivo en un cajón normal, una caja o el bolsillo, deje esta opción "
                        "desmarcada: el arqueo funciona igual contando ese dinero.", muted=True)
        nota.setWordWrap(True)
        f.addRow("", nota)
        self.pin = QLineEdit()
        self.pin2 = QLineEdit()
        for e, ph in ((self.pin, "4 a 8 dígitos (recomendado)"), (self.pin2, "Repita la clave")):
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setMaxLength(8)
            e.setPlaceholderText(ph)
        f.addRow("Clave de administrador", self.pin)
        f.addRow("Repetir clave", self.pin2)
        l.addLayout(f)
        l.addStretch()
        self.stack.addWidget(w)

        # 5. Impresora
        w, l = _pagina("Impresora de tiquetes", "Conecte y encienda la impresora térmica antes de continuar.")
        f = _form()
        self.impresora = QComboBox()
        self.impresora.setEditable(True)
        self.impresora.addItem("(Impresora predeterminada de Windows)", "")
        for p in pe.listar_impresoras():
            self.impresora.addItem(p, p)
        termicas = [i for i in range(self.impresora.count()) if any(
            k in self.impresora.itemText(i).lower() for k in ("jaltech", "pos", "80", "58", "thermal", "termica"))]
        if termicas:
            self.impresora.setCurrentIndex(termicas[0])
        f.addRow("Impresora", self.impresora)
        self.ancho = QComboBox()
        self.ancho.addItem("80 mm (rollo ancho)", "80")
        self.ancho.addItem("58 mm (rollo angosto)", "58")
        if "58" in self.impresora.currentText():
            self.ancho.setCurrentIndex(1)
        f.addRow("Papel", self.ancho)
        prueba = boton("Imprimir página de prueba", "primary")
        prueba.clicked.connect(self._prueba)
        f.addRow("", prueba)
        l.addLayout(f)
        l.addStretch()
        self.stack.addWidget(w)

        # 6. Inventario
        w, l = _pagina("Inventario", "Puede importar ahora su archivo de Excel (por ejemplo «Inventario General "
                                     "01.xlsx») o crear los productos después en el módulo Inventario.")
        self.chk_importar = QCheckBox("Importar el inventario de Excel al terminar")
        self.chk_importar.setChecked(True)
        l.addWidget(self.chk_importar)
        l.addStretch()
        self.stack.addWidget(w)

        # Navegación
        fila = QHBoxLayout()
        self.btn_atras = boton("Atrás", "ghost", "lg")
        self.btn_atras.clicked.connect(lambda: self._ir(self.stack.currentIndex() - 1))
        self.btn_sig = boton("Siguiente", "primary", "lg")
        self.btn_sig.clicked.connect(self._siguiente)
        fila.addWidget(self.btn_atras)
        fila.addStretch()
        fila.addWidget(self.btn_sig)
        raiz.addLayout(fila)
        self._ir(0)

    # ------------------------------------------------------------------
    def _ir(self, i: int) -> None:
        i = max(0, min(i, self.stack.count() - 1))
        self.stack.setCurrentIndex(i)
        aparecer(self.stack.currentWidget(), 260, desplazar=12)
        self.lbl_paso.setText(f"PASO {i + 1} DE {self.stack.count()}")
        self.btn_atras.setEnabled(i > 0)
        self.btn_sig.setText("Terminar" if i == self.stack.count() - 1 else "Siguiente")

    def _validar(self, i: int) -> str | None:
        if i == 1:
            if len(self.campos["razon_social"].text().strip()) < 2:
                return "Escriba el nombre o razón social del negocio."
            if len("".join(ch for ch in self.campos["nit"].text() if ch.isdigit())) < 5:
                return "Escriba un NIT o cédula válido."
        if i == 2 and self.rb_con.isChecked():
            if not self.res.text().strip() or not self.prefijo.text().strip():
                return "Escriba el número de resolución y el prefijo."
            try:
                d, h = int(self.desde.text()), int(self.hasta.text())
            except ValueError:
                return "Los números del rango deben ser enteros."
            if d > h:
                return "El rango «desde» no puede ser mayor que «hasta»."
        if i == 3:
            if self.pin.text() or self.pin2.text():
                if self.pin.text() != self.pin2.text():
                    return "Las claves no coinciden."
                if not self.pin.text().isdigit() or not 4 <= len(self.pin.text()) <= 8:
                    return "La clave debe tener entre 4 y 8 dígitos."
        return None

    def _siguiente(self) -> None:
        i = self.stack.currentIndex()
        problema = self._validar(i)
        if problema:
            error(self, problema)
            return
        if i < self.stack.count() - 1:
            self._ir(i + 1)
            return
        self._guardar()

    def _valores(self) -> dict[str, str]:
        v = {k: e.text().strip() for k, e in self.campos.items()}
        v["razon_social"] = v["razon_social"].upper()
        if self.rb_con.isChecked():
            v.update(resolucion_dian=self.res.text().strip(), prefijo=self.prefijo.text().strip().upper(),
                     rango_desde=str(int(self.desde.text())), rango_hasta=str(int(self.hasta.text())),
                     consecutivo_siguiente=str(int(self.desde.text())))
        else:  # comprobante interno numerado desde 1
            v.update(resolucion_dian="", prefijo="CI", rango_desde="1", rango_hasta="999999999",
                     consecutivo_siguiente="1")
        d = self.impresora.currentData()
        imp = self.impresora.currentText().strip() if d is None or self.impresora.currentText() != \
            self.impresora.itemText(self.impresora.currentIndex()) else d
        v.update(cajero_actual=self.cajero.text().strip() or "Cajero", stock_critico=str(self.stock.value()),
                 abrir_cajon_efectivo="1" if self.chk_cajon.isChecked() else "0",
                 impresora_tiquetes=imp or "", ancho_papel_mm=self.ancho.currentData(), transporte_impresion="win32")
        return v

    def _prueba(self) -> None:
        conf = dict(self.ctx.conf)
        conf.update(self._valores() if not self._validar(1) else {})
        datos = pe.construir_prueba(conf).bytes()
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo="Prueba"),
                         lambda _: self.ctx.notificar("Página de prueba enviada", "ok"),
                         lambda m: error(self, f"No se pudo imprimir:\n{m}"))

    def _guardar(self) -> None:
        db = self.ctx.db
        v = self._valores()
        v["configurado"] = "1"
        db.set_config(v)
        if self.pin.text():
            try:
                db.definir_pin(self.pin.text())
            except Exception as e:  # noqa: BLE001
                error(self, str(e))
        self.importar_al_final = self.chk_importar.isChecked()
        self.ctx.bus.config_cambiada.emit()
        self.ctx.notificar("Configuración inicial guardada", "ok")
        self.accept()
