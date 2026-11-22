from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config as cfg

ARCHIVO_CLAVE = "clave_publica_actualizaciones.pem"
CAMPOS_FIRMADOS = ("version", "url", "sha256", "tamano", "notas")


class ErrorActualizacion(Exception):
    pass


@dataclass
class InfoActualizacion:
    version: str
    url: str
    sha256: str
    tamano: int
    notas: str


# Utilidades
def version_tupla(v: str) -> tuple[int, ...]:
    partes = []
    for p in str(v).strip().lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        partes.append(int(num) if num else 0)
    return tuple(partes + [0] * (4 - len(partes)))


def mensaje_firmado(manifiesto: dict) -> bytes:
    datos = {k: manifiesto.get(k) for k in CAMPOS_FIRMADOS}
    return json.dumps(datos, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def url_efectiva(url_configurada: str | None) -> str:
    return (url_configurada or "").strip() or cfg.URL_ACTUALIZACIONES


def clave_publica() -> bytes | None:
    p = cfg.resource_path("assets", ARCHIVO_CLAVE)
    return p.read_bytes() if p.exists() else None


def verificar_firma(manifiesto: dict, pem: bytes | None = None) -> None:
    pem = pem or clave_publica()
    if not pem:
        raise ErrorActualizacion("Este programa no tiene clave pública de actualizaciones: "
                                 "las actualizaciones remotas están desactivadas.")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError as e:
        raise ErrorActualizacion("Falta la librería 'cryptography' para verificar la firma.") from e
    try:
        clave = load_pem_public_key(pem)
        clave.verify(base64.b64decode(manifiesto.get("firma", "")), mensaje_firmado(manifiesto))
    except (InvalidSignature, ValueError, TypeError) as e:
        raise ErrorActualizacion("La firma de la actualización NO es válida. Se cancela por seguridad.") from e


def _contextos_ssl():
    import ssl
    try:
        import truststore
        yield "windows", truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi
        yield "certifi", ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    yield "python", ssl.create_default_context()


def _abrir(url: str, timeout: int = 10):
    import ssl
    req = urllib.request.Request(url, headers={"User-Agent": f"ConfeccionesR4-POS/{cfg.APP_VERSION}"})
    ultimo: Exception | None = None
    for _nombre, ctx in _contextos_ssl():
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError) or "CERTIFICATE" in str(e).upper():
                ultimo = e
                continue
            raise
        except ssl.SSLError as e:
            ultimo = e
            continue
    raise ErrorActualizacion(
        "No se pudo verificar el certificado de seguridad del servidor de actualizaciones.\n"
        "Revise la fecha y hora de Windows, o si un antivirus está inspeccionando conexiones HTTPS.\n"
        f"Detalle: {ultimo}")


# API
def buscar(url_manifiesto: str, version_actual: str = cfg.APP_VERSION) -> InfoActualizacion | None:
    url_manifiesto = (url_manifiesto or "").strip()
    if not url_manifiesto.lower().startswith("https://"):
        raise ErrorActualizacion("La URL de actualizaciones debe empezar por https://")
    with _abrir(url_manifiesto) as r:
        manifiesto = json.loads(r.read(256_000).decode("utf-8"))
    verificar_firma(manifiesto)
    if version_tupla(manifiesto["version"]) <= version_tupla(version_actual):
        return None
    url = urllib.parse.urljoin(url_manifiesto, manifiesto["url"])
    if not url.lower().startswith("https://"):
        raise ErrorActualizacion("La descarga debe ser por https://")
    return InfoActualizacion(manifiesto["version"], url, manifiesto["sha256"].lower(),
                             int(manifiesto["tamano"]), manifiesto.get("notas") or "")


def descargar(info: InfoActualizacion, progreso: Callable[[int, int], None] | None = None,
              carpeta: Path | None = None) -> Path:
    carpeta = carpeta or Path(tempfile.gettempdir())
    destino = carpeta / f"ConfeccionesR4_{info.version}.descarga"
    h = hashlib.sha256()
    leidos = 0
    with _abrir(info.url, timeout=30) as r, open(destino, "wb") as f:
        while True:
            bloque = r.read(256 * 1024)
            if not bloque:
                break
            leidos += len(bloque)
            if leidos > info.tamano:
                break
            h.update(bloque)
            f.write(bloque)
            if progreso:
                progreso(leidos, info.tamano)
    if leidos != info.tamano or h.hexdigest() != info.sha256:
        destino.unlink(missing_ok=True)
        raise ErrorActualizacion("El archivo descargado no coincide con el publicado (tamaño o SHA-256).")
    return destino


def ejecutable_actual() -> Path:
    if not getattr(sys, "frozen", False):
        raise ErrorActualizacion("La actualización automática solo funciona en el programa compilado (.exe).")
    return Path(sys.executable)


def _reintentar(accion, intentos: int = 24, espera: float = 0.5) -> None:
    for i in range(intentos):
        try:
            accion()
            return
        except PermissionError:
            if i == intentos - 1:
                raise
            time.sleep(espera)


def _nombre_viejo(exe: Path) -> Path:
    viejo = exe.with_name(exe.stem + ".old.exe")
    try:
        viejo.unlink(missing_ok=True)
    except OSError:
        viejo = exe.with_name(f"{exe.stem}.old-{int(time.time())}.exe")
    return viejo


