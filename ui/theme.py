"""
ui/theme.py — Tema oscuro (Qt Style Sheets) pensado para mostrador táctil.

Los colores activos quedan en el diccionario global ``T`` para que los delegados que pintan
a mano (tarjetas de producto, gráficos) usen exactamente la misma paleta.
"""
from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Paleta única: modo oscuro profesional (alto contraste para mostrador)
OSCURO = {
    "bg": "#0F1318", "panel": "#161B22", "card": "#1C232C", "card2": "#232B36",
    "input": "#11161C", "border": "#2C3643", "hover": "#26303C", "pressed": "#2E3946",
    "text": "#E8EDF3", "muted": "#8D99AB", "header": "#1A212A", "zebra": "#1F2730",
    "primary": "#3B82F6", "primary_soft": "#1E3A66", "primary_text": "#FFFFFF",
    "success": "#22C55E", "success_dark": "#16A34A", "warning": "#F59E0B",
    "danger": "#EF4444", "danger_dark": "#DC2626",
    "warn_bg": "#4A3A12", "warn_fg": "#FCD34D", "crit_bg": "#4C1D1D", "crit_fg": "#FCA5A5",
    "sidebar": "#0B0F14",
}

# Colores de categoría (franja superior de las tarjetas del POS)
COLORES_CATEGORIA = {
    "Jeans Hombre": "#3B82F6", "Jeans Mujer": "#EC4899", "Blusas": "#A855F7",
    "Accesorios": "#F59E0B", "General": "#64748B", "Jeans Dama": "#EC4899", "Camisetas Hombre": "#0EA5E9",
    "Polo Hombre": "#6366F1", "Conjunto": "#F97316", "Bermuda Hombre": "#14B8A6", "Camisetas Dama": "#F472B6",
}

_PALETA_EXTRA = ["#14B8A6", "#F97316", "#84CC16", "#06B6D4", "#E11D48", "#8B5CF6", "#EAB308", "#10B981"]


def color_categoria(nombre: str) -> str:
    """Color fijo por categoría (las desconocidas reciben uno estable según su nombre)."""
    if nombre in COLORES_CATEGORIA:
        return COLORES_CATEGORIA[nombre]
    return _PALETA_EXTRA[sum(ord(c) for c in nombre) % len(_PALETA_EXTRA)]


T: dict[str, str] = dict(OSCURO)

