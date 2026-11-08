@echo off
REM ==========================================================================
REM  PUBLICAR_ACTUALIZACION.bat - Compila y firma una nueva version para
REM  que todos los POS instalados se actualicen solos.
REM  Antes: suba APP_VERSION en config.py (ej. 1.0.0 -> 1.1.0)
REM ==========================================================================
chcp 65001 >nul
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
set /p NOTAS="Describa los cambios de esta version: "
python build_app.py || goto :fin
python publicar_actualizacion.py --notas "%NOTAS%"
echo.
echo Suba los 2 archivos de la carpeta "publicar" a su release de GitHub
echo (o a cualquier hosting HTTPS) con los mismos nombres.
:fin
pause
