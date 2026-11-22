from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import config as cfg
from updater import ARCHIVO_CLAVE, mensaje_firmado, verificar_firma

PRIVADA = RAIZ / "claves_privadas" / "actualizacion_ed25519.pem"
PUBLICA = RAIZ / "assets" / ARCHIVO_CLAVE


def generar_claves(forzar: bool) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if PRIVADA.exists() and not forzar:
        sys.exit(f"Ya existe {PRIVADA}. Si genera otra, los POS instalados NO aceptarán las nuevas\n"
                 "actualizaciones hasta reinstalarlos. Use --forzar solo si perdió la clave.")
    clave = Ed25519PrivateKey.generate()
    PRIVADA.parent.mkdir(exist_ok=True)
    PRIVADA.write_bytes(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    (PRIVADA.parent / "LEAME.txt").write_text(
        "Esta carpeta contiene la clave PRIVADA que firma las actualizaciones de ConfeccionesR4 POS.\n"
        "- NO la suba a GitHub ni la comparta.\n- Haga copia de seguridad: sin ella no podrá publicar "
        "actualizaciones para los equipos ya instalados.\n", encoding="utf-8")
    PUBLICA.write_bytes(clave.public_key().public_bytes(serialization.Encoding.PEM,
                                                        serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"Clave privada: {PRIVADA}\nClave pública: {PUBLICA}\nAhora compile con: python build_app.py")


def clave_desde_entorno(variable: str) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    texto = os.environ.get(variable, "").strip()
    if not texto:
        sys.exit(f"Falta el secreto {variable} (Settings › Secrets and variables › Actions).")
    if "BEGIN" not in texto:
        texto = base64.b64decode(texto).decode()
    clave = load_pem_private_key(texto.encode(), password=None)
    PRIVADA.parent.mkdir(exist_ok=True)
    PRIVADA.write_bytes(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    PUBLICA.write_bytes(clave.public_key().public_bytes(serialization.Encoding.PEM,
                                                        serialization.PublicFormat.SubjectPublicKeyInfo))
    print("Clave de firma cargada desde el secreto y clave pública derivada.")


def publicar(exe: Path, notas: str, url: str, salida: Path) -> None:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    if not PRIVADA.exists():
        sys.exit("No hay clave privada. Ejecute primero: python publicar_actualizacion.py --generar-claves")
    if not exe.exists():
        sys.exit(f"No existe {exe}. Compile primero con: python build_app.py")
    datos = exe.read_bytes()
    manifiesto = {"version": cfg.APP_VERSION, "url": url or exe.name,
                  "sha256": hashlib.sha256(datos).hexdigest(), "tamano": len(datos), "notas": notas}
    clave = load_pem_private_key(PRIVADA.read_bytes(), password=None)
    manifiesto["firma"] = base64.b64encode(clave.sign(mensaje_firmado(manifiesto))).decode()
    verificar_firma(manifiesto, PUBLICA.read_bytes())
    salida.mkdir(exist_ok=True)
    shutil.copy2(exe, salida / exe.name)
    (salida / "latest.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Versión {cfg.APP_VERSION} lista en {salida}:")
    print(f"  {exe.name}  ({len(datos) / 1_048_576:.1f} MB, sha256 {manifiesto['sha256'][:16]}…)")
    print("  latest.json (firmado)")
    print(f"\nSúbalos, por ejemplo:\n  gh release create v{cfg.APP_VERSION} {salida / exe.name} {salida / 'latest.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Firma y publica una actualización de ConfeccionesR4 POS.")
    ap.add_argument("--generar-claves", action="store_true")
    ap.add_argument("--forzar", action="store_true")
    ap.add_argument("--exe", default=str(RAIZ / "dist" / "ConfeccionesR4" / "ConfeccionesR4.exe"))
    ap.add_argument("--notas", default="Mejoras y correcciones.")
    ap.add_argument("--url", default="", help="URL de descarga del .exe (por defecto, junto a latest.json)")
    ap.add_argument("--salida", default=str(RAIZ / "publicar"))
    ap.add_argument("--clave-desde-env", metavar="VARIABLE", help="Cargar la clave privada desde una variable")
    a = ap.parse_args()
    if a.generar_claves:
        generar_claves(a.forzar)
    elif a.clave_desde_env:
        clave_desde_entorno(a.clave_desde_env)
    else:
        notas = os.environ.get("NOTAS_VERSION", "").strip() or a.notas
        publicar(Path(a.exe), notas[:600], a.url, Path(a.salida))


if __name__ == "__main__":
    main()
