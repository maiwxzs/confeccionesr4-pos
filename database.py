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

SCHEMA_VERSION = 5

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
    observaciones       TEXT    NOT NULL DEFAULT '',
    total_entradas      INTEGER NOT NULL DEFAULT 0,
    total_salidas       INTEGER NOT NULL DEFAULT 0,
    conciliacion        TEXT    NOT NULL DEFAULT '', -- JSON: datáfono / Nequi / Daviplata declarado vs sistema
    reaperturas         INTEGER NOT NULL DEFAULT 0,
    por_metodo          TEXT    NOT NULL DEFAULT '', -- JSON {metodo: neto} (v5: Bre-B, pago mixto...)
    total_abonos        INTEGER NOT NULL DEFAULT 0,
    aperturas_cajon     INTEGER NOT NULL DEFAULT 0
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

-- Entradas / salidas de efectivo del cajón (gastos, retiros, cambio de sencillo...)
CREATE TABLE IF NOT EXISTS movimientos_caja (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    caja_id         INTEGER NOT NULL REFERENCES cierres_caja(id),
    fecha           TEXT    NOT NULL,
    tipo            TEXT    NOT NULL CHECK (tipo IN ('ENTRADA','SALIDA')),
    concepto        TEXT    NOT NULL,
    monto           INTEGER NOT NULL CHECK (monto > 0),
    cajero          TEXT    NOT NULL DEFAULT '',
    anulado         INTEGER NOT NULL DEFAULT 0,
    anulado_motivo  TEXT    NOT NULL DEFAULT '',
    anulado_fecha   TEXT
);
CREATE INDEX IF NOT EXISTS ix_mov_caja ON movimientos_caja(caja_id);

-- Importaciones de inventario (permite deshacer la última)
CREATE TABLE IF NOT EXISTS importaciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT    NOT NULL,
    modo        TEXT    NOT NULL,
    nuevos      INTEGER NOT NULL DEFAULT 0,
    actualizados INTEGER NOT NULL DEFAULT 0,
    instantanea TEXT    NOT NULL,                   -- JSON: estado previo de cada producto tocado
    deshecha    INTEGER NOT NULL DEFAULT 0
);

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

