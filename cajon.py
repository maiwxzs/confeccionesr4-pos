from __future__ import annotations

from typing import Any

import printer_engine as pe
from ui.widgets import Contexto, en_segundo_plano


def cajon_activo(ctx: Contexto) -> bool:
    return ctx.db.get_config("abrir_cajon_efectivo") == "1"


def despachar(ctx: Contexto, tk: pe.EscPos | None, titulo: str, origen: str, referencia: str = "",
              imprimir: bool = True, abrir: bool = False) -> None:
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
        except Exception:
            pass
    if datos:
        en_segundo_plano(lambda: pe.enviar(datos, conf, titulo=titulo),
                         al_fallar=lambda m: ctx.notificar(
                             f"La operación quedó guardada, pero no se pudo imprimir:\n{m}", "error"))


def abrir_manual(parent: Any, ctx: Contexto) -> None:
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
