"""
main.py — Punto de entrada de ConfeccionesR4 POS (PySide6).

Arranque:
  1. Modo actualizador (--aplicar-actualizacion): reemplaza el .exe y termina.
  2. Registro de errores en C:\\ProgramData\\ConfeccionesR4\\logs (sin consola visible).
  3. Instancia única (QLockFile): evita abrir el POS dos veces.
  4. Base de datos: se crea/migra sola en C:\\ProgramData\\ConfeccionesR4.
  5. Ventana principal maximizada, tema oscuro, 5 módulos con transiciones animadas:
     Dashboard · Vender · Documentos · Inventario · Caja   (+ Ajustes)
  6. Búsqueda silenciosa de actualizaciones firmadas (si hay internet y está configurada).

Atajos globales: Ctrl+1..5 cambian de módulo.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import updater  # noqa: E402

if updater.modo_aplicar_desde_argv(sys.argv):   # .exe nuevo ejecutándose como instalador
    sys.exit(0)

from PySide6.QtCore import QLockFile, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,  # noqa: E402
                               QMessageBox, QVBoxLayout, QWidget)

import config as cfg  # noqa: E402
from database import Database  # noqa: E402
from ui import theme  # noqa: E402
from ui.animations import FadeStack, IndicadorLateral  # noqa: E402
from ui.widgets import Bus, Contexto, Toast, boton, confirmar, en_segundo_plano, desplazable, refrescar_estilo  # noqa: E402

log = logging.getLogger("r4")

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# =========================================================================== #
# Infraestructura
# =========================================================================== #
def configurar_logs() -> None:
    h = RotatingFileHandler(cfg.logs_dir() / "confeccionesr4.log", maxBytes=2_000_000, backupCount=5,
                            encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])


def instalar_manejador_errores() -> None:
    def hook(tipo, valor, tb):
        log.critical("Error no controlado:\n%s", "".join(traceback.format_exception(tipo, valor, tb)))
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "Error inesperado",
                                 f"Ocurrió un error inesperado:\n\n{valor}\n\n"
                                 f"Los datos de ventas están a salvo. Detalle guardado en:\n{cfg.logs_dir()}")
    sys.excepthook = hook


def preparar_base_datos() -> None:
    """Primera ejecución: copia la BD limpia empaquetada dentro del .exe."""
    destino = cfg.db_path()
    if destino.exists():
        return
    plantilla = cfg.resource_path("data", cfg.DB_FILENAME)
    if plantilla.exists():
        shutil.copy2(plantilla, destino)
        log.info("Base de datos inicial copiada desde %s", plantilla)


def icono_app() -> QIcon:
    for nombre in ("logo_r4.ico", "logo_r4.png"):
        p = cfg.resource_path("assets", nombre)
        if p.exists():
            return QIcon(str(p))
    return QIcon()


def cerrar_splash() -> None:
    """Cierra la pantalla de carga de PyInstaller (--splash) si existe."""
    try:
        import pyi_splash  # type: ignore
        pyi_splash.close()
    except Exception:  # noqa: BLE001
        pass


# =========================================================================== #
# Ventana principal
# =========================================================================== #
class VentanaPrincipal(QMainWindow):
    # Altura (px) por debajo de la cual cada pantalla deja de encogerse y aparece la barra para bajar
    ALTO_MINIMO = {"dashboard": 760, "historial": 720, "inventario": 560, "caja": 820}

    MODULOS = [
        ("Dashboard", "dashboard"),
        ("Vender", "pos"),
        ("Documentos", "historial"),
        ("Inventario", "inventario"),
        ("Caja", "caja"),
    ]

    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        self.vistas: dict[str, QWidget] = {}
        self._paginas: dict[str, QWidget] = {}
        self.setWindowTitle(f"{cfg.APP_NAME} — {ctx.db.get_config('razon_social')}")
        self.setWindowIcon(icono_app())
        self.setMinimumSize(1000, 600)

        raiz = QWidget()
        raiz.setObjectName("root")
        self.setCentralWidget(raiz)
        h = QHBoxLayout(raiz)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._barra_lateral())
        derecha = QVBoxLayout()
        derecha.setContentsMargins(0, 0, 0, 0)
        derecha.setSpacing(0)
        derecha.addWidget(self._encabezado())
        self.stack = FadeStack()
        derecha.addWidget(self.stack, 1)
        h.addLayout(derecha, 1)

        self.toast = Toast(raiz)
        ctx.bus.toast.connect(self.toast.mostrar)
        ctx.bus.caja_cambiada.connect(self._estado_caja)
        ctx.bus.ventas_cambiadas.connect(self._estado_caja)
        ctx.bus.config_cambiada.connect(self._al_cambiar_config)

        for i in range(len(self.MODULOS)):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda i=i: self.ir_a(i))

        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._tick)
        self._reloj.start(10_000)
        self._tick()
        self._estado_caja()
        self.ir_a(0)                                     # el Dashboard es la primera pantalla
        self._act_pendiente = None
        QTimer.singleShot(900, self._avisos_inicio)
        QTimer.singleShot(1500, self._automatizaciones_inicio)
        QTimer.singleShot(6000, self._buscar_actualizacion)
        self._timer_act = QTimer(self)                     # revisa de nuevo cada 3 horas
        self._timer_act.timeout.connect(self._buscar_actualizacion)
        self._timer_act.start(3 * 60 * 60 * 1000)

    # ---------------------------------------------------------------- layout
    def _barra_lateral(self) -> QFrame:
        barra = QFrame()
        barra.setObjectName("sidebar")
        barra.setFixedWidth(184)
        v = QVBoxLayout(barra)
        v.setContentsMargins(14, 18, 14, 18)
        v.setSpacing(6)
        cab = QHBoxLayout()
        logo = QLabel("R4")
        logo.setObjectName("logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cab.addWidget(logo)
        textos = QVBoxLayout()
        textos.setSpacing(0)
        marca = QLabel("CONFECCIONES")
        marca.setObjectName("marca")
        sub = QLabel(f"POS  v{cfg.APP_VERSION}")
        sub.setObjectName("marcaSub")
        textos.addWidget(marca)
        textos.addWidget(sub)
        cab.addLayout(textos)
        v.addLayout(cab)
        v.addSpacing(22)

        self.indicador = IndicadorLateral(barra)
        self.grupo_nav = QButtonGroup(self)
        self.grupo_nav.setExclusive(True)
        for i, (txt, _) in enumerate(self.MODULOS):
            b = boton(txt)
            b.setObjectName("navBtn")
            b.setCheckable(True)
            b.setToolTip(f"{txt}  (Ctrl+{i + 1})")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, i=i: self.ir_a(i))
            self.grupo_nav.addButton(b, i)
            v.addWidget(b)
        v.addStretch()
        ajustes = boton("Ajustes")
        ajustes.setObjectName("navBtn")
        ajustes.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ajustes.clicked.connect(self.abrir_configuracion)
        v.addWidget(ajustes)
        return barra

    def _encabezado(self) -> QFrame:
        f = QFrame()
        f.setObjectName("header")
        f.setFixedHeight(60)
        h = QHBoxLayout(f)
        h.setContentsMargins(22, 0, 22, 0)
        h.setSpacing(14)
        self.lbl_titulo = QLabel("")
        self.lbl_titulo.setObjectName("headerTitle")
        h.addWidget(self.lbl_titulo)
        h.addStretch()
        self.lbl_caja = QLabel("")
        h.addWidget(self.lbl_caja)
        self.lbl_cajero = QLabel("")
        self.lbl_cajero.setObjectName("clock")
        h.addWidget(self.lbl_cajero)
        self.lbl_reloj = QLabel("")
        self.lbl_reloj.setObjectName("clock")
        h.addWidget(self.lbl_reloj)
        return f

    # ---------------------------------------------------------------- navegación
    def _crear_vista(self, clave: str) -> QWidget:
        # Importación diferida: solo se carga lo que se usa (arranque más rápido)
        if clave == "pos":
            from ui.pos_view import POSView
            return POSView(self.ctx)
        if clave == "historial":
            from ui.history_view import HistorialView
            return HistorialView(self.ctx)
        if clave == "inventario":
            from ui.inventory_view import InventarioView
            return InventarioView(self.ctx)
        if clave == "caja":
            from ui.cash_view import CajaView
            return CajaView(self.ctx)
        from ui.dashboard_view import DashboardView
        return DashboardView(self.ctx)

    def ir_a(self, i: int) -> None:
        txt, clave = self.MODULOS[i]
        if clave not in self.vistas:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                vista = self._crear_vista(clave)
            finally:
                QApplication.restoreOverrideCursor()
            self.vistas[clave] = vista
            # Vender ya se adapta solo (modo compacto); el resto se desplaza en pantallas bajas
            alto = self.ALTO_MINIMO.get(clave)
            pagina = vista if alto is None else desplazable(vista, alto)
            self._paginas[clave] = pagina
            self.stack.addWidget(pagina)
        vista = self.vistas[clave]
        self.stack.mostrar(self._paginas[clave])
        boton_nav = self.grupo_nav.button(i)
        boton_nav.setChecked(True)
        self.indicador.ir_a(boton_nav, animar=self.isVisible())
        self.lbl_titulo.setText(getattr(vista, "titulo", txt))

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(0, lambda: self.indicador.ir_a(self.grupo_nav.checkedButton(), animar=False))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        b = self.grupo_nav.checkedButton()
        if b is not None:
            QTimer.singleShot(0, lambda: self.indicador.ir_a(b, animar=False))

    # ---------------------------------------------------------------- estado
    def _tick(self) -> None:
        n = datetime.now()
        hora = n.strftime("%I:%M").lstrip("0")
        ampm = "a. m." if n.hour < 12 else "p. m."
        self.lbl_reloj.setText(f"{DIAS[n.weekday()]} {n.day:02d} {MESES[n.month - 1]} · {hora} {ampm}")

    def _estado_caja(self) -> None:
        caja = self.ctx.db.caja_abierta()
        estado = "ok" if caja else "off"
        self.lbl_caja.setText("CAJA ABIERTA" if caja else "CAJA CERRADA")
        if self.lbl_caja.property("chip") != estado:
            self.lbl_caja.setProperty("chip", estado)
            refrescar_estilo(self.lbl_caja)
        self.lbl_cajero.setText(f"Cajero: {self.ctx.db.get_config('cajero_actual')}")

    def _avisos_inicio(self) -> None:
        db = self.ctx.db
        conf = db.get_all_config()
        critico = db.get_config_int("stock_critico", 3)
        n = db.q1("SELECT COUNT(*) FROM productos WHERE activo=1 AND stock<=?", (critico,))[0]
        restantes = db.numeros_restantes()
        if restantes <= 1000:
            self.ctx.notificar(f"Quedan {restantes} números de la numeración configurada", "warn")
        elif cfg.es_resolucion_de_prueba(conf) and db.get_config("aviso_resolucion_visto") != "1":
            self.ctx.notificar("La numeración SETP configurada es la de PRUEBAS de la DIAN: los tiquetes se "
                               "imprimen como comprobante interno. Revise Ajustes › Empresa y DIAN.", "warn")
            db.set_config({"aviso_resolucion_visto": "1"}, auditar=False)
        elif not db.caja_abierta():
            self.ctx.notificar("Caja cerrada: se le pedirá la base inicial en la primera venta", "info")
        elif n:
            self.ctx.notificar(f"{n} productos con stock crítico o agotado", "warn")

    # ---------------------------------------------------------------- automatizaciones
    def _automatizaciones_inicio(self) -> None:
        from ui.asistente import AsistenteInicial, necesita_asistente
        if necesita_asistente(self.ctx.db):
            # Programa recién instalado o entregado: el nuevo dueño configura su negocio
            dlg = AsistenteInicial(self.ctx, self)
            if dlg.exec():
                self._estado_caja()
                if dlg.importar_al_final:
                    self.ir_a(3)
                    self.vistas["inventario"].importar()
            return
        self._autodetectar_impresora()
        self._respaldo_diario()
        if not self.ctx.db.q1("SELECT 1 FROM productos LIMIT 1"):
            self._asistente_primer_uso()

    def _autodetectar_impresora(self) -> None:
        """Si no hay impresora elegida, toma la térmica instalada (Jaltech / POS-80 / 58 mm…)."""
        db = self.ctx.db
        import printer_engine as pe
        instaladas = pe.listar_impresoras()
        # Impresora de etiquetas (SAT TT460…): se elige sola si hay exactamente una
        if not db.get_config("impresora_etiquetas"):
            etiq = pe.detectar_impresora_etiquetas(instaladas)
            if len(etiq) == 1:
                db.set_config({"impresora_etiquetas": etiq[0], "modo_etiquetas": "windows"})
                self.ctx.notificar(f"Impresora de etiquetas configurada: {etiq[0]}", "ok")
        if db.get_config("impresora_tiquetes") or db.get_config("transporte_impresion") != "win32":
            return
        claves = ("jaltech", "pos", "80", "58", "thermal", "termica", "térmica", "receipt", "xp-", "tm-t")
        termicas = [p for p in instaladas
                    if any(k in p.lower() for k in claves) and "pdf" not in p.lower() and "xps" not in p.lower()
                    and not pe.es_impresora_etiquetas(p)]
        if len(termicas) == 1:
            ancho = "58" if "58" in termicas[0] else "80"
            db.set_config({"impresora_tiquetes": termicas[0], "ancho_papel_mm": ancho})
            self.ctx.notificar(f"Impresora configurada automáticamente: {termicas[0]} ({ancho} mm)", "ok")

    def _respaldo_diario(self) -> None:
        respaldos = sorted(cfg.backups_dir().glob("respaldo_*.db"))
        viejo = not respaldos or (datetime.now().timestamp() - respaldos[-1].stat().st_mtime) > 20 * 3600
        if viejo:
            en_segundo_plano(self.ctx.db.respaldar, al_fallar=lambda m: log.warning("Respaldo diario: %s", m))

    def _asistente_primer_uso(self) -> None:
        caja = QMessageBox(self)
        caja.setWindowTitle("Bienvenido a ConfeccionesR4 POS")
        caja.setText("El inventario está vacío.\n\nImporte su archivo de Excel (por ejemplo "
                     "«Inventario General 01.xlsx») y quedará listo para vender.")
        importar = caja.addButton("Importar inventario", QMessageBox.ButtonRole.AcceptRole)
        caja.addButton("Más tarde", QMessageBox.ButtonRole.RejectRole)
        caja.exec()
        if caja.clickedButton() is importar:
            self.ir_a(3)
            self.vistas["inventario"].importar()

    # ---------------------------------------------------------------- actualizaciones
    def _url_actualizaciones(self) -> str:
        return updater.url_efectiva(self.ctx.db.get_config("url_actualizaciones"))

    def _vendiendo(self) -> bool:
        pos = self.vistas.get("pos")
        return pos is not None and bool(getattr(pos, "carrito", []))

    def _buscar_actualizacion(self) -> None:
        db = self.ctx.db
        url = self._url_actualizaciones()
        if not url or db.get_config("buscar_actualizaciones") != "1" or not updater.clave_publica() \
                or not getattr(sys, "frozen", False):
            return
        en_segundo_plano(lambda: updater.buscar(url), self._actualizacion_encontrada,
                         lambda m: log.info("Sin actualización (%s)", m))

    def _actualizacion_encontrada(self, info) -> None:
        if info is None:
            return
        self._act_pendiente = info
        # Nunca interrumpir una venta ni un diálogo abierto: se reintenta en 2 minutos
        if self._vendiendo() or QApplication.activeModalWidget() is not None:
            QTimer.singleShot(120_000, lambda: self._actualizacion_encontrada(self._act_pendiente))
            return
        if self.ctx.db.get_config("actualizar_automaticamente") == "1":
            log.info("Actualización automática a %s", info.version)
            updater.DialogoActualizacion(info, self).exec()
            return
        if confirmar(self, f"Hay una nueva versión de ConfeccionesR4 POS: {info.version}\n\n{info.notas}\n\n"
                           "¿Actualizar ahora? Tarda menos de un minuto; sus datos no se modifican.",
                     "Actualización disponible"):
            updater.DialogoActualizacion(info, self).exec()

    def _al_cambiar_config(self) -> None:
        self.setWindowTitle(f"{cfg.APP_NAME} — {self.ctx.db.get_config('razon_social')}")
        self._estado_caja()

    def abrir_configuracion(self) -> None:
        from ui.settings_dialog import ConfiguracionDialog
        if ConfiguracionDialog(self.ctx, self).exec():
            self.ctx.notificar("Configuración guardada", "ok")

    def closeEvent(self, e) -> None:  # noqa: N802
        actualizando = bool(QApplication.instance().property("actualizando"))
        if not actualizando and self._vendiendo() and not confirmar(
                self, "Hay una venta EN CURSO sin cobrar.\n\n¿Cerrar el programa de todos modos? "
                      "(la venta se pierde)", "Salir"):
            e.ignore()
            return
        if not actualizando and self.ctx.db.caja_abierta() and not confirmar(
                self, "La caja sigue ABIERTA.\n\nPuede cerrar el programa y continuar el turno después, "
                      "pero recuerde hacer el arqueo al final del día.\n\n¿Salir de todos modos?", "Salir"):
            e.ignore()
            return
        try:
            self.ctx.db.respaldar()
        except Exception:  # noqa: BLE001
            log.exception("No se pudo respaldar al salir")
        self.ctx.db.close()
        e.accept()


# =========================================================================== #
def main() -> int:
    configurar_logs()
    instalar_manejador_errores()
    if sys.platform == "win32":
        try:  # ícono propio en la barra de tareas (no el de Python)
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ConfeccionesR4.POS.1")
        except Exception:  # noqa: BLE001
            pass
    updater.limpiar_restos()

    app = QApplication(sys.argv)
    app.setApplicationName(cfg.APP_NAME)
    app.setOrganizationName(cfg.APP_ID)
    app.setApplicationVersion(cfg.APP_VERSION)
    app.setWindowIcon(icono_app())
    app.setFont(QFont("Segoe UI", 10))
    theme.aplicar_tema(app)

    candado = QLockFile(str(cfg.data_dir() / "confeccionesr4.lock"))
    candado.setStaleLockTime(0)
    if not candado.tryLock(5000):   # espera a la instancia anterior si se está reiniciando
        cerrar_splash()
        QMessageBox.information(None, cfg.APP_NAME, "ConfeccionesR4 POS ya está abierto.")
        return 0

    preparar_base_datos()
    try:
        db = Database()
    except Exception as e:  # noqa: BLE001
        log.exception("No se pudo abrir la base de datos")
        cerrar_splash()
        QMessageBox.critical(None, cfg.APP_NAME, f"No se pudo abrir la base de datos:\n{e}")
        return 1
    ctx = Contexto(db, Bus())
    log.info("Inicio %s v%s · BD %s", cfg.APP_NAME, cfg.APP_VERSION, db.path)

    ventana = VentanaPrincipal(ctx)
    cerrar_splash()
    ventana.showMaximized()
    codigo = app.exec()
    candado.unlock()
    return codigo


if __name__ == "__main__":
    sys.exit(main())
