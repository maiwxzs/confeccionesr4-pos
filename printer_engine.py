"""
printer_engine.py — Impresión térmica directa ESC/POS para impresoras Jaltech (80 mm / 58 mm).

Por qué así
-----------
* Los bytes ESC/POS se generan aquí mismo (sin plantillas PDF ni visores): un tiquete
  completo pesa ~2 KB y sale en menos de 1 segundo.
* Transporte por defecto ``win32``: se envía el flujo RAW a la cola de Windows con
  ``win32print`` usando el driver USB que Jaltech instala. Es lo más estable: no exige
  reemplazar el driver con Zadig/WinUSB y no abre ningún diálogo.
* Transporte ``usb`` (python-escpos + libusb) disponible para quien ya usa WinUSB.
* Transporte ``red`` (puerto 9100) para modelos Ethernet y ``archivo`` para pruebas.

Anchos: 80 mm = 48 columnas / 576 puntos · 58 mm = 32 columnas / 384 puntos (Fuente A).
"""
from __future__ import annotations

import socket
import unicodedata
from datetime import datetime
from typing import Any

import config as cfg

ESC, GS, FS = b"\x1b", b"\x1d", b"\x1c"

# Tabla de caracteres ESC t n más comunes en firmwares Epson/Jaltech/Xprinter
CODEPAGES = {"cp437": 0, "cp850": 2, "cp860": 3, "cp858": 19, "cp1252": 16}


class PrinterError(Exception):
    pass


