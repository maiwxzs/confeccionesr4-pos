# Operación diaria

## Rutina del día

| Momento | Acción | Dónde |
|---|---|---|
| Al abrir la tienda | Abrir caja con la base | **Caja** › Apertura |
| Durante el día | Vender, separar, cambiar prendas | **Vender** · **Separados** · **Documentos** |
| Al cerrar la tienda | Arqueo ciego y cierre | **Caja** › Arqueo ciego y cierre |

## Atajos de teclado

| Tecla | Acción |
|---|---|
| `Ctrl+1` … `Ctrl+6` | Cambiar de módulo |
| `F2` | Buscar prenda |
| `F5` | Nueva venta |
| `F7` | Separar |
| `F8` | Quitar la última prenda |
| `F9` | Reimprimir el último tiquete |
| `F10` | Abrir el cajón (pide motivo) |
| `F12` | Cobrar |
| `Esc` | Limpiar la búsqueda |

## Tareas frecuentes

| Necesito… | Cómo |
|---|---|
| Reimprimir un tiquete | **Documentos** › buscar o escanear el código › *Reimprimir* |
| Devolver una prenda | **Documentos** › tiquete › *Procesar devolución* |
| Cambiar una prenda por otra | **Documentos** › *Cambio de prenda* |
| Registrar un abono | **Separados** › separado › *Abonar* |
| Imprimir etiquetas | **Inventario** › seleccionar productos › *Etiquetas* › copias |
| Cargar mercancía nueva | **Inventario** › *Importar Excel* o *Nuevo producto* |
| Contar el inventario | **Inventario** › *Conteo físico* |
| Ver cierres anteriores | **Caja** › historial de cierres |

## Corrección de errores

Nada se borra en silencio: cada corrección queda registrada en la auditoría.

| Error | Solución | Dónde |
|---|---|---|
| Se registró un método de pago equivocado | *Corregir método de pago* (con la caja abierta) | Documentos |
| Una venta completa estaba mal | *Procesar devolución / Anular* (clave) | Documentos |
| Algunas prendas de una venta | Devolución parcial con cantidades y motivo | Documentos |
| Base inicial incorrecta | *Corregir base inicial* (clave) | Caja |
| Caja abierta por error | *Anular apertura* (solo sin ventas) | Caja |
| Caja cerrada antes de tiempo | *Reabrir último cierre* (clave) | Caja |
| Gasto mal registrado | *Anular movimiento* (clave) y registrarlo de nuevo | Caja |
| Producto creado por error | *Eliminar* (si ya se vendió, se desactiva) | Inventario |
| Importación equivocada | *Deshacer última importación* | Inventario |
| Producto desactivado | *Ver desactivados* › *Reactivar* | Inventario |
| Volver a un estado anterior | *Restaurar un respaldo* | Ajustes › Datos |

## Dinero electrónico

Los pagos con datáfono, Nequi y Bre-B no entran al cajón, por eso el arqueo cuenta solo efectivo.
Al cerrar se digitan los totales de esos medios y el reporte de cierre muestra cualquier diferencia
contra lo vendido.

## Clave de administrador

Se crea en *Ajustes › Seguridad* y protege descuentos altos, anulaciones, reaperturas de caja y
cambios sensibles de configuración.
