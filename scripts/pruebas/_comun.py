"""Utilidades compartidas por los scripts de prueba de componentes."""

import logging
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from robot.config import Configuracion, modo_simulacion  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def cargar_config() -> Configuracion:
    return Configuracion()


def conectar_pigpio():
    """Devuelve una conexión a pigpiod o termina con un mensaje claro."""
    try:
        import pigpio
    except ImportError:
        sys.exit("ERROR: falta el módulo pigpio. Ejecuta scripts/instalar_pi.sh o 'pip install pigpio'.")
    pi = pigpio.pi()
    if not pi.connected:
        sys.exit("ERROR: pigpiod no está en ejecución. Ejecuta: sudo systemctl start pigpiod")
    return pi


def confirmar(pregunta: str) -> bool:
    try:
        return input(f"{pregunta} [s/N]: ").strip().lower() in ("s", "si", "sí", "y")
    except EOFError:
        return False


def simulacion() -> bool:
    return modo_simulacion() or "--sim" in sys.argv


def titulo(texto: str) -> None:
    print("\n" + "=" * 64 + f"\n  {texto}\n" + "=" * 64)


__all__ = ["RAIZ", "cargar_config", "conectar_pigpio", "confirmar", "simulacion", "titulo", "os"]