QSS = Template("""
* { font-family: "Segoe UI", "Inter", "Roboto", sans-serif; font-size: 14px; color: $text; }
QMainWindow, QWidget#root, QDialog { background: $bg; }
QStackedWidget > QWidget { background: $bg; }
QWidget#transparente { background: transparent; }

/* ---------- Barra lateral ---------- */
QFrame#sidebar { background: $sidebar; border: none; }
QLabel#logo { color: white; font-size: 17px; font-weight: 900;
              background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3B82F6, stop:1 #1D4ED8);
              border-radius: 12px; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; }
QPushButton#navBtn { background: transparent; border: none; border-radius: 12px; color: #8D99AB;
                     padding: 10px 14px; font-size: 14px; font-weight: 700; min-height: 46px; text-align: left; }
QPushButton#navBtn:hover { color: #FFFFFF; background: rgba(255,255,255,0.04); }
QPushButton#navBtn:checked { color: #FFFFFF; background: transparent; }
QFrame#navIndicador { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563EB, stop:1 #3B82F6);
                      border-radius: 12px; }
QLabel#marca { color: #FFFFFF; font-size: 12px; font-weight: 900; letter-spacing: 0px; }
QLabel#marcaSub { color: #5F6B7C; font-size: 11px; font-weight: 700; }

/* ---------- Encabezado ---------- */
QFrame#header { background: $panel; border-bottom: 1px solid $border; }
QLabel#headerTitle { font-size: 20px; font-weight: 800; }
QLabel#clock { color: $muted; font-size: 14px; font-weight: 600; }
QLabel[chip="ok"] { background: rgba(34,197,94,0.15); color: $success; border-radius: 13px; padding: 4px 12px; font-weight: 700; }
QLabel[chip="off"] { background: rgba(239,68,68,0.15); color: $danger; border-radius: 13px; padding: 4px 12px; font-weight: 700; }

/* ---------- Tarjetas / paneles ---------- */
QFrame[card="true"] { background: $card; border: 1px solid $border; border-radius: 16px; }
QFrame[panel="true"] { background: $panel; border: 1px solid $border; border-radius: 16px; }
QLabel[muted="true"] { color: $muted; }
QLabel[titulo="true"] { font-size: 17px; font-weight: 800; }
QLabel[kpiTitulo="true"] { color: $muted; font-size: 13px; font-weight: 700; }
QLabel[kpiValor="true"] { font-size: 28px; font-weight: 800; }
QLabel[kpiSub="true"] { color: $muted; font-size: 12px; }

QLabel#totalLabel { font-size: 36px; font-weight: 900; color: $success; }
QLabel#cambioLabel { font-size: 32px; font-weight: 900; color: $success; }
QLabel#cambioLabel[falta="true"] { color: $danger; }
QFrame#cambioBox { background: $card2; border: 2px solid $success; border-radius: 14px; }
QFrame#cambioBox[falta="true"] { border-color: $danger; }
QLabel#banner { background: rgba(34,197,94,0.14); color: $success; border: 1px solid $success;
                border-radius: 12px; padding: 10px; font-size: 15px; font-weight: 800; }

/* ---------- Botones ---------- */
QPushButton { background: $card2; border: 1px solid $border; border-radius: 12px; padding: 8px 14px;
              font-weight: 600; min-height: 38px; }
QPushButton:hover { border-color: $primary; background: $hover; }
QPushButton:pressed { background: $pressed; }
QPushButton:disabled { color: $muted; background: $card; }
QPushButton[variant="primary"] { background: $primary; color: $primary_text; border: none; }
QPushButton[variant="primary"]:hover { background: #2563EB; }
QPushButton[variant="success"] { background: $success_dark; color: white; border: none; }
QPushButton[variant="success"]:hover { background: $success; }
QPushButton[variant="success"]:disabled { background: $border; color: $muted; }
QPushButton[variant="danger"] { background: transparent; color: $danger; border: 1px solid $danger; }
QPushButton[variant="danger"]:hover { background: $danger; color: white; }
QPushButton[variant="ghost"] { background: transparent; border: 1px solid transparent; color: $muted; }
QPushButton[variant="ghost"]:hover { color: $text; border-color: $border; }
QPushButton[size="xl"] { font-size: 22px; font-weight: 900; min-height: 66px; border-radius: 16px; }
QPushButton[size="lg"] { font-size: 16px; font-weight: 700; min-height: 50px; border-radius: 14px; }
QPushButton[size="sm"] { min-height: 30px; padding: 4px 10px; font-size: 13px; }
QPushButton[pago="true"] { min-height: 54px; font-size: 15px; font-weight: 800; }
QPushButton[pago="true"]:checked { background: $primary; color: white; border: 2px solid $primary; }
QPushButton[chip="true"] { border-radius: 19px; padding: 6px 18px; min-height: 38px; font-weight: 700; }
QPushButton[chip="true"]:checked { background: $primary; color: white; border-color: $primary; }
QPushButton[qty="true"] { min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
                          padding: 0; font-size: 20px; font-weight: 900; border-radius: 19px; }
QPushButton[del="true"] { min-height: 32px; max-height: 32px; padding: 0 8px; font-size: 12px;
                          color: $danger; background: transparent; border: 1px solid transparent; }
QPushButton[del="true"]:hover { background: rgba(239,68,68,0.15); }

/* ---------- Entradas ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QPlainTextEdit, QTextEdit {
    background: $input; border: 1px solid $border; border-radius: 10px; padding: 7px 10px;
    selection-background-color: $primary; selection-color: white; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 2px solid $primary; }
QLineEdit#search { font-size: 18px; min-height: 50px; padding-left: 16px; border-radius: 14px; }
QLineEdit[money="true"] { font-size: 20px; font-weight: 800; min-height: 46px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: $card; border: 1px solid $border; selection-background-color: $primary; }
QSpinBox::up-button, QSpinBox::down-button { width: 22px; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 22px; height: 22px; border-radius: 6px; border: 2px solid $border; background: $input; }
QCheckBox::indicator:checked { background: $primary; border-color: $primary; }

/* ---------- Tablas ---------- */
QTableView, QTableWidget, QListWidget { background: $card; alternate-background-color: $zebra; gridline-color: $border;
    border: 1px solid $border; border-radius: 12px; selection-background-color: $primary_soft; selection-color: $text; }
QTableView::item, QTableWidget::item { padding: 4px 8px; }
QHeaderView { background: transparent; }
QHeaderView::section { background: $header; color: $muted; font-weight: 800; padding: 9px 8px; border: none;
                       border-bottom: 1px solid $border; }
QTableCornerButton::section { background: $header; border: none; }
QListView#grid { background: transparent; border: none; }

/* ---------- Pestañas ---------- */
QTabWidget::pane { border: 1px solid $border; border-radius: 12px; background: $card; top: -1px; }
QTabBar::tab { background: transparent; color: $muted; padding: 10px 18px; font-weight: 700; border: none;
               border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: $primary; border-bottom: 3px solid $primary; }

/* ---------- Varios ---------- */
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 5px; min-height: 36px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: 5px; min-width: 36px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: $card; color: $text; border: 1px solid $border; padding: 6px; border-radius: 6px; }
QLabel#toast { background: rgba(22,27,34,0.97); color: white; border-radius: 14px; padding: 14px 22px;
               font-size: 15px; font-weight: 700; border: 1px solid $border; }
QLabel[estado="COMPLETADA"] { color: $success; font-weight: 800; }
QLabel[estado="ANULADO"] { color: $danger; font-weight: 800; }
QLabel[estado="DEVOLUCION PARCIAL"] { color: $warning; font-weight: 800; }
QFrame#avisoPrueba { background: rgba(245,158,11,0.10); border: 1px solid $warning; border-radius: 12px; }
QMessageBox QLabel { font-size: 15px; }
QMessageBox QPushButton { min-width: 110px; }
QGroupBox { border: 1px solid $border; border-radius: 12px; margin-top: 18px; padding: 12px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: $muted; }
QProgressBar { border: 1px solid $border; border-radius: 8px; text-align: center; background: $input; }
QProgressBar::chunk { background: $primary; border-radius: 8px; }
""")


def aplicar_tema(app: QApplication) -> None:
    """Aplica el tema oscuro (único) a toda la aplicación."""
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(T["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(T["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(T["input"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(T["zebra"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(T["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(T["card2"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(T["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(T["primary"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(T["card"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(T["text"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(T["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(QSS.substitute(**T))
