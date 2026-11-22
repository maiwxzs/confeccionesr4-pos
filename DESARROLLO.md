# Desarrollo

## Tecnologías

- Python 3.11+ · PySide6 (Qt 6) · SQLite
- Pillow y python-barcode para etiquetas y copias PDF · openpyxl y pandas para Excel
- python-escpos y pywin32 para impresión · cryptography (Ed25519) para actualizaciones
- PyInstaller e Inno Setup para el ejecutable y el instalador

## Estructura

```
ConfeccionesR4_POS/
├── main.py                    Ventana principal, barra lateral, instancia única, arranque
├── config.py                  Rutas de datos, valores por defecto, formato de pesos
├── database.py                Esquema SQLite, migraciones, ventas, caja, separados, auditoría
├── excel_engine.py            Importación y exportación de inventario y reportes
├── printer_engine.py          Tiquetes ESC/POS, etiquetas (Windows, ESC/POS, TSPL), cajón
├── barcode_engine.py          Composición de etiquetas EAN-13 / Code128 a 203 dpi
├── ticket_pdf.py              Copia PDF de cada comprobante
├── nube.py                    Respaldo en Google Drive y resumen de cierre al celular
├── updater.py                 Verificación, descarga e instalación de actualizaciones
├── publicar_actualizacion.py  Generación de claves y firma de versiones
├── ui/
│   ├── theme.py               Tema visual (QSS)
│   ├── animations.py          Transiciones y efectos
│   ├── widgets.py             Componentes compartidos, bus de eventos, hilos de impresión
│   ├── dashboard_view.py      Dashboard
│   ├── pos_view.py            Vender
│   ├── history_view.py        Documentos
│   ├── cambio_view.py         Cambio de prenda
│   ├── separados_view.py      Separados
│   ├── inventory_view.py      Inventario
│   ├── conteo_view.py         Conteo físico
│   ├── reportes_view.py       Reportes de gestión
│   ├── cash_view.py           Caja
│   ├── cajon.py               Apertura manual del cajón
│   ├── pagos.py               Diálogo de cobro y pago mixto
│   ├── seguridad.py           Clave de administrador y motivos
│   ├── asistente.py           Configuración inicial
│   └── settings_dialog.py     Ajustes
├── assets/                    Logos, ícono, splash, fuentes e imágenes del instalador
├── docs/                      Documentación
├── build_app.py               Compilación con PyInstaller
├── installer_setup.iss        Instalador Inno Setup
├── VERSION                    Versión mayor.menor
├── COMPILAR.bat               Compilación local
└── PUBLICAR_ACTUALIZACION.bat Publicación manual
```

## Entorno local

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Para trabajar con datos de prueba sin tocar los reales:

```bat
set R4_DATA_DIR=C:\pruebas\r4
python main.py
```

## Compilación local

`COMPILAR.bat` genera `dist\ConfeccionesR4\ConfeccionesR4.exe` (un solo archivo) e
`installer_output\Instalador_ConfeccionesR4_v1.0.exe`.

## Base de datos

El esquema se crea y migra automáticamente al iniciar. Tablas principales:

| Tabla | Contenido |
|---|---|
| `productos` | Catálogo e inventario |
| `ventas`, `detalle_ventas`, `pagos` | Comprobantes, líneas y medios de pago |
| `devoluciones`, `detalle_devoluciones` | Devoluciones y anulaciones |
| `separados`, `detalle_separados` | Separados y sus prendas |
| `cierres_caja`, `movimientos_caja`, `aperturas_cajon` | Caja |
| `ajustes_inventario`, `importaciones` | Conteos e importaciones |
| `documentos_historial`, `auditoria` | Trazabilidad |
| `configuracion` | Ajustes de la tienda |

Los registros de ventas, devoluciones, historial y auditoría están protegidos con *triggers* que
impiden borrarlos o modificarlos.

## Convenciones

- Montos en pesos enteros (`int`); el formato se aplica solo al mostrar.
- Fechas en ISO 8601, hora local.
- Toda operación que mueve dinero o inventario escribe en `auditoria`.
- Los datos de la tienda nunca se escriben en el código: se leen de `configuracion`.
