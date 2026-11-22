# Hardware

## Impresora de tiquetes

Compatible con impresoras térmicas ESC/POS de 80 y 58 mm (probado con Jaltech).

1. Instalar el driver del fabricante y confirmar que la impresora aparezca en
   *Configuración › Impresoras* de Windows.
2. En *Ajustes › Impresora de tiquetes*: conexión **Windows**, elegir la impresora y el ancho.
3. *Imprimir página de prueba*. Si las tildes salen mal, cambiar la *Tabla de caracteres*.

El programa detecta automáticamente la impresora en el primer inicio.

## Cajón monedero

Se conecta a la impresora de tiquetes (puerto RJ11). Con la opción activada, se abre solo en
cobros, devoluciones y abonos en efectivo. `F10` lo abre manualmente y solicita un motivo. Cada
apertura queda registrada y aparece en el cierre de caja.

## Impresora de etiquetas

*Ajustes › Etiquetas* ofrece tres métodos:

| Método | Uso |
|---|---|
| Driver de Windows | Recomendado; funciona con cualquier impresora instalada |
| ESC/POS raster | Imprime la etiqueta en la impresora térmica de tiquetes |
| TSPL | Impresoras de etiquetas TSC / Xprinter en modo nativo |

Parámetros ajustables: ancho y alto de la etiqueta, etiquetas por fila (1 a 4 columnas),
separación, margen izquierdo, simbología y letra del nombre de la tienda. Tamaño por defecto
50 × 30 mm. El botón *Detectar* busca la impresora de etiquetas instalada.

Contenido de cada etiqueta:

```
┌──────────────────────────────┐
│        Confecciones R4       │
│  ║│║║│║│║║│║║│║│║║│║║│║│║║   │
│        7701234567890         │
│     WIDE LEG | JEAN | 8      │  ← marca | categoría | talla
│          $ 89.900            │
└──────────────────────────────┘
```

Con rollos de varias columnas se imprime exactamente el número de copias pedido; las posiciones
restantes de la última fila quedan en blanco.

## Lector de código de barras

Cualquier lector USB que funcione como teclado. Se usa en *Vender*, *Documentos* (para ubicar un
tiquete por su código) y *Conteo físico*.
