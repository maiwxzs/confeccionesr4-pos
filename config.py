from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ConfeccionesR4 POS"
APP_ID = "ConfeccionesR4"
APP_VERSION = "1.1.0"
URL_ACTUALIZACIONES = ""
DB_FILENAME = "confeccionesr4.db"

# Rutas
def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def _cargar_build_info() -> None:
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
    global _DATA_DIR
    if _DATA_DIR is not None:
        return _DATA_DIR
    override = os.environ.get("R4_DATA_DIR")
    if override:
        root = Path(override)
    elif os.environ.get("PROGRAMDATA"):
        root = Path(os.environ["PROGRAMDATA"]) / APP_ID
    else:
        root = Path.home() / f".{APP_ID.lower()}"
    root.mkdir(parents=True, exist_ok=True)
    _migrar_desde_localappdata(root)
    _DATA_DIR = root
    return root


def _migrar_desde_localappdata(destino: Path) -> None:
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
    override = os.environ.get("R4_DB_PATH")
    return Path(override) if override else data_dir() / DB_FILENAME


def backups_dir() -> Path:
    p = data_dir() / "respaldos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def exports_dir() -> Path:
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    p = docs / "ConfeccionesR4 Reportes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tiquetes_dir(conf: dict | None = None) -> Path:
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


DEFAULT_CONFIG: dict[str, str] = {
    # Cabecera legal
    "razon_social": "CONFECCIONESR4",
    "nit": "",
    "regimen": "No Responsable de IVA (Art. 512-13 / 437 E.T. Colombia)",
    "direccion": "",
    "ciudad": "",
    "telefono": "",
    "correo": "",
    "redes": "",
    "resolucion_dian": "18760000001",
    "prefijo": "SETP",
    "rango_desde": "990000000",
    "rango_hasta": "995000000",
    "consecutivo_siguiente": "990000000",
    "mensaje_pie": "¡Gracias por su compra!\nCambios dentro de los 8 días con tiquete y etiqueta.",
    # Impresión
    "impresora_tiquetes": "",
    "impresora_etiquetas": "",
    "transporte_impresion": "win32",
    "usb_vendor_id": "0x0483",
    "usb_product_id": "0x5743",
    "ancho_papel_mm": "80",
    "codepage": "cp850",
    "guardar_pdf_tiquetes": "1",
    "carpeta_tiquetes": "",
    "abrir_cajon_efectivo": "0",
    "imprimir_codigo_barras_tiquete": "1",
    "mostrar_logo_tiquete": "1",
    "logo_tiquete": "",
    "tamano_logo_tiquete": "50",
    "etiqueta_ancho_mm": "50",
    "etiqueta_alto_mm": "30",
    "etiqueta_columnas": "1",
    "etiqueta_separacion_mm": "2",
    "etiqueta_margen_mm": "0",
    "etiqueta_simbologia": "auto",
    "modo_etiquetas": "windows",
    "etiqueta_mostrar_tienda": "1",
    "etiqueta_estilo": "cursiva",
    "etiqueta_fuente": "Dancing Script",
    # Operación
    "permitir_stock_negativo": "0",
    "stock_critico": "3",
    "arqueo_ciego": "1",
    "url_actualizaciones": "",
    "buscar_actualizaciones": "1",
    "actualizar_automaticamente": "1",
    "cajero_actual": "Cajero",
    # v1.1
    "imprimir_tiquete_defecto": "1",
    "tiquete_nombre_cursiva": "1",
    "separado_dias_limite": "30",
    "carpeta_respaldo_nube": "",
    "ultimo_respaldo_nube": "",
    "imprimir_cierre": "0",
    "whatsapp_dueno": "",
    "telegram_token": "",
    "telegram_chat": "",
}

CATEGORIAS_BASE = ["Jeans Hombre", "Jeans Mujer", "Blusas", "Accesorios"]

RESOLUCION_PRUEBA_DIAN = {"resolucion_dian": "18760000001", "prefijo": "SETP",
                          "rango_desde": "990000000", "rango_hasta": "995000000"}


def es_resolucion_de_prueba(conf: dict) -> bool:
    resol = str(conf.get("resolucion_dian", "")).strip()
    return (not resol or resol == RESOLUCION_PRUEBA_DIAN["resolucion_dian"]
            or str(conf.get("prefijo", "")).strip().upper() == RESOLUCION_PRUEBA_DIAN["prefijo"])
METODOS_PAGO = ["Efectivo", "Nequi", "Bre-B", "Datáfono"]
METODOS_HISTORICOS = METODOS_PAGO + ["Mixto", "Cambio", "Separado", "Daviplata"]
METODO_CREDITO = "Crédito cambio"
COLORES_METODO = {"Efectivo": "#22C55E", "Nequi": "#A855F7", "Bre-B": "#F97316", "Datáfono": "#3B82F6",
                  "Daviplata": "#EF4444", "Separado": "#EAB308", "Mixto": "#14B8A6", "Cambio": "#64748B"}

DENOMINACIONES_COP = [100_000, 50_000, 20_000, 10_000, 5_000, 2_000, 1_000, 500, 200, 100, 50]


# Formato monetario colombiano
def cop(valor: float | int | None, simbolo: bool = True) -> str:
    if valor is None:
        valor = 0
    negativo = valor < 0
    txt = f"{abs(round(valor)):,.0f}".replace(",", ".")
    txt = f"$ {txt}" if simbolo else txt
    return f"-{txt}" if negativo else txt


def parse_cop(texto: str | float | int | None) -> int:
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
    if "," in s and len(s.rsplit(",", 1)[1]) in (1, 2) and "." not in s.rsplit(",", 1)[1]:
        entero, dec = s.rsplit(",", 1)
        s = entero.replace(".", "").replace(",", "") + "." + dec
    elif "." in s and len(s.rsplit(".", 1)[1]) in (1, 2) and s.count(".") == 1 and "," not in s:
        pass
    else:
        s = s.replace(".", "").replace(",", "")
    try:
        v = int(round(float(s)))
    except ValueError:
        return 0
    return -v if neg else v
