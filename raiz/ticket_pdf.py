"""
ticket_pdf.py — Copia en PDF de cada tiquete, idéntica a la que sale por la impresora térmica.

Se dibuja con Pillow (ya incluido en el programa) a partir de las mismas líneas que se envían
a la Jaltech: encabezado, prendas, totales, negritas, letra doble y código de barras Code128.
Ancho real del rollo (80 mm o 58 mm) a 300 dpi, listo para imprimir en hoja o enviar por WhatsApp.

Ruta: <carpeta de tiquetes>\\AAAA\\MM\\<número>.pdf   (p. ej. SETP990000015.pdf)
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config as cfg

DPI = 300
MARGEN_MM = 4

_MONO = ["assets/fonts/DejaVuSansMono.ttf", "consola.ttf", "cour.ttf", "lucon.ttf", "DejaVuSansMono.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]
_MONO_B = ["assets/fonts/DejaVuSansMono-Bold.ttf", "consolab.ttf", "courbd.ttf", "DejaVuSansMono-Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
           "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"]


@lru_cache(maxsize=16)
def _fuente(tam: int, negrita: bool):
    for nombre in (_MONO_B if negrita else _MONO) + _MONO:
        ruta = cfg.resource_path(nombre) if nombre.startswith("assets/") else nombre
        try:
            return ImageFont.truetype(str(ruta), tam)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:
        return ImageFont.load_default()


def _ancho_px(texto: str, f) -> int:
    l, _t, r, _b = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), texto, font=f)
    return r - l


def renderizar(escpos) -> Image.Image:
    """Convierte un EscPos (printer_engine) en una imagen del tiquete."""
    mm = DPI / 25.4
    ancho_papel = int(escpos.ancho_mm * mm)
    margen = int(MARGEN_MM * mm)
    util = ancho_papel - 2 * margen
    # Tamaño de letra para que `cols` caracteres llenen el ancho útil
    tam = 10
    while _ancho_px("M" * escpos.cols, _fuente(tam + 1, False)) <= util:
        tam += 1
    alto_linea = int(tam * 1.32)

    bloques = []                             # (tipo, datos, alto)
    for ln in escpos.lineas:
        if "img" in ln:
            im = ln["img"]
            esc = ancho_papel / im.width          # la imagen ocupa el mismo ancho de papel que en la térmica
            bloques.append(("img", im.convert("L").resize((ancho_papel, max(1, int(im.height * esc))),
                                                          Image.NEAREST), int(im.height * esc)))
        elif "barcode" in ln:
            bloques.append(("bc", ln["barcode"], int(14 * mm)))
        else:
            factor = 2 if ln["h"] else 1
            bloques.append(("tx", ln, alto_linea * factor))
    alto = margen * 2 + sum(b[2] for b in bloques)
    img = Image.new("L", (ancho_papel, max(alto, int(40 * mm))), 255)
    d = ImageDraw.Draw(img)
    y = margen
    for tipo, dato, h in bloques:
        if tipo == "img":
            img.paste(dato, (0, y))
        elif tipo == "bc":
            from barcode_engine import modulos
            try:
                bits = modulos(dato, "code128")
            except Exception:  # noqa: BLE001
                bits = ""
            if bits:
                modulo = max(1, min(4, int(util * 0.8) // len(bits)))
                x0 = (ancho_papel - modulo * len(bits)) // 2
                barras = int(h * 0.7)
                for i, b in enumerate(bits):
                    if b == "1":
                        d.rectangle([x0 + i * modulo, y, x0 + (i + 1) * modulo - 1, y + barras], fill=0)
                f = _fuente(int(tam * 0.85), False)
                d.text(((ancho_papel - _ancho_px(dato, f)) // 2, y + barras + 4), dato, font=f, fill=0)
        else:
            ln = dato
            texto = ln["t"]
            if texto:
                f = _fuente(tam * (2 if ln["h"] else 1), ln["b"])
                if ln["w"] and not ln["h"]:
                    f = _fuente(tam * 2, ln["b"])
                if ln["h"] and not ln["w"]:
                    # alto doble, ancho normal: misma letra un poco más grande, sin pasarse del ancho
                    f = _fuente(int(tam * 1.5), ln["b"])
                w = _ancho_px(texto, f)
                if w > util:                 # nunca cortar texto: reducir si no cabe
                    f = _fuente(max(8, int(f.size * util / w)), ln["b"])
                    w = _ancho_px(texto, f)
                if ln["a"] == "centro":
                    x = (ancho_papel - w) // 2
                elif ln["a"] == "der":
                    x = ancho_papel - margen - w
                else:
                    x = margen
                d.text((x, y), texto, font=f, fill=0)
        y += h
    return img


def ruta_tiquete(conf: dict, numero: str, fecha_iso: str | None = None, sufijo: str = "") -> Path:
    try:
        f = datetime.strptime((fecha_iso or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        f = datetime.now()
    carpeta = cfg.tiquetes_dir(conf) / f"{f:%Y}" / f"{f:%m}"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta / f"{numero}{sufijo}.pdf"


def guardar(escpos, ruta: Path) -> Path:
    img = renderizar(escpos)
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    img.point(lambda v: 0 if v < 150 else 255).convert("1").save(tmp, "PDF", resolution=DPI)  # 1 bit: ~10x más liviano
    tmp.replace(ruta)
    return ruta


def guardar_venta(venta: dict, conf: dict) -> Path | None:
    """Copia PDF del tiquete original (sin la marca COPIA ni la apertura del cajón)."""
    if conf.get("guardar_pdf_tiquetes", "1") != "1":
        return None
    import printer_engine as pe
    tk = pe.construir_tiquete(venta, conf, reimpresion=False, abrir_cajon=False)
    return guardar(tk, ruta_tiquete(conf, venta["numero_tiquete"], venta["fecha"]))


def guardar_devolucion(venta: dict, conf: dict) -> Path | None:
    if conf.get("guardar_pdf_tiquetes", "1") != "1" or not venta.get("devolucion"):
        return None
    import printer_engine as pe
    tk = pe.construir_devolucion(venta, conf)
    dev = venta["devolucion"]["numero"]
    return guardar(tk, ruta_tiquete(conf, venta["numero_tiquete"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    f"_{dev}"))


def ruta_existente(venta: dict, conf: dict) -> Path | None:
    p = ruta_tiquete(conf, venta["numero_tiquete"], venta["fecha"])
    return p if p.exists() else None
