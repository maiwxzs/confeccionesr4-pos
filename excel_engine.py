"""
excel_engine.py — Importación masiva de inventario y exportación de reportes ejecutivos.

Importación
-----------
* Lee .xlsx/.xlsm (openpyxl, modo solo-lectura = rápido), .xls/.ods (pandas) y .csv.
* Detecta automáticamente la fila de encabezados (aunque el archivo tenga títulos arriba).
* Mapeo inteligente de columnas por sinónimos y puntuación ("Costo Compra", "Precio Venta",
  "Cód. Barras", "Existencias", "Descripción"...).
* Limpieza: espacios, mayúsculas/minúsculas, códigos numéricos que Excel volvió decimales
  o notación científica, montos con "$" y puntos de miles, categorías inferidas por nombre.

Exportación
-----------
* Encabezado con Razón Social, NIT y título del reporte.
* Títulos de tabla azul oscuro con texto blanco en negrita y centrado.
* Filas cebra, formato monetario `$ #,##0` NUMÉRICO (las sumas de Excel funcionan).
* Auto-ajuste real de ancho de columnas (nunca ### ni textos truncados).
* Fila de TOTALES en negrita con borde superior simple y borde inferior doble.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config as cfg

# =========================================================================== #
# Utilidades de texto
# =========================================================================== #
def normalizar(texto: Any) -> str:
    """'Código de Barras ' -> 'codigo de barras'."""
    s = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


_PALABRAS_MENORES = {"de", "del", "la", "las", "el", "los", "y", "con", "sin", "para", "en", "a", "x"}
_TALLAS = {"XS", "S", "M", "L", "XL", "XXL", "XXXL"}


def capitalizar_nombre(texto: str) -> str:
    """'JEAN  SKINNY dama t32 ' -> 'Jean Skinny Dama T32'. Respeta tokens con dígitos (T32, 2XL)."""
    palabras = re.sub(r"\s+", " ", str(texto or "")).strip().split(" ")
    out = []
    for i, p in enumerate(palabras):
        if not p:
            continue
        if any(ch.isdigit() for ch in p) or p.upper() in _TALLAS:
            out.append(p.upper())
        elif i > 0 and p.lower() in _PALABRAS_MENORES:
            out.append(p.lower())
        else:  # 'Y/OUT' -> 'Y/Out', 'TIRO-ALTO' -> 'Tiro-Alto'
            out.append(re.sub(r"[^/\-]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), p))
    return " ".join(out)


def limpiar_codigo(valor: Any) -> str:
    """7701234567890.0 / 7.70123E+12 / ' 00123 ' -> texto de código limpio."""
    if valor is None:
        return ""
    if isinstance(valor, float):
        if valor != valor:  # NaN
            return ""
        if valor.is_integer():
            return str(int(valor))
    s = str(valor).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    elif re.fullmatch(r"\d+(\.\d+)?[eE]\+\d+", s):
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    return re.sub(r"\s+", "", s).upper()


def a_entero(valor: Any) -> int:
    if valor is None:
        return 0
    if isinstance(valor, float) and valor != valor:
        return 0
    return cfg.parse_cop(valor)


# =========================================================================== #
# Mapeo inteligente de columnas
# =========================================================================== #
SINONIMOS: dict[str, list[str]] = {
    "codigo": ["codigo", "codigo interno", "cod", "codigo de barras", "cod barras", "codigo barras", "barcode",
               "ean", "ean13", "sku", "plu", "codigo producto", "codigo del producto"],
    "referencia": ["referencia", "ref", "modelo", "item", "ref fabrica", "referencia interna"],
    "nombre": ["producto", "nombre", "descripcion", "articulo", "prenda", "nombre producto",
               "nombre del producto", "detalle", "descripcion producto"],
    "categoria": ["categoria", "linea", "familia", "seccion", "grupo", "departamento"],
    "costo": ["costo compra", "costo", "precio compra", "valor compra", "costo unitario", "precio unitario compra",
              "precio costo", "costo unit", "vr compra", "p compra", "costo unitario compra"],
    "precio": ["precio venta", "precio", "pvp", "valor venta", "precio publico", "precio de venta",
               "vr venta", "p venta", "precio unitario venta", "precio unitario", "valor"],
    "stock": ["stock", "cantidad", "existencias", "existencia", "inventario", "unidades", "cant",
              "saldo", "disponible", "uds", "stock actual"],
    "stock_minimo": ["stock minimo", "stock min", "minimo", "existencia minima", "inventario minimo"],
    "marca": ["marca", "fabricante", "coleccion"],
    "color": ["color", "colores", "tono"],
    "talla": ["talla", "tallas", "size", "talla prenda", "medida"],
}
CAMPOS_OBLIGATORIOS = ("nombre",)
# Columnas de códigos técnicos (unidad, moneda, impuesto…): solo se aceptan por coincidencia exacta
_PALABRAS_TECNICAS = ("tipo", "impuesto", "moneda", "unidad", "iva", "tarifa")


def puntuar_columna(encabezado: str) -> tuple[str | None, int]:
    h = normalizar(encabezado)
    if not h:
        return None, 0
    tecnica = any(re.search(rf"\b{w}\b", h) for w in _PALABRAS_TECNICAS)
    mejor, puntos = None, 0
    for campo, sins in SINONIMOS.items():
        for s in sins:
            if h == s:
                sc = 100 + len(s)
            elif not tecnica and re.search(rf"\b{re.escape(s)}\b", h):
                sc = len(s)
            else:
                continue
            # Reglas de desambiguación compra/venta y stock/stock mínimo
            if campo == "precio" and re.search(r"\b(compra|costo)\b", h):
                continue
            if campo == "costo" and re.search(r"\bventa\b", h):
                continue
            if campo == "stock" and re.search(r"\b(minimo|min|maximo)\b", h):
                continue
            if sc > puntos:
                mejor, puntos = campo, sc
    return mejor, puntos


def mapear_columnas(encabezados: Sequence[Any]) -> dict[str, int]:
    """Devuelve {campo: índice_columna}. Cada columna y cada campo se asignan una sola vez
    (mayor puntaje primero; en empate gana la columna más a la izquierda)."""
    candidatos = []
    for idx, enc in enumerate(encabezados):
        campo, sc = puntuar_columna(enc)
        if campo:
            candidatos.append((-sc, idx, campo))
    candidatos.sort()
    mapeo: dict[str, int] = {}
    usadas: set[int] = set()
    for _sc, idx, campo in candidatos:
        if campo not in mapeo and idx not in usadas:
            mapeo[campo] = idx
            usadas.add(idx)
    return mapeo


# =========================================================================== #
# Inferencia de categoría y nombre de la variante
# =========================================================================== #
def inferir_categoria(nombre: str, categoria_archivo: str = "") -> str:
    base = {normalizar(c): c for c in cfg.CATEGORIAS_BASE}
    ca = normalizar(categoria_archivo)
    if ca:
        if ca in base:
            return base[ca]
        return capitalizar_nombre(categoria_archivo)
    n = normalizar(nombre)
    es_jean = bool(re.search(r"\b(jean|jeans|denim|vaquero|pantalon)\b", n))
    if es_jean and re.search(r"\b(hombre|caballero|masculino|men|man|hb)\b", n):
        return "Jeans Hombre"
    if es_jean and re.search(r"\b(mujer|dama|femenino|women|lady|dm)\b", n):
        return "Jeans Mujer"
    if re.search(r"\b(blusa|blusas|top|camisa dama|crop)\b", n):
        return "Blusas"
    if re.search(r"\b(correa|cinturon|gorra|bolso|billetera|media|medias|accesorio|aretes|collar|bufanda)\b", n):
        return "Accesorios"
    if es_jean:
        return "Jeans Mujer" if "skinny" in n or "tiro alto" in n else "Jeans Hombre"
    return "General"


def texto_talla(talla: Any) -> str:
    """14 -> 'T14' · '2xl' -> '2XL' · 'UNICA' -> 'Única' · None -> ''."""
    t = limpiar_codigo(talla)
    if not t or t in {"NONE", "NAN", "N/A", "NA", "-"}:
        return ""
    if t.isdigit():
        return f"T{t}"
    if normalizar(t) in {"unica", "unico", "u", "talla unica"}:
        return "Única"
    return t


def componer_nombre(nombre: str, color: str = "", talla: Any = None) -> str:
    """'STRAIGLETH LEG' + 'AZUL CLARO' + 14 -> 'Straigleth Leg · Azul Claro · T14'
    (así cada variante se distingue en el POS, el tiquete y la etiqueta)."""
    base = capitalizar_nombre(nombre)
    partes = [base]
    nb = normalizar(base)
    if color and normalizar(color) and normalizar(color) not in nb:
        partes.append(capitalizar_nombre(color))
    t = texto_talla(talla)
    if t and normalizar(t) not in nb.split():
        partes.append(t)
    return " · ".join(partes)


# =========================================================================== #
# Lectura de archivos
# =========================================================================== #
def _filas_no_vacias(filas: list[list[Any]]) -> int:
    return sum(1 for r in filas if any(v not in (None, "") for v in r))


def _leer_libro(path: Path) -> dict[str, list[list[Any]]]:
    """{nombre_hoja: filas}. Se conservan filas vacías para reportar el número real de fila."""
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
        finally:
            wb.close()
    if ext == ".csv":
        raw = path.read_bytes()
        texto = raw.decode("latin-1")
        for enc in ("utf-8-sig", "cp1252"):
            try:
                texto = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=";,\t|")
        return {path.stem: [list(r) for r in csv.reader(texto.splitlines(), dialecto)]}
    if ext in (".xls", ".ods"):
        import pandas as pd  # import diferido: no retrasa el arranque de la app
        hojas = pd.read_excel(path, header=None, dtype=object, sheet_name=None)
        return {n: [list(r) for r in df.where(df.notna(), None).itertuples(index=False)] for n, df in hojas.items()}
    raise ValueError(f"Formato no soportado: {ext}")


def _detectar_fila_encabezado(filas: list[list[Any]], max_busqueda: int = 20) -> tuple[int, dict[str, int]]:
    mejor_idx, mejor_mapeo, mejor_puntos = 0, {}, -1
    for i, fila in enumerate(filas[:max_busqueda]):
        mapeo = mapear_columnas(fila)
        puntos = len(mapeo) * 10 + (5 if "nombre" in mapeo else 0)
        if puntos > mejor_puntos:
            mejor_idx, mejor_mapeo, mejor_puntos = i, mapeo, puntos
    return mejor_idx, mejor_mapeo


def elegir_hoja(libro: dict[str, list[list[Any]]]) -> str:
    """Hoja con encabezados reconocibles y más filas; prefiere una llamada 'inventario'."""
    def puntaje(item):
        nombre, filas = item
        _, mapeo = _detectar_fila_encabezado(filas)
        valida = "nombre" in mapeo
        return (valida, "inventario" in normalizar(nombre), _filas_no_vacias(filas))
    return max(libro.items(), key=puntaje)[0]


@dataclass
class ResultadoImportacion:
    filas: list[dict[str, Any]] = field(default_factory=list)
    mapeo: dict[str, str] = field(default_factory=dict)       # campo -> encabezado original
    omitidas: list[tuple[int, str]] = field(default_factory=list)  # (fila Excel, motivo)
    advertencias: list[str] = field(default_factory=list)
    fila_encabezado: int = 1
    hoja: str = ""
    hojas: list[str] = field(default_factory=list)
    encabezados: list[str] = field(default_factory=list)      # columnas originales (para re-exportar)

    @property
    def resumen(self) -> str:
        cols = ", ".join(f"{k} ← «{v}»" for k, v in self.mapeo.items())
        txt = [f"Hoja: «{self.hoja}»" + (f"  (el libro tiene {len(self.hojas)} hojas)" if len(self.hojas) > 1 else ""),
               f"Filas válidas: {len(self.filas)}", f"Filas omitidas: {len(self.omitidas)}",
               f"Encabezados en la fila {self.fila_encabezado}", f"Columnas detectadas: {cols}"]
        adv = self.advertencias[:25]
        if len(self.advertencias) > 25:
            adv.append(f"… y {len(self.advertencias) - 25} advertencias más")
        return "\n".join(txt + adv)


def _valor_origen(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def importar_inventario(path: str | Path, hoja: str | None = None) -> ResultadoImportacion:
    """Lee y limpia un archivo de inventario (p. ej. 'Inventario General 01.xlsx')."""
    path = Path(path)
    libro = _leer_libro(path)
    res = ResultadoImportacion()
    res.hojas = list(libro)
    if not libro:
        res.advertencias.append("El archivo está vacío.")
        return res
    res.hoja = hoja if hoja in libro else elegir_hoja(libro)
    filas = libro[res.hoja]
    if not _filas_no_vacias(filas):
        res.advertencias.append("La hoja está vacía.")
        return res
    idx_enc, mapeo = _detectar_fila_encabezado(filas)
    res.fila_encabezado = idx_enc + 1
    encabezados = filas[idx_enc]
    res.encabezados = [str(e).strip() if e is not None else "" for e in encabezados]
    while res.encabezados and not res.encabezados[-1]:
        res.encabezados.pop()
    res.mapeo = {k: str(encabezados[v]).strip() for k, v in mapeo.items()}
    if "nombre" not in mapeo:
        raise ValueError("No se encontró la columna de nombre del producto "
                         "(Producto / Nombre / Descripción).")
    for falt in ("precio", "stock", "costo"):
        if falt not in mapeo:
            res.advertencias.append(f"⚠ No se encontró columna de «{falt}»: se usará 0.")

    def val(fila: list[Any], campo: str) -> Any:
        i = mapeo.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    vistos: dict[str, int] = {}
    for n, fila in enumerate(filas[idx_enc + 1:], start=idx_enc + 2):
        nombre_raw = val(fila, "nombre")
        if nombre_raw is None or not str(nombre_raw).strip():
            if any(v not in (None, "") for v in fila):
                res.omitidas.append((n, "Sin nombre de producto"))
            continue
        if normalizar(nombre_raw) in {"total", "totales", "total general", "subtotal"}:
            continue  # fila de totales del archivo original
        costo = a_entero(val(fila, "costo"))
        precio = a_entero(val(fila, "precio"))
        stock = a_entero(val(fila, "stock"))
        if precio < 0 or costo < 0:
            res.omitidas.append((n, "Precio o costo negativo"))
            continue
        if stock < 0:
            res.advertencias.append(f"Fila {n}: stock negativo ({stock}) ajustado a 0.")
            stock = 0
        if precio and costo > precio:
            res.advertencias.append(f"Fila {n}: el costo ({cfg.cop(costo)}) supera el precio ({cfg.cop(precio)}).")
        if not precio:
            res.advertencias.append(f"Fila {n}: «{str(nombre_raw).strip()}» sin precio de venta.")
        color = str(val(fila, "color") or "").strip()
        talla_raw = val(fila, "talla")
        talla = limpiar_codigo(talla_raw)
        if talla in {"NONE", "NAN"}:
            talla = ""
        nombre = componer_nombre(str(nombre_raw), color, talla_raw)
        registro = {
            "codigo": limpiar_codigo(val(fila, "codigo")),
            "referencia": limpiar_codigo(val(fila, "referencia")),
            "nombre": nombre,
            "categoria": inferir_categoria(nombre, str(val(fila, "categoria") or "")),
            "costo": costo,
            "precio": precio,
            "stock": stock,
            "stock_minimo": a_entero(val(fila, "stock_minimo")),
            "marca": str(val(fila, "marca") or "").strip(),
            "color": color,
            "talla": talla,
            "origen": {res.encabezados[i]: _valor_origen(fila[i]) for i in range(min(len(fila), len(res.encabezados)))
                       if res.encabezados[i]},
        }
        if registro["codigo"]:
            clave = registro["codigo"]
        elif registro["referencia"]:
            clave = f"REF:{registro['referencia']}"
        else:
            clave = ""
        if clave and clave in vistos:
            res.advertencias.append(f"Fila {n}: código {clave} repetido; se usa la última aparición.")
            res.filas[vistos[clave]] = registro
            continue
        if clave:
            vistos[clave] = len(res.filas)
        res.filas.append(registro)
    return res


# =========================================================================== #
# Exportación con formato ejecutivo
# =========================================================================== #
FMT_MONEDA = '"$ "#,##0;[Red]-"$ "#,##0'
FMT_ENTERO = '#,##0'
FMT_FECHA = 'dd/mm/yyyy hh:mm'
FMT_PORC = '0.0%'

AZUL_OSCURO = "1F3864"
GRIS_INSTITUCIONAL = "44546A"
CEBRA = "EEF2F8"
BORDE_SUAVE = "D0D7E2"


@dataclass
class Columna:
    titulo: str
    clave: str
    tipo: str = "texto"          # texto | moneda | entero | fecha | porcentaje
    total: bool = False          # ¿sumar en la fila de TOTALES?
    ancho_max: int = 60


def _texto_visible(valor: Any, tipo: str) -> str:
    """Cómo se verá el valor en Excel (para medir el ancho real)."""
    if valor is None:
        return ""
    if tipo == "moneda":
        return cfg.cop(valor)
    if tipo == "entero":
        return f"{int(valor):,}".replace(",", ".")
    if tipo == "porcentaje":
        return f"{float(valor) * 100:.1f}%"
    if tipo == "fecha":
        return "00/00/0000 00:00"
    return str(valor)


def _a_fecha(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v
    try:
        return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return v


class ReporteExcel:
    """Libro de Excel con una o varias hojas de formato ejecutivo homogéneo."""

    def __init__(self, empresa: dict[str, str]):
        self.empresa = empresa
        self.wb = Workbook()
        self._primera = True

    def _nueva_hoja(self, nombre: str):
        if self._primera:
            ws = self.wb.active
            ws.title = nombre[:31]
            self._primera = False
        else:
            ws = self.wb.create_sheet(nombre[:31])
        ws.sheet_view.showGridLines = False
        return ws

    def agregar_hoja(
        self, nombre_hoja: str, titulo: str, columnas: Sequence[Columna], filas: Iterable[Any],
        subtitulo: str = "", totales: bool = True, formato_fila: Callable[[dict], dict] | None = None,
    ):
        ws = self._nueva_hoja(nombre_hoja)
        ncol = len(columnas)
        ultima = get_column_letter(max(ncol, 1))
        filas = [dict(f) for f in filas]

        # --- Encabezado corporativo -------------------------------------------------
        e = self.empresa
        cab = [
            (e.get("razon_social", ""), Font(name="Calibri", size=16, bold=True, color=AZUL_OSCURO)),
            (f"NIT {e.get('nit','')}  ·  {e.get('regimen','')}", Font(name="Calibri", size=10, color=GRIS_INSTITUCIONAL)),
            (f"{e.get('direccion','')}  ·  Tel. {e.get('telefono','')}  ·  {e.get('ciudad','')}",
             Font(name="Calibri", size=10, color=GRIS_INSTITUCIONAL)),
            (titulo.upper(), Font(name="Calibri", size=13, bold=True, color="000000")),
            (subtitulo or f"Generado: {datetime.now():%d/%m/%Y %H:%M}",
             Font(name="Calibri", size=10, italic=True, color=GRIS_INSTITUCIONAL)),
        ]
        for i, (txt, font) in enumerate(cab, start=1):
            ws.cell(row=i, column=1, value=txt).font = font
            if ncol > 1:
                ws.merge_cells(f"A{i}:{ultima}{i}")
            ws.cell(row=i, column=1).alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[1].height = 24
        fila_tit = len(cab) + 2  # una fila en blanco

        # --- Títulos de tabla ------------------------------------------------------
        borde_suave = Side(style="thin", color=BORDE_SUAVE)
        relleno_tit = PatternFill("solid", fgColor=AZUL_OSCURO)
        for j, col in enumerate(columnas, start=1):
            c = ws.cell(row=fila_tit, column=j, value=col.titulo)
            c.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            c.fill = relleno_tit
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = Border(left=borde_suave, right=borde_suave, top=borde_suave, bottom=borde_suave)
        ws.row_dimensions[fila_tit].height = 30

        # --- Datos -----------------------------------------------------------------
        anchos = [len(col.titulo) * 1.15 + 2 for col in columnas]
        relleno_cebra = PatternFill("solid", fgColor=CEBRA)
        borde_celda = Border(left=borde_suave, right=borde_suave, bottom=borde_suave)
        sumas = [0] * ncol
        r = fila_tit
        for i, fila in enumerate(filas):
            r = fila_tit + 1 + i
            tipos_fila = formato_fila(fila) if formato_fila else {}
            for j, col in enumerate(columnas, start=1):
                tipo = tipos_fila.get(col.clave, col.tipo)
                v = fila.get(col.clave)
                if tipo == "fecha":
                    v = _a_fecha(v)
                c = ws.cell(row=r, column=j, value=v)
                c.border = borde_celda
                if i % 2 == 1:
                    c.fill = relleno_cebra
                if tipo == "moneda":
                    c.number_format = FMT_MONEDA
                    c.alignment = Alignment(horizontal="right")
                elif tipo == "entero":
                    c.number_format = FMT_ENTERO
                    c.alignment = Alignment(horizontal="center")
                elif tipo == "porcentaje":
                    c.number_format = FMT_PORC
                    c.alignment = Alignment(horizontal="center")
                elif tipo == "fecha":
                    c.number_format = FMT_FECHA
                    c.alignment = Alignment(horizontal="center")
                else:
                    c.alignment = Alignment(horizontal="left", indent=1)
                if col.total and isinstance(v, (int, float)):
                    sumas[j - 1] += v
                anchos[j - 1] = max(anchos[j - 1], len(_texto_visible(v, tipo)) + 2)

        # --- Fila de TOTALES -------------------------------------------------------
        if totales and filas and any(col.total for col in columnas):
            rt = r + 1
            ini, fin = fila_tit + 1, r
            borde_total = Border(top=Side(style="thin", color="000000"),
                                 bottom=Side(style="double", color="000000"))
            for j, col in enumerate(columnas, start=1):
                c = ws.cell(row=rt, column=j)
                c.font = Font(name="Calibri", size=11, bold=True)
                c.border = borde_total
                c.fill = PatternFill("solid", fgColor="DDE3EC")
                if j == 1:
                    c.value = "TOTALES"
                    c.alignment = Alignment(horizontal="left")
                elif col.total:
                    letra = get_column_letter(j)
                    c.value = f"=SUBTOTAL(9,{letra}{ini}:{letra}{fin})"  # respeta filtros
                    c.number_format = FMT_MONEDA if col.tipo == "moneda" else FMT_ENTERO
                    c.alignment = Alignment(horizontal="right" if col.tipo == "moneda" else "center")
                    anchos[j - 1] = max(anchos[j - 1], (len(_texto_visible(sumas[j - 1], col.tipo)) + 2) * 1.1)
            ws.row_dimensions[rt].height = 20

        # --- Auto-ajuste de ancho (con margen de cortesía) ---------------------------
        for j, col in enumerate(columnas, start=1):
            ws.column_dimensions[get_column_letter(j)].width = min(max(anchos[j - 1] + 3, 10), col.ancho_max)
            if anchos[j - 1] + 2 > col.ancho_max:
                for rr in range(fila_tit + 1, r + 1):
                    ws.cell(row=rr, column=j).alignment = Alignment(wrap_text=True, vertical="top")
        # Si el título corporativo es más ancho que la tabla, ampliar la 1.ª columna
        ancho_tabla = sum(ws.column_dimensions[get_column_letter(j)].width for j in range(1, ncol + 1))
        largo_cab = max(len(str(t)) for t, _ in cab) * 1.05
        if ancho_tabla < largo_cab and ncol:
            letra = get_column_letter(1)
            ws.column_dimensions[letra].width += (largo_cab - ancho_tabla)

        # --- Usabilidad ------------------------------------------------------------
        ws.freeze_panes = ws.cell(row=fila_tit + 1, column=1)
        if filas:
            ws.auto_filter.ref = f"A{fila_tit}:{ultima}{r}"
        ws.page_setup.orientation = "landscape" if ncol > 6 else "portrait"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = f"{fila_tit}:{fila_tit}"
        ws.oddFooter.center.text = "Página &P de &N"
        ws.oddFooter.right.text = e.get("razon_social", "")
        return ws

    def guardar(self, ruta: str | Path) -> Path:
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.wb.properties.creator = self.empresa.get("razon_social", "ConfeccionesR4")
        self.wb.properties.title = self.wb.active.title
        self.wb.save(ruta)
        return ruta


# =========================================================================== #
# Reportes listos para usar
# =========================================================================== #
def _empresa(db) -> dict[str, str]:
    return db.get_all_config()


def exportar_inventario(db, ruta: str | Path) -> Path:
    rep = ReporteExcel(_empresa(db))
    critico = db.get_config_int("stock_critico", 3)
    filas = [dict(r) for r in db.inventario_valorizado()]
    for f in filas:
        f["estado"] = "AGOTADO" if f["stock"] <= 0 else ("CRÍTICO" if f["stock"] <= critico else "OK")
    con_ref = any(f["referencia"] for f in filas)
    rep.agregar_hoja(
        "Inventario", "Inventario Valorizado",
        [Columna("Código", "codigo")] + ([Columna("Referencia", "referencia")] if con_ref else []) + [
         Columna("Producto", "nombre"),
         Columna("Categoría", "categoria"), Columna("Marca", "marca"), Columna("Color", "color"),
         Columna("Talla", "talla"), Columna("Stock", "stock", "entero", True),
         Columna("Costo Unit.", "costo", "moneda"), Columna("Precio Venta", "precio", "moneda"),
         Columna("Valor al Costo", "valor_costo", "moneda", True),
         Columna("Valor a Precio Venta", "valor_venta", "moneda", True),
         Columna("Utilidad Potencial", "utilidad_potencial", "moneda", True),
         Columna("Estado", "estado")],
        filas, subtitulo=f"Corte: {datetime.now():%d/%m/%Y %H:%M}  ·  {len(filas)} referencias activas",
    )
    return rep.guardar(ruta)


def exportar_ventas(db, desde: str, hasta: str, ruta: str | Path) -> Path:
    rep = ReporteExcel(_empresa(db))
    periodo = f"Periodo: {desde[:10]} a {hasta[:10]}  ·  Generado {datetime.now():%d/%m/%Y %H:%M}"
    k = db.kpis(desde, hasta)
    resumen = [
        {"concepto": "Número de ventas", "valor": k["num_ventas"], "_t": "entero"},
        {"concepto": "Unidades vendidas", "valor": k["unidades"], "_t": "entero"},
        {"concepto": "Tiquetes anulados", "valor": k["num_anuladas"], "_t": "entero"},
        {"concepto": "Ventas brutas (tiquetes emitidos)", "valor": k["ventas_brutas"], "_t": "moneda"},
        {"concepto": "Descuentos otorgados", "valor": k["descuentos"], "_t": "moneda"},
        {"concepto": "Devoluciones registradas en el periodo", "valor": k["devoluciones"], "_t": "moneda"},
        {"concepto": "Ventas netas (descontando anulaciones)", "valor": k["ventas_netas"], "_t": "moneda"},
        {"concepto": "Utilidad neta real (Precio − Costo)", "valor": k["utilidad_neta"], "_t": "moneda"},
        {"concepto": "Margen sobre ventas netas",
         "valor": (k["utilidad_neta"] / k["ventas_netas"]) if k["ventas_netas"] else 0, "_t": "porcentaje"},
        {"concepto": "Tiquete promedio", "valor": k["ticket_promedio"], "_t": "moneda"},
    ]
    rep.agregar_hoja("Resumen", "Resumen de Ventas",
                     [Columna("Indicador", "concepto"), Columna("Valor", "valor", "moneda")],
                     resumen, subtitulo=periodo, totales=False,
                     formato_fila=lambda f: {"valor": f["_t"]})

    met = db.ventas_por_metodo(desde, hasta)
    tot = sum(met.values()) or 1
    rep.agregar_hoja("Por Método", "Ingresos por Método de Pago",
                     [Columna("Método de Pago", "m"), Columna("Total", "t", "moneda", True),
                      Columna("Participación", "p", "porcentaje")],
                     [{"m": m, "t": v, "p": v / tot} for m, v in met.items()], subtitulo=periodo)

    rep.agregar_hoja("Ventas", "Relación de Ventas",
                     [Columna("Tiquete", "numero_tiquete"), Columna("Fecha", "fecha", "fecha"),
                      Columna("Cajero", "cajero"), Columna("Cédula cliente", "cliente_documento"),
                      Columna("Cliente", "cliente_nombre"), Columna("Método", "metodo_pago"),
                      Columna("Subtotal", "subtotal", "moneda", True),
                      Columna("Descuento", "descuento", "moneda", True),
                      Columna("Total", "total", "moneda", True),
                      Columna("Devuelto", "total_devuelto", "moneda", True),
                      Columna("Neto", "neto", "moneda", True),
                      Columna("Utilidad neta", "utilidad", "moneda", True), Columna("Estado", "estado")],
                     db.ventas_periodo(desde, hasta), subtitulo=periodo)

    rep.agregar_hoja("Detalle", "Detalle de Prendas Vendidas",
                     [Columna("Tiquete", "numero_tiquete"), Columna("Fecha", "fecha", "fecha"),
                      Columna("Código", "codigo"), Columna("Referencia", "referencia"),
                      Columna("Producto", "nombre"), Columna("Categoría", "categoria"),
                      Columna("Cant.", "cantidad", "entero", True),
                      Columna("Devueltas", "cantidad_devuelta", "entero", True),
                      Columna("Precio Unit.", "precio_unitario", "moneda"),
                      Columna("Costo Unit.", "costo_unitario", "moneda"),
                      Columna("Descuento", "descuento", "moneda", True),
                      Columna("Total", "total", "moneda", True),
                      Columna("Devuelto", "total_devuelto", "moneda", True),
                      Columna("Utilidad neta", "utilidad", "moneda", True),
                      Columna("Método", "metodo_pago"), Columna("Estado", "estado")],
                     db.detalle_periodo(desde, hasta), subtitulo=periodo)

    rep.agregar_hoja("Top Productos", "Productos de Mayor Rotación",
                     [Columna("Código", "codigo"), Columna("Producto", "nombre"),
                      Columna("Unidades", "unidades", "entero", True),
                      Columna("Ingresos", "ingresos", "moneda", True),
                      Columna("Utilidad", "utilidad", "moneda", True)],
                     db.top_productos(desde, hasta, 20), subtitulo=periodo)

    rep.agregar_hoja("Devoluciones", "Devoluciones y Anulaciones",
                     [Columna("Devolución", "numero"), Columna("Tiquete", "numero_tiquete"),
                      Columna("Fecha", "fecha", "fecha"), Columna("Tipo", "tipo"),
                      Columna("Prendas", "prendas", ancho_max=60), Columna("Método", "metodo_pago"),
                      Columna("Monto", "monto", "moneda", True), Columna("Motivo", "motivo", ancho_max=50),
                      Columna("Cajero", "cajero")],
                     db.devoluciones_periodo(desde, hasta), subtitulo=periodo)
    return rep.guardar(ruta)


def exportar_historial(db, filas: Iterable[Any], ruta: str | Path, subtitulo: str = "") -> Path:
    """Historial de documentos (tiquetes) con el filtro aplicado en pantalla + eventos."""
    rep = ReporteExcel(_empresa(db))
    filas = [dict(f) for f in filas]
    rep.agregar_hoja(
        "Documentos", "Historial de Documentos",
        [Columna("Tiquete", "numero_tiquete"), Columna("Fecha y hora", "fecha", "fecha"),
         Columna("Cajero", "cajero"), Columna("Cédula", "cliente_documento"), Columna("Cliente", "cliente_nombre"),
         Columna("Método", "metodo_pago"), Columna("Prendas", "unidades", "entero", True),
         Columna("Total", "total", "moneda", True), Columna("Recibido", "monto_recibido", "moneda"),
         Columna("Vuelto", "cambio", "moneda"), Columna("Devuelto", "total_devuelto", "moneda", True),
         Columna("Neto", "neto", "moneda", True), Columna("Reimpresiones", "reimpresiones", "entero"),
         Columna("Estado", "estado")],
        filas, subtitulo=subtitulo or f"{len(filas)} documentos  ·  Generado {datetime.now():%d/%m/%Y %H:%M}")
    eventos = []
    for f in filas[:3000]:
        eventos += [dict(e) for e in db.eventos_documento(f["id"])]
    rep.agregar_hoja(
        "Trazabilidad", "Trazabilidad de Documentos",
        [Columna("Fecha y hora", "fecha", "fecha"), Columna("Tiquete", "numero_tiquete"), Columna("Evento", "evento"),
         Columna("Descripción", "descripcion", ancho_max=70), Columna("Monto", "monto", "moneda"),
         Columna("Cajero", "cajero")],
        sorted(eventos, key=lambda e: e["id"]), totales=False)
    return rep.guardar(ruta)


def exportar_auditoria(db, ruta: str | Path, limite: int = 20000) -> Path:
    rep = ReporteExcel(_empresa(db))
    rep.agregar_hoja("Auditoría", "Registro de Auditoría",
                     [Columna("Fecha y hora", "fecha", "fecha"), Columna("Usuario", "usuario"),
                      Columna("Acción", "accion"), Columna("Entidad", "entidad"), Columna("Id", "entidad_id"),
                      Columna("Detalle", "detalle", ancho_max=90)],
                     db.auditoria(limite), totales=False)
    return rep.guardar(ruta)


def exportar_cierres(db, ruta: str | Path, limite: int = 500) -> Path:
    rep = ReporteExcel(_empresa(db))
    filas = [dict(r) for r in db.historial_cierres(limite) if r["estado"] == "CERRADA"]
    rep.agregar_hoja(
        "Cierres", "Historial de Cierres de Caja",
        [Columna("N.º", "id", "entero"), Columna("Apertura", "fecha_apertura", "fecha"),
         Columna("Cierre", "fecha_cierre", "fecha"), Columna("Cajero", "cajero"),
         Columna("Base", "base_inicial", "moneda", True), Columna("Ventas", "num_ventas", "entero", True),
         Columna("Total Ventas", "total_ventas", "moneda", True),
         Columna("Efectivo", "total_efectivo", "moneda", True), Columna("Nequi", "total_nequi", "moneda", True),
         Columna("Daviplata", "total_daviplata", "moneda", True),
         Columna("Datáfono", "total_datafono", "moneda", True),
         Columna("Devoluciones", "total_devoluciones", "moneda", True),
         Columna("Efectivo Esperado", "efectivo_esperado", "moneda", True),
         Columna("Efectivo Contado", "efectivo_contado", "moneda", True),
         Columna("Diferencia", "diferencia", "moneda", True), Columna("Resultado", "resultado"),
         Columna("Utilidad", "utilidad", "moneda", True), Columna("Observaciones", "observaciones")],
        filas,
    )
    return rep.guardar(ruta)


def exportar_cierre_individual(db, cierre: dict[str, Any], ruta: str | Path) -> Path:
    rep = ReporteExcel(_empresa(db))
    filas = [
        {"c": "Apertura", "v": cierre["fecha_apertura"], "_t": "fecha"},
        {"c": "Cierre", "v": cierre["fecha_cierre"], "_t": "fecha"},
        {"c": "Cajero", "v": cierre["cajero"], "_t": "texto"},
        {"c": "Número de ventas", "v": cierre["num_ventas"], "_t": "entero"},
        {"c": "Base inicial (fondo de sencillo)", "v": cierre["base_inicial"], "_t": "moneda"},
        {"c": "Ventas en Efectivo", "v": cierre["total_efectivo"], "_t": "moneda"},
        {"c": "Ventas Nequi", "v": cierre["total_nequi"], "_t": "moneda"},
        {"c": "Ventas Daviplata", "v": cierre["total_daviplata"], "_t": "moneda"},
        {"c": "Ventas Datáfono", "v": cierre["total_datafono"], "_t": "moneda"},
        {"c": "TOTAL VENTAS", "v": cierre["total_ventas"], "_t": "moneda"},
        {"c": "Devoluciones (total)", "v": cierre["total_devoluciones"], "_t": "moneda"},
        {"c": "Devoluciones en efectivo", "v": cierre["devoluciones_efectivo"], "_t": "moneda"},
        {"c": "Efectivo esperado en cajón", "v": cierre["efectivo_esperado"], "_t": "moneda"},
        {"c": "Efectivo contado (arqueo ciego)", "v": cierre["efectivo_contado"], "_t": "moneda"},
        {"c": "Diferencia", "v": cierre["diferencia"], "_t": "moneda"},
        {"c": "RESULTADO", "v": cierre["resultado"], "_t": "texto"},
        {"c": "Utilidad del turno", "v": cierre["utilidad"], "_t": "moneda"},
        {"c": "Observaciones", "v": cierre.get("observaciones", ""), "_t": "texto"},
    ]
    rep.agregar_hoja("Cierre", f"Cierre de Caja N.º {cierre['id']}",
                     [Columna("Concepto", "c"), Columna("Valor", "v", "moneda")], filas,
                     totales=False, formato_fila=lambda f: {"v": f["_t"]})
    return rep.guardar(ruta)


# Formato de carga del software de facturación que usa la tienda ("Inventario General 01.xlsx")
FORMATO_CARGA_DEFECTO = [
    "Nombre", "Código Interno", "Código Tipo de Unidad", "Código Tipo de Moneda", "Precio Unitario Venta",
    "Codigo Tipo de Impuesto Venta", "Precio Unitario Compra", "Codigo Tipo de Impuesto Compra", "Stock",
    "Stock Mínimo", "Categoria", "Marca", "Color", "Talla",
]
_DEFECTOS_TECNICOS = {"codigo tipo de unidad": 10, "codigo tipo de moneda": 170,
                      "codigo tipo de impuesto venta": 8, "codigo tipo de impuesto compra": 8}


def _numero_si_puede(v: Any) -> Any:
    s = str(v).strip()
    return int(s) if s.isdigit() and not s.startswith("0") else s


def exportar_formato_carga(db, ruta: str | Path) -> Path:
    """
    Inventario ACTUAL (stock descontado por las ventas) con las mismas columnas y el mismo
    orden del archivo importado, listo para volver a subir al software de facturación.
    Las columnas que el POS no maneja (unidad, moneda, impuestos…) se copian de la fila original.
    """
    import json
    try:
        encabezados = json.loads(db.get_config("formato_carga_columnas", "")) or FORMATO_CARGA_DEFECTO
    except ValueError:
        encabezados = FORMATO_CARGA_DEFECTO
    mapeo = mapear_columnas(encabezados)
    campo_de = {i: c for c, i in mapeo.items()}
    productos = [dict(r) for r in db.q("SELECT * FROM productos WHERE activo=1 ORDER BY id")]
    # Valor más común de cada columna técnica (para productos creados dentro del POS)
    comunes: dict[str, Any] = {}
    for p in productos:
        try:
            p["_origen"] = json.loads(p.get("datos_origen") or "{}")
        except ValueError:
            p["_origen"] = {}
    for h in encabezados:
        vals = [p["_origen"].get(h) for p in productos if p["_origen"].get(h) not in (None, "")]
        comunes[h] = max(set(vals), key=vals.count) if vals else _DEFECTOS_TECNICOS.get(normalizar(h), "")

    wb = Workbook()
    ws = wb.active
    ws.title = "INVENTARIO GENERAL"
    ws.append(encabezados)
    for p in productos:
        o = p["_origen"]
        fila = []
        for i, h in enumerate(encabezados):
            campo = campo_de.get(i)
            if campo == "nombre":
                v = o.get(h) or p["nombre"]
            elif campo == "categoria":
                orig = o.get(h)
                v = orig if orig and capitalizar_nombre(str(orig)) == p["categoria"] else p["categoria"].upper()
            elif campo in ("marca", "color"):
                v = p[campo] or o.get(h, "")
            elif campo == "talla":
                v = _numero_si_puede(p["talla"]) if p["talla"] else o.get(h, "")
            elif campo in ("codigo", "referencia"):
                v = p[campo]
            elif campo in ("precio", "costo", "stock", "stock_minimo"):
                v = int(p[campo] or 0)
            else:
                v = o.get(h, comunes.get(h, ""))
            fila.append(v)
        ws.append(fila)
    # Encabezado simple (igual al original) + anchos automáticos; sin filas extra para no romper la carga
    for j, h in enumerate(encabezados, start=1):
        ws.cell(row=1, column=j).font = Font(bold=True)
        largo = max([len(str(h))] + [len(str(ws.cell(row=r, column=j).value or "")) for r in range(2, ws.max_row + 1)])
        ws.column_dimensions[get_column_letter(j)].width = min(largo + 3, 50)
    ws.freeze_panes = "A2"
    ruta = Path(ruta)
    wb.save(ruta)
    return ruta


def generar_plantilla_inventario(ruta: str | Path) -> Path:
    """Plantilla vacía con el mismo formato de 'Inventario General 01.xlsx'."""
    wb = Workbook()
    ws = wb.active
    ws.title = "INVENTARIO GENERAL"
    ws.append(FORMATO_CARGA_DEFECTO)
    ws.append(["WIDE LEG", "DL001", 10, 170, 140000, 8, 70000, 8, 15, 1, "JEANS DAMA", "WIDE LEG 1", "AZUL INDIGO", 14])
    ws.append(["CLASICO 11", "CL011", 10, 170, 100000, 8, 55000, 8, 6, 1, "JEANS HOMBRE", "CLASICO", "AZUL OSCURO", 34])
    for j, t in enumerate(FORMATO_CARGA_DEFECTO, start=1):
        c = ws.cell(row=1, column=j)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=AZUL_OSCURO)
        ws.column_dimensions[get_column_letter(j)].width = max(len(t) + 4, 12)
    wb.save(ruta)
    return Path(ruta)
