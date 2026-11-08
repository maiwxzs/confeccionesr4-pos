"""
ui/cajon.py — Impresión de comprobantes + cajón monedero TRAZABLE.

Cada vez que el programa manda abrir el cajón (3nStar CD350 u otro conectado por RJ11 a la
impresora de tiquetes) queda un registro con fecha, motivo, documento y cajero. El cierre de
caja muestra cuántas veces se abrió y por qué; la apertura manual pide motivo (y la clave de
administrador, si está configurada).
"""
from __future__ import annotations

from typing import Any

import printer_engine as pe
from ui.widgets import Contexto, en_segundo_plano


def cajon_activo(ctx: Contexto) -> bool:
    return ctx.db.get_config("abrir_cajon_efectivo") == "1"


def despachar(ctx: Contexto, tk: pe.EscPos | None, titulo: str, origen: str, referencia: str = "",
              imprimir: bool = True, abrir: bool = False) -> None:
    """
    Envía el comprobante a la impresora (si `imprimir`) y abre el cajón si `abrir`.
    Si no se imprime pero hay que abrir el cajón, se manda solo el pulso de apertura.
    """
    conf = ctx.conf
    abrir = abrir and cajon_activo(ctx)
    datos = None
    if imprimir and tk is not None:
        if abrir and not getattr(tk, "cajon", False):
            tk.buf[:0] = pe.construir_apertura_cajon(conf).bytes()
        datos = tk.bytes()
    elif abrir:
        datos = pe.construir_apertura_cajon(conf).bytes()
    if abrir:
        try:
            ctx.db.registrar_apertura_cajon(origen, referencia, "", ctx.db.get_config("cajero_actual"))
        except Exception:  # noqa: BLE001 — nunca bloquear la venta por el registro
            pass
    if datos:
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo=titulo),
                         al_fallar=lambda m: ctx.notificar(
                             f"La operación quedó guardada, pero no se pudo imprimir:\n{m}", "error"))


def abrir_manual(parent: Any, ctx: Contexto) -> None:
    """Abrir el cajón sin venta (cambio de sencillo, revisar billetes…): siempre con motivo."""
    from ui.seguridad import autorizar, pedir_motivo
    from ui.widgets import error
    if not cajon_activo(ctx):
        error(parent, "El cajón monedero no está activado.\n\n"
                      "Actívelo en Ajustes › Impresora de tiquetes › «Tengo cajón monedero conectado».")
        return
    motivo = pedir_motivo(parent, "Abrir cajón", "¿Para qué se abre el cajón? Queda registrado en el cierre.",
                          ["Cambio de sencillo", "Revisar / organizar billetes", "Pago a proveedor",
                           "Retiro del dueño", "Otro"])
    if not motivo:
        return
    if not autorizar(parent, ctx, f"Abrir el cajón sin venta · {motivo}"):
        return
    conf = ctx.conf
    datos = pe.construir_apertura_cajon(conf).bytes()
    ctx.db.registrar_apertura_cajon("MANUAL", "", motivo, ctx.db.get_config("cajero_actual"))
    en_segundo_plano(lambda: pe.enviar(datos, conf, titulo="Cajon"),
                     lambda _: ctx.notificar("Cajón abierto (quedó registrado)", "ok"),
                     lambda m: error(parent, f"No se pudo abrir el cajón:\n{m}"))
