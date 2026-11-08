"""
ui/seguridad.py — Autorizaciones y confirmaciones para operaciones delicadas.

* autorizar(): pide la CLAVE DE ADMINISTRADOR (si está definida en Ajustes › Seguridad) antes de
  anular, borrar, reabrir, corregir o reiniciar. Sin clave definida, pide confirmación y
  recomienda crear una.
* pedir_motivo(): motivo obligatorio (queda en la auditoría).
* pedir_monto(): valor en pesos con formato.
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QVBoxLayout

from ui.animations import DialogoAnimado
from ui.widgets import Contexto, MoneyEdit, boton, confirmar, error, etiqueta


class _DialogoClave(DialogoAnimado):
    def __init__(self, accion: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Autorización requerida")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.addWidget(etiqueta("Clave de administrador", titulo=True))
        t = etiqueta(accion, muted=True)
        t.setWordWrap(True)
        lay.addWidget(t)
        self.clave = QLineEdit()
        self.clave.setEchoMode(QLineEdit.EchoMode.Password)
        self.clave.setPlaceholderText("Clave numérica")
        self.clave.setMaxLength(8)
        self.clave.setProperty("money", True)
        lay.addWidget(self.clave)
        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        ok = boton("Autorizar", "primary", "lg")
        ok.clicked.connect(self.accept)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)
        self.clave.returnPressed.connect(self.accept)
        self.clave.setFocus()


def autorizar(parent, ctx: Contexto, accion: str) -> bool:
    db = ctx.db
    if not db.tiene_pin():
        return confirmar(parent, f"{accion}\n\n¿Continuar?\n\n(Recomendación: defina una clave de administrador en "
                                 "Ajustes › Seguridad para proteger estas operaciones.)", "Confirmar")
    for intento in range(3):
        dlg = _DialogoClave(accion if intento == 0 else f"{accion}\n\nClave incorrecta. Intento {intento + 1} de 3.",
                            parent)
        if not dlg.exec():
            return False
        if db.verificar_pin(dlg.clave.text()):
            return True
    db.auditar("CLAVE_ADMIN_FALLIDA", "seguridad", "", {"accion": accion})
    error(parent, "Clave incorrecta 3 veces. La operación se canceló y quedó registrada en la auditoría.")
    return False


class _DialogoMotivo(DialogoAnimado):
    def __init__(self, titulo: str, texto: str, opciones: list[str] | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.addWidget(etiqueta(titulo, titulo=True))
        t = etiqueta(texto, muted=True)
        t.setWordWrap(True)
        lay.addWidget(t)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.addItems([""] + (opciones or []))
        self.combo.setMinimumHeight(42)
        self.combo.lineEdit().setPlaceholderText("Escriba o elija el motivo (obligatorio)")
        lay.addWidget(self.combo)
        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        ok = boton("Continuar", "primary", "lg")
        ok.clicked.connect(self._ok)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)

    def _ok(self) -> None:
        if len(self.combo.currentText().strip()) < 3:
            error(self, "Escriba el motivo (mínimo 3 letras). Queda registrado en la auditoría.")
            return
        self.accept()


def pedir_motivo(parent, titulo: str, texto: str = "", opciones: list[str] | None = None) -> str | None:
    dlg = _DialogoMotivo(titulo, texto, opciones, parent)
    return dlg.combo.currentText().strip() if dlg.exec() else None


class _DialogoMonto(DialogoAnimado):
    def __init__(self, titulo: str, texto: str, valor: int | None, concepto: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.addWidget(etiqueta(titulo, titulo=True))
        if texto:
            t = etiqueta(texto, muted=True)
            t.setWordWrap(True)
            lay.addWidget(t)
        self.monto = MoneyEdit()
        if valor is not None:
            self.monto.setValor(valor, emitir=False)
        lay.addWidget(self.monto)
        self.concepto = QComboBox()
        self.concepto.setEditable(True)
        self.concepto.setMinimumHeight(42)
        self.concepto.setVisible(concepto)
        lay.addWidget(self.concepto)
        fila = QHBoxLayout()
        c = boton("Cancelar", "ghost", "lg")
        c.clicked.connect(self.reject)
        ok = boton("Guardar", "primary", "lg")
        ok.clicked.connect(self.accept)
        fila.addWidget(c)
        fila.addStretch()
        fila.addWidget(ok)
        lay.addLayout(fila)
        self.monto.setFocus()


def pedir_monto(parent, titulo: str, texto: str = "", valor: int | None = None,
                conceptos: list[str] | None = None) -> tuple[int, str] | None:
    dlg = _DialogoMonto(titulo, texto, valor, conceptos is not None, parent)
    if conceptos is not None:
        dlg.concepto.addItems([""] + conceptos)
        dlg.concepto.lineEdit().setPlaceholderText("Concepto (obligatorio)")
    if not dlg.exec():
        return None
    return dlg.monto.valor(), dlg.concepto.currentText().strip()
