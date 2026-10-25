"""Interfaces comunes de los drivers de hardware.

Cada componente físico tiene una clase abstracta con dos implementaciones:
una real (Raspberry Pi) y una simulada (laptop, ``ROBOT_SIM=1``). El resto del
programa solo conoce estas interfaces, por lo que la lógica, el servidor web y
la interfaz son idénticos en ambos modos.

Además se incluyen implementaciones "nulas" que se usan cuando un componente
real falla al iniciar: el sistema sigue funcionando y la interfaz informa del
problema en lugar de detenerse.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class Driver(ABC):
    """Base de todos los drivers: nombre descriptivo, disponibilidad y cierre."""

    nombre: str = "driver"
    disponible: bool = True
    detalle: str = ""

    def estado(self) -> dict:
        return {"nombre": self.nombre, "disponible": self.disponible, "detalle": self.detalle}

    def cerrar(self) -> None:  # pragma: no cover - implementación opcional
        pass


class DriverMotores(Driver):
    """Dos motores DC (oruga izquierda y derecha)."""

    @abstractmethod
    def aplicar(self, duty_izq: float, duty_der: float) -> None:
        """Aplica ciclos de trabajo con signo en el rango [-1, 1]."""

    @abstractmethod
    def detener(self) -> None:
        """Pone ambos motores en cero inmediatamente."""

    @property
    def ultimo_duty(self) -> tuple[float, float]:
        return getattr(self, "_ultimo", (0.0, 0.0))


class DriverEncoders(Driver):
    """Encoders de cuadratura de ambos motores."""

    @abstractmethod
    def leer_conteos(self) -> tuple[int, int]:
        """Conteos acumulados (izquierdo, derecho), con signo."""

    def indicar_sentido(self, sentido_izq: int, sentido_der: int) -> None:
        """Sentido comandado (solo lo usa el modo de un canal)."""


class DriverCamaraTermica(Driver):
    """Sensor térmico matricial."""

    filas: int = 24
    columnas: int = 32

    @abstractmethod
    def leer_cuadro(self) -> np.ndarray | None:
        """Devuelve una matriz (24, 32) en °C (calculada con ε=0.95) o None."""


class DriverCamaraVisible(Driver):
    """Cámara de espectro visible."""

    @abstractmethod
    def leer_cuadro(self) -> np.ndarray | None:
        """Devuelve un cuadro BGR (alto, ancho, 3) uint8 o None."""


# ----------------------------------------------------------------------------
#  Implementaciones nulas (componente ausente o con falla)
# ----------------------------------------------------------------------------
class MotoresNulos(DriverMotores):
    nombre = "Motores no disponibles"
    disponible = False

    def __init__(self, detalle: str = ""):
        self.detalle = detalle
        self._ultimo = (0.0, 0.0)

    def aplicar(self, duty_izq: float, duty_der: float) -> None:
        self._ultimo = (0.0, 0.0)

    def detener(self) -> None:
        self._ultimo = (0.0, 0.0)


class EncodersNulos(DriverEncoders):
    nombre = "Encoders no disponibles"
    disponible = False

    def __init__(self, detalle: str = ""):
        self.detalle = detalle

    def leer_conteos(self) -> tuple[int, int]:
        return 0, 0


class CamaraTermicaNula(DriverCamaraTermica):
    nombre = "Cámara térmica no disponible"
    disponible = False

    def __init__(self, detalle: str = ""):
        self.detalle = detalle

    def leer_cuadro(self) -> np.ndarray | None:
        return None


class CamaraVisibleNula(DriverCamaraVisible):
    nombre = "Cámara visible no disponible"
    disponible = False

    def __init__(self, detalle: str = ""):
        self.detalle = detalle

    def leer_cuadro(self) -> np.ndarray | None:
        return None