def aplicar(nuevo: Path) -> None:
    exe = ejecutable_actual()
    viejo = _nombre_viejo(exe)
    renombrado = False
    try:
        _reintentar(lambda: os.replace(exe, viejo))
        renombrado = True

        def poner_nuevo():
            try:
                os.replace(nuevo, exe)
            except PermissionError:
                raise
            except OSError:
                import shutil
                shutil.copy2(nuevo, exe)
                nuevo.unlink(missing_ok=True)
        _reintentar(poner_nuevo)
    except PermissionError:
        if renombrado and not exe.exists():
            try:
                os.replace(viejo, exe)
            except OSError:
                pass
        _aplicar_como_administrador(nuevo, exe)
        return
    except OSError:
        if renombrado and not exe.exists():
            os.replace(viejo, exe)
        raise
    _lanzar(exe)


def entorno_limpio() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI", "_MEIPASS")) and k.upper() not in ("TCL_LIBRARY", "TK_LIBRARY")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def relanzar(exe: Path | str | None = None, args: list[str] | None = None) -> None:
    exe = Path(exe or sys.executable)
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200
    if getattr(sys, "frozen", False):
        cmd = [str(exe)] + list(args or [])
    else:
        cmd = [sys.executable, str(Path(__file__).with_name("main.py"))] + list(args or [])
    subprocess.Popen(cmd, close_fds=True, creationflags=flags, cwd=str(exe.parent), env=entorno_limpio())


def _lanzar(exe: Path) -> None:
    relanzar(exe)


def _aplicar_como_administrador(nuevo: Path, exe: Path) -> None:
    import ctypes
    args = f'--aplicar-actualizacion "{exe}" {os.getpid()}'
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(nuevo), args, None, 1)
    if r <= 32:
        raise ErrorActualizacion(
            "Windows no dejó reemplazar el programa y la ventana de permiso se canceló.\n\n"
            "No se perdió nada: el programa sigue igual y lo volverá a intentar más tarde.\n"
            "Para actualizar ya: cierre TODAS las ventanas de ConfeccionesR4, ábralo de nuevo y, si "
            "Windows pregunta «¿Permitir que esta aplicación haga cambios?», pulse «Sí».")


def modo_aplicar_desde_argv(argv: list[str]) -> bool:
    if "--aplicar-actualizacion" not in argv:
        return False
    i = argv.index("--aplicar-actualizacion")
    destino = Path(argv[i + 1])
    for _ in range(60):
        try:
            if destino.exists():
                os.replace(destino, _nombre_viejo(destino))
            break
        except OSError:
            time.sleep(0.5)
    import shutil
    for _ in range(60):
        try:
            shutil.copy2(sys.executable, destino)
            break
        except OSError:
            time.sleep(0.5)
    subprocess.Popen(["explorer.exe", str(destino)], env=entorno_limpio())
    return True


def limpiar_restos() -> None:
    try:
        exe = ejecutable_actual()
    except ErrorActualizacion:
        return
    for p in exe.parent.glob(f"{exe.stem}.old*.exe"):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    for p in Path(tempfile.gettempdir()).glob("ConfeccionesR4_*.descarga"):
        try:
            p.unlink()
        except OSError:
            pass


def DialogoActualizacion(info: InfoActualizacion, parent=None):
    from PySide6.QtCore import QObject, Signal, Slot
    from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout

    from ui.animations import DialogoAnimado
    from ui.widgets import en_segundo_plano, error, etiqueta

    class _Puente(QObject):
        avance = Signal(int, int)

    class _Dialogo(DialogoAnimado):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Actualizando ConfeccionesR4 POS")
            self.setMinimumWidth(520)
            lay = QVBoxLayout(self)
            lay.addWidget(etiqueta(f"Descargando la versión {info.version}", titulo=True))
            self.estado = QLabel("Conectando…")
            lay.addWidget(self.estado)
            self.barra = QProgressBar()
            self.barra.setRange(0, 1000)
            lay.addWidget(self.barra)
            notas = etiqueta(info.notas, muted=True)
            notas.setWordWrap(True)
            lay.addWidget(notas)
            self.puente = _Puente()
            self.puente.avance.connect(self._avance)
            en_segundo_plano(lambda: descargar(info, self.puente.avance.emit), self._listo, self._fallo)

        @Slot(int, int)
        def _avance(self, n: int, total: int) -> None:
            self.barra.setValue(int(n * 1000 / max(total, 1)))
            self.estado.setText(f"{n / 1_048_576:.1f} MB de {total / 1_048_576:.1f} MB")

        def _listo(self, ruta) -> None:
            self.estado.setText("Firma y archivo verificados. Instalando y reiniciando…")
            try:
                aplicar(Path(ruta))
            except Exception as e:
                error(self, f"No se pudo aplicar la actualización:\n{e}")
                self.reject()
                return
            app = QApplication.instance()
            app.setProperty("actualizando", True)
            app.closeAllWindows()
            app.quit()

        def _fallo(self, msg: str) -> None:
            error(self, f"No se pudo descargar la actualización:\n{msg}")
            self.reject()

    return _Dialogo()
