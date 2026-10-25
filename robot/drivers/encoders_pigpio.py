"""Driver real de los encoders de cuadratura usando callbacks de pigpio.

pigpiod muestrea los GPIO cada 5 µs (DMA) y registra cada flanco con su marca
de tiempo, por lo que no se pierden pulsos aunque Python esté ocupado: los
eventos se entregan en lote al hilo de callbacks.

Resolución (configurable en ``encoders``):

    cuentas por vuelta de la rueda = pulsos_por_vuelta_motor
                                     × relacion_reduccion
                                     × modo_cuadratura

Con el motor típico de 330 rpm (11 pulsos, 1:30) y cuadratura ×4 se obtienen
1320 cuentas por vuelta de la rueda motriz.
"""

from __future__ import annotations

import logging
import threading

from .base import DriverEncoders

log = logging.getLogger(__name__)

# Tabla de transición de cuadratura: índice = (estado_anterior << 2) | estado_actual
# con estado = (A << 1) | B. Los valores 0 son transiciones inválidas o sin cambio.
TABLA_CUADRATURA = (0, -1, 1, 0,
                    1, 0, 0, -1,
                    -1, 0, 0, 1,
                    0, 1, -1, 0)


class _EncoderCuadratura:
    """Un encoder: decodificación ×4 (A y B) o ×1 (flancos de subida de A)."""

    def __init__(self, pi, gpio_a: int, gpio_b: int, modo: int,
                 filtro_us: int, invertir: bool):
        import pigpio

        self.pi = pi
        self.a, self.b = gpio_a, gpio_b
        self.modo = 4 if modo == 4 else 1
        self.signo = -1 if invertir else 1
        self.sentido = 1          # usado en modo ×1
        self.conteo = 0
        self._bloqueo = threading.Lock()
        for g in (gpio_a, gpio_b):
            pi.set_mode(g, pigpio.INPUT)
            pi.set_pull_up_down(g, pigpio.PUD_UP)
            if filtro_us > 0:
                pi.set_glitch_filter(g, int(filtro_us))
        self._nivel_a = pi.read(gpio_a)
        self._nivel_b = pi.read(gpio_b)
        self._estado = (self._nivel_a << 1) | self._nivel_b

        if self.modo == 4:
            self._cbs = [pi.callback(gpio_a, pigpio.EITHER_EDGE, self._cb4),
                         pi.callback(gpio_b, pigpio.EITHER_EDGE, self._cb4)]
        else:
            self._cbs = [pi.callback(gpio_a, pigpio.RISING_EDGE, self._cb1)]

    def _cb4(self, gpio: int, nivel: int, _tick: int) -> None:
        if nivel > 1:  # 2 = watchdog de pigpio (sin cambio)
            return
        if gpio == self.a:
            self._nivel_a = nivel
        else:
            self._nivel_b = nivel
        nuevo = (self._nivel_a << 1) | self._nivel_b
        paso = TABLA_CUADRATURA[(self._estado << 2) | nuevo]
        self._estado = nuevo
        if paso:
            with self._bloqueo:
                self.conteo += paso

    def _cb1(self, _gpio: int, nivel: int, _tick: int) -> None:
        if nivel == 1:
            with self._bloqueo:
                self.conteo += self.sentido

    def leer(self) -> int:
        with self._bloqueo:
            return self.conteo * self.signo

    def cancelar(self) -> None:
        for cb in self._cbs:
            cb.cancel()


class EncodersPigpio(DriverEncoders):
    nombre = "Encoders de cuadratura (pigpio)"

    def __init__(self, pi, pines: dict, modo: int = 4, filtro_us: int = 20,
                 invertir_izq: bool = False, invertir_der: bool = False):
        self.izq = _EncoderCuadratura(pi, pines["encoder_izquierdo"]["a"],
                                      pines["encoder_izquierdo"]["b"], modo,
                                      filtro_us, invertir_izq)
        self.der = _EncoderCuadratura(pi, pines["encoder_derecho"]["a"],
                                      pines["encoder_derecho"]["b"], modo,
                                      filtro_us, invertir_der)
        self.detalle = f"Cuadratura ×{self.izq.modo}"
        log.info("Encoders inicializados (%s)", self.detalle)

    def leer_conteos(self) -> tuple[int, int]:
        return self.izq.leer(), self.der.leer()

    def indicar_sentido(self, sentido_izq: int, sentido_der: int) -> None:
        if sentido_izq:
            self.izq.sentido = 1 if sentido_izq > 0 else -1
        if sentido_der:
            self.der.sentido = 1 if sentido_der > 0 else -1

    def cerrar(self) -> None:
        self.izq.cancelar()
        self.der.cancelar()
