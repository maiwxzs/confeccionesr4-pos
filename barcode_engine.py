"""
barcode_engine.py — Generador de etiquetas adhesivas con código de barras.

* Usa `python-barcode` para codificar EAN-13 / Code128 y `Pillow` para componer la etiqueta.
* Las barras se dibujan a un número ENTERO de puntos por módulo a la resolución real de la
  impresora térmica (203 dpi = 8 puntos/mm). Así no hay re-escalado ni barras borrosas y
  los lectores láser leen a la primera.
* Si `python-barcode` no está disponible se usan codificadores internos EAN-13 y Code128.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import config as cfg

DPI_TERMICA = 203
PUNTOS_POR_MM = DPI_TERMICA / 25.4  # ≈ 8

# --------------------------------------------------------------------------- #
# Codificación
# --------------------------------------------------------------------------- #
_EAN_L = ["0001101", "0011001", "0010011", "0111101", "0100011",
          "0110001", "0101111", "0111011", "0110111", "0001011"]
_EAN_G = ["0100111", "0110011", "0011011", "0100001", "0011101",
          "0111001", "0000101", "0010001", "0001001", "0010111"]
_EAN_R = ["1110010", "1100110", "1101100", "1000010", "1011100",
          "1001110", "1010000", "1000100", "1001000", "1110100"]
_EAN_PARIDAD = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
                "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


# Code 128: anchos (barra, espacio, barra, espacio, barra, espacio) de los valores 0..106 (norma ISO/IEC 15417)
_C128 = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212",
    "221213", "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221",
    "223211", "221132", "221231", "213212", "223112", "312131", "311222", "321122", "321221",
    "312212", "322112", "322211", "212123", "212321", "232121", "111323", "131123", "131321",
    "112313", "132113", "132311", "211313", "231113", "231311", "112133", "112331", "132131",
    "113123", "113321", "133121", "313121", "211331", "231131", "213113", "213311", "213131",
    "311123", "311321", "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111", "111242",
    "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311",
    "113141", "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
]


def _code128_modulos_interno(datos: str) -> str:
    """Code 128 subconjunto B (ASCII imprimible), con dígito de control módulo 103."""
    if not datos or any(not 32 <= ord(ch) <= 126 for ch in datos):
        raise ValueError(f"Code128: caracteres no válidos en «{datos}»")
    valores = [104] + [ord(ch) - 32 for ch in datos]          # 104 = Start B
    control = (104 + sum(i * v for i, v in enumerate(valores[1:], start=1))) % 103
    valores += [control, 106]                                  # 106 = Stop
    bits = []
    for v in valores:
        for i, ancho in enumerate(_C128[v]):
            bits.append(("1" if i % 2 == 0 else "0") * int(ancho))
    return "".join(bits)


def ean13_valido(codigo: str) -> bool:
    if len(codigo) != 13 or not codigo.isdigit():
        return False
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(codigo[:12]))
    return (10 - s % 10) % 10 == int(codigo[12])


def _ean13_modulos_interno(codigo: str) -> str:
    primero, izq, der = int(codigo[0]), codigo[1:7], codigo[7:13]
    bits = "101"
    for d, p in zip(izq, _EAN_PARIDAD[primero]):
        bits += (_EAN_L if p == "L" else _EAN_G)[int(d)]
    bits += "01010"
    for d in der:
        bits += _EAN_R[int(d)]
    return bits + "101"


def elegir_simbologia(codigo: str, preferida: str = "auto") -> str:
    if preferida == "ean13" and ean13_valido(codigo):
        return "ean13"
    if preferida == "code128":
        return "code128"
    return "ean13" if ean13_valido(codigo) else "code128"


def modulos(codigo: str, simbologia: str) -> str:
    """Cadena de '1' (barra) y '0' (espacio) a nivel de módulo."""
    try:
        import barcode  # python-barcode
        clase = barcode.get_barcode_class("ean13" if simbologia == "ean13" else "code128")
        obj = clase(codigo[:12] if simbologia == "ean13" else codigo)
        return "".join(obj.build())
    except ImportError:
        # Respaldo interno (verificado contra la tabla de la norma): la etiqueta nunca deja de salir
        if simbologia == "ean13":
            return _ean13_modulos_interno(codigo)
        return _code128_modulos_interno(codigo)


# --------------------------------------------------------------------------- #
# Fuentes
# --------------------------------------------------------------------------- #
_CANDIDATAS_NEGRITA = [
    "assets/fonts/Roboto-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "segoeuib.ttf",
    "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_CANDIDATAS_NORMAL = [
    "assets/fonts/Roboto-Regular.ttf", "arial.ttf", "segoeui.ttf", "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


@lru_cache(maxsize=64)
def fuente(tamano: int, negrita: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for nombre in (_CANDIDATAS_NEGRITA if negrita else _CANDIDATAS_NORMAL):
        ruta = cfg.resource_path(nombre) if nombre.startswith("assets/") else nombre
        try:
            return ImageFont.truetype(str(ruta), tamano)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tamano)
    except TypeError:  # Pillow antiguo
        return ImageFont.load_default()


def _ancho_texto(draw: ImageDraw.ImageDraw, txt: str, f) -> int:
    l, t, r, b = draw.textbbox((0, 0), txt, font=f)
    return r - l


def _alto_texto(draw: ImageDraw.ImageDraw, txt: str, f) -> int:
    """Alto ocupado desde el punto de anclaje (incluye el desplazamiento superior de la fuente)."""
    l, t, r, b = draw.textbbox((0, 0), txt, font=f)
    return b


def _ajustar_fuente(draw, txt: str, ancho_max: int, tam_max: int, tam_min: int = 12, negrita=True):
    tam = tam_max
    while tam > tam_min and _ancho_texto(draw, txt, fuente(tam, negrita)) > ancho_max:
        tam -= 1
    return fuente(tam, negrita)


def _partir_en_lineas(draw, txt: str, f, ancho_max: int, max_lineas: int = 2) -> list[str]:
    palabras, lineas, actual = txt.split(), [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if _ancho_texto(draw, prueba, f) <= ancho_max:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = p
        if len(lineas) == max_lineas:
            break
    if actual and len(lineas) < max_lineas:
        lineas.append(actual)
    lineas = [l.strip(" ·") for l in lineas]  # no dejar separadores colgando al partir
    # Si se truncó, añadir puntos suspensivos a la última línea
    if len(" ".join(lineas)) < len(txt) and lineas:
        ult = lineas[-1]
        while ult and _ancho_texto(draw, ult + "…", f) > ancho_max:
            ult = ult[:-1]
        lineas[-1] = ult.rstrip() + "…"
    return lineas


# --------------------------------------------------------------------------- #
# Etiqueta
# --------------------------------------------------------------------------- #
def generar_etiqueta(producto: dict[str, Any] | Any, ancho_mm: float = 50, alto_mm: float = 30,
                     simbologia: str = "auto", tienda: str = "CONFECCIONESR4",
                     mostrar_tienda: bool = True) -> Image.Image:
    """
    Devuelve una imagen 1-bit (modo '1') lista para imprimir a 203 dpi:
    [tienda] / nombre del producto / código de barras / código legible / PRECIO.
    """
    p = dict(producto)
    codigo = str(p["codigo"]).strip()
    W = int(round(ancho_mm * PUNTOS_POR_MM))
    H = int(round(alto_mm * PUNTOS_POR_MM))
    margen = max(8, int(1.5 * PUNTOS_POR_MM))
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    util = W - 2 * margen
    y = margen // 2

    # 1. Tienda
    if mostrar_tienda and tienda:
        f = fuente(max(14, H // 14), True)
        d.text(((W - _ancho_texto(d, tienda, f)) // 2, y), tienda, font=f, fill=0)
        y += _alto_texto(d, tienda, f) + 4

    # 2. Nombre (hasta 2 líneas)
    f_nom = fuente(max(16, H // 11), True)
    for linea in _partir_en_lineas(d, str(p.get("nombre", "")), f_nom, util, 2):
        d.text(((W - _ancho_texto(d, linea, f_nom)) // 2, y), linea, font=f_nom, fill=0)
        y += _alto_texto(d, linea, f_nom) + 3
    y += 3

    # 3. Precio (reservar espacio abajo)
    precio_txt = cfg.cop(p.get("precio", 0))
    f_precio = _ajustar_fuente(d, precio_txt, util, max(26, H // 5), 18)
    alto_precio = _alto_texto(d, precio_txt, f_precio)
    f_cod = fuente(max(13, H // 15), False)
    alto_cod = _alto_texto(d, codigo, f_cod)

    # 4. Código de barras con módulos enteros (barras nítidas)
    simb = elegir_simbologia(codigo, simbologia)
    bits = modulos(codigo, simb)
    quiet = 10 if simb == "ean13" else 10  # zona silenciosa en módulos
    modulo = max(1, util // (len(bits) + 2 * quiet))
    modulo = min(modulo, 4)
    ancho_barras = modulo * len(bits)
    x0 = (W - ancho_barras) // 2
    alto_barras = H - y - alto_cod - alto_precio - margen - 10
    alto_barras = max(alto_barras, int(6 * PUNTOS_POR_MM))
    for i, b in enumerate(bits):
        if b == "1":
            d.rectangle([x0 + i * modulo, y, x0 + (i + 1) * modulo - 1, y + alto_barras], fill=0)
    y += alto_barras + 3

    # 5. Código legible
    d.text(((W - _ancho_texto(d, codigo, f_cod)) // 2, y), codigo, font=f_cod, fill=0)
    y += alto_cod + 4

    # 6. Precio
    d.text(((W - _ancho_texto(d, precio_txt, f_precio)) // 2, H - margen // 2 - alto_precio - 2),
           precio_txt, font=f_precio, fill=0)

    return img.point(lambda v: 0 if v < 160 else 255, mode="1")


def guardar_etiqueta_png(img: Image.Image, ruta: str | Path) -> Path:
    ruta = Path(ruta)
    img.save(ruta, dpi=(DPI_TERMICA, DPI_TERMICA))
    return ruta


def hoja_de_etiquetas(img: Image.Image, copias: int, columnas: int = 1, separacion_px: int = 16) -> Image.Image:
    """Apila varias copias en una tira continua (útil para rollos de etiquetas en ESC/POS)."""
    filas = (copias + columnas - 1) // columnas
    W = img.width * columnas + separacion_px * (columnas - 1)
    H = img.height * filas + separacion_px * (filas - 1)
    hoja = Image.new("1", (W, H), 1)
    for i in range(copias):
        r, c = divmod(i, columnas)
        hoja.paste(img, (c * (img.width + separacion_px), r * (img.height + separacion_px)))
    return hoja
