# Funcionamiento del sistema

ConfeccionesR4 POS está organizado en módulos accesibles desde la barra lateral
(`Ctrl+1` … `Ctrl+6`). Todos trabajan sobre una misma base de datos local, así que una venta se
refleja de inmediato en el inventario, la caja, el dashboard y los reportes.

## Flujo general

```
Apertura de caja ──► Ventas / Separados / Cambios ──► Arqueo ciego ──► Cierre interno
        │                       │                                          │
        │                       ├─► Inventario (stock se descuenta solo)    │
        │                       └─► Historial de documentos (inalterable)   │
        └──────────────────────────── Auditoría de todo el día ◄────────────┘
```

## Dashboard

Primera pantalla del programa. Muestra ventas netas, utilidad real, cantidad de tiquetes,
ticket promedio, participación por método de pago y prendas más vendidas, con filtros por día y
por mes. Las ventas netas ya descuentan anulaciones y devoluciones.

## Vender

- La prenda se agrega escaneando su etiqueta, buscando por nombre (`F2`) o tocándola en la lista.
- Cada línea admite cantidad y descuento; los descuentos superiores al 30 % del subtotal piden la
  clave de administrador.
- Métodos de pago: **Efectivo**, **Nequi**, **Bre-B**, **Datáfono** y **Mixto** (combinación de
  efectivo con un medio electrónico, con cálculo de cambio).
- Al cobrar (`F12`) se genera el comprobante numerado, se guarda su copia en PDF y, si está
  activado, se imprime el tiquete y se abre el cajón en pagos con efectivo.
- `F7` convierte el carrito en un **separado**.

El sistema no permite vender prendas sin precio o sin existencias y avisa cuando el monto
recibido parece mal digitado.

## Documentos

Historial completo de tiquetes, devoluciones, separados y cambios. Desde aquí se puede:

- **Reimprimir** cualquier comprobante.
- **Procesar devolución**: se eligen las unidades a devolver y el motivo (obligatorio). Las prendas
  vuelven al inventario y el valor se reintegra por el método original. Si se devuelve todo, el
  tiquete queda **ANULADO**; si no, **DEVOLUCIÓN PARCIAL**.
- **Cambio de prenda**: en un solo paso se recibe la prenda, se entrega otra y se liquida la
  diferencia en cualquier sentido.
- **Corregir método de pago** mientras la caja del día siga abierta.

## Separados

Reserva de prendas para un cliente con un abono inicial. Las prendas salen del inventario
disponible mientras el separado esté vigente.

- Abonos posteriores por cualquier método.
- **Entrega** cuando el saldo llega a cero.
- **Anulación** con devolución total o parcial de lo abonado.

Cada movimiento imprime su comprobante y queda en el historial.

## Inventario

- Creación y edición de productos: nombre, marca, categoría, talla, color, código, costo, precio y
  stock mínimo.
- **Importar Excel** reconoce el formato de inventario de la tienda y permite elegir la hoja.
  **Deshacer última importación** revierte la carga completa.
- **Exportar en formato original** devuelve las mismas columnas con el stock actualizado.
- **Etiquetas**: se imprimen con código de barras (EAN-13 o Code128) y una línea con
  **marca · categoría · talla**. La cantidad de copias es exacta: en rollos de varias columnas,
  las posiciones sobrantes de la última fila salen en blanco.
- **Conteo físico**: se escanean las prendas, el sistema compara contra lo registrado y propone
  los ajustes, que se aplican con motivo.
- Los productos ya vendidos no se eliminan: se desactivan y pueden reactivarse.

## Caja

1. **Apertura** con la base inicial en efectivo.
2. Durante el día: **entradas** y **salidas/gastos** de efectivo, y registro de cada apertura del
   cajón (venta, devolución, abono, cambio o manual con motivo).
3. **Arqueo ciego**: se cuenta el efectivo sin ver la cifra esperada. Si no cuadra, se pide un
   segundo conteo; si la diferencia persiste, se exige una observación.
4. Se registra el total del cierre del datáfono y lo recibido por Nequi y Bre-B para compararlo con
   las ventas.
5. **Cierre interno**: queda guardado y consultable en el programa; no se imprime.

```
Efectivo esperado = base + ventas en efectivo + abonos y entradas en efectivo
                    − devoluciones y salidas en efectivo
```

Opcionalmente, el resumen del cierre puede enviarse al celular del propietario (WhatsApp o
Telegram) desde *Ajustes › Nube y celular*.

## Reportes de gestión

Se abren desde el **Dashboard** o desde **Inventario** › *Reportes de gestión*.


- Mercancía quieta (sin rotación en el periodo elegido).
- Ventas por categoría, talla, color y marca.
- Comparativo mensual.
- Sugerido de compra según rotación y stock mínimo.

Todos se consultan en pantalla y se exportan a Excel con formato.

## Reglas de cálculo

- **Utilidad real por prenda** = (precio de venta − descuento prorrateado) − costo, guardada en el
  momento de la venta para que cambios de costo posteriores no alteren el histórico.
- Los descuentos se prorratean en centenas; los reintegros parciales se redondean a $50.
- Los valores se manejan en pesos colombianos enteros con formato `$ 1.250.000`.
