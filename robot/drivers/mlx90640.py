"""Driver real de la cámara térmica Melexis MLX90640 (32×24 píxeles, FOV 55°×35°).

Se conecta al bus I2C-1 de la Raspberry Pi (SDA = GPIO2, SCL = GPIO3,
dirección 0x33) y usa la biblioteca ``adafruit-circuitpython-mlx90640``.

Notas de desempeño en la Raspberry Pi 3 B+:

* El MLX90640 entrega cada cuadro en dos "subpáginas" (patrón de ajedrez).
  ``getFrame`` lee ambas, así que la frecuencia del sensor se configura al
  doble de la frecuencia de cuadros completos deseada (4 Hz → REFRESH_8_HZ).
* El bus I2C debe ir a 400 kHz o más (``dtparam=i2c_arm_baudrate=400000`` en
  ``/boot/firmware/config.txt``); a 100 kHz no se alcanzan 4 Hz.
* La biblioteca calcula las temperaturas con emisividad fija de 0.95; la
  corrección a la emisividad configurada se hace en
  :func:`robot.logica.analisis_termico.corregir_emisividad`.
"""

from __future__ import annotations

import logging

import numpy as np

from .base import DriverCamaraTermica

log = logging.getLogger(__name__)


class CamaraMLX90640(DriverCamaraTermica):
    nombre = "MLX90640 (I2C 0x33)"

    def __init__(self, frecuencia_hz: float = 4, i2c_frecuencia_hz: int = 400000,
                 rotar_180: bool = False, espejo_horizontal: bool = False):
        import adafruit_mlx90640
        import board
        import busio

        self._i2c = busio.I2C(board.SCL, board.SDA, frequency=int(i2c_frecuencia_hz))
        self._mlx = adafruit_mlx90640.MLX90640(self._i2c)
        tasas = {
            1: adafruit_mlx90640.RefreshRate.REFRESH_1_HZ,
            2: adafruit_mlx90640.RefreshRate.REFRESH_2_HZ,
            4: adafruit_mlx90640.RefreshRate.REFRESH_4_HZ,
            8: adafruit_mlx90640.RefreshRate.REFRESH_8_HZ,
            16: adafruit_mlx90640.RefreshRate.REFRESH_16_HZ,
        }
        subpaginas_hz = min(tasas, key=lambda t: abs(t - 2 * frecuencia_hz))
        self._mlx.refresh_rate = tasas[subpaginas_hz]
        self._buffer = [0.0] * 768
        self.rotar_180 = rotar_180
        self.espejo = espejo_horizontal
        self._errores = 0
        serie = "-".join(f"{x:04X}" for x in self._mlx.serial_number)
        self.detalle = f"Serie {serie}, subpáginas a {subpaginas_hz} Hz"
        log.info("MLX90640 inicializada (%s)", self.detalle)

    def leer_cuadro(self) -> np.ndarray | None:
        for _ in range(3):
            try:
                self._mlx.getFrame(self._buffer)
                break
            except (ValueError, RuntimeError, OSError) as e:
                # Errores de I2C esporádicos ("Too many retries"): reintentar
                self._errores += 1
                log.debug("Error leyendo MLX90640: %s", e)
        else:
            return None
        matriz = np.asarray(self._buffer, dtype=np.float32).reshape(24, 32)
        if self.rotar_180:
            matriz = matriz[::-1, ::-1]
        if self.espejo:
            matriz = matriz[:, ::-1]
        return np.ascontiguousarray(matriz)

    def estado(self) -> dict:
        d = super().estado()
        d["errores_i2c"] = self._errores
        return d

    def cerrar(self) -> None:
        try:
            self._i2c.deinit()
        except Exception:  # pragma: no cover
            pass
