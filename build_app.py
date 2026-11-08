"""
build_app.py — Compilación automatizada de ConfeccionesR4 POS para Windows.

Uso (en el PC de DESARROLLO con Windows 10/11 x64 y Python 3.11+):

    python build_app.py                       # UN SOLO archivo: dist/ConfeccionesR4/ConfeccionesR4.exe
    python build_app.py --instalar-deps       # instala requirements.txt antes de compilar
    python build_app.py --vcredist            # descarga vc_redist.x64.exe para el instalador
    python build_app.py --instalador          # además compila el instalador con Inno Setup
    python build_app.py --carpeta             # modo carpeta (--onedir), solo para depurar

Pasos:
  1. Verifica entorno y dependencias.
  2. Genera una base de datos LIMPIA (esquema + datos fiscales) que se empaqueta en data/.
  3. Escribe la ficha de versión del .exe (propiedades de Windows).
  4. Ejecuta PyInstaller --onefile: UN .exe autocontenido con Python runtime, Qt, pywin32,
     DLL de Visual C++, ícono, base de datos limpia y pantalla de carga (--splash).
     --windowed (sin consola) e --icon=assets/logo_r4.ico.
  5. (modo carpeta) Copia los DLL de Visual C++ junto al .exe.
  6. (Opcional) Compila installer_setup.iss con ISCC.exe → Instalador_ConfeccionesR4_v1.0.exe.

El PC del cliente NO necesita Python, pip ni nada adicional.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import config as cfg  # noqa: E402

NOMBRE = "ConfeccionesR4"
ICONO = RAIZ / "assets" / "logo_r4.ico"
DIST = RAIZ / "dist"
BUILD = RAIZ / "build"
TMP_DATA = BUILD / "_datos_empaquetados"
VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

# Módulos que NO se usan: reducen ~150 MB el paquete final y aceleran el arranque
EXCLUIR = [
    "tkinter", "matplotlib", "scipy", "IPython", "jupyter", "notebook", "pytest", "sphinx",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DExtras", "PySide6.Qt3DAnimation",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtHttpServer",
    "PySide6.QtSpatialAudio", "PySide6.QtUiTools", "PySide6.QtXml", "PySide6.QtHelp",
    "PySide6.QtOpenGLWidgets", "PySide6.QtSvgWidgets", "PySide6.QtNetworkAuth",
]
OCULTOS = ["win32print", "win32ui", "win32con", "win32timezone", "PySide6.QtCharts",
           "openpyxl.cell._writer", "barcode", "barcode.writer", "escpos", "escpos.printer",
           "cryptography.hazmat.primitives.asymmetric.ed25519",
           "cryptography.hazmat.primitives.serialization"]
DLLS_VC = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll",
           "msvcp140_2.dll", "concrt140.dll"]


def paso(n: int, txt: str) -> None:
    print(f"\n\033[1;34m[{n}] {txt}\033[0m")


def verificar_entorno(instalar: bool) -> None:
    if sys.platform != "win32":
        print("⚠  Este script debe ejecutarse en Windows para producir un .exe de Windows.")
    if sys.version_info < (3, 11):
        sys.exit("Se requiere Python 3.11 o superior.")
    if instalar:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "-r",
                               str(RAIZ / "requirements.txt")])
    faltan = []
    for mod in ("PySide6", "openpyxl", "PIL", "barcode", "PyInstaller", "pandas", "cryptography"):
        try:
            __import__(mod)
        except ImportError:
            faltan.append(mod)
    if sys.platform == "win32":
        try:
            __import__("win32print")
        except ImportError:
            faltan.append("pywin32")
    if faltan:
        sys.exit(f"Faltan dependencias: {', '.join(faltan)}\n"
                 f"Ejecute:  python build_app.py --instalar-deps")
    if not ICONO.exists():
        sys.exit(f"No se encontró el ícono: {ICONO}")
    if not (RAIZ / "assets" / "clave_publica_actualizaciones.pem").exists():
        print("⚠  No hay clave de actualizaciones: este .exe NO podrá actualizarse de forma remota.\n"
              "   Ejecute una vez:  python publicar_actualizacion.py --generar-claves   y vuelva a compilar.")


def escribir_build_info(version: str | None, url: str | None) -> None:
    """Fija versión y URL de actualizaciones dentro del .exe (assets/build_info.json)."""
    destino = RAIZ / "assets" / "build_info.json"
    info = {}
    if destino.exists():
        try:
            info = json.loads(destino.read_text(encoding="utf-8"))
        except ValueError:
            info = {}
    if version:
        info["version"] = version
    if url:
        info["url_actualizaciones"] = url
    if info:
        destino.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg.APP_VERSION = info.get("version", cfg.APP_VERSION)
        cfg.URL_ACTUALIZACIONES = info.get("url_actualizaciones", cfg.URL_ACTUALIZACIONES)
        print(f"   versión {cfg.APP_VERSION} · actualizaciones: {cfg.URL_ACTUALIZACIONES or '(sin URL)'}")


def generar_bd_limpia() -> Path:
    from database import crear_bd_limpia
    TMP_DATA.mkdir(parents=True, exist_ok=True)
    destino = TMP_DATA / cfg.DB_FILENAME
    os.environ.pop("R4_DB_PATH", None)
    crear_bd_limpia(destino)
    print(f"   BD limpia: {destino} ({destino.stat().st_size // 1024} KB)")
    return destino


def ficha_version() -> Path:
    v = tuple(int(x) for x in cfg.APP_VERSION.split(".")) + (0,)
    texto = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('240A04B0', [
      StringStruct('CompanyName', 'CONFECCIONESR4 - NIT 700571557-9'),
      StringStruct('FileDescription', '{cfg.APP_NAME} - Punto de Venta e Inventario'),
      StringStruct('FileVersion', '{cfg.APP_VERSION}'),
      StringStruct('InternalName', '{NOMBRE}'),
      StringStruct('LegalCopyright', 'CONFECCIONESR4 - Bogotá D.C.'),
      StringStruct('OriginalFilename', '{NOMBRE}.exe'),
      StringStruct('ProductName', '{cfg.APP_NAME}'),
      StringStruct('ProductVersion', '{cfg.APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [0x240A, 1200])])
  ]
)
"""
    ruta = BUILD / "version_info.txt"
    BUILD.mkdir(exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def _hay_tk() -> bool:
    try:
        import tkinter  # noqa: F401  (PyInstaller lo necesita para la pantalla de carga)
        return True
    except ImportError:
        return False


def ejecutar_pyinstaller(onefile: bool, version: Path) -> None:
    import PyInstaller.__main__
    sep = ";" if sys.platform == "win32" else ":"
    splash = onefile and _hay_tk() and (RAIZ / "assets" / "splash.png").exists()
    args = [
        str(RAIZ / "main.py"),
        "--name", NOMBRE,
        "--noconfirm", "--clean",
        "--windowed",                         # = --noconsole: oculta la consola negra
        f"--icon={ICONO}",
        "--onefile" if onefile else "--onedir",
        # Salida: dist/ConfeccionesR4/ConfeccionesR4.exe (un solo archivo)
        "--distpath", str(DIST / NOMBRE if onefile else DIST),
        "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        "--add-data", f"{RAIZ / 'assets'}{sep}assets",
        "--add-data", f"{TMP_DATA}{sep}data",
        "--collect-data", "barcode",          # fuentes internas de python-barcode
        "--version-file", str(version),
        "--noupx",                            # UPX dispara falsos positivos de antivirus
    ]
    if splash:
        args += ["--splash", str(RAIZ / "assets" / "splash.png")]
    else:
        print("   (sin pantalla de carga: tkinter no está disponible en este Python)")
    for m in OCULTOS:
        args += ["--hidden-import", m]
    for m in EXCLUIR:
        if m == "tkinter" and splash:
            continue
        args += ["--exclude-module", m]
    print("   pyinstaller " + " ".join(args[:12]) + " …")
    PyInstaller.__main__.run(args)


def copiar_runtime_vc(onefile: bool) -> None:
    """Copia los DLL de Visual C++ junto al .exe si PyInstaller no los incluyó."""
    if sys.platform != "win32" or onefile:
        return
    destino = DIST / NOMBRE / "_internal"
    if not destino.exists():
        destino = DIST / NOMBRE
    origenes = [Path(sys.base_prefix), Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"]
    for dll in DLLS_VC:
        if any(destino.rglob(dll)):
            continue
        for o in origenes:
            if (o / dll).exists():
                shutil.copy2(o / dll, destino / dll)
                print(f"   + {dll}")
                break


def descargar_vcredist() -> None:
    destino = RAIZ / "redist" / "vc_redist.x64.exe"
    if destino.exists():
        print(f"   Ya existe {destino}")
        return
    destino.parent.mkdir(exist_ok=True)
    print(f"   Descargando {VCREDIST_URL} …")
    urllib.request.urlretrieve(VCREDIST_URL, destino)
    print(f"   OK ({destino.stat().st_size // 1_048_576} MB)")


def compilar_instalador() -> None:
    candidatos = [
        shutil.which("ISCC.exe") or "",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"),
    ]
    iscc = next((c for c in candidatos if c and Path(c).exists()), None)
    if not iscc:
        print("⚠  No se encontró Inno Setup 6 (ISCC.exe). Descárguelo de https://jrsoftware.org/isdl.php\n"
              "   y abra installer_setup.iss → Build › Compile.")
        return
    subprocess.check_call([iscc, f"/DMyAppVersion={cfg.APP_VERSION}", str(RAIZ / "installer_setup.iss")],
                          cwd=str(RAIZ))
    salida = RAIZ / "installer_output" / "Instalador_ConfeccionesR4_v1.0.exe"
    if salida.exists():
        print(f"   ✅ Instalador: {salida} ({salida.stat().st_size // 1_048_576} MB)")


def tamano_carpeta(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1_048_576


def main() -> None:
    ap = argparse.ArgumentParser(description="Compila ConfeccionesR4 POS")
    ap.add_argument("--carpeta", action="store_true", help="Modo carpeta (--onedir) para depurar")
    ap.add_argument("--instalar-deps", action="store_true", help="pip install -r requirements.txt")
    ap.add_argument("--vcredist", action="store_true", help="Descargar vc_redist.x64.exe para el instalador")
    ap.add_argument("--instalador", action="store_true", help="Compilar también el instalador Inno Setup")
    ap.add_argument("--version", help="Versión a grabar en el .exe (la usa GitHub Actions: 1.0.<n>)")
    ap.add_argument("--url-actualizaciones", help="URL del latest.json que consultarán los POS instalados")
    a = ap.parse_args()

    onefile = not a.carpeta
    t0 = time.time()
    paso(1, "Verificando entorno")
    escribir_build_info(a.version, a.url_actualizaciones)
    verificar_entorno(a.instalar_deps)
    paso(2, "Generando base de datos limpia")
    generar_bd_limpia()
    paso(3, "Ficha de versión del ejecutable")
    version = ficha_version()
    paso(4, "Empaquetando con PyInstaller (puede tardar 2-5 min)")
    ejecutar_pyinstaller(onefile, version)
    paso(5, "Runtime de Visual C++")
    copiar_runtime_vc(onefile)
    if a.vcredist:
        paso(6, "Descargando Visual C++ Redistributable")
        descargar_vcredist()
    exe = DIST / NOMBRE / f"{NOMBRE}.exe"
    if not exe.exists():
        sys.exit("❌ No se generó el ejecutable. Revise los mensajes de PyInstaller.")
    tam = exe.stat().st_size / 1_048_576 if onefile else tamano_carpeta(exe.parent)
    print(f"\n✅ Ejecutable listo: {exe}  ({tam:.0f} MB)  en {time.time() - t0:.0f} s")
    if a.instalador:
        paso(7, "Compilando instalador Inno Setup")
        if not onefile:
            print("⚠  El instalador espera el .exe único. Recompile sin --carpeta.")
        else:
            compilar_instalador()


if __name__ == "__main__":
    main()
