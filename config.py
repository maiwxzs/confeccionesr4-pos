"""
config.py — Constantes globales, rutas y utilidades de formato.

ConfeccionesR4 POS · Bogotá D.C.

Rutas
-----
* Recursos de solo lectura (íconos, BD plantilla) se buscan dentro del paquete
  generado por PyInstaller (``sys._MEIPASS``) o junto al código fuente.
* Los datos de trabajo (base de datos, respaldos, logs) viven en
  ``C:\\ProgramData\\ConfeccionesR4``: carpeta compartida por todos los usuarios de Windows del
  equipo, que NO se toca al actualizar ni al desinstalar el programa.
  (Versiones anteriores usaban %LOCALAPPDATA%; se migra automáticamente.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ConfeccionesR4 POS"
APP_ID = "ConfeccionesR4"
APP_VERSION = "1.0.0"            # la compilación automática (GitHub Actions) la reemplaza: 1.0.<n>
URL_ACTUALIZACIONES = ""         # idem: https://github.com/<usuario>/<repo>/releases/latest/download/latest.json
DB_FILENAME = "confeccionesr4.db"

# --------------------------------------------------------------------------- #
# Rutas
# --------------------------------------------------------------------------- #
def resource_dir() -> Path:
    """Directorio de recursos empaquetados (assets, plantillas)."""
    if getattr(sys, "frozen", False):
        # PyInstaller --onedir: _MEIPASS = carpeta _internal; --onefile: carpeta temporal
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def _cargar_build_info() -> None:
    """assets/build_info.json lo escribe build_app.py: versión y URL de actualizaciones del .exe."""
    global APP_VERSION, URL_ACTUALIZACIONES
    p = resource_path("assets", "build_info.json")
    if p.exists():
        import json
        try:
            info = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return
        APP_VERSION = info.get("version") or APP_VERSION
        URL_ACTUALIZACIONES = info.get("url_actualizaciones") or URL_ACTUALIZACIONES


_cargar_build_info()


_DATA_DIR: Path | None = None


def data_dir() -> Path:
    """Directorio de datos compartido: %PROGRAMDATA%\\ConfeccionesR4 (o R4_DATA_DIR para pruebas)."""
    global _DATA_DIR
    if _DATA_DIR is not None:
        return _DATA_DIR
    override = os.environ.get("R4_DATA_DIR")
    if override:
        root = Path(override)
    elif os.environ.get("PROGRAMDATA"):
        root = Path(os.environ["PROGRAMDATA"]) / APP_ID
    else:  # Linux / macOS (desarrollo)
        root = Path.home() / f".{APP_ID.lower()}"
    root.mkdir(parents=True, exist_ok=True)
    _migrar_desde_localappdata(root)
    _DATA_DIR = root
    return root


def _migrar_desde_localappdata(destino: Path) -> None:
    """La v1.0 inicial guardaba en %LOCALAPPDATA%: se mueve una sola vez a ProgramData."""
    viejo = os.environ.get("LOCALAPPDATA")
    if not viejo or os.environ.get("R4_DATA_DIR"):
        return
    origen = Path(viejo) / APP_ID
    if not (origen / DB_FILENAME).exists() or (destino / DB_FILENAME).exists() or origen == destino:
        return
    import shutil
    for nombre in (DB_FILENAME, DB_FILENAME + "-wal", DB_FILENAME + "-shm"):
        if (origen / nombre).exists():
            shutil.copy2(origen / nombre, destino / nombre)
    if (origen / "respaldos").exists():
        shutil.copytree(origen / "respaldos", destino / "respaldos", dirs_exist_ok=True)
    (origen / "MOVIDO_A_PROGRAMDATA.txt").write_text(
        f"Los datos se movieron a {destino} el {__import__('datetime').datetime.now():%d/%m/%Y %H:%M}.\n",
        encoding="utf-8")


def db_path() -> Path:
    # Permite forzar otra ruta (pruebas, red local) con variable de entorno
    override = os.environ.get("R4_DB_PATH")
    return Path(override) if override else data_dir() / DB_FILENAME


def backups_dir() -> Path:
    p = data_dir() / "respaldos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def exports_dir() -> Path:
    """Carpeta por defecto para reportes: Documentos\\ConfeccionesR4 Reportes."""
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    p = docs / "ConfeccionesR4 Reportes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tiquetes_dir(conf: dict | None = None) -> Path:
    """Carpeta de las copias PDF de los tiquetes (configurable en Ajustes)."""
    personalizada = (conf or {}).get("carpeta_tiquetes", "").strip()
    if personalizada:
        p = Path(personalizada)
    else:
        docs = Path.home() / "Documents"
        p = (docs if docs.exists() else Path.home()) / "ConfeccionesR4 Tiquetes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- #
# Datos fiscales por defecto (se inyectan en la tabla `configuracion`)
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG: dict[str, str] = {
    # Cabecera legal
    "razon_social": "CONFECCIONESR4",
    "nit": "700571557-9",
    "regimen": "No Responsable de IVA (Art. 512-13 / 437 E.T. Colombia)",
    "direccion": "CRA 24 #26-45 BARRIO SALITRE LC-1",
    "ciudad": "Bogotá D.C.",
    "telefono": "3028694690",
    "correo": "",                      # opcional, sale bajo el teléfono en el tiquete
    "redes": "",                       # opcional, p. ej. «IG @confeccionesr4 · WhatsApp 302...»
    "resolucion_dian": "18760000001",
    "prefijo": "SETP",
    "rango_desde": "990000000",
    "rango_hasta": "995000000",
    "consecutivo_siguiente": "990000000",
    "mensaje_pie": "¡Gracias por su compra!\nCambios dentro de los 8 días con tiquete y etiqueta.",
    # Impresión
    "impresora_tiquetes": "",          # Nombre de la impresora Windows (vacío = predeterminada)
    "impresora_etiquetas": "",         # Puede ser la misma térmica u otra
    "transporte_impresion": "win32",   # win32 | usb | archivo
    "usb_vendor_id": "0x0483",         # Solo si transporte = usb (python-escpos + WinUSB)
    "usb_product_id": "0x5743",
    "ancho_papel_mm": "80",            # 80 | 58
    "codepage": "cp850",               # cp850 (ESC t 2) recomendado para Jaltech
    "guardar_pdf_tiquetes": "1",       # copia PDF de cada tiquete y comprobante
    "carpeta_tiquetes": "",            # vacío = Documentos\\ConfeccionesR4 Tiquetes
    "abrir_cajon_efectivo": "0",       # 1 solo si hay cajón monedero conectado a la impresora (RJ11)
    "imprimir_codigo_barras_tiquete": "1",
    "mostrar_logo_tiquete": "1",
    "logo_tiquete": "",                # vacío = águila de ConfeccionesR4 incluida en el programa
    "tamano_logo_tiquete": "50",       # % del ancho del papel
    "etiqueta_ancho_mm": "50",
    "etiqueta_alto_mm": "30",
    "etiqueta_columnas": "1",         # etiquetas por fila del rollo (1, 2, 3 o 4)
    "etiqueta_separacion_mm": "2",    # espacio entre una etiqueta y la de al lado
    "etiqueta_margen_mm": "0",        # corrimiento desde el borde izquierdo del rollo
    "etiqueta_simbologia": "auto",     # auto (EAN-13 si es válido, si no Code128) | code128 | ean13
    "modo_etiquetas": "windows",       # windows (driver GDI, cualquier impresora) | escpos | tspl
    "etiqueta_mostrar_tienda": "1",
    "etiqueta_estilo": "cursiva",      # cursiva | normal (solo el nombre de la tienda)
    "etiqueta_fuente": "Dancing Script",  # Dancing Script | Great Vibes | Allura
    # Operación
    "permitir_stock_negativo": "0",
    "stock_critico": "3",
    "arqueo_ciego": "1",               # Oculta el efectivo esperado durante el turno
    # Actualizaciones remotas (opcional; el POS funciona 100 % sin internet)
    "url_actualizaciones": "",         # vacío = usar la URL integrada al compilar (URL_ACTUALIZACIONES)
    "buscar_actualizaciones": "1",
    "actualizar_automaticamente": "1", # instala sin preguntar al abrir / cuando no hay venta en curso
    "cajero_actual": "Cajero",
}

CATEGORIAS_BASE = ["Jeans Hombre", "Jeans Mujer", "Blusas", "Accesorios"]

# Resolución PÚBLICA del set de pruebas de la DIAN (habilitación de facturación electrónica).
# No es una autorización real: si sigue configurada, el tiquete se marca como comprobante interno.
RESOLUCION_PRUEBA_DIAN = {"resolucion_dian": "18760000001", "prefijo": "SETP",
                          "rango_desde": "990000000", "rango_hasta": "995000000"}


def es_resolucion_de_prueba(conf: dict) -> bool:
    """True si NO hay una resolución DIAN real: la pública de pruebas (SETP) o ninguna.
    En ese caso el tiquete se imprime como «Comprobante interno de venta»."""
    resol = str(conf.get("resolucion_dian", "")).strip()
    return (not resol or resol == RESOLUCION_PRUEBA_DIAN["resolucion_dian"]
            or str(conf.get("prefijo", "")).strip().upper() == RESOLUCION_PRUEBA_DIAN["prefijo"])
METODOS_PAGO = ["Efectivo", "Nequi", "Daviplata", "Datáfono"]

# Denominaciones COP en circulación para el arqueo
DENOMINACIONES_COP = [100_000, 50_000, 20_000, 10_000, 5_000, 2_000, 1_000, 500, 200, 100, 50]


# --------------------------------------------------------------------------- #
# Formato monetario colombiano
# --------------------------------------------------------------------------- #
def cop(valor: float | int | None, simbolo: bool = True) -> str:
    """Formatea un valor en pesos colombianos: 1250000 -> '$ 1.250.000'."""
    if valor is None:
        valor = 0
    negativo = valor < 0
    txt = f"{abs(round(valor)):,.0f}".replace(",", ".")
    txt = f"$ {txt}" if simbolo else txt
    return f"-{txt}" if negativo else txt


def parse_cop(texto: str | float | int | None) -> int:
    """Convierte '$ 1.250.000', '1250000', '80,000' o 80000.0 en entero de pesos."""
    if texto is None:
        return 0
    if isinstance(texto, (int, float)):
        try:
            return int(round(float(texto)))
        except (TypeError, ValueError):
            return 0
    s = str(texto).strip().replace("$", "").replace("COP", "").replace(" ", "")
    if not s:
        return 0
    neg = s.startswith("-")
    s = s.lstrip("-")
    # Pesos colombianos no usan decimales en el día a día: separadores . y , son de miles,
    # salvo que haya exactamente 1-2 dígitos al final después de una coma (ej. "80000,50").
    if "," in s and len(s.rsplit(",", 1)[1]) in (1, 2) and "." not in s.rsplit(",", 1)[1]:
        entero, dec = s.rsplit(",", 1)
        s = entero.replace(".", "").replace(",", "") + "." + dec
    elif "." in s and len(s.rsplit(".", 1)[1]) in (1, 2) and s.count(".") == 1 and "," not in s:
        pass  # "80000.5" -> decimal
    else:
        s = s.replace(".", "").replace(",", "")
    try:
        v = int(round(float(s)))
    except ValueError:
        return 0
    return -v if neg else v
