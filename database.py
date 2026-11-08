"""
database.py — Capa de datos SQLite3 de ConfeccionesR4 POS (esquema v3).

Diseño
------
* Valores monetarios en ENTEROS (pesos COP, sin decimales): sin errores de redondeo.
* WAL + synchronous=NORMAL: una venta completa se escribe en < 10 ms.
* Cada operación de varias tablas (venta, devolución, cierre) corre en UNA transacción
  ``BEGIN IMMEDIATE``: o se guarda todo o no se guarda nada.
* Integridad referencial (FOREIGN KEYS) y documentos INMUTABLES: triggers impiden borrar
  ventas, detalles, devoluciones o eventos, y modificar los valores de un tiquete emitido.
  Las anulaciones nunca borran: se registran como devoluciones y eventos de historial.
* Trazabilidad: ``documentos_historial`` (emisión, reimpresión, devolución, anulación de cada
  tiquete) y ``auditoria`` (cambios de precios, stock, caja, configuración, importaciones).

Tablas: configuracion, productos, cierres_caja, ventas, detalle_ventas, devoluciones,
        detalle_devoluciones, documentos_historial, auditoria.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

import config as cfg

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS configuracion (
    clave   TEXT PRIMARY KEY,
    valor   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS productos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo       TEXT    NOT NULL UNIQUE,          -- Código de barras (Code128 / EAN-13)
    referencia   TEXT    NOT NULL DEFAULT '',
    nombre       TEXT    NOT NULL,
    categoria    TEXT    NOT NULL DEFAULT 'General',
    costo        INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
    precio       INTEGER NOT NULL DEFAULT 0 CHECK (precio >= 0),
    stock        INTEGER NOT NULL DEFAULT 0,
    marca        TEXT    NOT NULL DEFAULT '',
    color        TEXT    NOT NULL DEFAULT '',
    talla        TEXT    NOT NULL DEFAULT '',
    stock_minimo INTEGER NOT NULL DEFAULT 0,
    datos_origen TEXT    NOT NULL DEFAULT '',          -- JSON: fila original del Excel importado
    activo       INTEGER NOT NULL DEFAULT 1,
    creado       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    actualizado  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_productos_nombre     ON productos(nombre);
CREATE INDEX IF NOT EXISTS ix_productos_referencia ON productos(referencia);
CREATE INDEX IF NOT EXISTS ix_productos_categoria  ON productos(categoria);

CREATE TABLE IF NOT EXISTS cierres_caja (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_apertura      TEXT    NOT NULL,
    cajero              TEXT    NOT NULL DEFAULT '',
    base_inicial        INTEGER NOT NULL DEFAULT 0,
    estado              TEXT    NOT NULL DEFAULT 'ABIERTA' CHECK (estado IN ('ABIERTA','CERRADA')),
    fecha_cierre        TEXT,
    num_ventas          INTEGER NOT NULL DEFAULT 0,
    total_ventas        INTEGER NOT NULL DEFAULT 0,
    total_efectivo      INTEGER NOT NULL DEFAULT 0,
    total_nequi         INTEGER NOT NULL DEFAULT 0,
    total_daviplata     INTEGER NOT NULL DEFAULT 0,
    total_datafono      INTEGER NOT NULL DEFAULT 0,
    total_devoluciones  INTEGER NOT NULL DEFAULT 0,
    devoluciones_efectivo INTEGER NOT NULL DEFAULT 0,
    utilidad            INTEGER NOT NULL DEFAULT 0,
    efectivo_esperado   INTEGER,
    efectivo_contado    INTEGER,
    diferencia          INTEGER,
    resultado           TEXT,                       -- CUADRE PERFECTO / SOBRANTE / FALTANTE
    denominaciones      TEXT,                       -- JSON {"50000": 3, ...}
    observaciones       TEXT    NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_caja_abierta ON cierres_caja(estado) WHERE estado = 'ABIERTA';

CREATE TABLE IF NOT EXISTS ventas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_tiquete     TEXT    NOT NULL UNIQUE,     -- Ej. SETP990000000
    consecutivo        INTEGER NOT NULL,
    fecha              TEXT    NOT NULL,
    caja_id            INTEGER NOT NULL REFERENCES cierres_caja(id),
    cajero             TEXT    NOT NULL DEFAULT '',
    cliente_documento  TEXT    NOT NULL DEFAULT '',
    cliente_nombre     TEXT    NOT NULL DEFAULT '',
    subtotal           INTEGER NOT NULL,
    descuento          INTEGER NOT NULL DEFAULT 0,
    total              INTEGER NOT NULL,
    costo_total        INTEGER NOT NULL,
    utilidad           INTEGER NOT NULL,
    metodo_pago        TEXT    NOT NULL,
    monto_recibido     INTEGER NOT NULL DEFAULT 0,
    cambio             INTEGER NOT NULL DEFAULT 0,
    total_devuelto     INTEGER NOT NULL DEFAULT 0,
    utilidad_devuelta  INTEGER NOT NULL DEFAULT 0,
    estado             TEXT    NOT NULL DEFAULT 'COMPLETADA'
                       CHECK (estado IN ('COMPLETADA','DEVOLUCION PARCIAL','ANULADO'))
);
CREATE INDEX IF NOT EXISTS ix_ventas_fecha   ON ventas(fecha);
CREATE INDEX IF NOT EXISTS ix_ventas_caja    ON ventas(caja_id);
CREATE INDEX IF NOT EXISTS ix_ventas_cliente ON ventas(cliente_documento);

CREATE TABLE IF NOT EXISTS detalle_ventas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id           INTEGER NOT NULL REFERENCES ventas(id),
    producto_id        INTEGER REFERENCES productos(id),
    codigo             TEXT    NOT NULL,
    referencia         TEXT    NOT NULL DEFAULT '',
    nombre             TEXT    NOT NULL,
    categoria          TEXT    NOT NULL DEFAULT '',
    cantidad           INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario    INTEGER NOT NULL,
    costo_unitario     INTEGER NOT NULL,
    subtotal           INTEGER NOT NULL,
    descuento          INTEGER NOT NULL DEFAULT 0,
    total              INTEGER NOT NULL,
    utilidad           INTEGER NOT NULL,
    cantidad_devuelta  INTEGER NOT NULL DEFAULT 0,
    total_devuelto     INTEGER NOT NULL DEFAULT 0,
    utilidad_devuelta  INTEGER NOT NULL DEFAULT 0,
    CHECK (cantidad_devuelta BETWEEN 0 AND cantidad)
);
CREATE INDEX IF NOT EXISTS ix_detalle_venta    ON detalle_ventas(venta_id);
CREATE INDEX IF NOT EXISTS ix_detalle_producto ON detalle_ventas(producto_id);

CREATE TABLE IF NOT EXISTS devoluciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    numero      TEXT    NOT NULL UNIQUE,            -- DEV-000001
    venta_id    INTEGER NOT NULL REFERENCES ventas(id),
    caja_id     INTEGER NOT NULL REFERENCES cierres_caja(id),
    fecha       TEXT    NOT NULL,
    tipo        TEXT    NOT NULL CHECK (tipo IN ('PARCIAL','TOTAL')),
    monto       INTEGER NOT NULL,
    utilidad    INTEGER NOT NULL,
    metodo_pago TEXT    NOT NULL,
    motivo      TEXT    NOT NULL,
    cajero      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_devoluciones_fecha ON devoluciones(fecha);
CREATE INDEX IF NOT EXISTS ix_devoluciones_venta ON devoluciones(venta_id);

CREATE TABLE IF NOT EXISTS detalle_devoluciones (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    devolucion_id  INTEGER NOT NULL REFERENCES devoluciones(id),
    detalle_id     INTEGER NOT NULL REFERENCES detalle_ventas(id),
    producto_id    INTEGER REFERENCES productos(id),
    nombre         TEXT    NOT NULL,
    cantidad       INTEGER NOT NULL CHECK (cantidad > 0),
    monto          INTEGER NOT NULL,
    utilidad       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_detdev_dev ON detalle_devoluciones(devolucion_id);

-- Trazabilidad de cada tiquete: EMITIDO, REIMPRESO, DEVOLUCION PARCIAL, ANULADO
CREATE TABLE IF NOT EXISTS documentos_historial (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha           TEXT    NOT NULL,
    venta_id        INTEGER NOT NULL REFERENCES ventas(id),
    numero_tiquete  TEXT    NOT NULL,
    evento          TEXT    NOT NULL,
    descripcion     TEXT    NOT NULL DEFAULT '',
    monto           INTEGER NOT NULL DEFAULT 0,
    cajero          TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_hist_venta ON documentos_historial(venta_id);
CREATE INDEX IF NOT EXISTS ix_hist_fecha ON documentos_historial(fecha);

-- Auditoría general (solo inserción)
CREATE TABLE IF NOT EXISTS auditoria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    usuario     TEXT    NOT NULL DEFAULT '',
    accion      TEXT    NOT NULL,
    entidad     TEXT    NOT NULL DEFAULT '',
    entidad_id  TEXT    NOT NULL DEFAULT '',
    detalle     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_auditoria_fecha ON auditoria(fecha);
"""