-- v5 · Separados (apartados) con abonos: la mercancía queda reservada hasta pagarla completa
CREATE TABLE IF NOT EXISTS separados (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    numero             TEXT    NOT NULL UNIQUE,     -- SEP-000001
    fecha              TEXT    NOT NULL,
    cliente_documento  TEXT    NOT NULL,
    cliente_nombre     TEXT    NOT NULL DEFAULT '',
    telefono           TEXT    NOT NULL DEFAULT '',
    subtotal           INTEGER NOT NULL,
    descuento          INTEGER NOT NULL DEFAULT 0,
    total              INTEGER NOT NULL,
    abonado            INTEGER NOT NULL DEFAULT 0,
    fecha_limite       TEXT    NOT NULL DEFAULT '',
    estado             TEXT    NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','ENTREGADO','ANULADO')),
    venta_id           INTEGER REFERENCES ventas(id),
    cajero             TEXT    NOT NULL DEFAULT '',
    observaciones      TEXT    NOT NULL DEFAULT '',
    cierre_fecha       TEXT,
    cierre_motivo      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_separados_cliente ON separados(cliente_documento);
CREATE INDEX IF NOT EXISTS ix_separados_estado  ON separados(estado);

CREATE TABLE IF NOT EXISTS detalle_separados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    separado_id     INTEGER NOT NULL REFERENCES separados(id),
    producto_id     INTEGER REFERENCES productos(id),
    codigo          TEXT    NOT NULL,
    referencia      TEXT    NOT NULL DEFAULT '',
    nombre          TEXT    NOT NULL,
    categoria       TEXT    NOT NULL DEFAULT '',
    cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario INTEGER NOT NULL,
    costo_unitario  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_detsep ON detalle_separados(separado_id);

-- v5 · Todo el dinero que entra o sale, por método (permite pago mixto). Positivo = entra.
CREATE TABLE IF NOT EXISTS pagos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    caja_id        INTEGER NOT NULL REFERENCES cierres_caja(id),
    fecha          TEXT    NOT NULL,
    origen         TEXT    NOT NULL CHECK (origen IN ('VENTA','DEVOLUCION','ABONO','REEMBOLSO_SEPARADO')),
    metodo         TEXT    NOT NULL,
    monto          INTEGER NOT NULL,
    venta_id       INTEGER REFERENCES ventas(id),
    devolucion_id  INTEGER REFERENCES devoluciones(id),
    separado_id    INTEGER REFERENCES separados(id),
    cajero         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_pagos_caja     ON pagos(caja_id);
CREATE INDEX IF NOT EXISTS ix_pagos_venta    ON pagos(venta_id);
CREATE INDEX IF NOT EXISTS ix_pagos_separado ON pagos(separado_id);
CREATE INDEX IF NOT EXISTS ix_pagos_fecha    ON pagos(fecha);

-- v5 · Cada vez que se abre el cajón monedero (venta, devolución, abono o apertura manual)
CREATE TABLE IF NOT EXISTS aperturas_cajon (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT    NOT NULL,
    caja_id     INTEGER,
    origen      TEXT    NOT NULL,                   -- VENTA, DEVOLUCION, ABONO, CAMBIO, MANUAL, PRUEBA
    referencia  TEXT    NOT NULL DEFAULT '',
    motivo      TEXT    NOT NULL DEFAULT '',
    cajero      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_cajon_caja ON aperturas_cajon(caja_id);

-- v5 · Ajustes de stock por conteo físico (antes / después / motivo)
CREATE TABLE IF NOT EXISTS ajustes_inventario (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT    NOT NULL,
    producto_id INTEGER REFERENCES productos(id),
    codigo      TEXT    NOT NULL,
    nombre      TEXT    NOT NULL,
    antes       INTEGER NOT NULL,
    despues     INTEGER NOT NULL,
    diferencia  INTEGER NOT NULL,
    origen      TEXT    NOT NULL DEFAULT 'CONTEO',
    motivo      TEXT    NOT NULL DEFAULT '',
    cajero      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_ajustes_fecha ON ajustes_inventario(fecha);
"""

# Documentos inmutables: nada se borra y los valores de un tiquete emitido no cambian.
TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS tr_ventas_no_borrar BEFORE DELETE ON ventas
BEGIN SELECT RAISE(ABORT, 'Los tiquetes no se pueden borrar: use Anular / Devolución'); END;
CREATE TRIGGER IF NOT EXISTS tr_ventas_inmutable_v4
BEFORE UPDATE OF numero_tiquete, consecutivo, fecha, caja_id, subtotal, descuento, total, costo_total,
                 utilidad, monto_recibido, cambio ON ventas
BEGIN SELECT RAISE(ABORT, 'Un tiquete emitido no se puede modificar'); END;
-- El método de pago solo se corrige mientras la caja de esa venta siga abierta
CREATE TRIGGER IF NOT EXISTS tr_ventas_metodo
BEFORE UPDATE OF metodo_pago ON ventas
WHEN (SELECT estado FROM cierres_caja WHERE id = OLD.caja_id) = 'CERRADA'
BEGIN SELECT RAISE(ABORT, 'No se puede cambiar el método de pago de una caja ya cerrada'); END;
CREATE TRIGGER IF NOT EXISTS tr_mov_no_borrar BEFORE DELETE ON movimientos_caja
BEGIN SELECT RAISE(ABORT, 'Los movimientos de caja no se borran: se anulan'); END;
CREATE TRIGGER IF NOT EXISTS tr_mov_inmutable
BEFORE UPDATE OF caja_id, fecha, tipo, concepto, monto ON movimientos_caja
BEGIN SELECT RAISE(ABORT, 'Un movimiento de caja no se puede modificar: anúlelo y regístrelo de nuevo'); END;
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
-- Un cierre solo admite REABRIRSE (con clave y auditoría), nunca editarse
CREATE TRIGGER IF NOT EXISTS tr_cierre_cerrado BEFORE UPDATE ON cierres_caja
WHEN OLD.estado = 'CERRADA' AND NEW.estado <> 'ABIERTA'
BEGIN SELECT RAISE(ABORT, 'Un cierre de caja no se puede modificar'); END;
-- Solo se puede borrar una apertura hecha por error: abierta y sin ningún movimiento
CREATE TRIGGER IF NOT EXISTS tr_cierre_borrar_v5 BEFORE DELETE ON cierres_caja
WHEN OLD.estado = 'CERRADA'
  OR EXISTS (SELECT 1 FROM ventas WHERE caja_id = OLD.id)
  OR EXISTS (SELECT 1 FROM devoluciones WHERE caja_id = OLD.id)
  OR EXISTS (SELECT 1 FROM movimientos_caja WHERE caja_id = OLD.id)
  OR EXISTS (SELECT 1 FROM pagos WHERE caja_id = OLD.id)
BEGIN SELECT RAISE(ABORT, 'Esta caja tiene movimientos: no se puede borrar'); END;
CREATE TRIGGER IF NOT EXISTS tr_pagos_no_borrar BEFORE DELETE ON pagos
BEGIN SELECT RAISE(ABORT, 'Los pagos no se pueden borrar'); END;
CREATE TRIGGER IF NOT EXISTS tr_pagos_inmutable
BEFORE UPDATE OF caja_id, fecha, origen, monto, venta_id, devolucion_id, separado_id ON pagos
BEGIN SELECT RAISE(ABORT, 'Un pago registrado no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS tr_pagos_metodo BEFORE UPDATE OF metodo ON pagos
WHEN (SELECT estado FROM cierres_caja WHERE id = OLD.caja_id) = 'CERRADA'
BEGIN SELECT RAISE(ABORT, 'No se puede cambiar el método de pago de una caja ya cerrada'); END;
CREATE TRIGGER IF NOT EXISTS tr_sep_no_borrar BEFORE DELETE ON separados
BEGIN SELECT RAISE(ABORT, 'Los separados no se borran: se anulan'); END;
CREATE TRIGGER IF NOT EXISTS tr_sep_inmutable
BEFORE UPDATE OF numero, fecha, subtotal, descuento, total ON separados
BEGIN SELECT RAISE(ABORT, 'Un separado no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS tr_detsep_no_borrar BEFORE DELETE ON detalle_separados
BEGIN SELECT RAISE(ABORT, 'El detalle de un separado no se puede borrar'); END;
CREATE TRIGGER IF NOT EXISTS tr_cajon_no_borrar BEFORE DELETE ON aperturas_cajon
BEGIN SELECT RAISE(ABORT, 'El registro del cajón es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS tr_cajon_no_editar BEFORE UPDATE ON aperturas_cajon
BEGIN SELECT RAISE(ABORT, 'El registro del cajón es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS tr_ajuste_no_borrar BEFORE DELETE ON ajustes_inventario
BEGIN SELECT RAISE(ABORT, 'Los ajustes de inventario no se borran'); END;
CREATE TRIGGER IF NOT EXISTS tr_ajuste_no_editar BEFORE UPDATE ON ajustes_inventario
BEGIN SELECT RAISE(ABORT, 'Los ajustes de inventario no se modifican'); END;
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
            for viejo in ("tr_ventas_inmutable", "tr_cierre_no_reabrir", "tr_cierre_no_borrar", "tr_cierre_borrar"):
                self.conn.execute(f"DROP TRIGGER IF EXISTS {viejo}")
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
            # v3 -> v4: columnas de cierre (movimientos y conciliación)
            cier = self._columnas("cierres_caja")
            for col, ddl in (("total_entradas", "INTEGER NOT NULL DEFAULT 0"), ("total_salidas", "INTEGER NOT NULL DEFAULT 0"),
                             ("conciliacion", "TEXT NOT NULL DEFAULT ''"), ("reaperturas", "INTEGER NOT NULL DEFAULT 0"),
                             ("por_metodo", "TEXT NOT NULL DEFAULT ''"), ("total_abonos", "INTEGER NOT NULL DEFAULT 0"),
                             ("aperturas_cajon", "INTEGER NOT NULL DEFAULT 0")):
                if col not in cier:
                    c.execute(f"ALTER TABLE cierres_caja ADD COLUMN {col} {ddl}")
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
            # v4 -> v5: cada venta y devolución anterior pasa a la tabla de pagos (un solo método)
            if not c.execute("SELECT 1 FROM pagos LIMIT 1").fetchone():
                c.execute("INSERT INTO pagos(caja_id, fecha, origen, metodo, monto, venta_id, cajero)"
                          " SELECT caja_id, fecha, 'VENTA', metodo_pago, total, id, cajero FROM ventas")
                c.execute("INSERT INTO pagos(caja_id, fecha, origen, metodo, monto, venta_id, devolucion_id, cajero)"
                          " SELECT caja_id, fecha, 'DEVOLUCION', metodo_pago, -monto, venta_id, id, cajero"
                          " FROM devoluciones")
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
        instantanea: list[dict[str, Any]] = []
        campos_snap = ("referencia", "nombre", "categoria", "costo", "precio", "stock", "marca", "color", "talla",
                       "stock_minimo", "datos_origen", "activo")
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
                    previo = c.execute("SELECT * FROM productos WHERE id=?", (existente["id"],)).fetchone()
                    instantanea.append({"id": previo["id"], "antes": {k: previo[k] for k in campos_snap}})
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
                    cur = c.execute(
                        "INSERT INTO productos(codigo, referencia, nombre, categoria, costo, precio, stock,"
                        " marca, color, talla, stock_minimo, datos_origen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (codigo, ref, f["nombre"], f.get("categoria") or "General",
                         int(f.get("costo") or 0), int(f.get("precio") or 0), int(f.get("stock") or 0), *extra),
                    )
                    instantanea.append({"id": cur.lastrowid, "antes": None})
                    nuevos += 1
            if encabezados_origen:
                c.execute("INSERT INTO configuracion(clave, valor) VALUES('formato_carga_columnas', ?)"
                          " ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                          (json.dumps(list(encabezados_origen), ensure_ascii=False),))
            c.execute("INSERT INTO importaciones(fecha, modo, nuevos, actualizados, instantanea) VALUES (?,?,?,?,?)",
                      (_ahora(), modo_stock, nuevos, actualizados, json.dumps(instantanea, ensure_ascii=False)))
            self.auditar("IMPORTACION_INVENTARIO", "productos", "",
                         {"nuevos": nuevos, "actualizados": actualizados, "modo_stock": modo_stock,
                          "unidades": sum(int(f.get("stock") or 0) for f in filas)}, c)
        return {"nuevos": nuevos, "actualizados": actualizados}

    def ultima_importacion(self) -> sqlite3.Row | None:
        return self.q1("SELECT id, fecha, modo, nuevos, actualizados, deshecha FROM importaciones"
                       " ORDER BY id DESC LIMIT 1")

    def deshacer_importacion(self) -> dict[str, int]:
        """Revierte la ÚLTIMA importación: restaura los productos que modificó y elimina los que creó
        (los que ya tienen ventas no se borran: se desactivan, para no romper el historial)."""
        imp = self.q1("SELECT * FROM importaciones ORDER BY id DESC LIMIT 1")
        if imp is None or imp["deshecha"]:
            raise POSError("No hay ninguna importación pendiente por deshacer.")
        restaurados = eliminados = desactivados = 0
        with self.transaccion() as c:
            for item in json.loads(imp["instantanea"]):
                pid, antes = item["id"], item["antes"]
                if antes is None:
                    usado = c.execute("SELECT 1 FROM detalle_ventas WHERE producto_id=? LIMIT 1", (pid,)).fetchone()
                    if usado:
                        c.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))
                        desactivados += 1
                    else:
                        c.execute("DELETE FROM productos WHERE id=?", (pid,))
                        eliminados += 1
                else:
                    sets = ", ".join(f"{k}=?" for k in antes)
                    c.execute(f"UPDATE productos SET {sets}, actualizado=datetime('now','localtime') WHERE id=?",
                              (*antes.values(), pid))
                    restaurados += 1
            c.execute("UPDATE importaciones SET deshecha=1 WHERE id=?", (imp["id"],))
            r = {"restaurados": restaurados, "eliminados": eliminados, "desactivados": desactivados}
            self.auditar("IMPORTACION_DESHECHA", "importaciones", imp["id"], r, c)
        return r

    def eliminar_producto(self, pid: int) -> None:
        """Borra un producto creado por error. Si ya se vendió, no se puede (se debe desactivar)."""
        p = self.producto_por_id(pid)
        if p is None:
            raise POSError("El producto no existe.")
        if self.q1("SELECT 1 FROM detalle_ventas WHERE producto_id=? LIMIT 1", (pid,)):
            raise POSError(f"«{p['nombre']}» ya tiene ventas registradas: no se puede borrar sin dañar el "
                           "historial. Use «Desactivar» para ocultarlo del punto de venta.")
        with self.transaccion() as c:
            c.execute("DELETE FROM productos WHERE id=?", (pid,))
            self.auditar("PRODUCTO_ELIMINADO", "productos", pid, dict(p), c)

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

    def _por_metodo_caja(self, caja_id: int) -> dict[str, dict[str, int]]:
        """Dinero del turno por método, sumando la tabla de pagos (ventas, devoluciones, abonos)."""
        vacio = lambda: {"cantidad": 0, "total": 0, "devuelto": 0, "abonos": 0, "reembolsos": 0, "neto": 0}  # noqa: E731
        por_metodo = {m: vacio() for m in cfg.METODOS_PAGO}
        for r in self.q("SELECT metodo, origen, COUNT(*) n, COALESCE(SUM(monto),0) t FROM pagos"
                        " WHERE caja_id=? GROUP BY metodo, origen", (caja_id,)):
            if r["metodo"] == cfg.METODO_CREDITO:
                continue                      # crédito interno de un cambio de prenda: no es dinero
            d = por_metodo.setdefault(r["metodo"], vacio())
            if r["origen"] == "VENTA":
                d["cantidad"] += r["n"]
                d["total"] += r["t"]
            elif r["origen"] == "DEVOLUCION":
                d["devuelto"] += -r["t"]
            elif r["origen"] == "ABONO":
                d["abonos"] += r["t"]
            else:
                d["reembolsos"] += -r["t"]
        for d in por_metodo.values():
            d["neto"] = d["total"] - d["devuelto"] + d["abonos"] - d["reembolsos"]
        return por_metodo

    def resumen_caja(self, caja_id: int) -> dict[str, Any]:
        """Totales acumulados del turno (en vivo). Las devoluciones restan del método en que se reintegraron."""
        caja = self.q1("SELECT * FROM cierres_caja WHERE id=?", (caja_id,))
        if caja is None:
            raise POSError("Caja no encontrada.")
        por_metodo = self._por_metodo_caja(caja_id)
        tot = self.q1("SELECT COUNT(*) n, COALESCE(SUM(total),0) t, COALESCE(SUM(utilidad),0) u"
                      " FROM ventas WHERE caja_id=?", (caja_id,))
        dev = self.q1("SELECT COALESCE(SUM(monto),0) t, COALESCE(SUM(utilidad),0) u, COUNT(*) n"
                      " FROM devoluciones WHERE caja_id=?", (caja_id,))
        mov = self.q1("SELECT COALESCE(SUM(CASE WHEN tipo='ENTRADA' THEN monto END),0) e,"
                      " COALESCE(SUM(CASE WHEN tipo='SALIDA' THEN monto END),0) s"
                      " FROM movimientos_caja WHERE caja_id=? AND anulado=0", (caja_id,))
        ab = self.q1("SELECT COALESCE(SUM(CASE WHEN origen='ABONO' THEN monto END),0) a,"
                     " COALESCE(SUM(CASE WHEN origen='REEMBOLSO_SEPARADO' THEN -monto END),0) r,"
                     " COUNT(CASE WHEN origen='ABONO' THEN 1 END) n FROM pagos WHERE caja_id=?", (caja_id,))
        cajon = self.q1("SELECT COUNT(*) FROM aperturas_cajon WHERE caja_id=?", (caja_id,))[0]
        ef = por_metodo.get("Efectivo", {"neto": 0, "devuelto": 0})
        return {
            "caja": dict(caja),
            "por_metodo": por_metodo,
            "entradas": mov["e"],
            "salidas": mov["s"],
            "num_ventas": tot["n"],
            "total_ventas": tot["t"],
            "ventas_netas": tot["t"] - dev["t"],
            "utilidad": tot["u"] - dev["u"],
            "total_devoluciones": dev["t"],
            "num_devoluciones": dev["n"],
            "devoluciones_efectivo": ef["devuelto"],
            "abonos": ab["a"],
            "num_abonos": ab["n"],
            "reembolsos_separados": ab["r"],
            "aperturas_cajon": cajon,
            "efectivo_esperado": caja["base_inicial"] + ef["neto"] + mov["e"] - mov["s"],
        }

    def cerrar_caja(self, efectivo_contado: int, denominaciones: dict[int, int] | None = None,
                    observaciones: str = "", declarados: dict[str, int | None] | None = None) -> dict[str, Any]:
        """
        Arqueo ciego: compara el efectivo contado contra el esperado y cierra el turno.
        declarados: totales que reporta el cierre del datáfono y las apps de Nequi/Bre-B
        (dinero que NO entra al cajón: va directo al banco). Se concilian contra el sistema.
        """
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("No hay ninguna caja abierta.")
        if int(efectivo_contado) < 0:
            raise POSError("El efectivo contado no puede ser negativo.")
        res = self.resumen_caja(caja["id"])
        conciliacion = {}
        for m, valor in (declarados or {}).items():
            if valor is None or m == "Efectivo":
                continue
            sistema = res["por_metodo"].get(m, {}).get("neto", 0)
            conciliacion[m] = {"sistema": sistema, "declarado": int(valor), "diferencia": int(valor) - sistema}
        esperado = res["efectivo_esperado"]
        diferencia = int(efectivo_contado) - esperado
        resultado = "CUADRE PERFECTO" if diferencia == 0 else ("SOBRANTE" if diferencia > 0 else "FALTANTE")
        pm = res["por_metodo"]
        g = lambda m: pm.get(m, {}).get("total", 0)  # noqa: E731
        with self.transaccion() as c:
            c.execute(
                "UPDATE cierres_caja SET estado='CERRADA', fecha_cierre=?, num_ventas=?, total_ventas=?,"
                " total_efectivo=?, total_nequi=?, total_daviplata=?, total_datafono=?,"
                " total_devoluciones=?, devoluciones_efectivo=?, utilidad=?, efectivo_esperado=?,"
                " efectivo_contado=?, diferencia=?, resultado=?, denominaciones=?, observaciones=?,"
                " total_entradas=?, total_salidas=?, conciliacion=?, por_metodo=?, total_abonos=?,"
                " aperturas_cajon=? WHERE id=?",
                (_ahora(), res["num_ventas"], res["total_ventas"], g("Efectivo"), g("Nequi"), g("Daviplata"),
                 g("Datáfono"), res["total_devoluciones"], res["devoluciones_efectivo"], res["utilidad"], esperado,
                 int(efectivo_contado), diferencia, resultado,
                 json.dumps({str(k): v for k, v in (denominaciones or {}).items() if v}),
                 observaciones, res["entradas"], res["salidas"], json.dumps(conciliacion, ensure_ascii=False),
                 json.dumps(pm, ensure_ascii=False), res["abonos"], res["aperturas_cajon"], caja["id"]),
            )
            self.auditar("CAJA_CERRADA", "cierres_caja", caja["id"],
                         {"esperado": esperado, "contado": int(efectivo_contado), "resultado": resultado}, c)
        return self.obtener_cierre(caja["id"])

    def obtener_cierre(self, caja_id: int) -> dict[str, Any] | None:
        r = self.q1("SELECT * FROM cierres_caja WHERE id=?", (caja_id,))
        if r is None:
            return None
        d = dict(r)
        try:
            pm = json.loads(d.get("por_metodo") or "{}")
        except ValueError:
            pm = {}
        if not pm:                               # cierres anteriores a la v1.1
            pm = {"Efectivo": {"total": d["total_efectivo"]}, "Nequi": {"total": d["total_nequi"]},
                  "Daviplata": {"total": d["total_daviplata"]}, "Datáfono": {"total": d["total_datafono"]}}
        for m in cfg.METODOS_PAGO:
            pm.setdefault(m, {"total": 0})
        d["por_metodo"] = pm
        try:
            d["conciliacion"] = json.loads(d.get("conciliacion") or "{}")
        except ValueError:
            d["conciliacion"] = {}
        d["movimientos"] = [dict(r) for r in self.movimientos_caja(caja_id)]
        d["cajon"] = {r["origen"]: r["n"] for r in self.q(
            "SELECT origen, COUNT(*) n FROM aperturas_cajon WHERE caja_id=? GROUP BY origen", (caja_id,))}
        return d

    # ---------- Correcciones de caja ----------
    def movimientos_caja(self, caja_id: int) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM movimientos_caja WHERE caja_id=? ORDER BY id", (caja_id,))

    def registrar_movimiento(self, tipo: str, monto: int, concepto: str, cajero: str = "") -> int:
        """Entrada o salida de efectivo del cajón (pago a proveedor, gasto, retiro, cambio de sencillo)."""
        tipo = tipo.upper()
        concepto = (concepto or "").strip()
        if tipo not in ("ENTRADA", "SALIDA"):
            raise POSError("Tipo de movimiento no válido.")
        if int(monto) <= 0:
            raise POSError("El valor debe ser mayor que cero.")
        if len(concepto) < 3:
            raise POSError("Escriba el concepto (por ejemplo: «Pago domicilio», «Retiro dueño»).")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe haber una caja abierta.")
        if tipo == "SALIDA" and int(monto) > self.resumen_caja(caja["id"])["efectivo_esperado"]:
            raise POSError("No hay suficiente efectivo en el cajón para esa salida.")
        with self.transaccion() as c:
            cur = c.execute("INSERT INTO movimientos_caja(caja_id, fecha, tipo, concepto, monto, cajero)"
                            " VALUES (?,?,?,?,?,?)", (caja["id"], _ahora(), tipo, concepto, int(monto), cajero))
            self.auditar(f"CAJA_{tipo}", "movimientos_caja", cur.lastrowid, {"monto": int(monto), "concepto": concepto}, c)
            return int(cur.lastrowid)

    def anular_movimiento(self, mov_id: int, motivo: str) -> None:
        motivo = (motivo or "").strip()
        if not motivo:
            raise POSError("Indique el motivo de la anulación.")
        m = self.q1("SELECT m.*, c.estado FROM movimientos_caja m JOIN cierres_caja c ON c.id=m.caja_id WHERE m.id=?",
                    (mov_id,))
        if m is None:
            raise POSError("Movimiento no encontrado.")
        if m["anulado"]:
            raise POSError("Ese movimiento ya está anulado.")
        if m["estado"] != "ABIERTA":
            raise POSError("Solo se anulan movimientos de la caja abierta.")
        with self.transaccion() as c:
            c.execute("UPDATE movimientos_caja SET anulado=1, anulado_motivo=?, anulado_fecha=? WHERE id=?",
                      (motivo, _ahora(), mov_id))
            self.auditar("CAJA_MOVIMIENTO_ANULADO", "movimientos_caja", mov_id,
                         {"tipo": m["tipo"], "monto": m["monto"], "concepto": m["concepto"], "motivo": motivo}, c)

    def corregir_base(self, nueva_base: int, motivo: str) -> None:
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("No hay caja abierta.")
        if int(nueva_base) < 0:
            raise POSError("La base no puede ser negativa.")
        if not (motivo or "").strip():
            raise POSError("Indique el motivo de la corrección.")
        with self.transaccion() as c:
            c.execute("UPDATE cierres_caja SET base_inicial=? WHERE id=?", (int(nueva_base), caja["id"]))
            self.auditar("CAJA_BASE_CORREGIDA", "cierres_caja", caja["id"],
                         {"antes": caja["base_inicial"], "despues": int(nueva_base), "motivo": motivo}, c)

    def anular_apertura(self, motivo: str) -> None:
        """Elimina una apertura hecha por error (solo si la caja no tiene ningún movimiento)."""
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("No hay caja abierta.")
        try:
            with self.transaccion() as c:
                c.execute("DELETE FROM cierres_caja WHERE id=?", (caja["id"],))
                self.auditar("CAJA_APERTURA_ANULADA", "cierres_caja", caja["id"],
                             {"base": caja["base_inicial"], "cajero": caja["cajero"], "motivo": motivo}, c)
        except sqlite3.IntegrityError as e:
            raise POSError("Esta caja ya tiene ventas o movimientos: no se puede anular la apertura. "
                           "Corrija la base o haga el cierre normal.") from e

    def reabrir_caja(self, caja_id: int, motivo: str) -> None:
        """Reabre el ÚLTIMO cierre (p. ej. se cerró por error o faltó registrar algo)."""
        if self.caja_abierta():
            raise POSError("Ya hay una caja abierta: ciérrela o anule su apertura primero.")
        ultima = self.q1("SELECT * FROM cierres_caja ORDER BY id DESC LIMIT 1")
        if ultima is None or ultima["id"] != caja_id:
            raise POSError("Solo se puede reabrir el cierre más reciente.")
        if not (motivo or "").strip():
            raise POSError("Indique el motivo de la reapertura.")
        with self.transaccion() as c:
            c.execute("UPDATE cierres_caja SET estado='ABIERTA', fecha_cierre=NULL, efectivo_contado=NULL,"
                      " efectivo_esperado=NULL, diferencia=NULL, resultado=NULL, reaperturas=reaperturas+1"
                      " WHERE id=?", (caja_id,))
            self.auditar("CAJA_REABIERTA", "cierres_caja", caja_id,
                         {"resultado_previo": ultima["resultado"], "contado_previo": ultima["efectivo_contado"],
                          "motivo": motivo}, c)

    # ---------- Clave de administrador ----------
    def tiene_pin(self) -> bool:
        return bool(self.get_config("pin_admin", ""))

    def definir_pin(self, pin: str, pin_actual: str | None = None) -> None:
        pin = (pin or "").strip()
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise POSError("La clave debe tener entre 4 y 8 dígitos.")
        if self.tiene_pin() and not self.verificar_pin(pin_actual or ""):
            raise POSError("La clave actual no es correcta.")
        import hashlib
        import secrets
        sal = secrets.token_hex(8)
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), sal.encode(), 120_000).hex()
        self.set_config({"pin_admin": f"{sal}${h}"}, auditar=False)
        self.auditar("CLAVE_ADMIN_DEFINIDA", "configuracion")

    def verificar_pin(self, pin: str) -> bool:
        guardado = self.get_config("pin_admin", "")
        if not guardado:
            return True
        import hashlib
        import hmac
        sal, h = guardado.split("$", 1)
        calc = hashlib.pbkdf2_hmac("sha256", (pin or "").strip().encode(), sal.encode(), 120_000).hex()
        return hmac.compare_digest(calc, h)

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

    # ------------------------------------------------------------------ #
    # Pagos (un método o mixto)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalizar_pagos(pagos: list[tuple[str, int]], total: int) -> tuple[list[tuple[str, int]], int, int, str]:
        """
        pagos: [(método, valor)]. El valor del efectivo es lo que ENTREGÓ el cliente (puede haber cambio);
        los demás métodos deben ser exactos. Devuelve (pagos aplicados, recibido, cambio, etiqueta).
        """
        limpio: dict[str, int] = {}
        for metodo, valor in pagos or []:
            valor = int(valor or 0)
            if valor <= 0:
                continue
            if metodo not in cfg.METODOS_PAGO:
                raise POSError(f"Método de pago no válido: {metodo}")
            limpio[metodo] = limpio.get(metodo, 0) + valor
        if not limpio:
            raise POSError("Indique cómo pagó el cliente.")
        efectivo = limpio.pop("Efectivo", 0)
        otros = sum(limpio.values())
        if otros > total:
            raise POSError(f"Lo pagado por {', '.join(limpio)} ({cfg.cop(otros)}) supera el total ({cfg.cop(total)}).")
        efectivo_aplicado = total - otros
        if efectivo < efectivo_aplicado:
            raise POSError(f"Faltan {cfg.cop(efectivo_aplicado - efectivo)} por pagar.")
        if efectivo and efectivo_aplicado == 0:
            raise POSError("Con los otros medios ya se completó el total: no hace falta efectivo.")
        aplicados = ([("Efectivo", efectivo_aplicado)] if efectivo_aplicado else []) + list(limpio.items())
        cambio = efectivo - efectivo_aplicado
        etiqueta = aplicados[0][0] if len(aplicados) == 1 else "Mixto"
        return aplicados, otros + efectivo, cambio, etiqueta

    def _insertar_pagos(self, c: sqlite3.Connection, caja_id: int, origen: str, pagos: list[tuple[str, int]],
                        cajero: str, venta_id: int | None = None, devolucion_id: int | None = None,
                        separado_id: int | None = None, signo: int = 1) -> None:
        fecha = _ahora()
        c.executemany("INSERT INTO pagos(caja_id, fecha, origen, metodo, monto, venta_id, devolucion_id, separado_id,"
                      " cajero) VALUES (?,?,?,?,?,?,?,?,?)",
                      [(caja_id, fecha, origen, m, signo * int(v), venta_id, devolucion_id, separado_id, cajero)
                       for m, v in pagos if int(v)])

    # ------------------------------------------------------------------ #
    # Ventas
    # ------------------------------------------------------------------ #
    def _lineas_desde_productos(self, c: sqlite3.Connection, items: list[ItemCarrito],
                                permitir_negativo: bool) -> list[dict[str, Any]]:
        lineas = []
        for it in items:
            if it.cantidad <= 0:
                continue
            p = c.execute("SELECT * FROM productos WHERE id=?", (it.producto_id,)).fetchone()
            if p is None:
                raise POSError(f"Producto {it.producto_id} no existe.")
            pedidas = int(it.cantidad) + sum(l["cant"] for l in lineas if l["producto_id"] == p["id"])
            if not permitir_negativo and p["stock"] < pedidas:
                raise POSError(f"Stock insuficiente para «{p['nombre']}».\n"
                               f"Disponible: {p['stock']} · Solicitado: {pedidas}")
            precio = int(it.precio_unitario if it.precio_unitario is not None else p["precio"])
            lineas.append({"producto_id": p["id"], "codigo": p["codigo"], "referencia": p["referencia"],
                           "nombre": p["nombre"], "categoria": p["categoria"], "cant": int(it.cantidad),
                           "precio": precio, "costo_u": int(p["costo"])})
        if not lineas:
            raise POSError("El carrito está vacío.")
        return lineas

    @staticmethod
    def _calcular(lineas: list[dict[str, Any]], descuento: int) -> dict[str, int]:
        """Subtotal, descuento prorrateado en centenas (la última línea absorbe el residuo) y utilidad."""
        for l in lineas:
            l["subtotal"] = l["precio"] * l["cant"]
        subtotal = sum(l["subtotal"] for l in lineas)
        descuento = max(0, min(int(descuento or 0), subtotal))
        restante = descuento
        for i, l in enumerate(lineas):
            if i == len(lineas) - 1:
                l["desc"] = restante
            else:
                propuesto = int(round(descuento * l["subtotal"] / subtotal / 100.0)) * 100 if subtotal else 0
                l["desc"] = max(0, min(propuesto, restante, l["subtotal"]))
                restante -= l["desc"]
            l["total"] = l["subtotal"] - l["desc"]
            l["utilidad"] = l["total"] - l["costo_u"] * l["cant"]
        costo = sum(l["costo_u"] * l["cant"] for l in lineas)
        return {"subtotal": subtotal, "descuento": descuento, "total": subtotal - descuento,
                "costo_total": costo, "utilidad": subtotal - descuento - costo}

    def _insertar_venta(self, c: sqlite3.Connection, caja_id: int, lineas: list[dict[str, Any]],
                        tot: dict[str, int], etiqueta: str, recibido: int, cambio: int,
                        pagos: list[tuple[str, int]], cajero: str, cliente_documento: str, cliente_nombre: str,
                        descontar_stock: bool = True, descripcion: str = "") -> tuple[int, str]:
        consecutivo, numero = self._siguiente_numero(c)
        cur = c.execute(
            "INSERT INTO ventas(numero_tiquete, consecutivo, fecha, caja_id, cajero, cliente_documento,"
            " cliente_nombre, subtotal, descuento, total, costo_total, utilidad, metodo_pago,"
            " monto_recibido, cambio) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (numero, consecutivo, _ahora(), caja_id, cajero, (cliente_documento or "").strip(),
             (cliente_nombre or "").strip().upper(), tot["subtotal"], tot["descuento"], tot["total"],
             tot["costo_total"], tot["utilidad"], etiqueta, int(recibido), int(cambio)))
        venta_id = int(cur.lastrowid)
        c.executemany(
            "INSERT INTO detalle_ventas(venta_id, producto_id, codigo, referencia, nombre, categoria,"
            " cantidad, precio_unitario, costo_unitario, subtotal, descuento, total, utilidad)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(venta_id, l["producto_id"], l["codigo"], l["referencia"], l["nombre"], l["categoria"], l["cant"],
              l["precio"], l["costo_u"], l["subtotal"], l["desc"], l["total"], l["utilidad"]) for l in lineas])
        if descontar_stock:
            c.executemany("UPDATE productos SET stock = stock - ?, actualizado=datetime('now','localtime') WHERE id=?",
                          [(l["cant"], l["producto_id"]) for l in lineas if l["producto_id"]])
        self._insertar_pagos(c, caja_id, "VENTA", pagos, cajero, venta_id=venta_id)
        unidades = sum(l["cant"] for l in lineas)
        detalle_pago = " + ".join(f"{m} {cfg.cop(v)}" for m, v in pagos) if len(pagos) > 1 else etiqueta
        self._evento(c, venta_id, numero, "EMITIDO",
                     descripcion or (f"{unidades} prenda(s) · {detalle_pago}"
                                     + (f" · cambio {cfg.cop(cambio)}" if cambio else "")), tot["total"], cajero)
        return venta_id, numero

    def registrar_venta(self, items: list[ItemCarrito], metodo_pago: str = "Efectivo", descuento: int = 0,
                        monto_recibido: int = 0, cajero: str = "", cliente_documento: str = "",
                        cliente_nombre: str = "", pagos: list[tuple[str, int]] | None = None) -> dict[str, Any]:
        """
        Registra la venta completa en UNA transacción: valida stock, asigna consecutivo, guarda
        encabezado, detalle (costo/precio), pagos por método (uno o varios = pago mixto),
        descuenta inventario y deja el evento EMITIDO en el historial de documentos.
        pagos: [(método, valor)] para pago mixto; si es None se usa metodo_pago + monto_recibido.
        """
        if not items:
            raise POSError("El carrito está vacío.")
        if pagos is None and metodo_pago not in cfg.METODOS_PAGO:
            raise POSError(f"Método de pago no válido: {metodo_pago}")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe ABRIR CAJA antes de registrar ventas.")
        permitir_negativo = self.get_config("permitir_stock_negativo") == "1"
        with self.transaccion() as c:
            lineas = self._lineas_desde_productos(c, items, permitir_negativo)
            tot = self._calcular(lineas, descuento)
            if pagos is None:
                if metodo_pago == "Efectivo":
                    if monto_recibido and monto_recibido < tot["total"]:
                        raise POSError(f"El monto recibido ({cfg.cop(monto_recibido)}) es menor que el total.")
                    pagos = [("Efectivo", int(monto_recibido or tot["total"]))]
                else:
                    pagos = [(metodo_pago, tot["total"])]
            aplicados, recibido, cambio, etiqueta = self._normalizar_pagos(pagos, tot["total"])
            venta_id, _ = self._insertar_venta(c, caja["id"], lineas, tot, etiqueta, recibido, cambio, aplicados,
                                               cajero, cliente_documento, cliente_nombre)
        return self.obtener_venta(venta_id)

    def pagos_de_venta(self, venta_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.q("SELECT metodo, monto, origen, fecha FROM pagos WHERE venta_id=?"
                                        " AND origen='VENTA' ORDER BY id", (venta_id,))]

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
        d["pagos"] = [(p["metodo"], p["monto"]) for p in self.pagos_de_venta(v["id"])]
        sep = self.q1("SELECT numero, abonado FROM separados WHERE venta_id=?", (v["id"],))
        if sep:
            d["separado"] = dict(sep)
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

    def _preparar_devolucion(self, venta: dict[str, Any], cantidades: dict[int, int] | None) -> dict[str, Any]:
        if venta["estado"] == "ANULADO":
            raise POSError("Ese tiquete ya está ANULADO.")
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
        queda = sum(it["cantidad"] - it["cantidad_devuelta"] for it in venta["items"]) - sum(l["cant"] for l in lineas)
        return {"lineas": lineas, "monto": sum(l["monto"] for l in lineas), "util": sum(l["util"] for l in lineas),
                "tipo": "TOTAL" if queda == 0 else "PARCIAL",
                "estado": "ANULADO" if queda == 0 else "DEVOLUCION PARCIAL"}

    def _insertar_devolucion(self, c: sqlite3.Connection, caja_id: int, venta: dict[str, Any], prep: dict[str, Any],
                             motivo: str, cajero: str, reintegros: list[tuple[str, int]], etiqueta: str,
                             evento: str | None = None) -> tuple[int, str]:
        n = c.execute("SELECT COUNT(*) FROM devoluciones").fetchone()[0] + 1
        numero_dev = f"DEV-{n:06d}"
        cur = c.execute(
            "INSERT INTO devoluciones(numero, venta_id, caja_id, fecha, tipo, monto, utilidad, metodo_pago,"
            " motivo, cajero) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (numero_dev, venta["id"], caja_id, _ahora(), prep["tipo"], prep["monto"], prep["util"], etiqueta,
             motivo, cajero))
        dev_id = int(cur.lastrowid)
        for l in prep["lineas"]:
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
                  " estado=? WHERE id=?", (prep["monto"], prep["util"], prep["estado"], venta["id"]))
        self._insertar_pagos(c, caja_id, "DEVOLUCION", reintegros, cajero, venta_id=venta["id"],
                             devolucion_id=dev_id, signo=-1)
        prendas = ", ".join(f"{l['cant']}× {l['it']['nombre']}" for l in prep["lineas"])
        self._evento(c, venta["id"], venta["numero_tiquete"],
                     evento or ("ANULADO" if prep["tipo"] == "TOTAL" else "DEVOLUCION PARCIAL"),
                     f"{numero_dev} · {prendas} · Motivo: {motivo}", -prep["monto"], cajero)
        self.auditar("DEVOLUCION_" + prep["tipo"], "ventas", venta["numero_tiquete"],
                     {"devolucion": numero_dev, "monto": prep["monto"], "motivo": motivo,
                      "reintegro": reintegros}, c)
        return dev_id, numero_dev

    @staticmethod
    def metodo_reintegro_sugerido(venta: dict[str, Any]) -> str:
        """Se devuelve por el mismo medio si fue uno solo; si fue mixto, separado o cambio: efectivo."""
        return venta["metodo_pago"] if venta["metodo_pago"] in cfg.METODOS_PAGO else "Efectivo"

    def registrar_devolucion(self, numero_tiquete: str, cantidades: dict[int, int] | None, motivo: str,
                             cajero: str = "", metodo_reintegro: str | None = None) -> dict[str, Any]:
        """
        Devolución total (anulación) o parcial de un tiquete.
        cantidades: {detalle_id: unidades a devolver}; None = todo lo pendiente (ANULAR).
        Reingresa el stock, descuenta el valor del método por el que se devuelve el dinero y deja
        trazado el evento en documentos_historial + auditoría.
        """
        motivo = (motivo or "").strip()
        if not motivo:
            raise POSError("Indique el motivo de la devolución.")
        venta = self.obtener_venta(numero_tiquete)
        if venta is None:
            raise POSError(f"No se encontró el tiquete {numero_tiquete}.")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe haber una caja abierta para registrar devoluciones.")
        prep = self._preparar_devolucion(venta, cantidades)
        metodo = metodo_reintegro or self.metodo_reintegro_sugerido(venta)
        if metodo not in cfg.METODOS_PAGO:
            raise POSError(f"Método de reintegro no válido: {metodo}")
        if metodo == "Efectivo" and self.resumen_caja(caja["id"])["efectivo_esperado"] < prep["monto"]:
            raise POSError("No hay suficiente efectivo en caja para esta devolución.")
        with self.transaccion() as c:
            _, numero_dev = self._insertar_devolucion(c, caja["id"], venta, prep, motivo, cajero,
                                                      [(metodo, prep["monto"])], metodo)
        resultado = self.obtener_venta(venta["id"])
        resultado["devolucion"] = {"numero": numero_dev, "tipo": prep["tipo"], "monto": prep["monto"],
                                   "motivo": motivo, "metodo": metodo,
                                   "lineas": [{"nombre": l["it"]["nombre"], "cantidad": l["cant"],
                                               "monto": l["monto"]} for l in prep["lineas"]]}
        return resultado

    def registrar_cambio(self, numero_tiquete: str, cantidades: dict[int, int], items_nuevos: list[ItemCarrito],
                         motivo: str, cajero: str = "", pagos: list[tuple[str, int]] | None = None,
                         metodo_reintegro: str = "Efectivo") -> dict[str, Any]:
        """
        Cambio de prenda en UN solo paso: devuelve las prendas elegidas del tiquete original y emite un
        tiquete nuevo con las que se lleva. El valor devuelto se usa como crédito:
          · si lo nuevo cuesta más, el cliente paga la diferencia (pagos);
          · si cuesta menos, se le reintegra la diferencia (metodo_reintegro).
        Todo queda en una transacción y trazado en ambos tiquetes.
        """
        motivo = (motivo or "").strip() or "Cambio de prenda"
        venta = self.obtener_venta(numero_tiquete)
        if venta is None:
            raise POSError(f"No se encontró el tiquete {numero_tiquete}.")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe haber una caja abierta para hacer cambios.")
        if not items_nuevos:
            raise POSError("Escanee la(s) prenda(s) que se lleva el cliente.")
        prep = self._preparar_devolucion(venta, cantidades)
        credito = prep["monto"]
        permitir_negativo = self.get_config("permitir_stock_negativo") == "1"
        with self.transaccion() as c:
            lineas = self._lineas_desde_productos(c, items_nuevos, permitir_negativo)
            tot = self._calcular(lineas, 0)
            diferencia = tot["total"] - credito
            if diferencia > 0:
                aplicados, recibido, cambio, etiqueta = self._normalizar_pagos(pagos or [], diferencia)
                pagos_venta = [(cfg.METODO_CREDITO, credito)] + aplicados
                reintegros = [(cfg.METODO_CREDITO, credito)]
                recibido += credito
            else:
                reintegro = -diferencia
                if metodo_reintegro not in cfg.METODOS_PAGO:
                    raise POSError("Método de reintegro no válido.")
                if reintegro and metodo_reintegro == "Efectivo" and \
                        self.resumen_caja(caja["id"])["efectivo_esperado"] < reintegro:
                    raise POSError("No hay suficiente efectivo en caja para devolver la diferencia.")
                pagos_venta = [(cfg.METODO_CREDITO, tot["total"])]
                reintegros = [(cfg.METODO_CREDITO, tot["total"])] + ([(metodo_reintegro, reintegro)] if reintegro else [])
                recibido, cambio, aplicados = tot["total"], 0, []
            dev_id, numero_dev = self._insertar_devolucion(
                c, caja["id"], venta, prep, f"Cambio de prenda · {motivo}", cajero, reintegros, "Cambio",
                evento="CAMBIO")
            nueva_id, nuevo_numero = self._insertar_venta(
                c, caja["id"], lineas, tot, "Cambio", recibido, cambio, pagos_venta, cajero,
                venta["cliente_documento"], venta["cliente_nombre"],
                descripcion=f"Cambio del tiquete {venta['numero_tiquete']} ({numero_dev}) · crédito {cfg.cop(credito)}"
                            + (f" · pagó diferencia {cfg.cop(diferencia)}" if diferencia > 0 else "")
                            + (f" · se devolvió {cfg.cop(-diferencia)} en {metodo_reintegro}" if diferencia < 0 else ""))
            self._evento(c, venta["id"], venta["numero_tiquete"], "CAMBIO",
                         f"Cambiado por el tiquete {nuevo_numero}", 0, cajero)
        nueva = self.obtener_venta(nueva_id)
        nueva["cambio_info"] = {"original": venta["numero_tiquete"], "devolucion": numero_dev, "credito": credito,
                                "diferencia": diferencia, "metodo_reintegro": metodo_reintegro if diferencia < 0 else "",
                                "pagos_diferencia": aplicados,
                                "devueltas": [{"nombre": l["it"]["nombre"], "cantidad": l["cant"], "monto": l["monto"]}
                                              for l in prep["lineas"]]}
        return nueva

    def cambiar_metodo_pago(self, venta_id: int, nuevo: str, motivo: str, cajero: str = "") -> None:
        """Corrige el método de pago (p. ej. se marcó Efectivo pero pagó con Bre-B)."""
        if nuevo not in cfg.METODOS_PAGO:
            raise POSError("Método de pago no válido.")
        v = self.q1("SELECT v.*, c.estado AS estado_caja FROM ventas v JOIN cierres_caja c ON c.id=v.caja_id"
                    " WHERE v.id=?", (venta_id,))
        if v is None:
            raise POSError("Tiquete no encontrado.")
        pagos = self.q("SELECT id, metodo FROM pagos WHERE venta_id=? AND origen='VENTA'", (venta_id,))
        if len(pagos) != 1 or v["metodo_pago"] in ("Mixto", "Cambio", "Separado"):
            raise POSError("Este tiquete se pagó con varios medios (o es un cambio/separado): para corregirlo, "
                           "anúlelo y vuelva a cobrarlo.")
        if v["metodo_pago"] == nuevo:
            raise POSError("El tiquete ya tiene ese método de pago.")
        if v["estado_caja"] != "ABIERTA":
            raise POSError("La caja de esa venta ya se cerró: el método no se puede cambiar.")
        if v["total_devuelto"]:
            raise POSError("El tiquete tiene devoluciones: no se puede cambiar el método de pago.")
        if not (motivo or "").strip():
            raise POSError("Indique el motivo de la corrección.")
        if v["metodo_pago"] == "Efectivo" and self.resumen_caja(v["caja_id"])["efectivo_esperado"] < v["total"]:
            raise POSError("El efectivo del cajón no alcanza para esta corrección.")
        with self.transaccion() as c:
            c.execute("UPDATE ventas SET metodo_pago=? WHERE id=?", (nuevo, venta_id))
            c.execute("UPDATE pagos SET metodo=? WHERE id=?", (nuevo, pagos[0]["id"]))
            self._evento(c, venta_id, v["numero_tiquete"], "CORRECCION",
                         f"Método de pago: {v['metodo_pago']} → {nuevo} · Motivo: {motivo}", 0, cajero)
            self.auditar("METODO_PAGO_CORREGIDO", "ventas", v["numero_tiquete"],
                         {"antes": v["metodo_pago"], "despues": nuevo, "motivo": motivo}, c)

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
        """Ventas netas por medio de pago (el pago mixto se reparte en cada medio)."""
        d = {m: 0 for m in cfg.METODOS_PAGO}
        for r in self.q("SELECT metodo, SUM(monto) t FROM pagos WHERE origen IN ('VENTA','DEVOLUCION')"
                        " AND fecha BETWEEN ? AND ? AND metodo<>? GROUP BY metodo", (desde, hasta, cfg.METODO_CREDITO)):
            d[r["metodo"]] = d.get(r["metodo"], 0) + r["t"]
        sep = self.q1("SELECT COALESCE(SUM(total-total_devuelto),0) FROM ventas WHERE metodo_pago='Separado'"
                      " AND fecha BETWEEN ? AND ?", (desde, hasta))[0]
        if sep:
            d["Separado"] = sep
        return {k: v for k, v in d.items() if v or k in cfg.METODOS_PAGO}

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


    # ------------------------------------------------------------------ #
    # Separados (apartados) con abonos
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pagos_abono(pagos: list[tuple[str, int]], saldo: int) -> list[tuple[str, int]]:
        limpio: dict[str, int] = {}
        for m, v in pagos or []:
            v = int(v or 0)
            if v <= 0:
                continue
            if m not in cfg.METODOS_PAGO:
                raise POSError(f"Método de pago no válido: {m}")
            limpio[m] = limpio.get(m, 0) + v
        if sum(limpio.values()) > saldo:
            raise POSError(f"El abono ({cfg.cop(sum(limpio.values()))}) supera el saldo pendiente ({cfg.cop(saldo)}).")
        return list(limpio.items())

    def crear_separado(self, items: list[ItemCarrito], cliente_documento: str, cliente_nombre: str,
                       telefono: str = "", descuento: int = 0, abono: list[tuple[str, int]] | None = None,
                       cajero: str = "", dias_limite: int | None = None, observaciones: str = "") -> dict[str, Any]:
        """Aparta prendas para un cliente: el stock queda reservado y se registra el abono inicial."""
        doc = (cliente_documento or "").strip()
        nombre = (cliente_nombre or "").strip().upper()
        if len(doc) < 5:
            raise POSError("Para separar escriba la cédula del cliente.")
        if len(nombre) < 3:
            raise POSError("Para separar escriba el nombre del cliente.")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe ABRIR CAJA antes de registrar separados.")
        dias = int(dias_limite if dias_limite is not None else self.get_config_int("separado_dias_limite", 30))
        with self.transaccion() as c:
            lineas = self._lineas_desde_productos(c, items, self.get_config("permitir_stock_negativo") == "1")
            tot = self._calcular(lineas, descuento)
            pagos = self._pagos_abono(abono or [], tot["total"])
            abonado = sum(v for _, v in pagos)
            n = c.execute("SELECT COUNT(*) FROM separados").fetchone()[0] + 1
            numero = f"SEP-{n:06d}"
            limite = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
            cur = c.execute(
                "INSERT INTO separados(numero, fecha, cliente_documento, cliente_nombre, telefono, subtotal, descuento,"
                " total, abonado, fecha_limite, cajero, observaciones) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (numero, _ahora(), doc, nombre, (telefono or "").strip(), tot["subtotal"], tot["descuento"],
                 tot["total"], abonado, limite, cajero, (observaciones or "").strip()))
            sep_id = int(cur.lastrowid)
            c.executemany("INSERT INTO detalle_separados(separado_id, producto_id, codigo, referencia, nombre, categoria,"
                          " cantidad, precio_unitario, costo_unitario) VALUES (?,?,?,?,?,?,?,?,?)",
                          [(sep_id, l["producto_id"], l["codigo"], l["referencia"], l["nombre"], l["categoria"],
                            l["cant"], l["precio"], l["costo_u"]) for l in lineas])
            c.executemany("UPDATE productos SET stock = stock - ?, actualizado=datetime('now','localtime') WHERE id=?",
                          [(l["cant"], l["producto_id"]) for l in lineas])
            self._insertar_pagos(c, caja["id"], "ABONO", pagos, cajero, separado_id=sep_id)
            self.auditar("SEPARADO_CREADO", "separados", numero,
                         {"cliente": f"{doc} {nombre}", "total": tot["total"], "abono": abonado}, c)
        return self.obtener_separado(sep_id)

    def obtener_separado(self, id_o_numero: int | str) -> dict[str, Any] | None:
        if isinstance(id_o_numero, int):
            s = self.q1("SELECT * FROM separados WHERE id=?", (id_o_numero,))
        else:
            txt = str(id_o_numero).strip().upper()
            if txt.isdigit():
                txt = f"SEP-{int(txt):06d}"
            s = self.q1("SELECT * FROM separados WHERE numero=?", (txt,))
        if s is None:
            return None
        d = dict(s)
        d["saldo"] = d["total"] - d["abonado"]
        d["items"] = [dict(r) for r in self.q("SELECT * FROM detalle_separados WHERE separado_id=? ORDER BY id",
                                              (s["id"],))]
        d["abonos"] = [dict(r) for r in self.q("SELECT fecha, origen, metodo, monto, cajero FROM pagos"
                                               " WHERE separado_id=? ORDER BY id", (s["id"],))]
        d["vencido"] = d["estado"] == "ACTIVO" and d["fecha_limite"] and d["fecha_limite"] < date.today().isoformat()
        return d

    def listar_separados(self, estado: str = "ACTIVO", texto: str = "") -> list[dict[str, Any]]:
        estado, texto = str(estado or ""), str(texto or "")
        sql, p = ["SELECT s.*, s.total - s.abonado AS saldo,"
                  " (SELECT SUM(cantidad) FROM detalle_separados d WHERE d.separado_id=s.id) AS unidades"
                  " FROM separados s WHERE 1=1"], []
        if estado and estado != "Todos":
            sql.append("AND s.estado=?")
            p.append(estado)
        for palabra in (texto or "").split():
            sql.append("AND (s.numero LIKE ? OR s.cliente_documento LIKE ? OR s.cliente_nombre LIKE ? OR s.telefono LIKE ?)")
            p += [f"%{palabra.upper()}%", f"%{palabra}%", f"%{palabra.upper()}%", f"%{palabra}%"]
        sql.append("ORDER BY s.id DESC LIMIT 1000")
        hoy = date.today().isoformat()
        out = []
        for r in self.q(" ".join(sql), p):
            d = dict(r)
            d["vencido"] = d["estado"] == "ACTIVO" and d["fecha_limite"] and d["fecha_limite"] < hoy
            out.append(d)
        return out

    def abonar_separado(self, id_o_numero: int | str, pagos: list[tuple[str, int]], cajero: str = "") -> dict[str, Any]:
        s = self.obtener_separado(id_o_numero)
        if s is None:
            raise POSError("Separado no encontrado.")
        if s["estado"] != "ACTIVO":
            raise POSError(f"Ese separado está {s['estado']}.")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe haber una caja abierta para recibir abonos.")
        limpios = self._pagos_abono(pagos, s["saldo"])
        monto = sum(v for _, v in limpios)
        if monto <= 0:
            raise POSError("Escriba el valor del abono.")
        with self.transaccion() as c:
            c.execute("UPDATE separados SET abonado = abonado + ? WHERE id=?", (monto, s["id"]))
            self._insertar_pagos(c, caja["id"], "ABONO", limpios, cajero, separado_id=s["id"])
            self.auditar("SEPARADO_ABONO", "separados", s["numero"], {"abono": monto, "pagos": limpios}, c)
        d = self.obtener_separado(s["id"])
        d["ultimo_abono"] = {"monto": monto, "pagos": limpios}
        return d

    def entregar_separado(self, id_o_numero: int | str, pagos_finales: list[tuple[str, int]] | None = None,
                          cajero: str = "") -> dict[str, Any]:
        """El cliente termina de pagar y se lleva la ropa: se emite el tiquete de venta (método «Separado»)."""
        s = self.obtener_separado(id_o_numero)
        if s is None:
            raise POSError("Separado no encontrado.")
        if s["estado"] != "ACTIVO":
            raise POSError(f"Ese separado está {s['estado']}.")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe haber una caja abierta para entregar separados.")
        limpios = self._pagos_abono(pagos_finales or [], s["saldo"])
        if s["abonado"] + sum(v for _, v in limpios) < s["total"]:
            raise POSError(f"Aún faltan {cfg.cop(s['saldo'] - sum(v for _, v in limpios))} por pagar.")
        with self.transaccion() as c:
            if limpios:
                monto = sum(v for _, v in limpios)
                c.execute("UPDATE separados SET abonado = abonado + ? WHERE id=?", (monto, s["id"]))
                self._insertar_pagos(c, caja["id"], "ABONO", limpios, cajero, separado_id=s["id"])
            lineas = [{"producto_id": it["producto_id"], "codigo": it["codigo"], "referencia": it["referencia"],
                       "nombre": it["nombre"], "categoria": it["categoria"], "cant": it["cantidad"],
                       "precio": it["precio_unitario"], "costo_u": it["costo_unitario"]} for it in s["items"]]
            tot = self._calcular(lineas, s["descuento"])
            venta_id, numero = self._insertar_venta(
                c, caja["id"], lineas, tot, "Separado", tot["total"], 0, [], cajero, s["cliente_documento"],
                s["cliente_nombre"], descontar_stock=False,
                descripcion=f"Entrega del separado {s['numero']} · pagado en {len(s['abonos']) + (1 if limpios else 0)} abono(s)")
            c.execute("UPDATE separados SET estado='ENTREGADO', venta_id=?, cierre_fecha=? WHERE id=?",
                      (venta_id, _ahora(), s["id"]))
            self.auditar("SEPARADO_ENTREGADO", "separados", s["numero"], {"tiquete": numero}, c)
        return self.obtener_venta(venta_id)

    def anular_separado(self, id_o_numero: int | str, motivo: str, reintegro: int = 0,
                        metodo_reintegro: str = "Efectivo", cajero: str = "") -> dict[str, Any]:
        """Cancela el separado: la ropa vuelve al inventario. Se puede devolver todo, parte o nada de lo abonado."""
        motivo = (motivo or "").strip()
        if not motivo:
            raise POSError("Indique el motivo de la anulación.")
        s = self.obtener_separado(id_o_numero)
        if s is None:
            raise POSError("Separado no encontrado.")
        if s["estado"] != "ACTIVO":
            raise POSError(f"Ese separado está {s['estado']}.")
        reintegro = int(reintegro or 0)
        if not 0 <= reintegro <= s["abonado"]:
            raise POSError(f"Lo devuelto debe estar entre $ 0 y lo abonado ({cfg.cop(s['abonado'])}).")
        caja = self.caja_abierta()
        if caja is None:
            raise POSError("Debe haber una caja abierta.")
        if reintegro and metodo_reintegro == "Efectivo" and \
                self.resumen_caja(caja["id"])["efectivo_esperado"] < reintegro:
            raise POSError("No hay suficiente efectivo en caja para devolver ese valor.")
        with self.transaccion() as c:
            for it in s["items"]:
                if it["producto_id"]:
                    c.execute("UPDATE productos SET stock=stock+?, actualizado=datetime('now','localtime') WHERE id=?",
                              (it["cantidad"], it["producto_id"]))
            if reintegro:
                self._insertar_pagos(c, caja["id"], "REEMBOLSO_SEPARADO", [(metodo_reintegro, reintegro)], cajero,
                                     separado_id=s["id"], signo=-1)
            c.execute("UPDATE separados SET estado='ANULADO', cierre_fecha=?, cierre_motivo=? WHERE id=?",
                      (_ahora(), motivo, s["id"]))
            self.auditar("SEPARADO_ANULADO", "separados", s["numero"],
                         {"motivo": motivo, "abonado": s["abonado"], "devuelto": reintegro,
                          "retenido": s["abonado"] - reintegro}, c)
        return self.obtener_separado(s["id"])

    # ------------------------------------------------------------------ #
    # Cajón monedero (trazabilidad de cada apertura)
    # ------------------------------------------------------------------ #
    def registrar_apertura_cajon(self, origen: str, referencia: str = "", motivo: str = "", cajero: str = "") -> None:
        caja = self.caja_abierta()
        with self.transaccion() as c:
            c.execute("INSERT INTO aperturas_cajon(fecha, caja_id, origen, referencia, motivo, cajero)"
                      " VALUES (?,?,?,?,?,?)", (_ahora(), caja["id"] if caja else None, origen.upper(),
                                                str(referencia or ""), (motivo or "").strip(), cajero))
            if origen.upper() == "MANUAL":
                self.auditar("CAJON_ABIERTO_MANUAL", "aperturas_cajon", referencia, {"motivo": motivo}, c)

    def aperturas_cajon(self, caja_id: int | None = None, limite: int = 500) -> list[sqlite3.Row]:
        if caja_id is None:
            return self.q("SELECT * FROM aperturas_cajon ORDER BY id DESC LIMIT ?", (limite,))
        return self.q("SELECT * FROM aperturas_cajon WHERE caja_id=? ORDER BY id", (caja_id,))

    # ------------------------------------------------------------------ #
    # Conteo físico
    # ------------------------------------------------------------------ #
    def aplicar_conteo(self, conteos: dict[int, int], motivo: str, cajero: str = "") -> list[dict[str, Any]]:
        """Ajusta el stock a lo contado. Cada diferencia queda en ajustes_inventario (antes/después/motivo)."""
        motivo = (motivo or "").strip()
        if not motivo:
            raise POSError("Indique el motivo del ajuste (p. ej. «Conteo mensual noviembre»).")
        cambios = []
        with self.transaccion() as c:
            for pid, contado in conteos.items():
                contado = int(contado)
                if contado < 0:
                    raise POSError("Una cantidad contada no puede ser negativa.")
                p = c.execute("SELECT id, codigo, nombre, stock FROM productos WHERE id=?", (pid,)).fetchone()
                if p is None or p["stock"] == contado:
                    continue
                c.execute("UPDATE productos SET stock=?, actualizado=datetime('now','localtime') WHERE id=?",
                          (contado, pid))
                c.execute("INSERT INTO ajustes_inventario(fecha, producto_id, codigo, nombre, antes, despues,"
                          " diferencia, origen, motivo, cajero) VALUES (?,?,?,?,?,?,?, 'CONTEO', ?, ?)",
                          (_ahora(), pid, p["codigo"], p["nombre"], p["stock"], contado, contado - p["stock"],
                           motivo, cajero))
                cambios.append({"codigo": p["codigo"], "nombre": p["nombre"], "antes": p["stock"],
                                "despues": contado, "diferencia": contado - p["stock"]})
            if cambios:
                self.auditar("CONTEO_FISICO", "productos", "",
                             {"motivo": motivo, "productos_ajustados": len(cambios),
                              "unidades": sum(x["diferencia"] for x in cambios)}, c)
        return cambios

    def ajustes_inventario(self, limite: int = 2000) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM ajustes_inventario ORDER BY id DESC LIMIT ?", (limite,))

    # ------------------------------------------------------------------ #
    # Reportes de gestión
    # ------------------------------------------------------------------ #
    def mercancia_quieta(self, dias: int = 60) -> list[dict[str, Any]]:
        """Prendas con existencias que no se venden hace `dias` días (o nunca)."""
        hoy = datetime.now()
        out = []
        for r in self.q(
                "SELECT p.*, (SELECT MAX(v.fecha) FROM detalle_ventas dv JOIN ventas v ON v.id=dv.venta_id"
                " WHERE dv.producto_id=p.id) AS ultima_venta FROM productos p WHERE p.activo=1 AND p.stock>0"):
            ref = r["ultima_venta"] or r["creado"]
            try:
                dias_sin = (hoy - datetime.strptime(ref[:19], "%Y-%m-%d %H:%M:%S")).days
            except (TypeError, ValueError):
                dias_sin = 0
            if dias_sin < dias:
                continue
            out.append({"codigo": r["codigo"], "nombre": r["nombre"], "categoria": r["categoria"],
                        "talla": r["talla"], "color": r["color"], "marca": r["marca"], "stock": r["stock"],
                        "precio": r["precio"], "valor_costo": r["stock"] * r["costo"],
                        "valor_venta": r["stock"] * r["precio"],
                        "ultima_venta": (r["ultima_venta"] or "")[:10] or "Nunca", "dias_sin_venta": dias_sin})
        out.sort(key=lambda x: (-x["dias_sin_venta"], -x["valor_costo"]))
        return out

    def ventas_por_dimension(self, dimension: str, desde: str, hasta: str) -> list[dict[str, Any]]:
        """Ventas netas agrupadas por categoría, talla, color o marca."""
        col = {"categoria": "dv.categoria", "talla": "p.talla", "color": "p.color", "marca": "p.marca"}.get(dimension)
        if col is None:
            raise POSError("Dimensión no válida.")
        filas = [dict(r) for r in self.q(
            f"SELECT COALESCE(NULLIF(TRIM({col}),''),'(Sin dato)') AS clave,"
            " SUM(dv.cantidad - dv.cantidad_devuelta) AS unidades, SUM(dv.total - dv.total_devuelto) AS ingresos,"
            " SUM(dv.utilidad - dv.utilidad_devuelta) AS utilidad, COUNT(DISTINCT v.id) AS tiquetes"
            " FROM detalle_ventas dv JOIN ventas v ON v.id=dv.venta_id LEFT JOIN productos p ON p.id=dv.producto_id"
            " WHERE v.fecha BETWEEN ? AND ? GROUP BY clave HAVING unidades > 0 ORDER BY ingresos DESC",
            (desde, hasta))]
        total = sum(f["ingresos"] for f in filas) or 1
        for f in filas:
            f["participacion"] = round(f["ingresos"] * 100 / total, 1)
            f["margen"] = round(f["utilidad"] * 100 / f["ingresos"], 1) if f["ingresos"] else 0
        return filas

    def comparativo_mensual(self, meses: int = 12) -> list[dict[str, Any]]:
        hoy = date.today().replace(day=1)
        lista = []
        y, m = hoy.year, hoy.month
        for _ in range(meses):
            lista.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        lista.reverse()
        datos = {r["mes"]: dict(r) for r in self.q(
            "SELECT strftime('%Y-%m', fecha) AS mes, SUM(CASE WHEN estado<>'ANULADO' THEN 1 ELSE 0 END) AS tiquetes,"
            " SUM(total - total_devuelto) AS ventas, SUM(utilidad - utilidad_devuelta) AS utilidad"
            " FROM ventas WHERE fecha >= ? GROUP BY mes", (lista[0] + "-01",))}
        und = {r["mes"]: r["u"] for r in self.q(
            "SELECT strftime('%Y-%m', v.fecha) AS mes, SUM(dv.cantidad - dv.cantidad_devuelta) AS u"
            " FROM detalle_ventas dv JOIN ventas v ON v.id=dv.venta_id WHERE v.fecha >= ? GROUP BY mes",
            (lista[0] + "-01",))}
        nombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        out, previo = [], None
        for mes in lista:
            d = datos.get(mes, {})
            ventas = int(d.get("ventas") or 0)
            fila = {"mes": f"{nombres[int(mes[5:]) - 1]} {mes[:4]}", "tiquetes": int(d.get("tiquetes") or 0),
                    "unidades": int(und.get(mes) or 0), "ventas": ventas, "utilidad": int(d.get("utilidad") or 0)}
            fila["ticket_promedio"] = ventas // fila["tiquetes"] if fila["tiquetes"] else 0
            fila["variacion"] = (round((ventas - previo) * 100 / previo, 1) if previo else None)
            previo = ventas or previo
            out.append(fila)
        return out

    def sugerido_compra(self, dias: int = 30, cobertura: int = 30) -> list[dict[str, Any]]:
        """Qué reponer: ritmo de venta de los últimos `dias` × días de `cobertura` − existencias."""
        desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d 00:00:00")
        out = []
        for r in self.q(
                "SELECT p.codigo, p.nombre, p.categoria, p.talla, p.color, p.marca, p.stock, p.costo,"
                " SUM(dv.cantidad - dv.cantidad_devuelta) AS vendidas"
                " FROM detalle_ventas dv JOIN ventas v ON v.id=dv.venta_id JOIN productos p ON p.id=dv.producto_id"
                " WHERE v.fecha >= ? AND p.activo=1 GROUP BY p.id HAVING vendidas > 0", (desde,)):
            ritmo = r["vendidas"] / max(1, dias)
            sugerido = max(0, int(-(-ritmo * cobertura // 1)) - max(0, r["stock"]))
            if sugerido <= 0:
                continue
            out.append({"codigo": r["codigo"], "nombre": r["nombre"], "categoria": r["categoria"],
                        "talla": r["talla"], "color": r["color"], "marca": r["marca"], "vendidas": r["vendidas"],
                        "stock": r["stock"],
                        "dias_inventario": round(max(0, r["stock"]) / ritmo, 1) if ritmo else None,
                        "sugerido": sugerido, "costo_estimado": sugerido * r["costo"]})
        out.sort(key=lambda x: (x["dias_inventario"] or 0, -x["vendidas"]))
        return out

def crear_bd_limpia(destino: Path) -> Path:
    """Genera una base de datos vacía con esquema + configuración (usada por build_app.py)."""
    destino = Path(destino)
    if destino.exists():
        destino.unlink()
    db = Database(destino)
    db.conn.execute("PRAGMA journal_mode=DELETE")  # archivo único, sin -wal/-shm
    db.close()
    return destino


def reiniciar_datos(db: "Database", conservar_inventario: bool) -> tuple["Database", Path]:
    """
    Deja el programa en blanco. SIEMPRE guarda antes una copia completa en respaldos/.
      conservar_inventario=False -> programa virgen (todo de fábrica).
      conservar_inventario=True  -> borra ventas, caja, historial y auditoría; conserva productos
                                    (con su stock) y la configuración; el consecutivo vuelve al inicio.
    Devuelve (nueva_base_de_datos, ruta_del_respaldo).
    """
    ruta = db.path
    respaldo = db.respaldar(cfg.backups_dir() / f"antes_de_reinicio_{datetime.now():%Y%m%d_%H%M%S}.db",
                            conservar=10_000)
    productos = [dict(r) for r in db.q("SELECT * FROM productos")] if conservar_inventario else []
    config = db.get_all_config() if conservar_inventario else {}
    formato = db.get_config("formato_carga_columnas", "") if conservar_inventario else ""
    db.close()
    for sufijo in ("", "-wal", "-shm"):
        p = Path(str(ruta) + sufijo)
        if p.exists():
            p.unlink()
    nueva = Database(ruta)
    if conservar_inventario:
        config.pop("pin_admin", None)
        config["consecutivo_siguiente"] = config.get("rango_desde", cfg.DEFAULT_CONFIG["rango_desde"])
        config.pop("aviso_resolucion_visto", None)
        if formato:
            config["formato_carga_columnas"] = formato
        nueva.set_config(config, auditar=False)
        if productos:
            cols = [k for k in productos[0] if k != "id"]
            with nueva.transaccion() as c:
                c.executemany(f"INSERT INTO productos({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                              [tuple(p[k] for k in cols) for p in productos])
    nueva.auditar("REINICIO_DATOS", "base_datos", "",
                  {"conservar_inventario": conservar_inventario, "respaldo": str(respaldo)})
    return nueva, respaldo


def restaurar_respaldo(db: "Database", archivo: Path) -> tuple["Database", Path]:
    """Reemplaza la base de datos por un respaldo (guarda antes la actual)."""
    archivo = Path(archivo)
    prueba = sqlite3.connect(str(archivo))
    try:
        tablas = {r[0] for r in prueba.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        prueba.close()
    if not {"productos", "ventas", "configuracion"} <= tablas:
        raise POSError("Ese archivo no es un respaldo válido de ConfeccionesR4.")
    ruta = db.path
    previo = db.respaldar(cfg.backups_dir() / f"antes_de_restaurar_{datetime.now():%Y%m%d_%H%M%S}.db",
                          conservar=10_000)
    db.close()
    for sufijo in ("-wal", "-shm"):
        p = Path(str(ruta) + sufijo)
        if p.exists():
            p.unlink()
    import shutil
    shutil.copy2(archivo, ruta)
    nueva = Database(ruta)                     # migra si el respaldo es de una versión anterior
    nueva.auditar("RESPALDO_RESTAURADO", "base_datos", "", {"archivo": archivo.name, "previo": str(previo)})
    return nueva, previo
