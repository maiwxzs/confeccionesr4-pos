"""
ui/settings_dialog.py — Configuración: datos fiscales/DIAN, impresoras, etiquetas, operación y respaldos.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

import config as cfg
import printer_engine as pe
import excel_engine as xe
from ui.animations import DialogoAnimado
from ui.seguridad import autorizar
from ui.widgets import (Contexto, abrir_archivo, boton, confirmar, en_segundo_plano, error, etiqueta,
                        desplazable, exportar_y_ofrecer_abrir, info)


def _form() -> tuple[QWidget, QFormLayout]:
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(18, 18, 18, 18)
    f.setVerticalSpacing(10)
    f.setHorizontalSpacing(16)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return w, f


class ConfiguracionDialog(DialogoAnimado):
    def __init__(self, ctx: Contexto, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.c = ctx.conf
        self.campos: dict[str, QWidget] = {}
        self.setWindowTitle("Configuración")
        self.resize(780, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(etiqueta("Configuración del sistema", titulo=True))
        tabs = QTabWidget()
        tabs.addTab(desplazable(self._tab_empresa()), "Empresa y DIAN")
        tabs.addTab(desplazable(self._tab_impresion()), "Impresora de tiquetes")
        tabs.addTab(desplazable(self._tab_etiquetas()), "Etiquetas")
        tabs.addTab(desplazable(self._tab_operacion()), "Operación")
        tabs.addTab(desplazable(self._tab_seguridad()), "Seguridad")
        tabs.addTab(desplazable(self._tab_datos()), "Datos y respaldos")
        tabs.addTab(desplazable(self._tab_actualizaciones()), "Actualizaciones")
        lay.addWidget(tabs, 1)
        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        ok = boton("Guardar cambios", "success", "lg")
        ok.clicked.connect(self.guardar)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)

    # ---------------------------------------------------------------- helpers
    def _linea(self, clave: str) -> QLineEdit:
        w = QLineEdit(self.c.get(clave, ""))
        self.campos[clave] = w
        return w

    def _combo(self, clave: str, opciones: list[tuple[str, str]], editable: bool = False) -> QComboBox:
        cb = QComboBox()
        cb.setEditable(editable)
        for txt, val in opciones:
            cb.addItem(txt, val)
        i = cb.findData(self.c.get(clave, ""))
        if i >= 0:
            cb.setCurrentIndex(i)
        elif editable:
            cb.setEditText(self.c.get(clave, ""))
        self.campos[clave] = cb
        return cb

    def _check(self, clave: str, texto: str) -> QCheckBox:
        ch = QCheckBox(texto)
        ch.setChecked(self.c.get(clave, "0") == "1")
        self.campos[clave] = ch
        return ch

    def _spin(self, clave: str, a: int, b: int, sufijo: str = "") -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(a, b)
        sp.setSuffix(sufijo)
        try:
            sp.setValue(int(float(self.c.get(clave, a))))
        except ValueError:
            sp.setValue(a)
        self.campos[clave] = sp
        return sp

    def _valores(self) -> dict[str, str]:
        out = {}
        for k, w in self.campos.items():
            if isinstance(w, QLineEdit):
                out[k] = w.text().strip()
            elif isinstance(w, QPlainTextEdit):
                out[k] = w.toPlainText().strip()
            elif isinstance(w, QComboBox):
                i = w.currentIndex()
                if w.isEditable() and (i < 0 or w.currentText() != w.itemText(i)):
                    out[k] = w.currentText().strip()  # texto escrito a mano (p. ej. IP:9100)
                else:
                    d = w.currentData()
                    out[k] = str(d) if d is not None else w.currentText().strip()
            elif isinstance(w, QCheckBox):
                out[k] = "1" if w.isChecked() else "0"
            elif isinstance(w, QSpinBox):
                out[k] = str(w.value())
        return out

    # ---------------------------------------------------------------- pestañas
    def _tab_empresa(self) -> QWidget:
        w, f = _form()
        if cfg.es_resolucion_de_prueba(self.c):
            aviso = QFrame()
            aviso.setObjectName("avisoPrueba")
            al = QVBoxLayout(aviso)
            t = etiqueta("La numeración SETP / 18760000001 / 990000000-995000000 es la resolución PÚBLICA del "
                         "set de pruebas de la DIAN, no una autorización de su negocio. Mientras esté configurada, "
                         "el tiquete se imprime como «Comprobante interno de venta». Si la DIAN le autorizó una "
                         "numeración propia, escríbala aquí.")
            t.setWordWrap(True)
            t.setStyleSheet("color:#FCD34D;")
            al.addWidget(t)
            f.addRow(aviso)
        for clave, txt in (("razon_social", "Razón social"), ("nit", "NIT"), ("regimen", "Régimen tributario"),
                           ("direccion", "Dirección"), ("ciudad", "Ciudad"), ("telefono", "Teléfono"),
                           ("correo", "Correo (opcional)"), ("redes", "Redes / WhatsApp (opcional)"),
                           ("resolucion_dian", "Resolución DIAN No."), ("prefijo", "Prefijo"),
                           ("rango_desde", "Rango desde"), ("rango_hasta", "Rango hasta")):
            f.addRow(txt, self._linea(clave))
        cons = self._linea("consecutivo_siguiente")
        cons.setToolTip("Siguiente número que se asignará. Modifíquelo solo al cambiar de resolución.")
        f.addRow("Próximo consecutivo", cons)
        pie = QPlainTextEdit(self.c.get("mensaje_pie", ""))
        pie.setMaximumHeight(80)
        self.campos["mensaje_pie"] = pie
        f.addRow("Mensaje al pie del tiquete", pie)
        restantes = self.ctx.db.numeros_restantes()
        f.addRow("", etiqueta(f"Números disponibles en la resolución actual: {restantes:,}".replace(",", "."),
                              muted=True))
        return w

    def _tab_impresion(self) -> QWidget:
        w, f = _form()
        f.addRow("Conexión", self._combo("transporte_impresion", [
            ("Windows (driver USB de la impresora) – recomendado", "win32"),
            ("USB directo (python-escpos + WinUSB)", "usb"),
            ("Red / Ethernet (IP:9100)", "red"),
            ("Archivo (modo prueba, sin impresora)", "archivo")]))
        impresoras = pe.listar_impresoras()
        opciones = [("(Impresora predeterminada de Windows)", "")] + [(p, p) for p in impresoras]
        f.addRow("Impresora / IP", self._combo("impresora_tiquetes", opciones, editable=True))
        f.addRow("Ancho de papel", self._combo("ancho_papel_mm", [("80 mm (48 columnas)", "80"),
                                                                  ("58 mm (32 columnas)", "58")]))
        f.addRow("Tabla de caracteres", self._combo("codepage", [
            ("PC850 Multilingüe (recomendado Jaltech)", "cp850"), ("PC858 (con €)", "cp858"),
            ("WPC1252 Windows Latin", "cp1252"), ("PC437 EE. UU. (sin tildes)", "cp437")]))
        f.addRow("", self._check("abrir_cajon_efectivo", "Tengo cajón monedero conectado a la impresora "
                                                         "(se abre solo al cobrar en efectivo)"))
        f.addRow("", self._check("imprimir_codigo_barras_tiquete", "Imprimir código de barras del tiquete "
                                                                    "(para devoluciones rápidas)"))
        f.addRow("", self._check("guardar_pdf_tiquetes", "Guardar una copia PDF de cada tiquete y comprobante"))
        carpeta = self._linea("carpeta_tiquetes")
        carpeta.setPlaceholderText(str(cfg.tiquetes_dir({})))
        fila_c = QHBoxLayout()
        fila_c.addWidget(carpeta, 1)
        b_carp = boton("Abrir carpeta")
        b_carp.clicked.connect(lambda: abrir_archivo(str(cfg.tiquetes_dir(self._valores()))))
        fila_c.addWidget(b_carp)
        f.addRow("Carpeta de PDF", fila_c)
        # ---- Logo del tiquete
        f.addRow("", self._check("mostrar_logo_tiquete", "Imprimir el logo de la empresa arriba del tiquete"))
        self.logo_ruta = self._linea("logo_tiquete")
        self.logo_ruta.setReadOnly(True)
        self.logo_ruta.setPlaceholderText("Águila de ConfeccionesR4 (incluida)")
        self.logo_prev = QLabel()
        self.logo_prev.setFixedSize(120, 120)
        self.logo_prev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_prev.setStyleSheet("background:white;border-radius:8px;")
        col = QVBoxLayout()
        col.addWidget(self.logo_ruta)
        fila_l = QHBoxLayout()
        b_logo = boton("Cambiar logo…")
        b_logo.clicked.connect(self._elegir_logo)
        b_logo_def = boton("Usar el de ConfeccionesR4", "ghost")
        b_logo_def.clicked.connect(lambda: (self.logo_ruta.setText(""), self._mostrar_logo()))
        fila_l.addWidget(b_logo)
        fila_l.addWidget(b_logo_def)
        fila_l.addStretch()
        col.addLayout(fila_l)
        fila_tam = QHBoxLayout()
        fila_tam.addWidget(QLabel("Tamaño"))
        fila_tam.addWidget(self._spin("tamano_logo_tiquete", 25, 90, " % del ancho"))
        fila_tam.addStretch()
        col.addLayout(fila_tam)
        caja = QHBoxLayout()
        caja.addWidget(self.logo_prev)
        caja.addLayout(col, 1)
        f.addRow("Logo", caja)
        self._mostrar_logo()
        f.addRow("USB Vendor ID", self._linea("usb_vendor_id"))
        f.addRow("USB Product ID", self._linea("usb_product_id"))
        fila = QHBoxLayout()
        prueba = boton("Imprimir tiquete de prueba", "primary")
        prueba.clicked.connect(self._prueba)
        cajon = boton("Probar cajón")
        cajon.clicked.connect(self._cajon)
        vista = boton("Ver cómo queda el tiquete")
        vista.clicked.connect(self._vista_previa)
        fila.addWidget(prueba)
        fila.addWidget(vista)
        fila.addWidget(cajon)
        fila.addStretch()
        f.addRow("", fila)
        nota = etiqueta("Si no ve su impresora Jaltech en la lista, instale primero el driver del fabricante "
                        "y verifique que aparezca en Configuración › Impresoras de Windows.", muted=True)
        nota.setWordWrap(True)
        f.addRow("", nota)
        return w

    def _tab_etiquetas(self) -> QWidget:
        w, f = _form()
        f.addRow("Método de impresión", self._combo("modo_etiquetas", [
            ("Driver de Windows (cualquier impresora) – recomendado", "windows"),
            ("ESC/POS raster en la impresora térmica de tiquetes", "escpos"),
            ("TSPL (impresoras de etiquetas TSC / Xprinter)", "tspl")]))
        impresoras = pe.listar_impresoras()
        opciones = [("(Sin elegir: saldría por la de tiquetes)", "")] + [(p, p) for p in impresoras]
        self.cb_etiquetas = self._combo("impresora_etiquetas", opciones, editable=True)
        fila_imp = QHBoxLayout()
        fila_imp.addWidget(self.cb_etiquetas, 1)
        b_det = boton("Detectar")
        b_det.setToolTip("Busca la impresora de etiquetas instalada (SAT TT460, TSC, Zebra…)")
        b_det.clicked.connect(self._detectar_etiquetas)
        fila_imp.addWidget(b_det)
        f.addRow("Impresora de etiquetas", fila_imp)
        f.addRow("Ancho etiqueta", self._spin("etiqueta_ancho_mm", 25, 104, " mm"))
        f.addRow("Alto etiqueta", self._spin("etiqueta_alto_mm", 15, 100, " mm"))
        f.addRow("Simbología", self._combo("etiqueta_simbologia", [
            ("Automática (EAN-13 si el código es válido, si no Code128)", "auto"),
            ("Code128", "code128"), ("EAN-13", "ean13")]))
        f.addRow("", self._check("etiqueta_mostrar_tienda", "Mostrar el nombre de la tienda en la etiqueta"))
        f.addRow("Letra del nombre", self._combo("etiqueta_estilo", [
            ("Cursiva (elegante)", "cursiva"), ("Normal (letra de imprenta)", "normal")]))
        import barcode_engine as be
        f.addRow("Tipo de cursiva", self._combo("etiqueta_fuente", [(n, n) for n in be.FUENTES_CURSIVAS]))
        fila_p = QHBoxLayout()
        b_prueba_et = boton("Imprimir etiqueta de prueba", "primary")
        b_prueba_et.clicked.connect(self._prueba_etiqueta)
        fila_p.addWidget(b_prueba_et)
        fila_p.addStretch()
        f.addRow("", fila_p)
        guia = etiqueta("Etiqueta de prueba: sale 1 sola, con un marco en el borde. Si el marco sale completo y "
                        "centrado, el tamaño está bien. Si se corta o sale corrido, ajuste el ancho/alto aquí y en "
                        "Windows › Impresoras › SAT TT460 › Preferencias de impresión (mismo tamaño), y calibre "
                        "la impresora con el botón FEED.", muted=True)
        guia.setWordWrap(True)
        f.addRow("", guia)
        nota = etiqueta("La cursiva solo se aplica al nombre de la tienda. Las barras y el código impreso "
                        "debajo no cambian, para que el lector los siga leyendo sin errores. "
                        "Vea el resultado en Inventario › Etiquetas antes de imprimir el lote.", muted=True)
        nota.setWordWrap(True)
        f.addRow("", nota)
        return w

    def _tab_operacion(self) -> QWidget:
        w, f = _form()
        f.addRow("Cajero actual", self._linea("cajero_actual"))
        f.addRow("Stock crítico (≤)", self._spin("stock_critico", 0, 1000, " und"))
        f.addRow("", self._check("permitir_stock_negativo", "Permitir vender sin existencias (stock negativo)"))
        f.addRow("", self._check("arqueo_ciego", "Arqueo ciego: ocultar el efectivo esperado durante el turno"))
        return w

    def _tab_datos(self) -> QWidget:
        w, f = _form()
        ruta = QLineEdit(str(self.ctx.db.path))
        ruta.setReadOnly(True)
        f.addRow("Base de datos", ruta)
        fila = QHBoxLayout()
        b1 = boton("Crear respaldo ahora", "primary")
        b1.clicked.connect(self._respaldar)
        b2 = boton("Abrir carpeta de respaldos")
        b2.clicked.connect(lambda: abrir_archivo(str(cfg.backups_dir())))
        b3 = boton("Carpeta de reportes")
        b3.clicked.connect(lambda: abrir_archivo(str(cfg.exports_dir())))
        fila.addWidget(b1)
        fila.addWidget(b2)
        fila.addWidget(b3)
        fila.addStretch()
        f.addRow("", fila)
        nota = etiqueta("Se crea un respaldo automático al cerrar caja, al importar inventario y al salir "
                        "del programa (se conservan los últimos 30). Copie periódicamente la carpeta de "
                        "respaldos a una USB o a la nube.", muted=True)
        nota.setWordWrap(True)
        f.addRow("", nota)
        restaurar = boton("Restaurar un respaldo…")
        restaurar.clicked.connect(self._restaurar)
        reiniciar = boton("Borrar datos / dejar listo para ENTREGAR…", "danger")
        reiniciar.clicked.connect(self._reiniciar)
        fila2 = QHBoxLayout()
        fila2.addWidget(restaurar)
        fila2.addWidget(reiniciar)
        fila2.addStretch()
        f.addRow("Recuperación", fila2)
        aud = boton("Exportar auditoría a Excel")
        aud.clicked.connect(lambda: exportar_y_ofrecer_abrir(
            self, self.ctx, "Auditoria_ConfeccionesR4.xlsx", lambda ruta: xe.exportar_auditoria(self.ctx.db, ruta)))
        f.addRow("Auditoría", aud)
        f.addRow("Versión", QLabel(f"{cfg.APP_NAME} {cfg.APP_VERSION}"))
        return w

    def _tab_seguridad(self) -> QWidget:
        w, f = _form()
        estado = "DEFINIDA" if self.ctx.db.tiene_pin() else "NO definida (recomendado crearla)"
        self.lbl_pin = QLabel(f"Clave de administrador: {estado}")
        f.addRow(self.lbl_pin)
        self.pin_actual = QLineEdit()
        self.pin_nuevo = QLineEdit()
        self.pin_rep = QLineEdit()
        for e, ph in ((self.pin_actual, "Clave actual (si ya existe)"), (self.pin_nuevo, "Nueva clave (4 a 8 dígitos)"),
                      (self.pin_rep, "Repita la nueva clave")):
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setMaxLength(8)
            e.setPlaceholderText(ph)
        if self.ctx.db.tiene_pin():
            f.addRow("Clave actual", self.pin_actual)
        f.addRow("Nueva clave", self.pin_nuevo)
        f.addRow("Repetir", self.pin_rep)
        b = boton("Guardar clave", "primary")
        b.clicked.connect(self._guardar_pin)
        f.addRow("", b)
        nota = etiqueta("La clave se pide para: anular tiquetes, corregir el método de pago, corregir la base, "
                        "anular aperturas, reabrir cierres, anular movimientos de caja, eliminar productos, "
                        "deshacer importaciones, descuentos mayores al 30 %, restaurar respaldos y borrar datos. "
                        "Las ventas y devoluciones parciales del día a día no la piden.", muted=True)
        nota.setWordWrap(True)
        f.addRow("", nota)
        return w

    def _guardar_pin(self) -> None:
        if self.pin_nuevo.text() != self.pin_rep.text():
            error(self, "Las claves no coinciden.")
            return
        try:
            self.ctx.db.definir_pin(self.pin_nuevo.text(), self.pin_actual.text())
        except Exception as e:  # noqa: BLE001
            error(self, str(e))
            return
        for e in (self.pin_actual, self.pin_nuevo, self.pin_rep):
            e.clear()
        self.lbl_pin.setText("Clave de administrador: DEFINIDA")
        info(self, "Clave de administrador guardada. Anótela en un lugar seguro.")

    def _reiniciar_app(self, nueva_db) -> None:
        """Reemplaza la conexión y reinicia el programa para cargar los datos nuevos."""
        from PySide6.QtWidgets import QApplication

        import updater
        self.ctx.db = nueva_db
        app = QApplication.instance()
        app.setProperty("actualizando", True)
        updater.relanzar()          # instancia nueva con entorno limpio (ver updater.entorno_limpio)
        app.closeAllWindows()
        app.quit()

    def _restaurar(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        import database as dbm
        ruta, _ = QFileDialog.getOpenFileName(self, "Elija el respaldo a restaurar", str(cfg.backups_dir()),
                                              "Respaldo (*.db)")
        if not ruta or not autorizar(self, self.ctx, "Restaurar un respaldo: los datos actuales se reemplazan "
                                                     "(antes se guarda una copia de ellos)."):
            return
        try:
            nueva, previo = dbm.restaurar_respaldo(self.ctx.db, ruta)
        except Exception as e:  # noqa: BLE001
            error(self, f"No se pudo restaurar:\n{e}")
            return
        info(self, f"Respaldo restaurado. Los datos anteriores quedaron guardados en:\n{previo}\n\n"
                   "El programa se reiniciará.")
        self._reiniciar_app(nueva)

    def _reiniciar(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        import database as dbm
        opciones = ["Dejar listo para ENTREGAR: borrar TODO (al abrir, el nuevo dueño configura su negocio)",
                    "Borrar solo ventas, cajas e historial (conservar inventario y configuración)"]
        eleccion, ok = QInputDialog.getItem(self, "Borrar datos", "¿Qué desea borrar?\n"
                                            "En ambos casos se guarda antes un respaldo completo.", opciones, 0, False)
        if not ok:
            return
        conservar = eleccion == opciones[1]
        if not autorizar(self, self.ctx, eleccion):
            return
        texto, ok = QInputDialog.getText(self, "Confirmación final",
                                         "Esta acción borra los datos del programa.\nEscriba BORRAR para confirmar:")
        if not ok or texto.strip().upper() != "BORRAR":
            info(self, "No se borró nada.")
            return
        try:
            nueva, respaldo = dbm.reiniciar_datos(self.ctx.db, conservar)
        except Exception as e:  # noqa: BLE001
            error(self, f"No se pudo reiniciar:\n{e}")
            return
        extra = ("\n\nAl abrir, aparecerá el asistente de configuración inicial para el nuevo dueño."
                 if not conservar else "")
        info(self, f"Listo. Copia de seguridad de lo borrado:\n{respaldo}{extra}\n\nEl programa se reiniciará.")
        self._reiniciar_app(nueva)

    def _tab_actualizaciones(self) -> QWidget:
        import updater
        w, f = _form()
        f.addRow("Versión instalada", QLabel(cfg.APP_VERSION))
        url = self._linea("url_actualizaciones")
        url.setPlaceholderText(cfg.URL_ACTUALIZACIONES or "https://github.com/USUARIO/REPO/releases/latest/download/latest.json")
        f.addRow("URL del manifiesto", url)
        if cfg.URL_ACTUALIZACIONES:
            f.addRow("", etiqueta("Vacío = usar la URL integrada en este programa (recomendado).", muted=True))
        f.addRow("", self._check("buscar_actualizaciones", "Buscar actualizaciones al abrir y cada 3 horas (si hay internet)"))
        f.addRow("", self._check("actualizar_automaticamente",
                                 "Instalar automáticamente, sin preguntar (nunca durante una venta)"))
        firma = "Activa (clave pública incluida en el programa)" if updater.clave_publica() else \
            "NO configurada: compile después de generar las claves (ver README)"
        f.addRow("Verificación de firma", QLabel(firma))
        b = boton("Buscar actualizaciones ahora", "primary")
        b.clicked.connect(self._buscar_actualizacion)
        f.addRow("", b)
        nota = etiqueta("Cada actualización se descarga por HTTPS y solo se instala si su firma digital "
                        "corresponde a la clave de ConfeccionesR4. Sus datos (ventas, inventario, caja) no se tocan: "
                        "están en C:\\ProgramData\\ConfeccionesR4 y la base de datos se migra sola.", muted=True)
        nota.setWordWrap(True)
        f.addRow("", nota)
        return w

    def _buscar_actualizacion(self) -> None:
        import updater
        url = updater.url_efectiva(self._valores().get("url_actualizaciones", ""))
        if not url:
            error(self, "Escriba primero la URL del manifiesto de actualizaciones.")
            return

        def ok(info_act):
            if info_act is None:
                info(self, f"Ya tiene la última versión ({cfg.APP_VERSION}).")
                return
            if confirmar(self, f"Nueva versión {info_act.version} disponible.\n\n{info_act.notas}\n\n"
                               "¿Descargar e instalar ahora? El programa se reiniciará.", "Actualización"):
                updater.DialogoActualizacion(info_act, self).exec()

        en_segundo_plano(lambda: updater.buscar(url), ok,
                         lambda m: error(self, f"No se pudo consultar la actualización:\n{m}"))

    # ---------------------------------------------------------------- acciones
    def _conf_temporal(self) -> dict[str, str]:
        c = dict(self.c)
        c.update(self._valores())
        return c

    def _prueba(self) -> None:
        conf = self._conf_temporal()
        datos = pe.construir_prueba(conf).bytes()
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo="Prueba"),
                         lambda _: self.ctx.notificar("Página de prueba enviada", "ok"),
                         lambda m: error(self, m, "Error de impresión"))

    def _mostrar_logo(self) -> None:
        from PySide6.QtGui import QPixmap
        ruta = pe.ruta_logo({"logo_tiquete": self.logo_ruta.text()})
        pm = QPixmap(str(ruta)) if ruta else QPixmap()
        if pm.isNull():
            self.logo_prev.setText("Sin logo")
        else:
            self.logo_prev.setPixmap(pm.scaled(110, 110, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))

    def _elegir_logo(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        ruta, _ = QFileDialog.getOpenFileName(self, "Elegir logo", "",
                                              "Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not ruta:
            return
        try:
            destino = pe.preparar_logo(ruta)
        except Exception as e:  # noqa: BLE001
            error(self, f"No se pudo usar esa imagen:\n{e}")
            return
        self.logo_ruta.setText(str(destino))
        self._mostrar_logo()
        self.ctx.notificar("Logo listo. Use «Ver cómo queda el tiquete» para revisarlo.", "ok")

    def _vista_previa(self) -> None:
        try:
            ruta = pe.tiquete_de_muestra(self._conf_temporal())
        except Exception as e:  # noqa: BLE001
            error(self, f"No se pudo generar la vista previa:\n{e}")
            return
        abrir_archivo(str(ruta))

    def _detectar_etiquetas(self) -> None:
        encontradas = pe.detectar_impresora_etiquetas()
        if not encontradas:
            error(self, "No encontré ninguna impresora de etiquetas instalada.\n\n"
                        "Instale el driver de la SAT TT460 (Seagull) y verifique que aparezca en "
                        "Configuración › Bluetooth y dispositivos › Impresoras de Windows.")
            return
        nombre = encontradas[0]
        i = self.cb_etiquetas.findData(nombre)
        if i >= 0:
            self.cb_etiquetas.setCurrentIndex(i)
        else:
            self.cb_etiquetas.setEditText(nombre)
        if self.campos["modo_etiquetas"].currentData() != "windows":
            self.campos["modo_etiquetas"].setCurrentIndex(self.campos["modo_etiquetas"].findData("windows"))
        self.ctx.notificar(f"Impresora de etiquetas: {nombre}", "ok")

    def _prueba_etiqueta(self) -> None:
        conf = self._conf_temporal()
        modo = conf.get("modo_etiquetas", "windows")
        destino = conf.get("impresora_etiquetas", "")
        if modo != "escpos" and (not destino or destino == conf.get("impresora_tiquetes")):
            error(self, "Primero elija la impresora de ETIQUETAS (la SAT TT460) en la lista o use «Detectar».\n\n"
                        "Si no, la etiqueta saldría por la impresora de tiquetes.")
            return
        try:
            img = pe.etiqueta_de_prueba(conf)
        except Exception as e:  # noqa: BLE001
            error(self, f"No se pudo generar la etiqueta de prueba:\n{e}")
            return
        en_segundo_plano(lambda: pe.imprimir_etiquetas(img, 1, conf),
                         lambda _: self.ctx.notificar(f"Etiqueta de prueba enviada a {destino or 'la impresora'}", "ok"),
                         lambda m: error(self, m, "Error imprimiendo la etiqueta"))

    def _cajon(self) -> None:
        conf = self._conf_temporal()
        datos = pe.construir_apertura_cajon(conf).bytes()
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo="Cajon"),
                         al_fallar=lambda m: error(self, m, "Error"))

    def _respaldar(self) -> None:
        try:
            r = self.ctx.db.respaldar()
        except Exception as e:  # noqa: BLE001
            error(self, f"No se pudo crear el respaldo:\n{e}")
            return
        info(self, f"Respaldo creado:\n{r}")

    def guardar(self) -> None:
        v = self._valores()
        try:
            desde, hasta, sig = int(v["rango_desde"]), int(v["rango_hasta"]), int(v["consecutivo_siguiente"])
        except ValueError:
            error(self, "Los rangos y el consecutivo deben ser números enteros.")
            return
        if not (desde <= sig <= hasta + 1) or desde > hasta:
            error(self, "El próximo consecutivo debe estar dentro del rango autorizado.")
            return
        ultimo = self.ctx.db.q1("SELECT MAX(consecutivo) FROM ventas WHERE numero_tiquete LIKE ?",
                                (v["prefijo"] + "%",))[0]
        if ultimo is not None and sig <= ultimo:
            error(self, f"El consecutivo {sig} ya fue usado (último emitido: {ultimo}).")
            return
        self.ctx.db.set_config(v)
        self.ctx.bus.config_cambiada.emit()
        self.accept()
