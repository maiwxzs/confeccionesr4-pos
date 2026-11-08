# ConfeccionesR4 POS v1.0

Punto de Venta, Historial de Documentos, Inventario, Cierre de Caja y Dashboard para
**CONFECCIONESR4** (Bogotá). Aplicación de escritorio nativa (PySide6 + SQLite), tema oscuro,
100 % offline y entregada como **un solo archivo `ConfeccionesR4.exe`**: el PC del cliente no
necesita Python, pip ni consola.

---

## 1. Estructura del proyecto

```
ConfeccionesR4_POS/
├── main.py                    Punto de entrada: ventana, barra lateral animada, instancia única
├── config.py                  Rutas, datos fiscales por defecto, formato $ COP
├── database.py                SQLite v3: ventas, historial, devoluciones, auditoría, migraciones
├── excel_engine.py            Importación inteligente + exportación con formato ejecutivo
├── printer_engine.py          ESC/POS directo Jaltech 80/58 mm (win32print RAW, USB, red)
├── barcode_engine.py          Etiquetas Code128 / EAN-13 a 203 dpi
├── updater.py                 Actualización remota con firma digital Ed25519
├── publicar_actualizacion.py  Genera claves y firma cada nueva versión
├── ui/
│   ├── theme.py               Tema oscuro (QSS)
│   ├── animations.py          Transiciones, contadores, pulsos, destellos, diálogos animados
│   ├── widgets.py             Bus de eventos, campo $ COP, KPI, toasts, hilos de impresión
│   ├── dashboard_view.py      MÓDULO 5 · Dashboard (primera pantalla)
│   ├── pos_view.py            MÓDULO 1 · Punto de venta
│   ├── history_view.py        MÓDULO 2 · Historial de documentos y trazabilidad
│   ├── inventory_view.py      MÓDULO 3 · Inventario + etiquetas
│   ├── cash_view.py           MÓDULO 4 · Apertura, arqueo ciego y cierre
│   └── settings_dialog.py     Ajustes (empresa/DIAN, impresoras, etiquetas, datos, actualizaciones)
├── assets/                    Ícono, splash, imágenes del instalador
├── ejemplos/                  Tiquetes, devolución, etiquetas y reportes generados por el sistema
├── build_app.py               PyInstaller automatizado (un solo .exe)
├── installer_setup.iss        Instalador Inno Setup
├── .github/workflows/         Compilación y publicación automáticas (GitHub Actions)
├── COMPILAR.bat               Alternativa: compilar en su PC
└── PUBLICAR_ACTUALIZACION.bat Alternativa: publicar a mano
```

## 2. Todo automático (recomendado): GitHub compila, publica y actualiza

No necesita instalar Python ni Inno Setup. GitHub Actions (gratis) compila en la nube.

**Configuración (una sola vez, desde el navegador):**
1. Entre a github.com (cree la cuenta si no tiene) → **New repository** → nombre
   `confeccionesr4-pos` → **Public** → *Create repository*.
2. En el repositorio: **Settings › Secrets and variables › Actions › New repository secret**
   - Name: `FIRMA_ACTUALIZACIONES`
   - Secret: pegue el contenido completo del archivo `FIRMA_ACTUALIZACIONES.txt`
     (guarde ese archivo en un lugar seguro y no lo comparta).
3. Pestaña **Code › uploading an existing file**: arrastre TODO el contenido de esta carpeta
   (incluida la carpeta `.github`) → *Commit changes*.
   Si la carpeta `.github` no subió: *Add file › Create new file*, nombre
   `.github/workflows/compilar-y-publicar.yml`, pegue el contenido de ese archivo y guarde.
4. Pestaña **Actions**: verá "Compilar y publicar" trabajando (≈ 10-15 min).
5. Pestaña **Releases**: descargue **`Instalador_ConfeccionesR4_v1.0.exe`** e instálelo en la tienda.
   Enlace fijo para descargarlo siempre:
   `https://github.com/USUARIO/confeccionesr4-pos/releases/latest/download/Instalador_ConfeccionesR4_v1.0.exe`

**Desde ese momento, todo es automático:**
- Cualquier cambio que suba al repositorio → GitHub compila la versión `1.0.<n>`, la firma y la publica.
- Cada POS instalado revisa al abrir y cada 3 horas; si hay versión nueva **se instala sola**
  (nunca durante una venta) y se reinicia. La URL viene integrada en el programa.
- Para publicar sin cambiar código: *Actions › Compilar y publicar › Run workflow*.

**En la tienda, también automático:** detecta la impresora térmica, ofrece importar el
inventario la primera vez, hace respaldo diario y migra la base de datos en cada versión.

### Alternativa: compilar en su propio PC
Python 3.12 (64 bits, *Add to PATH*) + Inno Setup 6 → doble clic en **`COMPILAR.bat`**.
Resultado: `dist\ConfeccionesR4\ConfeccionesR4.exe` (un solo archivo) e
`installer_output\Instalador_ConfeccionesR4_v1.0.exe`. Para actualizar a mano: vuelva a compilar
e instale encima (los datos se conservan).

## 3. Dónde se guarda todo

