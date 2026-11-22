# Datos, respaldos y seguridad

## Dónde se guarda la información

| Qué | Ubicación |
|---|---|
| Programa | `C:\Program Files\ConfeccionesR4\` |
| Base de datos | `C:\ProgramData\ConfeccionesR4\confeccionesr4.db` |
| Respaldos automáticos (últimos 30) | `C:\ProgramData\ConfeccionesR4\respaldos\` |
| Registro de errores | `C:\ProgramData\ConfeccionesR4\logs\` |
| Reportes de Excel exportados | `Documentos\ConfeccionesR4 Reportes\` |
| Copia PDF de cada comprobante | `Documentos\ConfeccionesR4 Tiquetes\AAAA\MM\` |

Actualizar o desinstalar el programa no borra los datos.

## Respaldos

- **Locales**: diario, al cerrar caja y al salir del programa. Se conservan los 30 más recientes.
- **En la nube** (opcional): copia en una carpeta de Google Drive para escritorio, configurable en
  *Ajustes › Nube y celular*. El programa detecta la carpeta de Drive automáticamente.
- **Restaurar**: *Ajustes › Datos › Restaurar un respaldo*. Antes de restaurar se guarda un
  respaldo del estado actual.
- **Empezar de cero**: *Ajustes › Datos › Borrar datos*, con opción de conservar el inventario y
  siempre con respaldo previo.

## Integridad del historial

Las ventas, sus detalles, las devoluciones, los separados, el historial de documentos y la
auditoría están protegidos en la base de datos: no se pueden borrar ni modificar, solo
complementar con nuevos movimientos (devoluciones, anulaciones, correcciones). Así cada peso
tiene un rastro verificable.

## Auditoría

Queda registro de cambios de precio y stock, importaciones, aperturas y cierres de caja,
aperturas del cajón, devoluciones, correcciones y cambios de configuración. Se exporta desde
*Ajustes › Datos › Exportar auditoría*.

## Control de acceso

La clave de administrador (*Ajustes › Seguridad*) protege las operaciones sensibles: descuentos
mayores al 30 %, anulaciones, reapertura de caja, corrección de base y anulación de movimientos.

## Privacidad de la empresa

- Los datos del negocio (razón social, NIT, dirección, teléfono, resolución) se registran en el
  asistente de configuración y se guardan únicamente en la base de datos local.
- El código fuente no contiene información de la tienda, de sus clientes ni de sus ventas.
- El archivo `.gitignore` impide subir bases de datos, respaldos, registros, hojas de cálculo,
  PDF y claves privadas al repositorio.

## Facturación y DIAN

- El programa emite un **comprobante interno de venta** numerado. No genera factura electrónica
  ni documento equivalente electrónico POS, y no transmite información a la DIAN.
- Desde el 1 de julio de 2024, quienes están obligados a facturar deben usar factura electrónica o
  el documento equivalente electrónico tiquete POS (Resolución DIAN 000165 de 2023), mediante un
  proveedor tecnológico. Las personas naturales no responsables de IVA que cumplan los requisitos
  pueden estar entre los no obligados. La situación de la empresa debe confirmarse con su contador.
- Si la DIAN autoriza una numeración propia, se registra en *Ajustes › Empresa y DIAN* y el
  comprobante mostrará la resolución.
