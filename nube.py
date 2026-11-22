from __future__ import annotations

import json
import os
import shutil
import string
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import config as cfg

CARPETA = "ConfeccionesR4 Respaldos"
CONSERVAR = 60


# Google Drive
def detectar_drive() -> Path | None:
    candidatos: list[Path] = []
    for letra in "GHIJKLMNOPQRSTUVWXYZDEF":
        for nombre in ("Mi unidad", "My Drive"):
            candidatos.append(Path(f"{letra}:\\{nombre}"))
    casa = Path.home()
    for base in (casa / "Google Drive", casa / "GoogleDrive", casa / "Mi unidad", casa / "My Drive"):
        candidatos += [base / "Mi unidad", base / "My Drive", base]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            candidatos.append(Path(os.environ[var]))
    for c in candidatos:
        try:
            if c.is_dir():
                return c / CARPETA
        except OSError:
            continue
    return None


def respaldar_en_nube(db, motivo: str = "") -> Path | None:
    carpeta = (db.get_config("carpeta_respaldo_nube") or "").strip()
    if not carpeta:
        return None
    destino_dir = Path(carpeta)
    if not destino_dir.parent.exists():
        raise OSError(f"No se encuentra la carpeta {destino_dir.parent}.\n"
                      "¿Está abierto Google Drive para escritorio y con la sesión iniciada?")
    destino_dir.mkdir(parents=True, exist_ok=True)
    nombre = f"respaldo_{datetime.now():%Y%m%d_%H%M%S}{('_' + motivo) if motivo else ''}.db"
    temporal = cfg.backups_dir() / ("nube_" + nombre)
    db.respaldar(temporal, conservar=10_000)
    final = destino_dir / nombre
    shutil.copy2(temporal, destino_dir / (nombre + ".parcial"))
    os.replace(destino_dir / (nombre + ".parcial"), final)
    try:
        temporal.unlink()
    except OSError:
        pass
    for viejo in sorted(destino_dir.glob("respaldo_*.db"))[:-CONSERVAR]:
        try:
            viejo.unlink()
        except OSError:
            pass
    db.set_config({"ultimo_respaldo_nube": datetime.now().strftime("%Y-%m-%d %H:%M")}, auditar=False)
    return final


def respaldo_nube_pendiente(db, horas: int = 20) -> bool:
    if not (db.get_config("carpeta_respaldo_nube") or "").strip():
        return False
    ultimo = db.get_config("ultimo_respaldo_nube") or ""
    try:
        return (datetime.now() - datetime.strptime(ultimo, "%Y-%m-%d %H:%M")).total_seconds() > horas * 3600
    except ValueError:
        return True


# Resumen del cierre
def texto_cierre(cierre: dict[str, Any], conf: dict[str, str]) -> str:
    cop = cfg.cop
    try:
        fecha = datetime.strptime((cierre.get("fecha_cierre") or "")[:19], "%Y-%m-%d %H:%M:%S")
        cuando = fecha.strftime("%d/%m/%Y %I:%M %p")
    except ValueError:
        cuando = cierre.get("fecha_cierre") or ""
    lineas = [f"*{conf.get('razon_social', cfg.APP_NAME)} · Cierre de caja N.º {cierre['id']}*",
              f"{cuando} · Cajero: {cierre.get('cajero') or '-'}", "",
              f"*{cierre['resultado']}*"
              + (f" ({cop(cierre['diferencia'])})" if cierre.get("diferencia") else ""), "",
              f"Ventas: {cop(cierre['total_ventas'])} en {cierre['num_ventas']} tiquetes"]
    if cierre.get("total_devoluciones"):
        lineas.append(f"Devoluciones: -{cop(cierre['total_devoluciones'])}")
    if cierre.get("total_abonos"):
        lineas.append(f"Abonos de separados: {cop(cierre['total_abonos'])}")
    lineas.append(f"Utilidad: {cop(cierre.get('utilidad') or 0)}")
    lineas.append("")
    for m, d in cierre["por_metodo"].items():
        valor = d.get("neto", d.get("total", 0))
        if valor or m in cfg.METODOS_PAGO:
            lineas.append(f"• {m}: {cop(valor)}")
    lineas += ["", f"Efectivo esperado: {cop(cierre['efectivo_esperado'])}",
               f"Efectivo contado: {cop(cierre['efectivo_contado'])}"]
    if cierre.get("total_salidas"):
        lineas.append(f"Gastos / retiros: {cop(cierre['total_salidas'])}")
    cajon = cierre.get("cajon") or {}
    if cajon:
        manual = cajon.get("MANUAL", 0)
        lineas.append(f"Cajón abierto {sum(cajon.values())} veces" + (f" ({manual} sin venta)" if manual else ""))
    if cierre.get("observaciones"):
        lineas += ["", f"Obs: {cierre['observaciones']}"]
    return "\n".join(lineas)


def numero_whatsapp(numero: str) -> str:
    digitos = "".join(ch for ch in str(numero) if ch.isdigit())
    if len(digitos) == 10 and digitos.startswith("3"):
        digitos = "57" + digitos
    return digitos


def url_whatsapp(numero: str, texto: str) -> str:
    return f"https://wa.me/{numero_whatsapp(numero)}?text={urllib.parse.quote(texto)}"


def enviar_telegram(token: str, chat: str, texto: str) -> None:
    import updater
    token = (token or "").strip()
    if not token or not str(chat).strip() or any(ch not in string.ascii_letters + string.digits + ":_-" for ch in token):
        raise ValueError("Token o chat de Telegram no válidos.")
    datos = urllib.parse.urlencode({"chat_id": str(chat).strip(), "text": texto.replace("*", "")}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ultimo: Exception | None = None
    for _n, ctx in updater._contextos_ssl():
        try:
            req = urllib.request.Request(url, data=datos, headers={"User-Agent": f"ConfeccionesR4-POS/{cfg.APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                if not json.loads(r.read().decode("utf-8")).get("ok"):
                    raise ValueError("Telegram rechazó el mensaje (revise el token y el chat).")
            return
        except Exception as e:
            ultimo = e
            if "CERTIFICATE" not in str(e).upper():
                break
    raise ultimo or RuntimeError("No se pudo enviar por Telegram.")