# =========================================================================== #
# Constructor de comandos ESC/POS
# =========================================================================== #
class EscPos:
    """Construye el flujo de bytes y, en paralelo, una vista previa en texto plano."""

    def __init__(self, ancho_mm: int = 80, codepage: str = "cp850"):
        self.ancho_mm = 58 if int(ancho_mm) == 58 else 80
        self.cols = 32 if self.ancho_mm == 58 else 48
        self.dots = 384 if self.ancho_mm == 58 else 576
        self.codepage = codepage if codepage in CODEPAGES else "cp850"
        self.buf = bytearray()
        self.preview: list[str] = []
        self.lineas: list[dict] = []      # copia con formato, para generar el PDF del tiquete
        self._doble = False
        self._alto = False
        self._negrita = False
        self.buf += ESC + b"@"                                   # Inicializar
        self.buf += ESC + b"t" + bytes([CODEPAGES[self.codepage]])  # Tabla de caracteres
        self.buf += FS + b"."                                    # Cancelar modo Kanji (clones chinos)

    # ---------- texto ----------
    def _enc(self, txt: str) -> bytes:
        try:
            return txt.encode(self.codepage)
        except UnicodeEncodeError:
            out = bytearray()
            for ch in txt:
                try:
                    out += ch.encode(self.codepage)
                except UnicodeEncodeError:
                    base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore")
                    out += base or b"?"
            return bytes(out)

    @property
    def ancho(self) -> int:
        return self.cols // 2 if self._doble else self.cols

    def alinear(self, modo: str = "izq") -> "EscPos":
        self.buf += ESC + b"a" + bytes([{"izq": 0, "centro": 1, "der": 2}[modo]])
        self._alin = modo
        return self

    def negrita(self, on: bool = True) -> "EscPos":
        self.buf += ESC + b"E" + (b"\x01" if on else b"\x00")
        self._negrita = on
        return self

    def tamano(self, ancho: int = 1, alto: int = 1) -> "EscPos":
        n = ((max(1, min(ancho, 8)) - 1) << 4) | (max(1, min(alto, 8)) - 1)
        self.buf += GS + b"!" + bytes([n])
        self._doble = ancho > 1
        self._alto = alto > 1
        return self

    def texto(self, txt: str = "", salto: bool = True) -> "EscPos":
        self.buf += self._enc(txt)
        if salto:
            self.buf += b"\n"
        alin = getattr(self, "_alin", "izq")
        w = self.ancho
        linea = txt.center(w) if alin == "centro" else (txt.rjust(w) if alin == "der" else txt)
        if self._doble:  # simula el doble ancho en la vista previa
            linea = " ".join(linea[:w])
        self.preview.append(linea.rstrip()[: self.cols])
        self.lineas.append({"t": txt, "b": self._negrita, "w": self._doble, "h": self._alto, "a": alin})
        return self

    def envolver(self, txt: str) -> "EscPos":
        """Texto largo partido en líneas del ancho disponible (sin cortar palabras)."""
        for parrafo in str(txt).split("\n"):
            linea = ""
            for palabra in parrafo.split():
                if len(linea) + len(palabra) + (1 if linea else 0) <= self.ancho:
                    linea = f"{linea} {palabra}".strip()
                else:
                    if linea.strip(" ·"):
                        self.texto(linea.strip(" ·"))
                    linea = palabra[: self.ancho]
            self.texto(linea.strip(" ·"))
        return self

    def dos_columnas(self, izq: str, der: str) -> "EscPos":
        w = self.ancho
        izq = izq[: max(0, w - len(der) - 1)]
        return self.texto(izq + " " * (w - len(izq) - len(der)) + der)

    def separador(self, ch: str = "-") -> "EscPos":
        return self.texto(ch * self.ancho)

    def avanzar(self, lineas: int = 1) -> "EscPos":
        self.buf += ESC + b"d" + bytes([max(0, min(lineas, 255))])
        self.preview += [""] * lineas
        self.lineas += [{"t": "", "b": False, "w": False, "h": False, "a": "izq"}] * lineas
        return self

    # ---------- códigos ----------
    def codigo_barras(self, datos: str, alto: int = 60, ancho_modulo: int = 2) -> "EscPos":
        datos = "".join(ch for ch in datos if 32 <= ord(ch) < 127)
        self.buf += GS + b"h" + bytes([alto])            # Alto en puntos
        self.buf += GS + b"w" + bytes([ancho_modulo])    # Ancho de módulo (2..6)
        self.buf += GS + b"H" + b"\x02"                  # Texto legible debajo
        self.buf += GS + b"f" + b"\x00"
        payload = b"{B" + datos.encode("ascii")
        self.buf += GS + b"k" + bytes([73, len(payload)]) + payload  # CODE128 (función B)
        self.buf += b"\n"
        self.preview.append(f"||| {datos} |||".center(self.cols))
        self.lineas.append({"barcode": datos})
        return self

    def imagen(self, img, fragmento: int = 256) -> "EscPos":
        """Imagen PIL en modo raster GS v 0 (centrada y ajustada al ancho del papel)."""
        from PIL import Image, ImageOps
        im = img.convert("L")
        if im.width > self.dots:
            alto = int(im.height * self.dots / im.width)
            im = im.resize((self.dots, alto), Image.NEAREST)
        # Centrar en el ancho del papel
        lienzo = Image.new("L", (self.dots, im.height), 255)
        lienzo.paste(im, ((self.dots - im.width) // 2, 0))
        bw = ImageOps.invert(lienzo).point(lambda v: 255 if v > 96 else 0).convert("1")
        ancho_bytes = (bw.width + 7) // 8
        datos = bw.tobytes()
        for y in range(0, bw.height, fragmento):
            h = min(fragmento, bw.height - y)
            trozo = datos[y * ancho_bytes:(y + h) * ancho_bytes]
            self.buf += GS + b"v0\x00" + bytes([ancho_bytes & 0xFF, ancho_bytes >> 8, h & 0xFF, h >> 8])
            self.buf += trozo
        self.preview.append(f"[imagen {img.width}x{img.height}]".center(self.cols))
        # Copia en blanco y negro (negro = 0) para el PDF del tiquete
        self.lineas.append({"img": lienzo.point(lambda v: 0 if v < 160 else 255).convert("1")})
        return self

    # ---------- hardware ----------
    def abrir_cajon(self) -> "EscPos":
        self.buf += ESC + b"p\x00\x19\xfa"   # Pin 2, pulso 50 ms / 500 ms
        return self

    def cortar(self) -> "EscPos":
        self.buf += GS + b"V" + bytes([66, 3])  # Avanza 3 líneas y corte parcial
        self.preview.append("✂" + "-" * (self.cols - 1))
        return self

    def bytes(self) -> bytes:
        return bytes(self.buf)

    def texto_preview(self) -> str:
        return "\n".join(self.preview)


# =========================================================================== #
# Transportes
# =========================================================================== #
def listar_impresoras() -> list[str]:
    try:
        import win32print
    except ImportError:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def impresora_predeterminada() -> str:
    try:
        import win32print
        return win32print.GetDefaultPrinter()
    except Exception:
        return ""


def _enviar_win32(datos: bytes, nombre: str, titulo: str) -> None:
    try:
        import win32print
    except ImportError as e:
        raise PrinterError("pywin32 no está disponible (solo Windows).") from e
    nombre = nombre or win32print.GetDefaultPrinter()
    try:
        h = win32print.OpenPrinter(nombre)
    except Exception as e:
        raise PrinterError(f"No se encontró la impresora «{nombre}».\nRevise el cable USB y la "
                           "configuración de impresoras.") from e
    try:
        win32print.StartDocPrinter(h, 1, (titulo, None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, datos)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def _enviar_usb(datos: bytes, vendor: str, product: str) -> None:
    try:
        from escpos.printer import Usb
    except ImportError as e:
        raise PrinterError("python-escpos no está instalado.") from e
    try:
        p = Usb(int(vendor, 16), int(product, 16), timeout=0)
        p._raw(datos)
        p.close()
    except Exception as e:
        raise PrinterError(f"Error USB directo: {e}\n¿Instaló el driver WinUSB con Zadig?") from e


def _enviar_red(datos: bytes, destino: str) -> None:
    host, _, puerto = destino.partition(":")
    try:
        with socket.create_connection((host, int(puerto or 9100)), timeout=3) as s:
            s.sendall(datos)
    except OSError as e:
        raise PrinterError(f"No hay conexión con la impresora de red {destino}: {e}") from e


def enviar(datos: bytes, conf: dict[str, str], impresora: str | None = None,
           titulo: str = "ConfeccionesR4") -> None:
    """Despacha bytes RAW según el transporte configurado."""
    transporte = conf.get("transporte_impresion", "win32")
    nombre = conf.get("impresora_tiquetes", "") if impresora is None else impresora
    if transporte == "usb":
        _enviar_usb(datos, conf.get("usb_vendor_id", "0x0483"), conf.get("usb_product_id", "0x5743"))
    elif transporte == "red":
        _enviar_red(datos, nombre)
    elif transporte == "archivo":
        destino = cfg.data_dir() / "impresiones"
        destino.mkdir(exist_ok=True)
        (destino / f"{titulo}_{datetime.now():%Y%m%d_%H%M%S_%f}.bin").write_bytes(datos)
    else:
        _enviar_win32(datos, nombre, titulo)


# =========================================================================== #
# Documentos
# =========================================================================== #
def _nuevo(conf: dict[str, str]) -> EscPos:
    return EscPos(int(conf.get("ancho_papel_mm", 80) or 80), conf.get("codepage", "cp850"))


LOGO_PREDETERMINADO = "logo_ticket.png"


def ruta_logo(conf: dict[str, str]):
    """Logo del tiquete: el que se cargó en Ajustes o, si no hay, el águila de ConfeccionesR4."""
    from pathlib import Path
    propio = str(conf.get("logo_tiquete") or "").strip()
    if propio and Path(propio).is_file():
        return Path(propio)
    p = cfg.resource_path("assets", LOGO_PREDETERMINADO)
    return p if p.is_file() else None


def _logo(p: EscPos, conf: dict[str, str]) -> None:
    if conf.get("mostrar_logo_tiquete", "1") != "1":
        return
    ruta = ruta_logo(conf)
    if not ruta:
        return
    try:
        from PIL import Image
        im = Image.open(ruta)
        im.load()
    except Exception:  # noqa: BLE001 — un logo dañado nunca debe impedir imprimir la venta
        return
    try:
        pct = max(25, min(90, int(conf.get("tamano_logo_tiquete", "50") or 50)))
    except ValueError:
        pct = 50
    ancho = int(p.dots * pct / 100)
    alto = max(1, int(im.height * ancho / im.width))
    tope = int(p.dots * 0.75)                 # logos muy altos: limitar para no gastar papel
    if alto > tope:
        ancho, alto = max(1, int(ancho * tope / alto)), tope
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fondo = Image.new("RGBA", im.size, (255, 255, 255, 255))
        fondo.alpha_composite(im)
        im = fondo
    im = im.convert("L").resize((ancho, alto), Image.LANCZOS)
    # Tramado (puntos) para que los grises y degradados del logo también salgan en papel térmico
    im = im.convert("1").convert("L")
    p.alinear("centro").imagen(im)


def preparar_logo(origen) -> "Path":
    """Copia el logo elegido a la carpeta de datos, en escala de grises, recortado y sin fondo
    transparente. Devuelve la ruta para guardarla en la configuración."""
    from pathlib import Path
    from PIL import Image, ImageChops, ImageOps
    im = Image.open(origen)
    im.load()
    im = im.convert("RGBA")
    fondo = Image.new("RGBA", im.size, (255, 255, 255, 255))
    fondo.alpha_composite(im)
    gris = ImageOps.autocontrast(fondo.convert("L"), cutoff=1)
    caja = ImageChops.invert(gris).point(lambda v: 255 if v > 24 else 0).getbbox()
    if caja:
        margen = 6
        caja = (max(0, caja[0] - margen), max(0, caja[1] - margen),
                min(gris.width, caja[2] + margen), min(gris.height, caja[3] + margen))
        gris = gris.crop(caja)
    if max(gris.size) > 1200:
        gris.thumbnail((1200, 1200), Image.LANCZOS)
    destino = Path(cfg.data_dir()) / "logo_tiquete.png"
    tmp = destino.with_suffix(".tmp.png")
    gris.save(tmp)
    tmp.replace(destino)
    return destino


def tiquete_de_muestra(conf: dict[str, str]):
    """PDF de ejemplo con el diseño actual (no gasta consecutivo ni papel)."""
    import ticket_pdf
    venta = {
        "numero_tiquete": f"{conf.get('prefijo', 'SETP')}{conf.get('consecutivo_siguiente', '990000000')}",
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "estado": "EMITIDO",
        "cajero": conf.get("cajero_actual", "Cajero"), "cliente_nombre": "", "cliente_documento": "",
        "metodo_pago": "Efectivo", "subtotal": 145000, "descuento": 5000, "total": 140000,
        "monto_recibido": 150000, "cambio": 10000,
        "items": [
            {"codigo": "BG004", "referencia": "BG004", "nombre": "BOGGY JEANS DAMA TALLA 8",
             "cantidad": 1, "precio_unitario": 85000, "subtotal": 85000},
            {"codigo": "ST007", "referencia": "ST007", "nombre": "BLUSA STRAPLESS NEGRA",
             "cantidad": 2, "precio_unitario": 30000, "subtotal": 60000},
        ],
    }
    tk = construir_tiquete(venta, conf, abrir_cajon=False)
    return ticket_pdf.guardar(tk, cfg.exports_dir() / "Vista_previa_tiquete.pdf")


def _cabecera(p: EscPos, conf: dict[str, str], resolucion: bool = True) -> None:
    _logo(p, conf)
    p.alinear("centro").negrita(True).tamano(2, 2).texto(conf.get("razon_social", "")).tamano(1, 1)
    p.texto(f"NIT {conf.get('nit', '')}").negrita(False)
    p.envolver(conf.get("regimen", ""))
    p.envolver(conf.get("direccion", ""))
    tel = conf.get("telefono", "")
    ciudad = conf.get("ciudad", "")
    p.envolver(" - ".join(x for x in (f"Tel. {tel}" if tel else "", ciudad) if x))
    if conf.get("correo"):
        p.envolver(conf["correo"])
    if conf.get("redes"):
        p.envolver(conf["redes"])
    if resolucion:
        p.separador("=")
        if cfg.es_resolucion_de_prueba(conf):
            # Numeración pública del set de pruebas DIAN: no se presenta como autorización real
            p.envolver("COMPROBANTE INTERNO DE VENTA - No es factura ni documento equivalente electrónico")
        else:
            p.envolver(f"Resolución DIAN No. {conf.get('resolucion_dian', '')}")
            p.envolver(f"Prefijo {conf.get('prefijo', '')} del {conf.get('rango_desde', '')} "
                       f"al {conf.get('rango_hasta', '')}")
    p.alinear("izq").separador("=")


def _fecha_txt(iso: str) -> str:
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(iso)


def _fecha_hora(iso: str) -> tuple[str, str]:
    try:
        f = datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S")
        return f.strftime("%d/%m/%Y"), f.strftime("%I:%M %p").lower().replace("am", "a.m.").replace("pm", "p.m.")
    except (TypeError, ValueError):
        return str(iso), ""


def construir_tiquete(venta: dict[str, Any], conf: dict[str, str], reimpresion: bool = False,
                      abrir_cajon: bool | None = None) -> EscPos:
    p = _nuevo(conf)
    if abrir_cajon is None:
        abrir_cajon = venta["metodo_pago"] == "Efectivo" and conf.get("abrir_cajon_efectivo") == "1" \
            and not reimpresion
    if abrir_cajon:
        p.abrir_cajon()
    _cabecera(p, conf)
    titulo = "COMPROBANTE DE VENTA" if cfg.es_resolucion_de_prueba(conf) else "TIQUETE DE VENTA POS"
    p.alinear("centro").negrita(True).texto(titulo)
    p.tamano(1, 2).texto(f"N.º {venta['numero_tiquete']}").tamano(1, 1).negrita(False)
    if reimpresion:
        p.texto("*** COPIA / REIMPRESIÓN ***")
    if venta.get("estado") == "ANULADO":
        p.negrita(True).texto("*** TIQUETE ANULADO ***").negrita(False)
    elif venta.get("estado") == "DEVOLUCION PARCIAL":
        p.negrita(True).texto("*** CON DEVOLUCIÓN PARCIAL ***").negrita(False)
    p.alinear("izq").separador("-")
    fecha, hora = _fecha_hora(venta["fecha"])
    p.dos_columnas(f"Fecha: {fecha}", (f"Hora: {hora}" if p.cols >= 48 else hora) if hora else "")
    if venta.get("cajero"):
        p.texto(f"Atendido por: {venta['cajero']}")
    if venta.get("cliente_nombre") or venta.get("cliente_documento"):
        p.envolver(f"Cliente: {venta.get('cliente_nombre') or ''}".rstrip())
        if venta.get("cliente_documento"):
            p.texto(f"C.C./NIT: {venta['cliente_documento']}")
    p.separador("-")
    # Tabla: CANT  DESCRIPCIÓN ................ VALOR
    w_val = 11 if p.cols >= 48 else 10
    w_cant = 5 if p.cols >= 48 else 4
    w_desc = p.cols - w_cant - w_val
    p.negrita(True).texto(("CANT" if p.cols >= 48 else "UND").ljust(w_cant) + "DESCRIPCIÓN".ljust(w_desc) + "VALOR".rjust(w_val))
    p.negrita(False).separador("-")
    unidades = 0
    for it in venta["items"]:
        unidades += it["cantidad"]
        palabras, lineas, actual = str(it["nombre"]).split(), [], ""
        for pal in palabras:
            if len(actual) + len(pal) + (1 if actual else 0) <= w_desc - 1:
                actual = f"{actual} {pal}".strip()
            else:
                if actual:
                    lineas.append(actual)
                actual = pal[: w_desc - 1]
        lineas.append(actual)
        p.texto(str(it["cantidad"]).ljust(w_cant) + lineas[0].ljust(w_desc)
                + cfg.cop(it["subtotal"]).rjust(w_val))
        for extra in lineas[1:]:
            p.texto(" " * w_cant + extra)
        ref = it.get("referencia") or it["codigo"]
        detalle = f"{ref}  ·  {cfg.cop(it['precio_unitario'])} c/u" if it["cantidad"] > 1 else f"{ref}"
        p.texto(" " * w_cant + detalle[: w_desc + w_val])
    p.separador("-")
    p.dos_columnas(f"Artículos: {unidades}", "")
    p.dos_columnas("Subtotal", cfg.cop(venta["subtotal"]))
    if venta["descuento"]:
        p.dos_columnas("Descuento", "-" + cfg.cop(venta["descuento"]))
    p.separador("=")
    total_txt = cfg.cop(venta["total"])
    if len("TOTAL") + 1 + len(total_txt) <= p.cols // 2:
        p.negrita(True).tamano(2, 2).dos_columnas("TOTAL", total_txt).tamano(1, 1).negrita(False)
    else:   # montos grandes en 58 mm: letra alta pero de ancho normal, para que nunca se corte
        p.negrita(True).tamano(1, 2).dos_columnas("TOTAL A PAGAR", total_txt).tamano(1, 1).negrita(False)
    p.separador("=")
    p.dos_columnas("Forma de pago:", venta["metodo_pago"])
    if venta["metodo_pago"] == "Efectivo":
        p.dos_columnas("Recibido:", cfg.cop(venta["monto_recibido"]))
        p.negrita(True).dos_columnas("Cambio:", cfg.cop(venta["cambio"])).negrita(False)
    p.separador("-")
    p.alinear("centro")
    p.envolver("No responsable de IVA · Precios finales")
    if conf.get("imprimir_codigo_barras_tiquete", "1") == "1":
        p.avanzar(1)
        p.codigo_barras(venta["numero_tiquete"], alto=50, ancho_modulo=2)
    if conf.get("mensaje_pie"):
        p.avanzar(1)
        p.negrita(True).envolver(conf["mensaje_pie"]).negrita(False)
    p.texto(f"{cfg.APP_NAME} v{cfg.APP_VERSION}")
    p.alinear("izq").avanzar(1).cortar()
    return p


def construir_cierre(cierre: dict[str, Any], conf: dict[str, str]) -> EscPos:
    p = _nuevo(conf)
    _cabecera(p, conf, resolucion=False)
    p.alinear("centro").negrita(True).tamano(1, 2).texto(f"CIERRE DE CAJA N.º {cierre['id']}")
    p.tamano(1, 1).negrita(False).alinear("izq")
    p.dos_columnas("Apertura:", _fecha_txt(cierre["fecha_apertura"]))
    p.dos_columnas("Cierre:", _fecha_txt(cierre["fecha_cierre"] or ""))
    p.dos_columnas("Cajero:", cierre.get("cajero") or "-")
    p.dos_columnas("N.º ventas:", str(cierre["num_ventas"]))
    p.separador()
    p.negrita(True).texto("INGRESOS POR MÉTODO").negrita(False)
    for m in cfg.METODOS_PAGO:
        p.dos_columnas(f"  {m}", cfg.cop(cierre["por_metodo"][m]["total"]))
    p.negrita(True).dos_columnas("TOTAL VENTAS", cfg.cop(cierre["total_ventas"])).negrita(False)
    p.dos_columnas("Devoluciones", "-" + cfg.cop(cierre["total_devoluciones"]))
    p.separador()
    p.negrita(True).texto("ARQUEO DE EFECTIVO").negrita(False)
    p.dos_columnas("Base inicial", cfg.cop(cierre["base_inicial"]))
    p.dos_columnas("+ Ventas efectivo", cfg.cop(cierre["total_efectivo"]))
    p.dos_columnas("- Devol. efectivo", cfg.cop(cierre["devoluciones_efectivo"]))
    if cierre.get("total_entradas"):
        p.dos_columnas("+ Entradas de efectivo", cfg.cop(cierre["total_entradas"]))
    if cierre.get("total_salidas"):
        p.dos_columnas("- Salidas (gastos/retiros)", cfg.cop(cierre["total_salidas"]))
    p.dos_columnas("= Esperado en cajón", cfg.cop(cierre["efectivo_esperado"]))
    p.dos_columnas("Contado (ciego)", cfg.cop(cierre["efectivo_contado"]))
    p.negrita(True).dos_columnas("DIFERENCIA", cfg.cop(cierre["diferencia"])).negrita(False)
    p.separador("=")
    p.alinear("centro").negrita(True).tamano(2, 2).texto(cierre["resultado"]).tamano(1, 1).negrita(False)
    p.alinear("izq").separador("=")
    movs = [m for m in cierre.get("movimientos", []) if not m.get("anulado")]
    if movs:
        p.negrita(True).texto("MOVIMIENTOS DE EFECTIVO").negrita(False)
        for m in movs:
            signo = "+" if m["tipo"] == "ENTRADA" else "-"
            p.dos_columnas(f"{signo} {m['concepto']}", cfg.cop(m["monto"]))
        p.separador()
    conc = cierre.get("conciliacion") or {}
    if conc:
        p.negrita(True).texto("CONCILIACIÓN PAGOS DIGITALES").negrita(False)
        for m, d in conc.items():
            p.texto(m)
            p.dos_columnas("  Sistema / Reportado", f"{cfg.cop(d['sistema'])} / {cfg.cop(d['declarado'])}")
            estado = "OK" if d["diferencia"] == 0 else cfg.cop(d["diferencia"])
            p.dos_columnas("  Diferencia", estado)
        p.separador()
    if cierre.get("reaperturas"):
        p.texto(f"Caja reabierta {cierre['reaperturas']} vez/veces (ver auditoría)")
    if cierre.get("observaciones"):
        p.envolver(f"Obs: {cierre['observaciones']}")
    p.avanzar(2).texto("Firma cajero: ____________________")
    p.avanzar(1).texto("Firma revisor: ___________________")
    p.avanzar(1).cortar()
    return p


def construir_devolucion(venta: dict[str, Any], conf: dict[str, str], motivo: str = "",
                         abrir_cajon: bool = False) -> EscPos:
    """Comprobante de devolución / anulación (usa venta['devolucion'] si viene de registrar_devolucion)."""
    p = _nuevo(conf)
    if abrir_cajon:
        p.abrir_cajon()
    dev = venta.get("devolucion") or {
        "numero": "", "tipo": "TOTAL", "monto": venta["total"], "motivo": motivo,
        "lineas": [{"nombre": it["nombre"], "cantidad": it["cantidad"], "monto": it["total"]}
                   for it in venta["items"]]}
    _cabecera(p, conf, resolucion=False)
    titulo = "ANULACIÓN DE TIQUETE" if dev["tipo"] == "TOTAL" else "DEVOLUCIÓN PARCIAL"
    p.alinear("centro").negrita(True).tamano(1, 2).texto(titulo).tamano(1, 1)
    if dev["numero"]:
        p.texto(dev["numero"])
    p.negrita(False).alinear("izq")
    p.dos_columnas("Tiquete original:", venta["numero_tiquete"])
    p.dos_columnas("Fecha venta:", _fecha_txt(venta["fecha"]))
    p.dos_columnas("Fecha devolución:", datetime.now().strftime("%d/%m/%Y %H:%M"))
    p.dos_columnas("Reintegro por:", venta["metodo_pago"])
    p.separador()
    for l in dev["lineas"]:
        p.envolver(l["nombre"])
        p.dos_columnas(f"  {l['cantidad']} und", cfg.cop(l["monto"]))
    p.separador()
    p.negrita(True).dos_columnas("TOTAL DEVUELTO", cfg.cop(dev["monto"])).negrita(False)
    if venta.get("estado") == "DEVOLUCION PARCIAL":
        p.dos_columnas("Saldo vigente del tiquete:", cfg.cop(venta["total"] - venta["total_devuelto"]))
    if dev.get("motivo"):
        p.envolver(f"Motivo: {dev['motivo']}")
    p.avanzar(2).texto("Firma cliente: ___________________")
    p.avanzar(1).cortar()
    return p


def construir_apertura_cajon(conf: dict[str, str]) -> EscPos:
    return _nuevo(conf).abrir_cajon()


def construir_prueba(conf: dict[str, str]) -> EscPos:
    p = _nuevo(conf)
    _cabecera(p, conf)
    p.alinear("centro").negrita(True).texto("PÁGINA DE PRUEBA").negrita(False)
    p.texto(f"Papel {p.ancho_mm} mm - {p.cols} columnas")
    p.texto(f"Tabla de caracteres: {p.codepage}")
    p.texto("áéíóú ÁÉÍÓÚ ñÑ ¡¿ $ 1.250.000")
    p.texto("1234567890" * (p.cols // 10))
    p.codigo_barras("R4-PRUEBA")
    p.alinear("izq").avanzar(1).cortar()
    return p


# =========================================================================== #
# Etiquetas
# =========================================================================== #
def _tspl(img, ancho_mm: float, alto_mm: float, copias: int) -> bytes:
    """TSPL (impresoras de etiquetas tipo TSC/Xprinter/Jaltech de etiquetas)."""
    bw = img.convert("1")  # en TSPL BITMAP: bit 1 = blanco, bit 0 = punto impreso
    ancho_bytes = (bw.width + 7) // 8
    cab = (f"SIZE {ancho_mm} mm,{alto_mm} mm\r\nGAP 2 mm,0 mm\r\nDIRECTION 1\r\nCLS\r\n"
           f"BITMAP 0,0,{ancho_bytes},{bw.height},0,").encode("ascii")
    return cab + bw.tobytes() + f"\r\nPRINT 1,{int(copias)}\r\n".encode("ascii")


def _imprimir_gdi(img, impresora: str, ancho_mm: float, alto_mm: float, copias: int) -> None:
    """A través del driver de Windows (sirve con CUALQUIER impresora instalada)."""
    try:
        import win32print
        import win32ui
        from PIL import ImageWin
    except ImportError as e:
        raise PrinterError("pywin32 no está disponible (solo Windows).") from e
    nombre = impresora or win32print.GetDefaultPrinter()
    LOGPIXELSX, LOGPIXELSY, HORZRES = 88, 90, 8
    dc = win32ui.CreateDC()
    try:
        dc.CreatePrinterDC(nombre)
    except Exception as e:
        raise PrinterError(f"No se pudo abrir la impresora «{nombre}».") from e
    try:
        dpi_x, dpi_y = dc.GetDeviceCaps(LOGPIXELSX), dc.GetDeviceCaps(LOGPIXELSY)
        w = int(ancho_mm / 25.4 * dpi_x)
        h = int(alto_mm / 25.4 * dpi_y)
        pagina_mm = dc.GetDeviceCaps(HORZRES) / dpi_x * 25.4
        if ancho_mm > pagina_mm + 2:         # nunca encoger: un código aplastado no se puede leer
            raise PrinterError(
                f"El papel configurado en el driver de «{nombre}» mide {pagina_mm:.0f} mm de ancho, pero la "
                f"fila de etiquetas mide {ancho_mm:.0f} mm.\n\nAbra Windows › Impresoras › {nombre} › "
                f"Preferencias de impresión y ponga un tamaño de {ancho_mm:.0f} × {alto_mm:.0f} mm (o el ancho "
                "total del rollo).")
        dib = ImageWin.Dib(img.convert("RGB"))
        dc.StartDoc("Etiqueta ConfeccionesR4")
        for _ in range(max(1, copias)):
            dc.StartPage()
            dib.draw(dc.GetHandleOutput(), (0, 0, w, h))
            dc.EndPage()
        dc.EndDoc()
    finally:
        dc.DeleteDC()


# Nombres con los que Windows suele instalar impresoras de ETIQUETAS (SAT TT460 con driver Seagull, TSC…)
CLAVES_ETIQUETAS = ("tt460", "sat tt", "sat xt", "tsc", "ttp-", "te200", "te210", "zdesigner", "zebra", "godex",
                    "argox", "honeywell", "label", "etiqueta", "4barcode", "barcode")


def es_impresora_etiquetas(nombre: str) -> bool:
    n = (nombre or "").lower()
    return any(k in n for k in CLAVES_ETIQUETAS)


def detectar_impresora_etiquetas(lista: list[str] | None = None) -> list[str]:
    lista = listar_impresoras() if lista is None else lista
    return [p for p in lista if es_impresora_etiquetas(p)]


def etiqueta_de_prueba(conf: dict[str, str]):
    """Etiqueta del tamaño configurado con un marco en el borde: si el marco sale completo y centrado,
    el tamaño está bien; si se corta, hay que ajustar el tamaño aquí y en el driver de Windows."""
    import barcode_engine as be
    from PIL import ImageDraw
    ancho = float(conf.get("etiqueta_ancho_mm", 50) or 50)
    alto = float(conf.get("etiqueta_alto_mm", 30) or 30)
    producto = {"codigo": "R4PRUEBA", "nombre": "PRUEBA", "categoria": f"{ancho:g}x{alto:g} mm",
                "talla": "", "precio": 12345, "codigo_barras": ""}
    img = be.generar_etiqueta(producto, ancho, alto, "code128", conf.get("razon_social", "CONFECCIONESR4"),
                              conf.get("etiqueta_mostrar_tienda", "1") == "1",
                              conf.get("etiqueta_estilo", "cursiva"), conf.get("etiqueta_fuente", "Dancing Script"))
    img = img.convert("L")
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, img.width - 3, img.height - 3], outline=0, width=2)
    return img


def _num(conf: dict[str, str], clave: str, defecto: float) -> float:
    try:
        return float(str(conf.get(clave, defecto)).replace(",", ".") or defecto)
    except ValueError:
        return defecto


def columnas_etiquetas(conf: dict[str, str]) -> int:
    return max(1, min(4, int(_num(conf, "etiqueta_columnas", 1))))


def componer_fila(img, conf: dict[str, str]):
    """Rollos de 2, 3 o 4 etiquetas por fila: repite la etiqueta en cada columna, con la separación
    y el margen configurados. Devuelve (imagen de la fila completa, ancho total en mm)."""
    from PIL import Image
    cols = columnas_etiquetas(conf)
    ancho = _num(conf, "etiqueta_ancho_mm", 50)
    sep = _num(conf, "etiqueta_separacion_mm", 2)
    margen = _num(conf, "etiqueta_margen_mm", 0)
    total_mm = margen + cols * ancho + (cols - 1) * sep
    if cols == 1 and not margen:
        return img, ancho
    px_mm = img.width / ancho
    fila = Image.new("L", (round(total_mm * px_mm), img.height), 255)
    for i in range(cols):
        fila.paste(img.convert("L"), (round((margen + i * (ancho + sep)) * px_mm), 0))
    return fila, total_mm


def imprimir_etiquetas(img, copias: int, conf: dict[str, str]) -> int:
    """Imprime `copias` etiquetas. En rollos de varias columnas se imprimen filas completas
    (p. ej. 5 copias en rollo de 3 → 2 filas = 6 etiquetas). Devuelve cuántas etiquetas salieron."""
    modo = conf.get("modo_etiquetas", "windows")
    impresora = conf.get("impresora_etiquetas") or conf.get("impresora_tiquetes", "")
    alto = _num(conf, "etiqueta_alto_mm", 30)
    copias = max(1, int(copias))
    if modo != "escpos":
        cols = columnas_etiquetas(conf)
        img, ancho = componer_fila(img, conf)
        filas = -(-copias // cols)
        _imprimir_lote(img, filas, conf, modo, impresora, ancho, alto)
        return filas * cols
    _imprimir_lote(img, copias, conf, modo, impresora, _num(conf, "etiqueta_ancho_mm", 50), alto)
    return copias


def _imprimir_lote(img, copias: int, conf: dict[str, str], modo: str, impresora: str,
                   ancho: float, alto: float) -> None:
    if modo == "tspl":
        enviar(_tspl(img, ancho, alto, copias), conf, impresora, "Etiquetas")
    elif modo == "escpos":
        p = _nuevo(conf)
        for i in range(copias):
            p.alinear("centro").imagen(img)
            p.avanzar(1)
            if i < copias - 1:
                p.texto("- " * (p.cols // 2))
        p.cortar()
        enviar(p.bytes(), conf, impresora, "Etiquetas")
    else:
        if conf.get("transporte_impresion") == "archivo":
            destino = cfg.data_dir() / "impresiones"
            destino.mkdir(exist_ok=True)
            img.save(destino / f"etiqueta_{datetime.now():%Y%m%d_%H%M%S_%f}.png")
            return
        _imprimir_gdi(img, impresora, ancho, alto, copias)
