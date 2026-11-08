@echo off
REM ==========================================================================
REM  COMPILAR.bat - Genera ConfeccionesR4.exe (UN solo archivo) y el instalador
REM  Requisitos en el PC de desarrollo: Python 3.11+ (64 bits) e Inno Setup 6
REM ==========================================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    py -3.11 -m venv .venv || python -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

REM Claves de firma para actualizaciones remotas (solo la primera vez)
if not exist "claves_privadas\actualizacion_ed25519.pem" (
    python publicar_actualizacion.py --generar-claves
    echo.
    echo  IMPORTANTE: haga copia de seguridad de la carpeta claves_privadas
    echo  (sin ella no podra publicar actualizaciones). NO la comparta.
    echo.
)

python build_app.py --vcredist --instalador
echo.
echo Listo:
echo   Programa:   dist\ConfeccionesR4\ConfeccionesR4.exe
echo   Instalador: installer_output\Instalador_ConfeccionesR4_v1.0.exe
pause