# Documentos inmutables: nada se borra y los valores de un tiquete emitido no cambian.
TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS tr_ventas_no_borrar BEFORE DELETE ON ventas
BEGIN SELECT RAISE(ABORT, 'Los tiquetes no se pueden borrar: use Anular / Devolución'); END;
CREATE TRIGGER IF NOT EXISTS tr_ventas_inmutable
BEFORE UPDATE OF numero_tiquete, consecutivo, fecha, caja_id, subtotal, descuento, total, costo_total,
                 utilidad, metodo_pago, monto_recibido, cambio ON ventas
BEGIN SELECT RAISE(ABORT, 'Un tiquete emitido no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS tr_detalle_no_borrar BEFORE DELETE ON detalle_ventas
BEGIN SELECT RAISE(ABORT, 'El detalle de un tiquete no se puede borrar'); END;
CREATE TRIGGER IF NOT EXISTS tr_detalle_inmutable
BEFORE UPDATE OF venta_id, producto_id, cantidad, precio_unitario, costo_unitario, subtotal, descuento,
                 total, utilidad ON detalle_ventas
BEGIN SELECT RAISE(ABORT, 'El detalle de un tiquete no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS tr_dev_no_borrar BEFORE DELETE ON devoluciones
BEGIN SELECT RAISE(ABORT, 'Las devoluciones no se pueden borrar'); END;
CREATE TRIGGER IF NOT EXISTS tr_dev_no_editar BEFORE UPDATE ON devoluciones
BEGIN SELECT RAISE(ABORT, 'Las devoluciones no se pueden modificar'); END;
CREATE TRIGGER IF NOT EXISTS tr_detdev_no_borrar BEFORE DELETE ON detalle_devoluciones
BEGIN SELECT RAISE(ABORT, 'Las devoluciones no se pueden borrar'); END;
CREATE TRIGGER IF NOT EXISTS tr_hist_no_borrar BEFORE DELETE ON documentos_historial
BEGIN SELECT RAISE(ABORT, 'El historial de documentos es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS tr_hist_no_editar BEFORE UPDATE ON documentos_historial
BEGIN SELECT RAISE(ABORT, 'El historial de documentos es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS tr_audit_no_borrar BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS tr_audit_no_editar BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS tr_cierre_no_reabrir BEFORE UPDATE ON cierres_caja
WHEN OLD.estado = 'CERRADA'
BEGIN SELECT RAISE(ABORT, 'Un cierre de caja no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS tr_cierre_no_borrar BEFORE DELETE ON cierres_caja
BEGIN SELECT RAISE(ABORT, 'Los cierres de caja no se pueden borrar'); END;
"""


class POSError(Exception):
    """Error de negocio que se muestra tal cual al cajero."""


@dataclass
class ItemCarrito:
    producto_id: int
    cantidad: int
    precio_unitario: int | None = None   # None = usar el precio vigente del catálogo


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rango_dia(d: date) -> tuple[str, str]:
    return f"{d:%Y-%m-%d} 00:00:00", f"{d:%Y-%m-%d} 23:59:59"


def rango_periodo(clave: str, hoy: date | None = None) -> tuple[str, str]:
    """Devuelve (desde, hasta) en texto ISO para: hoy, ayer, 7d, 30d, mes, anio."""
    hoy = hoy or date.today()
    if clave == "ayer":
        return _rango_dia(hoy - timedelta(days=1))
    if clave == "7d":
        return f"{hoy - timedelta(days=6):%Y-%m-%d} 00:00:00", _rango_dia(hoy)[1]
    if clave == "30d":
        return f"{hoy - timedelta(days=29):%Y-%m-%d} 00:00:00", _rango_dia(hoy)[1]
    if clave == "mes":
        return f"{hoy:%Y-%m}-01 00:00:00", _rango_dia(hoy)[1]
    if clave == "anio":
        return f"{hoy:%Y}-01-01 00:00:00", _rango_dia(hoy)[1]
    return _rango_dia(hoy)


def ean13_checksum(doce: str) -> str:
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(doce[:12]))
    return str((10 - s % 10) % 10)


class Database:
    """Acceso centralizado a la base de datos. Una conexión por aplicación."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else cfg.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False, timeout=10
        )
        self.conn.row_factory = sqlite3.Row
        self._configurar_pragmas()
        self._crear_esquema()
        self._sembrar_configuracion()

    # ------------------------------------------------------------------ #
    # Infraestructura y migraciones
    # ------------------------------------------------------------------ #
    def _configurar_pragmas(self) -> None:
        c = self.conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA cache_size=-16000")  # ~16 MB

    def _columnas(self, tabla: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({tabla})")}

    def _crear_esquema(self) -> None:
        with self._lock:
            existe = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ventas'").fetchone()
            if existe:
                self._migrar()
            self.conn.executescript(SCHEMA_SQL)
            self.conn.executescript(TRIGGERS_SQL)
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrar(self) -> None:
        """Lleva bases v1/v2 al esquema v3 sin perder datos (respaldo previo automático)."""
        c = self.conn
        ver = c.execute("PRAGMA user_version").fetchone()[0]
        if ver >= SCHEMA_VERSION:
            return
        try:
            self.respaldar(cfg.backups_dir() / f"antes_migracion_v{ver}_{datetime.now():%Y%m%d_%H%M%S}.db")
        except Exception:  # noqa: BLE001
            pass
        c.execute("PRAGMA foreign_keys=OFF")
        try:
            c.execute("BEGIN IMMEDIATE")
            # v1 -> v2: variantes de producto
            prod = self._columnas("productos")
            for col, ddl in (("marca", "TEXT NOT NULL DEFAULT ''"), ("color", "TEXT NOT NULL DEFAULT ''"),
                             ("talla", "TEXT NOT NULL DEFAULT ''"), ("stock_minimo", "INTEGER NOT NULL DEFAULT 0"),
                             ("datos_origen", "TEXT NOT NULL DEFAULT ''")):
                if col not in prod:
                    c.execute(f"ALTER TABLE productos ADD COLUMN {col} {ddl}")
            # v2 -> v3: ventas (nuevo CHECK de estado + cliente + devuelto) se reconstruye
            for t in ("tr_ventas_no_borrar", "tr_ventas_inmutable", "tr_detalle_no_borrar", "tr_detalle_inmutable",
                      "tr_dev_no_borrar", "tr_dev_no_editar"):
                c.execute(f"DROP TRIGGER IF EXISTS {t}")
            if "cliente_documento" not in self._columnas("ventas"):
                c.execute("ALTER TABLE ventas RENAME TO ventas_v2")
                c.execute("ALTER TABLE detalle_ventas RENAME TO detalle_ventas_v2")
                hay_dev = c.execute("SELECT 1 FROM sqlite_master WHERE name='devoluciones'").fetchone()
                if hay_dev:
                    c.execute("ALTER TABLE devoluciones RENAME TO devoluciones_v2")
                for idx in ("ix_ventas_fecha", "ix_ventas_caja", "ix_detalle_venta", "ix_detalle_producto",
                            "ix_devoluciones_fecha"):
                    c.execute(f"DROP INDEX IF EXISTS {idx}")
                self._script(c, SCHEMA_SQL)
                c.execute(
                    "INSERT INTO ventas(id, numero_tiquete, consecutivo, fecha, caja_id, cajero, subtotal, descuento,"
                    " total, costo_total, utilidad, metodo_pago, monto_recibido, cambio, estado)"
                    " SELECT id, numero_tiquete, consecutivo, fecha, caja_id, cajero, subtotal, descuento, total,"
                    " costo_total, utilidad, metodo_pago, monto_recibido, cambio,"
                    " CASE estado WHEN 'DEVUELTA' THEN 'ANULADO' ELSE 'COMPLETADA' END FROM ventas_v2")
                c.execute(
                    "INSERT INTO detalle_ventas(id, venta_id, producto_id, codigo, referencia, nombre, categoria,"
                    " cantidad, precio_unitario, costo_unitario, subtotal, descuento, total, utilidad)"
                    " SELECT id, venta_id, producto_id, codigo, referencia, nombre, categoria, cantidad,"
                    " precio_unitario, costo_unitario, subtotal, descuento, total, utilidad FROM detalle_ventas_v2")
                if hay_dev:
                    for i, d in enumerate(c.execute("SELECT * FROM devoluciones_v2 ORDER BY id").fetchall(), 1):
                        cur = c.execute(
                            "INSERT INTO devoluciones(numero, venta_id, caja_id, fecha, tipo, monto, utilidad,"
                            " metodo_pago, motivo, cajero) VALUES (?,?,?,?, 'TOTAL', ?,?,?,?,?)",
                            (f"DEV-{i:06d}", d["venta_id"], d["caja_id"], d["fecha"], d["monto"], d["utilidad"],
                             d["metodo_pago"], d["motivo"] or "Migrado", d["cajero"]))
                        for it in c.execute("SELECT * FROM detalle_ventas WHERE venta_id=?", (d["venta_id"],)).fetchall():
                            c.execute("INSERT INTO detalle_devoluciones(devolucion_id, detalle_id, producto_id, nombre,"
                                      " cantidad, monto, utilidad) VALUES (?,?,?,?,?,?,?)",
                                      (cur.lastrowid, it["id"], it["producto_id"], it["nombre"], it["cantidad"],
                                       it["total"], it["utilidad"]))
                            c.execute("UPDATE detalle_ventas SET cantidad_devuelta=cantidad, total_devuelto=total,"
                                      " utilidad_devuelta=utilidad WHERE id=?", (it["id"],))
                        c.execute("UPDATE ventas SET total_devuelto=total, utilidad_devuelta=utilidad WHERE id=?",
                                  (d["venta_id"],))
                    c.execute("DROP TABLE devoluciones_v2")
                # Historial: un evento EMITIDO por cada tiquete existente
                c.execute("INSERT INTO documentos_historial(fecha, venta_id, numero_tiquete, evento, descripcion,"
                          " monto, cajero) SELECT fecha, id, numero_tiquete, 'EMITIDO', 'Migrado de versión anterior',"
                          " total, cajero FROM ventas")
                c.execute("DROP TABLE detalle_ventas_v2")
                c.execute("DROP TABLE ventas_v2")
            self._script(c, SCHEMA_SQL)
            c.execute("INSERT INTO auditoria(accion, entidad, detalle) VALUES ('MIGRACION', 'base_datos', ?)",
                      (f"v{ver} -> v{SCHEMA_VERSION}",))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.execute("PRAGMA foreign_keys=ON")
        problemas = c.execute("PRAGMA foreign_key_check").fetchall()
        if problemas:
            raise POSError(f"Migración con referencias inválidas: {problemas[:3]}")

    @staticmethod
    def _script(c: sqlite3.Connection, sql: str) -> None:
        """Ejecuta un script SIN el COMMIT implícito de executescript (sirve dentro de una transacción)."""
        for sentencia in sql.split(";"):
            limpia = "\n".join(l for l in sentencia.splitlines() if not l.strip().startswith("--")).strip()
            if limpia:
                c.execute(limpia)

    def _sembrar_configuracion(self) -> None:
        with self.transaccion() as c:
            c.executemany(
                "INSERT OR IGNORE INTO configuracion(clave, valor) VALUES (?, ?)",
                list(cfg.DEFAULT_CONFIG.items()),
            )

    @contextmanager
    def transaccion(self) -> Iterator[sqlite3.Connection]:
        """Transacción atómica con bloqueo de escritura inmediato."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def q1(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.execute("PRAGMA optimize")
            finally:
                self.conn.close()

    def respaldar(self, destino: Path | None = None, conservar: int = 30) -> Path:
        """Copia de seguridad en caliente (API backup de SQLite). Conserva las N últimas."""
        carpeta = cfg.backups_dir()
        destino = destino or carpeta / f"respaldo_{datetime.now():%Y%m%d_%H%M%S}.db"
        with self._lock:
            dst = sqlite3.connect(str(destino))
            try:
                self.conn.backup(dst)
            finally:
                dst.close()
        for f in sorted(carpeta.glob("respaldo_*.db"))[:-conservar]:
            try:
                f.unlink()
            except OSError:
                pass
        return destino

    # ------------------------------------------------------------------ #
    # Auditoría
    # ------------------------------------------------------------------ #
    def _usuario(self) -> str:
        r = self.conn.execute("SELECT valor FROM configuracion WHERE clave='cajero_actual'").fetchone()
        return r[0] if r else ""

    def auditar(self, accion: str, entidad: str = "", entidad_id: Any = "", detalle: Any = "",
                c: sqlite3.Connection | None = None) -> None:
        texto = detalle if isinstance(detalle, str) else json.dumps(detalle, ensure_ascii=False, default=str)
        sql = ("INSERT INTO auditoria(fecha, usuario, accion, entidad, entidad_id, detalle) VALUES (?,?,?,?,?,?)")
        if c is not None:
            c.execute(sql, (_ahora(), self._usuario(), accion, entidad, str(entidad_id), texto))
        else:
            with self.transaccion() as cx:
                cx.execute(sql, (_ahora(), self._usuario(), accion, entidad, str(entidad_id), texto))

    def auditoria(self, limite: int = 500) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))

    # ------------------------------------------------------------------ #
    # Configuración
    # ------------------------------------------------------------------ #
    def get_config(self, clave: str, defecto: str | None = None) -> str:
        r = self.q1("SELECT valor FROM configuracion WHERE clave=?", (clave,))
        if r is not None:
            return r["valor"]
        return defecto if defecto is not None else cfg.DEFAULT_CONFIG.get(clave, "")

    def get_config_int(self, clave: str, defecto: int = 0) -> int:
        try:
            return int(self.get_config(clave, str(defecto)))
        except ValueError:
            return defecto

    def get_all_config(self) -> dict[str, str]:
        d = dict(cfg.DEFAULT_CONFIG)
        d.update({r["clave"]: r["valor"] for r in self.q("SELECT clave, valor FROM configuracion")})
        return d

    def set_config(self, valores: dict[str, Any], auditar: bool = True) -> None:
        antes = self.get_all_config()
        cambios = {k: {"antes": antes.get(k), "despues": str(v)} for k, v in valores.items()
                   if str(v) != antes.get(k)}
        with self.transaccion() as c:
            c.executemany(
                "INSERT INTO configuracion(clave, valor) VALUES(?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                [(k, str(v)) for k, v in valores.items()],
            )
            if auditar and cambios and set(cambios) - {"cajero_actual"}:
                self.auditar("CONFIGURACION", "configuracion", "", cambios, c)

    # ------------------------------------------------------------------ #
    # Productos
    # ------------------------------------------------------------------ #
    def listar_productos(
        self, texto: str = "", categoria: str | None = None, limite: int | None = None,
        incluir_inactivos: bool = False,
    ) -> list[sqlite3.Row]:
        sql = ["SELECT * FROM productos WHERE 1=1"]
        p: list[Any] = []
        if not incluir_inactivos:
            sql.append("AND activo=1")
        if categoria and categoria != "Todos":
            sql.append("AND categoria=?")
            p.append(categoria)
        for palabra in (texto or "").split():
            sql.append("AND (codigo LIKE ? OR referencia LIKE ? OR nombre LIKE ? OR marca LIKE ?"
                       " OR color LIKE ? OR talla LIKE ?)")
            p += [f"%{palabra}%"] * 6
        sql.append("ORDER BY nombre COLLATE NOCASE")
        if limite:
            sql.append(f"LIMIT {int(limite)}")
        return self.q(" ".join(sql), p)

    def producto_por_id(self, pid: int) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM productos WHERE id=?", (pid,))

    def producto_por_codigo(self, codigo: str) -> sqlite3.Row | None:
        """Búsqueda exacta para el lector de código de barras (código o referencia)."""
        codigo = (codigo or "").strip()
        if not codigo:
            return None
        r = self.q1("SELECT * FROM productos WHERE codigo=? AND activo=1", (codigo,))
        if r is None:  # digitado a mano en minúsculas (st001)
            r = self.q1("SELECT * FROM productos WHERE codigo=? COLLATE NOCASE AND activo=1", (codigo,))
        if r is None:
            r = self.q1("SELECT * FROM productos WHERE referencia=? COLLATE NOCASE AND activo=1 LIMIT 1",
                        (codigo,))
        return r

    def categorias(self) -> list[str]:
        """Categorías con productos (las de la tienda primero). Sin inventario: las de base."""
        en_bd = [r[0] for r in self.q(
            "SELECT categoria, COUNT(*) n FROM productos WHERE activo=1 GROUP BY categoria ORDER BY n DESC")]
        if not en_bd:
            return list(cfg.CATEGORIAS_BASE)
        base = [c for c in cfg.CATEGORIAS_BASE if c in en_bd]
        return base + [c for c in en_bd if c not in base]

    def generar_codigo(self) -> str:
        """EAN-13 interno de uso en tienda: prefijo 20 (rango reservado GS1 para uso interno)."""
        n = int(self.q1("SELECT COALESCE(MAX(id), 0) + 1 FROM productos")[0])
        while True:
            doce = f"20{n:010d}"
            codigo = doce + ean13_checksum(doce)
            if not self.q1("SELECT 1 FROM productos WHERE codigo=?", (codigo,)):
                return codigo
            n += 1

    def crear_producto(self, datos: dict[str, Any]) -> int:
        codigo = str(datos.get("codigo") or "").strip() or self.generar_codigo()
        nombre = str(datos.get("nombre") or "").strip()
        if not nombre:
            raise POSError("El producto debe tener nombre.")
        try:
            with self.transaccion() as c:
                cur = c.execute(
                    "INSERT INTO productos(codigo, referencia, nombre, categoria, costo, precio, stock,"
                    " marca, color, talla, stock_minimo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (codigo, str(datos.get("referencia") or "").strip(), nombre,
                     str(datos.get("categoria") or "General").strip(),
                     int(datos.get("costo") or 0), int(datos.get("precio") or 0),
                     int(datos.get("stock") or 0), str(datos.get("marca") or "").strip(),
                     str(datos.get("color") or "").strip(), str(datos.get("talla") or "").strip(),
                     int(datos.get("stock_minimo") or 0)),
                )
                self.auditar("PRODUCTO_CREADO", "productos", cur.lastrowid,
                             {"codigo": codigo, "nombre": nombre, "stock": int(datos.get("stock") or 0)}, c)
                return int(cur.lastrowid)
        except sqlite3.IntegrityError as e:
            raise POSError(f"Ya existe un producto con el código {codigo}.") from e

    CAMPOS_EDITABLES = {"codigo", "referencia", "nombre", "categoria", "costo", "precio", "stock", "activo",
                        "marca", "color", "talla", "stock_minimo"}

    def actualizar_producto(self, pid: int, campos: dict[str, Any]) -> None:
        campos = {k: v for k, v in campos.items() if k in self.CAMPOS_EDITABLES}
        if not campos:
            return
        for k in ("costo", "precio"):
            if k in campos and int(campos[k]) < 0:
                raise POSError("Los valores no pueden ser negativos.")
        antes = self.producto_por_id(pid)
        sets = ", ".join(f"{k}=?" for k in campos) + ", actualizado=datetime('now','localtime')"
        try:
            with self.transaccion() as c:
                c.execute(f"UPDATE productos SET {sets} WHERE id=?", (*campos.values(), pid))
                if antes is not None:
                    detalle = {k: {"antes": antes[k], "despues": v} for k, v in campos.items()}
                    detalle["producto"] = f"{antes['codigo']} {antes['nombre']}"
                    self.auditar("PRODUCTO_EDITADO", "productos", pid, detalle, c)
        except sqlite3.IntegrityError as e:
            raise POSError("Ese código de barras ya pertenece a otro producto.") from e

    def desactivar_producto(self, pid: int) -> None:
        """Borrado lógico: el histórico de ventas se conserva."""
        self.actualizar_producto(pid, {"activo": 0})

    def importar_productos(self, filas: list[dict[str, Any]], modo_stock: str = "reemplazar",
                           encabezados_origen: list[str] | None = None) -> dict[str, int]:
        """
        Inserta/actualiza en lote (una sola transacción). Empareja por código y luego por referencia.
        modo_stock: 'reemplazar' (stock = archivo) | 'sumar' (stock += archivo).
        encabezados_origen: columnas del Excel original, para exportar luego en el mismo formato.
        """
        nuevos = actualizados = 0
        with self.transaccion() as c:
            for f in filas:
                codigo = str(f.get("codigo") or "").strip()
                ref = str(f.get("referencia") or "").strip()
                existente = None
                if codigo:
                    existente = c.execute("SELECT id FROM productos WHERE codigo=?", (codigo,)).fetchone()
                if existente is None and ref:
                    existente = c.execute(
                        "SELECT id FROM productos WHERE referencia=? COLLATE NOCASE", (ref,)).fetchone()
                extra = (str(f.get("marca") or ""), str(f.get("color") or ""), str(f.get("talla") or ""),
                         int(f.get("stock_minimo") or 0), json.dumps(f.get("origen") or {}, ensure_ascii=False))
                if existente:
                    stock_sql = "stock = stock + ?" if modo_stock == "sumar" else "stock = ?"
                    c.execute(
                        f"UPDATE productos SET referencia=COALESCE(NULLIF(?,''), referencia), nombre=?,"
                        f" categoria=?, costo=?, precio=?, {stock_sql}, marca=?, color=?, talla=?,"
                        f" stock_minimo=?, datos_origen=?, activo=1,"
                        f" actualizado=datetime('now','localtime') WHERE id=?",
                        (ref, f["nombre"], f.get("categoria") or "General", int(f.get("costo") or 0),
                         int(f.get("precio") or 0), int(f.get("stock") or 0), *extra, existente["id"]),
                    )
                    actualizados += 1
                else:
                    if not codigo:
                        n = c.execute("SELECT COALESCE(MAX(id),0)+1 FROM productos").fetchone()[0]
                        while True:
                            doce = f"20{n:010d}"
                            codigo = doce + ean13_checksum(doce)
                            if not c.execute("SELECT 1 FROM productos WHERE codigo=?", (codigo,)).fetchone():
                                break
                            n += 1
                    c.execute(
                        "INSERT INTO productos(codigo, referencia, nombre, categoria, costo, precio, stock,"
                        " marca, color, talla, stock_minimo, datos_origen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (codigo, ref, f["nombre"], f.get("categoria") or "General",
                         int(f.get("costo") or 0), int(f.get("precio") or 0), int(f.get("stock") or 0), *extra),
                    )
                    nuevos += 1
            if encabezados_origen:
                c.execute("INSERT INTO configuracion(clave, valor) VALUES('formato_carga_columnas', ?)"
                          " ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                          (json.dumps(list(encabezados_origen), ensure_ascii=False),))
            self.auditar("IMPORTACION_INVENTARIO", "productos", "",
                         {"nuevos": nuevos, "actualizados": actualizados, "modo_stock": modo_stock,
                          "unidades": sum(int(f.get("stock") or 0) for f in filas)}, c)
        return {"nuevos": nuevos, "actualizados": actualizados}

    def inventario_valorizado(self) -> list[sqlite3.Row]:
        return self.q(
            "SELECT codigo, referencia, nombre, categoria, marca, color, talla, stock, costo, precio,"
            " stock*costo AS valor_costo, stock*precio AS valor_venta,"
            " stock*(precio-costo) AS utilidad_potencial"
            " FROM productos WHERE activo=1 ORDER BY categoria, nombre"
        )

    # ------------------------------------------------------------------ #
    # Caja
    # ------------------------------------------------------------------ #
    def caja_abierta(self) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM cierres_caja WHERE estado='ABIERTA'")

    def abrir_caja(self, base_inicial: int, cajero: str = "") -> int:
        if base_inicial < 0:
            raise POSError("La base inicial no puede ser negativa.")
        if self.caja_abierta():
            raise POSError("Ya hay una caja abierta. Ciérrela antes de abrir otra.")
        with self.transaccion() as c:
            cur = c.execute(
                "INSERT INTO cierres_caja(fecha_apertura, cajero, base_inicial) VALUES (?,?,?)",
                (_ahora(), cajero, int(base_inicial)),
            )
            self.auditar("CAJA_ABIERTA", "cierres_caja", cur.lastrowid, {"base": base_inicial, "cajero": cajero}, c)
            return int(cur.lastrowid)

    def resumen_caja(self, caja_id: int) -> dict[str, Any]:
        """Totales acumulados del turno (en vivo). Las devoluciones restan del método original."""
        caja = self.q1("SELECT * FROM cierres_caja WHERE id=?", (caja_id,))
        if caja is None:
            raise POSError("Caja no encontrada.")
        por_metodo = {m: {"cantidad": 0, "total": 0, "devuelto": 0, "neto": 0} for m in cfg.METODOS_PAGO}
        for r in self.q("SELECT metodo_pago, COUNT(*) n, COALESCE(SUM(total),0) t FROM ventas"
                        " WHERE caja_id=? GROUP BY metodo_pago", (caja_id,)):
            por_metodo.setdefault(r["metodo_pago"], {"cantidad": 0, "total": 0, "devuelto": 0, "neto": 0})
            por_metodo[r["metodo_pago"]].update(cantidad=r["n"], total=r["t"])
        for r in self.q("SELECT metodo_pago, COALESCE(SUM(monto),0) t FROM devoluciones WHERE caja_id=?"
                        " GROUP BY metodo_pago", (caja_id,)):
            por_metodo.setdefault(r["metodo_pago"], {"cantidad": 0, "total": 0, "devuelto": 0, "neto": 0})
            por_metodo[r["metodo_pago"]]["devuelto"] = r["t"]
        for d in por_metodo.values():
            d["neto"] = d["total"] - d["devuelto"]
        tot = self.q1("SELECT COUNT(*) n, COALESCE(SUM(total),0) t, COALESCE(SUM(utilidad),0) u"
                      " FROM ventas WHERE caja_id=?", (caja_id,))
        dev = self.q1("SELECT COALESCE(SUM(monto),0) t, COALESCE(SUM(utilidad),0) u, COUNT(*) n,"
                      " COALESCE(SUM(CASE WHEN metodo_pago='Efectivo' THEN monto END),0) e"
                      " FROM devoluciones WHERE caja_id=?", (caja_id,))
        return {
            "caja": dict(caja),
            "por_metodo": por_metodo,
            "num_ventas": tot["n"],
            "total_ventas": tot["t"],
            "ventas_netas": tot["t"] - dev["t"],
            "utilidad": tot["u"] - dev["u"],
            "total_devoluciones": dev["t"],
            "num_devoluciones": dev["n"],
            "devoluciones_efectivo": dev["e"],
            "efectivo_esperado": caja["base_inicial"] + por_metodo["Efectivo"]["total"] - dev["e"],
        }

    def cerrar_caja(self, efectivo_contado: int, denominaciones: dict[int, int] | None = None,
                    observaciones: str = "") -> dict[str, Any]:
        """Arqueo ciego: compara lo contado contra lo esperado y cierra el turno."""
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("No hay ninguna caja abierta.")
        res = self.resumen_caja(caja["id"])
        esperado = res["efectivo_esperado"]
        diferencia = int(efectivo_contado) - esperado
        resultado = "CUADRE PERFECTO" if diferencia == 0 else ("SOBRANTE" if diferencia > 0 else "FALTANTE")
        pm = res["por_metodo"]
        with self.transaccion() as c:
            c.execute(
                "UPDATE cierres_caja SET estado='CERRADA', fecha_cierre=?, num_ventas=?, total_ventas=?,"
                " total_efectivo=?, total_nequi=?, total_daviplata=?, total_datafono=?,"
                " total_devoluciones=?, devoluciones_efectivo=?, utilidad=?, efectivo_esperado=?,"
                " efectivo_contado=?, diferencia=?, resultado=?, denominaciones=?, observaciones=?"
                " WHERE id=?",
                (_ahora(), res["num_ventas"], res["total_ventas"], pm["Efectivo"]["total"],
                 pm["Nequi"]["total"], pm["Daviplata"]["total"], pm["Datáfono"]["total"],
                 res["total_devoluciones"], res["devoluciones_efectivo"], res["utilidad"], esperado,
                 int(efectivo_contado), diferencia, resultado,
                 json.dumps({str(k): v for k, v in (denominaciones or {}).items() if v}),
                 observaciones, caja["id"]),
            )
            self.auditar("CAJA_CERRADA", "cierres_caja", caja["id"],
                         {"esperado": esperado, "contado": int(efectivo_contado), "resultado": resultado}, c)
        return self.obtener_cierre(caja["id"]) | {"por_metodo": pm}

    def obtener_cierre(self, caja_id: int) -> dict[str, Any] | None:
        r = self.q1("SELECT * FROM cierres_caja WHERE id=?", (caja_id,))
        if r is None:
            return None
        d = dict(r)
        d["por_metodo"] = {
            "Efectivo": {"total": d["total_efectivo"]}, "Nequi": {"total": d["total_nequi"]},
            "Daviplata": {"total": d["total_daviplata"]}, "Datáfono": {"total": d["total_datafono"]},
        }
        return d

    def historial_cierres(self, limite: int = 200) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM cierres_caja ORDER BY id DESC LIMIT ?", (limite,))

    # ------------------------------------------------------------------ #
    # Ventas
    # ------------------------------------------------------------------ #
    def _siguiente_numero(self, c: sqlite3.Connection) -> tuple[int, str]:
        get = lambda k: c.execute("SELECT valor FROM configuracion WHERE clave=?", (k,)).fetchone()  # noqa: E731
        consecutivo = int(get("consecutivo_siguiente")["valor"])
        hasta = int(get("rango_hasta")["valor"])
        prefijo = get("prefijo")["valor"]
        if consecutivo > hasta:
            raise POSError(
                "Se agotó el rango de numeración configurado.\n"
                "Actualice la resolución en Ajustes antes de seguir vendiendo.")
        c.execute("UPDATE configuracion SET valor=? WHERE clave='consecutivo_siguiente'", (str(consecutivo + 1),))
        return consecutivo, f"{prefijo}{consecutivo}"

    def numeros_restantes(self) -> int:
        return self.get_config_int("rango_hasta") - self.get_config_int("consecutivo_siguiente") + 1

    def _evento(self, c: sqlite3.Connection, venta_id: int, numero: str, evento: str, descripcion: str = "",
                monto: int = 0, cajero: str = "") -> None:
        c.execute("INSERT INTO documentos_historial(fecha, venta_id, numero_tiquete, evento, descripcion, monto,"
                  " cajero) VALUES (?,?,?,?,?,?,?)", (_ahora(), venta_id, numero, evento, descripcion, monto, cajero))

    def registrar_venta(self, items: list[ItemCarrito], metodo_pago: str, descuento: int = 0,
                        monto_recibido: int = 0, cajero: str = "", cliente_documento: str = "",
                        cliente_nombre: str = "") -> dict[str, Any]:
        """
        Registra la venta completa en UNA transacción: valida stock, asigna consecutivo, guarda
        encabezado y detalle con costo/precio, prorratea el descuento, calcula la utilidad,
        descuenta inventario y deja el evento EMITIDO en el historial de documentos.
        """
        if not items:
            raise POSError("El carrito está vacío.")
        if metodo_pago not in cfg.METODOS_PAGO:
            raise POSError(f"Método de pago no válido: {metodo_pago}")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe ABRIR CAJA antes de registrar ventas.")
        permitir_negativo = self.get_config("permitir_stock_negativo") == "1"

        with self.transaccion() as c:
            lineas = []
            for it in items:
                if it.cantidad <= 0:
                    continue
                p = c.execute("SELECT * FROM productos WHERE id=?", (it.producto_id,)).fetchone()
                if p is None:
                    raise POSError(f"Producto {it.producto_id} no existe.")
                if not permitir_negativo and p["stock"] < it.cantidad:
                    raise POSError(f"Stock insuficiente para «{p['nombre']}».\n"
                                   f"Disponible: {p['stock']} · Solicitado: {it.cantidad}")
                precio = int(it.precio_unitario if it.precio_unitario is not None else p["precio"])
                lineas.append({"p": p, "cant": int(it.cantidad), "precio": precio,
                               "subtotal": precio * int(it.cantidad)})
            if not lineas:
                raise POSError("El carrito está vacío.")

            subtotal = sum(l["subtotal"] for l in lineas)
            descuento = max(0, min(int(descuento or 0), subtotal))
            total = subtotal - descuento
            if metodo_pago == "Efectivo":
                if monto_recibido and monto_recibido < total:
                    raise POSError(f"El monto recibido ({cfg.cop(monto_recibido)}) es menor que el total.")
                recibido = int(monto_recibido or total)
            else:
                recibido = total
            cambio = recibido - total

            # Prorrateo del descuento en centenas (sin valores raros como $ 8.484);
            # la última línea absorbe el residuo para que la suma sea exacta.
            restante = descuento
            for i, l in enumerate(lineas):
                if i == len(lineas) - 1:
                    l["desc"] = restante
                else:
                    propuesto = int(round(descuento * l["subtotal"] / subtotal / 100.0)) * 100 if subtotal else 0
                    l["desc"] = max(0, min(propuesto, restante, l["subtotal"]))
                    restante -= l["desc"]
                l["total"] = l["subtotal"] - l["desc"]
                l["costo_u"] = int(l["p"]["costo"])
                l["utilidad"] = l["total"] - l["costo_u"] * l["cant"]
            costo_total = sum(l["costo_u"] * l["cant"] for l in lineas)
            utilidad = total - costo_total

            consecutivo, numero = self._siguiente_numero(c)
            cur = c.execute(
                "INSERT INTO ventas(numero_tiquete, consecutivo, fecha, caja_id, cajero, cliente_documento,"
                " cliente_nombre, subtotal, descuento, total, costo_total, utilidad, metodo_pago,"
                " monto_recibido, cambio) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (numero, consecutivo, _ahora(), caja["id"], cajero, (cliente_documento or "").strip(),
                 (cliente_nombre or "").strip().upper(), subtotal, descuento, total, costo_total, utilidad,
                 metodo_pago, recibido, cambio),
            )
            venta_id = int(cur.lastrowid)
            c.executemany(
                "INSERT INTO detalle_ventas(venta_id, producto_id, codigo, referencia, nombre, categoria,"
                " cantidad, precio_unitario, costo_unitario, subtotal, descuento, total, utilidad)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(venta_id, l["p"]["id"], l["p"]["codigo"], l["p"]["referencia"], l["p"]["nombre"],
                  l["p"]["categoria"], l["cant"], l["precio"], l["costo_u"], l["subtotal"], l["desc"],
                  l["total"], l["utilidad"]) for l in lineas],
            )
            c.executemany(
                "UPDATE productos SET stock = stock - ?, actualizado=datetime('now','localtime') WHERE id=?",
                [(l["cant"], l["p"]["id"]) for l in lineas],
            )
            unidades = sum(l["cant"] for l in lineas)
            self._evento(c, venta_id, numero, "EMITIDO",
                         f"{unidades} prenda(s) · {metodo_pago}" + (f" · cambio {cfg.cop(cambio)}" if cambio else ""),
                         total, cajero)
        return self.obtener_venta(venta_id)

    def obtener_venta(self, venta_id_o_numero: int | str) -> dict[str, Any] | None:
        if isinstance(venta_id_o_numero, int):
            v = self.q1("SELECT * FROM ventas WHERE id=?", (venta_id_o_numero,))
        else:
            txt = str(venta_id_o_numero).strip().upper().replace("-", "").replace(" ", "")
            v = self.q1("SELECT * FROM ventas WHERE numero_tiquete=?", (txt,))
            if v is None and txt.isdigit():
                v = self.q1("SELECT * FROM ventas WHERE consecutivo=?", (int(txt),))
        if v is None:
            return None
        d = dict(v)
        d["items"] = [dict(r) for r in self.q("SELECT * FROM detalle_ventas WHERE venta_id=? ORDER BY id", (v["id"],))]
        d["neto"] = d["total"] - d["total_devuelto"]
        return d

    def ultima_venta(self) -> dict[str, Any] | None:
        r = self.q1("SELECT id FROM ventas ORDER BY id DESC LIMIT 1")
        return self.obtener_venta(int(r["id"])) if r else None

    def cliente_por_documento(self, documento: str) -> str:
        """Nombre del cliente según su última compra (autocompletar en el POS)."""
        documento = (documento or "").strip()
        if not documento:
            return ""
        r = self.q1("SELECT cliente_nombre FROM ventas WHERE cliente_documento=? AND cliente_nombre<>''"
                    " ORDER BY id DESC LIMIT 1", (documento,))
        return r[0] if r else ""

    def registrar_reimpresion(self, venta_id: int, cajero: str = "") -> None:
        """La reimpresión NO altera el consecutivo: solo queda trazada en el historial."""
        v = self.q1("SELECT numero_tiquete FROM ventas WHERE id=?", (venta_id,))
        if v is None:
            raise POSError("Tiquete no encontrado.")
        with self.transaccion() as c:
            n = c.execute("SELECT COUNT(*) FROM documentos_historial WHERE venta_id=? AND evento='REIMPRESO'",
                          (venta_id,)).fetchone()[0]
            self._evento(c, venta_id, v["numero_tiquete"], "REIMPRESO", f"Copia N.º {n + 1}", 0, cajero)

    def registrar_devolucion(self, numero_tiquete: str, cantidades: dict[int, int] | None, motivo: str,
                             cajero: str = "") -> dict[str, Any]:
        """
        Devolución total (anulación) o parcial de un tiquete.
        cantidades: {detalle_id: unidades a devolver}; None = todo lo pendiente (ANULAR).
        Reingresa el stock, descuenta el valor del arqueo (método original) y de las métricas,
        y deja trazado el evento en documentos_historial + auditoría.
        """
        motivo = (motivo or "").strip()
        if not motivo:
            raise POSError("Indique el motivo de la devolución.")
        venta = self.obtener_venta(numero_tiquete)
        if venta is None:
            raise POSError(f"No se encontró el tiquete {numero_tiquete}.")
        if venta["estado"] == "ANULADO":
            raise POSError("Ese tiquete ya está ANULADO.")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe haber una caja abierta para registrar devoluciones.")

        lineas = []
        for it in venta["items"]:
            pendiente = it["cantidad"] - it["cantidad_devuelta"]
            pedir = pendiente if cantidades is None else int(cantidades.get(it["id"], 0) or 0)
            if pedir <= 0:
                continue
            if pedir > pendiente:
                raise POSError(f"«{it['nombre']}»: solo quedan {pendiente} unidad(es) por devolver.")
            if pedir == pendiente:  # últimas unidades: absorbe el residuo del redondeo
                monto = it["total"] - it["total_devuelto"]
                util = it["utilidad"] - it["utilidad_devuelta"]
            else:  # proporcional, redondeado a $ 50 (moneda mínima en circulación)
                monto = min(int(round(it["total"] * pedir / it["cantidad"] / 50.0)) * 50,
                            it["total"] - it["total_devuelto"])
                util = monto - it["costo_unitario"] * pedir
            lineas.append({"it": it, "cant": pedir, "monto": monto, "util": util})
        if not lineas:
            raise POSError("Seleccione al menos una prenda para devolver.")
        monto_total = sum(l["monto"] for l in lineas)
        util_total = sum(l["util"] for l in lineas)
        if venta["metodo_pago"] == "Efectivo":
            if self.resumen_caja(caja["id"])["efectivo_esperado"] < monto_total:
                raise POSError("No hay suficiente efectivo en caja para esta devolución.")
        queda = sum(it["cantidad"] - it["cantidad_devuelta"] for it in venta["items"]) - sum(l["cant"] for l in lineas)
        tipo = "TOTAL" if queda == 0 else "PARCIAL"
        nuevo_estado = "ANULADO" if queda == 0 else "DEVOLUCION PARCIAL"

        with self.transaccion() as c:
            n = c.execute("SELECT COUNT(*) FROM devoluciones").fetchone()[0] + 1
            numero_dev = f"DEV-{n:06d}"
            cur = c.execute(
                "INSERT INTO devoluciones(numero, venta_id, caja_id, fecha, tipo, monto, utilidad, metodo_pago,"
                " motivo, cajero) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (numero_dev, venta["id"], caja["id"], _ahora(), tipo, monto_total, util_total,
                 venta["metodo_pago"], motivo, cajero))
            dev_id = int(cur.lastrowid)
            for l in lineas:
                it = l["it"]
                c.execute("INSERT INTO detalle_devoluciones(devolucion_id, detalle_id, producto_id, nombre, cantidad,"
                          " monto, utilidad) VALUES (?,?,?,?,?,?,?)",
                          (dev_id, it["id"], it["producto_id"], it["nombre"], l["cant"], l["monto"], l["util"]))
                c.execute("UPDATE detalle_ventas SET cantidad_devuelta=cantidad_devuelta+?,"
                          " total_devuelto=total_devuelto+?, utilidad_devuelta=utilidad_devuelta+? WHERE id=?",
                          (l["cant"], l["monto"], l["util"], it["id"]))
                if it["producto_id"]:
                    c.execute("UPDATE productos SET stock=stock+?, actualizado=datetime('now','localtime')"
                              " WHERE id=?", (l["cant"], it["producto_id"]))
            c.execute("UPDATE ventas SET total_devuelto=total_devuelto+?, utilidad_devuelta=utilidad_devuelta+?,"
                      " estado=? WHERE id=?", (monto_total, util_total, nuevo_estado, venta["id"]))
            prendas = ", ".join(f"{l['cant']}× {l['it']['nombre']}" for l in lineas)
            self._evento(c, venta["id"], venta["numero_tiquete"],
                         "ANULADO" if tipo == "TOTAL" else "DEVOLUCION PARCIAL",
                         f"{numero_dev} · {prendas} · Motivo: {motivo}", -monto_total, cajero)
            self.auditar("DEVOLUCION_" + tipo, "ventas", venta["numero_tiquete"],
                         {"devolucion": numero_dev, "monto": monto_total, "motivo": motivo}, c)
        resultado = self.obtener_venta(venta["id"])
        resultado["devolucion"] = {"numero": numero_dev, "tipo": tipo, "monto": monto_total, "motivo": motivo,
                                   "lineas": [{"nombre": l["it"]["nombre"], "cantidad": l["cant"],
                                               "monto": l["monto"]} for l in lineas]}
        return resultado

    # ------------------------------------------------------------------ #
    # Historial de documentos
    # ------------------------------------------------------------------ #
    def buscar_documentos(self, desde: str | None = None, hasta: str | None = None, numero: str = "",
                          cliente: str = "", metodo: str = "", estado: str = "", limite: int = 2000
                          ) -> list[sqlite3.Row]:
        sql = ["SELECT v.*, (v.total - v.total_devuelto) AS neto,"
               " (SELECT COUNT(*) FROM documentos_historial h WHERE h.venta_id=v.id AND h.evento='REIMPRESO')"
               " AS reimpresiones, (SELECT SUM(cantidad) FROM detalle_ventas d WHERE d.venta_id=v.id) AS unidades"
               " FROM ventas v WHERE 1=1"]
        p: list[Any] = []
        if desde:
            sql.append("AND v.fecha >= ?")
            p.append(desde)
        if hasta:
            sql.append("AND v.fecha <= ?")
            p.append(hasta)
        numero = (numero or "").strip().upper().replace("-", "").replace(" ", "")
        if numero:
            sql.append("AND (v.numero_tiquete LIKE ? OR CAST(v.consecutivo AS TEXT) LIKE ?)")
            p += [f"%{numero}%", f"%{numero}%"]
        for palabra in (cliente or "").split():
            sql.append("AND (v.cliente_documento LIKE ? OR v.cliente_nombre LIKE ?)")
            p += [f"%{palabra}%", f"%{palabra.upper()}%"]
        if metodo and metodo != "Todos":
            sql.append("AND v.metodo_pago=?")
            p.append(metodo)
        if estado and estado != "Todos":
            sql.append("AND v.estado=?")
            p.append(estado)
        sql.append("ORDER BY v.id DESC LIMIT ?")
        p.append(int(limite))
        return self.q(" ".join(sql), p)

    def eventos_documento(self, venta_id: int) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM documentos_historial WHERE venta_id=? ORDER BY id", (venta_id,))

    def devoluciones_de_venta(self, venta_id: int) -> list[dict[str, Any]]:
        out = []
        for d in self.q("SELECT * FROM devoluciones WHERE venta_id=? ORDER BY id", (venta_id,)):
            dd = dict(d)
            dd["lineas"] = [dict(r) for r in self.q("SELECT * FROM detalle_devoluciones WHERE devolucion_id=?",
                                                    (d["id"],))]
            out.append(dd)
        return out

    # ------------------------------------------------------------------ #
    # Reportes / Dashboard (métricas NETAS: lo devuelto se descuenta)
    # ------------------------------------------------------------------ #
    def kpis(self, desde: str, hasta: str) -> dict[str, int]:
        v = self.q1(
            "SELECT COUNT(*) n, COALESCE(SUM(total),0) t, COALESCE(SUM(total-total_devuelto),0) neto,"
            " COALESCE(SUM(utilidad-utilidad_devuelta),0) u, COALESCE(SUM(descuento),0) d,"
            " SUM(CASE WHEN estado<>'ANULADO' THEN 1 ELSE 0 END) validas"
            " FROM ventas WHERE fecha BETWEEN ? AND ?", (desde, hasta))
        d = self.q1("SELECT COUNT(*) n, COALESCE(SUM(monto),0) t FROM devoluciones WHERE fecha BETWEEN ? AND ?",
                    (desde, hasta))
        unidades = self.q1(
            "SELECT COALESCE(SUM(dv.cantidad - dv.cantidad_devuelta),0) FROM detalle_ventas dv"
            " JOIN ventas v ON v.id=dv.venta_id WHERE v.fecha BETWEEN ? AND ?", (desde, hasta))[0]
        caja = self.caja_abierta()
        validas = v["validas"] or 0
        return {
            "num_ventas": validas,
            "num_anuladas": v["n"] - validas,
            "ventas_brutas": v["t"],
            "ventas_netas": v["neto"],
            "devoluciones": d["t"],
            "num_devoluciones": d["n"],
            "utilidad_neta": v["u"],
            "descuentos": v["d"],
            "unidades": unidades,
            "ticket_promedio": (v["neto"] // validas) if validas else 0,
            "efectivo_esperado": self.resumen_caja(caja["id"])["efectivo_esperado"] if caja else 0,
            "caja_abierta": 1 if caja else 0,
        }

    def ventas_por_hora(self, desde: str, hasta: str) -> dict[int, int]:
        horas = {h: 0 for h in range(24)}
        for r in self.q("SELECT CAST(strftime('%H', fecha) AS INTEGER) h, SUM(total-total_devuelto) t FROM ventas"
                        " WHERE fecha BETWEEN ? AND ? GROUP BY h", (desde, hasta)):
            horas[r["h"]] = r["t"]
        return horas

    def ventas_por_metodo(self, desde: str, hasta: str) -> dict[str, int]:
        d = {m: 0 for m in cfg.METODOS_PAGO}
        for r in self.q("SELECT metodo_pago, SUM(total-total_devuelto) t FROM ventas WHERE fecha BETWEEN ? AND ?"
                        " GROUP BY metodo_pago", (desde, hasta)):
            d[r["metodo_pago"]] = r["t"]
        return d

    def top_productos(self, desde: str, hasta: str, n: int = 5) -> list[sqlite3.Row]:
        return self.q(
            "SELECT dv.codigo, dv.nombre, SUM(dv.cantidad-dv.cantidad_devuelta) unidades,"
            " SUM(dv.total-dv.total_devuelto) ingresos, SUM(dv.utilidad-dv.utilidad_devuelta) utilidad"
            " FROM detalle_ventas dv JOIN ventas v ON v.id=dv.venta_id WHERE v.fecha BETWEEN ? AND ?"
            " GROUP BY dv.codigo, dv.nombre HAVING unidades > 0 ORDER BY unidades DESC, ingresos DESC LIMIT ?",
            (desde, hasta, n))

    def ventas_periodo(self, desde: str, hasta: str) -> list[sqlite3.Row]:
        return self.q(
            "SELECT numero_tiquete, fecha, cajero, cliente_documento, cliente_nombre, metodo_pago, subtotal,"
            " descuento, total, total_devuelto, total-total_devuelto AS neto, costo_total,"
            " utilidad-utilidad_devuelta AS utilidad, monto_recibido, cambio, estado"
            " FROM ventas WHERE fecha BETWEEN ? AND ? ORDER BY fecha", (desde, hasta))

    def detalle_periodo(self, desde: str, hasta: str) -> list[sqlite3.Row]:
        return self.q(
            "SELECT v.numero_tiquete, v.fecha, dv.codigo, dv.referencia, dv.nombre, dv.categoria,"
            " dv.cantidad, dv.cantidad_devuelta, dv.precio_unitario, dv.costo_unitario, dv.descuento, dv.total,"
            " dv.total_devuelto, dv.utilidad - dv.utilidad_devuelta AS utilidad, v.metodo_pago, v.estado"
            " FROM detalle_ventas dv JOIN ventas v ON v.id=dv.venta_id"
            " WHERE v.fecha BETWEEN ? AND ? ORDER BY v.fecha, dv.id", (desde, hasta))

    def devoluciones_periodo(self, desde: str, hasta: str) -> list[sqlite3.Row]:
        return self.q(
            "SELECT d.numero, v.numero_tiquete, d.fecha, d.tipo, d.metodo_pago, d.monto, d.motivo, d.cajero,"
            " (SELECT GROUP_CONCAT(dd.cantidad || '× ' || dd.nombre, '; ') FROM detalle_devoluciones dd"
            "  WHERE dd.devolucion_id=d.id) AS prendas"
            " FROM devoluciones d JOIN ventas v ON v.id=d.venta_id"
            " WHERE d.fecha BETWEEN ? AND ? ORDER BY d.fecha", (desde, hasta))


def crear_bd_limpia(destino: Path) -> Path:
    """Genera una base de datos vacía con esquema + configuración (usada por build_app.py)."""
    destino = Path(destino)
    if destino.exists():
        destino.unlink()
    db = Database(destino)
    db.conn.execute("PRAGMA journal_mode=DELETE")  # archivo único, sin -wal/-shm
    db.close()
    return destino
