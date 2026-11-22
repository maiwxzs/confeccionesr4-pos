# Instalación y actualizaciones

## Instalación en la tienda

1. Descargar `Instalador_ConfeccionesR4_v1.0.exe` desde **Releases** (el enlace fijo
   `…/releases/latest/download/Instalador_ConfeccionesR4_v1.0.exe` siempre entrega la última
   versión).
2. Ejecutarlo y seguir el asistente: *Siguiente › Instalar › Finalizar*.
3. Completar el asistente de configuración inicial en el primer inicio.

### Windows bloquea el programa

El ejecutable no tiene firma de código comercial, por lo que Windows puede advertir:

- **SmartScreen**: *Más información › Ejecutar de todas formas*.
- **Control inteligente de aplicaciones** (Windows 11): bloquea programas sin firma y no ofrece
  excepción. Se desactiva en *Seguridad de Windows › Control de aplicaciones y navegador ›
  Configuración de Control inteligente de aplicaciones › Desactivado*.

## Actualizaciones automáticas

- El POS busca versiones nuevas al abrir y cada 3 horas.
- Si encuentra una, la descarga y la instala cuando no hay una venta en curso, y se reinicia.
- Solo se instalan archivos con **firma digital Ed25519 válida** y **SHA-256 correcto**. Si algo
  falla, se restaura el ejecutable anterior.
- *Ajustes › Actualizaciones* permite buscar manualmente o desactivar la instalación automática.

## Publicación de versiones

La compilación es automática con GitHub Actions (`.github/workflows/compilar-y-publicar.yml`).
Cada cambio subido a `main`:

1. Calcula la versión `<VERSION>.<n>` (por ejemplo `1.1.7`), donde `VERSION` es el archivo de la
   raíz y `n` la cantidad de cambios desde que se editó.
2. Compila el ejecutable y el instalador en Windows.
3. Firma el manifiesto `latest.json` con el secreto del repositorio `FIRMA_ACTUALIZACIONES`.
4. Publica un Release con el instalador, el ejecutable y el manifiesto.

Los cambios que solo tocan archivos `.md` (como esta documentación) no generan versión nueva.
Para publicar sin cambiar código: *Actions › Compilar y publicar › Run workflow*.

### Configuración inicial del repositorio

1. *Settings › Secrets and variables › Actions › New repository secret*
   - Nombre: `FIRMA_ACTUALIZACIONES`
   - Valor: contenido completo del archivo de firma privada.
2. Subir el código a la rama `main` y esperar a que *Compilar y publicar* termine en verde.

> La clave de firma privada nunca se sube al repositorio ni se comparte. Si se pierde, se genera
> una nueva con `publicar_actualizacion.py` y se reinstala una vez el programa en la tienda.

### Publicación manual

En un equipo con Python 3.11+ e Inno Setup 6: aumentar `APP_VERSION` y ejecutar
`PUBLICAR_ACTUALIZACION.bat`.
