"""Configuración común de pytest: rutas y fixtures en modo simulación."""

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("ROBOT_SIM", "1")


class RelojFalso:
    """Reloj controlable para probar tiempos sin esperas reales."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def avanzar(self, segundos: float) -> None:
        self.t += segundos


@pytest.fixture
def reloj():
    return RelojFalso()


@pytest.fixture
def config_temporal(tmp_path):
    from robot.config import Configuracion

    return Configuracion(sobrescribir={"datos": {"directorio": str(tmp_path / "data")}})


@pytest.fixture
def robot_sim(config_temporal):
    from robot.logica.robot import Robot

    r = Robot(config_temporal, simulacion=True)
    yield r
    r.cerrar()