| Qué | Dónde |
|---|---|
| Programa | `C:\Program Files\ConfeccionesR4\ConfeccionesR4.exe` |
| Base de datos (ventas, inventario, caja, historial, auditoría) | `C:\ProgramData\ConfeccionesR4\confeccionesr4.db` |
| Respaldos automáticos (diario + al cerrar caja/salir; últimos 30) | `C:\ProgramData\ConfeccionesR4\respaldos\` |
| Registro de errores | `C:\ProgramData\ConfeccionesR4\logs\` |
| Reportes de Excel exportados | `Documentos\ConfeccionesR4 Reportes\` |

Actualizar o desinstalar el programa **no borra** los datos.

## 4. Seguridad de las actualizaciones

Cada versión se firma (Ed25519) con el secreto `FIRMA_ACTUALIZACIONES`; el POS solo instala
archivos con firma válida y SHA-256 correcto, y si algo falla restaura el .exe anterior.
Si pierde la clave, genere otra y reinstale una vez los equipos con el instalador nuevo.
Ajustes › Actualizaciones permite desactivar la instalación automática o buscar manualmente.

## 5. Impresora Jaltech

1. Instale el driver de Jaltech; verifique que aparezca en *Configuración › Impresoras*.
2. *Ajustes › Impresora de tiquetes* → Conexión *Windows*, elija la impresora, ancho 80/58 mm
   → **Imprimir página de prueba**. Si las tildes salen mal, cambie la *Tabla de caracteres*.
3. El cajón monedero abre solo en ventas y devoluciones en efectivo.

Etiquetas: *Ajustes › Etiquetas* ("Driver de Windows" sirve con cualquier impresora; "TSPL"
para impresoras de etiquetas TSC/Xprinter). Tamaño por defecto 50 × 30 mm.

## 6. Uso diario

| Acción | Dónde |
|---|---|
| Ver cómo va el día | **Dashboard** (primera pantalla) |
| Vender | **Vender**: escanee o toque la prenda · cliente opcional · método · **F12** |
| Reimprimir un tiquete | **Documentos** › buscar › *Reimprimir comprobante* (o **F9** en Vender para el último) |
| Devolución parcial / anular | **Documentos** › buscar (escanee el código del tiquete) › *Procesar devolución / Anular* |
| Cerrar el día | **Caja** › *Arqueo ciego y cierre* |

Atajos: `Ctrl+1..5` módulos · en Vender `F2` buscar, `F12` pagar, `F5` nueva venta, `F8` quitar último, `Esc` limpiar.

### Devoluciones y anulaciones
- Se elige cuántas unidades de cada prenda se devuelven y el **motivo** (obligatorio).
- Las prendas vuelven al inventario; el valor se reintegra por el método original y se descuenta
  del arqueo de la caja abierta y de las métricas (Ventas Totales, Utilidad, gráficos).
- Si se devuelve todo, el tiquete queda **ANULADO**; si no, **DEVOLUCION PARCIAL**.
- Nada se borra: los tiquetes, sus detalles, las devoluciones, el historial y la auditoría son
  de solo lectura en la base de datos (protegidos con *triggers*). Se imprime comprobante.

### Importar `Inventario General 01.xlsx`
Inventario › **Importar Excel** reconoce el formato de la tienda:

| Columna del archivo | Uso en el POS |
|---|---|
| Nombre + Color + Talla | `Wide Leg · Azul Indigo · T8` (cada talla se distingue) |
| Código Interno | Código de barras (Code128) para escanear y etiquetar |
| Precio Unitario Venta / Compra | Precio de venta / costo (utilidad real) |
| Stock, Stock Mínimo, Categoria, Marca | Igual que en el archivo |
| Códigos de unidad / moneda / impuesto | Se guardan para re-exportar |

Si el libro tiene varias hojas usa "INVENTARIO GENERAL" y permite cambiarla en la vista previa.
**Exportar en formato original** devuelve las mismas 14 columnas con el stock actualizado.

## 7. Facturación y DIAN (léalo)

- El programa imprime un **comprobante de venta numerado**, 100 % offline. **No genera factura
  electrónica ni documento equivalente electrónico POS, ni transmite nada a la DIAN.**
- Desde el 1 de julio de 2024 el tiquete POS en papel dejó de ser válido como documento
  equivalente para quienes están **obligados** a facturar; ellos deben usar factura electrónica
  o el *documento equivalente electrónico tiquete POS* (Resolución DIAN 000165 de 2023,
  modificada por la 000008 de 2024), a través de un proveedor tecnológico.
- Las personas naturales **no responsables de IVA** que cumplen todos los requisitos legales
  figuran entre los no obligados a facturar. **Confirme con su contador** en cuál caso está.
- **La numeración SETP / 18760000001 / 990000000–995000000 es la resolución pública del set de
  pruebas de la DIAN**, no una autorización del negocio. Mientras esté configurada, el tiquete
  sale como *"Comprobante interno de venta – No es factura ni documento equivalente
  electrónico"*. Si la DIAN le autoriza una numeración propia, escríbala en *Ajustes › Empresa
  y DIAN* y el tiquete mostrará la resolución.

## 8. Notas técnicas

- Utilidad neta real = (precio de venta − descuento prorrateado) − costo, guardada por prenda al
  vender. Descuentos prorrateados en centenas; reintegros parciales redondeados a $50.
- Ventas Totales del Dashboard = netas (descontando anulaciones y devoluciones).
- Efectivo esperado = base + ventas en efectivo − devoluciones en efectivo de la caja abierta.
- Auditoría (*Ajustes › Datos › Exportar auditoría*): cambios de precio/stock, importaciones,
  aperturas y cierres de caja, devoluciones y cambios de configuración.
- El formato `$ #,##0` de Excel usa el separador de miles de Windows (Colombia → `$ 1.250.000`).
- Desarrollo: `pip install -r requirements.txt` y `python main.py`.
  `R4_DATA_DIR=<carpeta>` usa otra carpeta de datos (pruebas).
