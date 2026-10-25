"""Driver real de los motores DC con puente H L298N usando pigpio.

Conexiones (ver config.yaml):

    L298N   Raspberry Pi      Función
    -----   ---------------   -------------------------------
    ENA     GPIO18 (PWM0)     Velocidad motor izquierdo
    IN1     GPIO23            Dirección motor izquierdo
    IN2     GPIO24            Dirección motor izquierdo
    ENB     GPIO13 (PWM1)     Velocidad motor derecho
    IN3     GPIO27            Dirección motor derecho
    IN4     GPIO22            Dirección motor derecho

GPIO18 y GPIO13 pertenecen a canales distintos del PWM por hardware del
BCM2837, por lo que ambos motores pueden tener ciclos de trabajo
independientes sin "jitter" de software. El PWM por hardware comparte
periférico con el audio analógico, por eso el instalador agrega
``dtparam=audio=off``.
"""

from __future__ import annotations

import logging

from .base import DriverMotores

log = logging.getLogger(__name__)


class MotoresL298N(DriverMotores):
    nombre = "L298N (pigpio, PWM por hardware)"

    def __init__(self, pi, pines: dict, frecuencia_hz: int = 2000,
                 invertir_izq: bool = False, invertir_der: bool = False):
        import pigpio  # importación diferida: solo existe en la Pi

        self._pigpio = pigpio
        self.pi = pi
        self.frecuencia = int(frecuencia_hz)
        self._lados = {
            "izq": dict(pines["motor_izquierdo"], invertir=invertir_izq),
            "der": dict(pines["motor_derecho"], invertir=invertir_der),
        }
        for lado in self._lados.values():
            for pin in (lado["in1"], lado["in2"]):
                pi.set_mode(pin, pigpio.OUTPUT)
                pi.write(pin, 0)
            pi.set_mode(lado["pwm"], pigpio.OUTPUT)
        self._ultimo = (0.0, 0.0)
        self.detener()  # Seguridad: motores en cero al arrancar
        self.detalle = f"PWM {self.frecuencia} Hz en GPIO{self._lados['izq']['pwm']}/GPIO{self._lados['der']['pwm']}"
        log.info("Motores L298N inicializados (%s)", self.detalle)

    def _aplicar_lado(self, lado: dict, duty: float) -> None:
        duty = max(-1.0, min(1.0, float(duty)))
        if lado["invertir"]:
            duty = -duty
        if duty > 0:
            self.pi.write(lado["in1"], 1)
            self.pi.write(lado["in2"], 0)
        elif duty < 0:
            self.pi.write(lado["in1"], 0)
            self.pi.write(lado["in2"], 1)
        else:
            self.pi.write(lado["in1"], 0)
            self.pi.write(lado["in2"], 0)
        # hardware_PWM: ciclo de trabajo en millonésimas (0 - 1 000 000)
        self.pi.hardware_PWM(lado["pwm"], self.frecuencia, int(abs(duty) * 1_000_000))

    def aplicar(self, duty_izq: float, duty_der: float) -> None:
        self._aplicar_lado(self._lados["izq"], duty_izq)
        self._aplicar_lado(self._lados["der"], duty_der)
        self._ultimo = (float(duty_izq), float(duty_der))

    def detener(self) -> None:
        for lado in self._lados.values():
            try:
                self.pi.hardware_PWM(lado["pwm"], self.frecuencia, 0)
                self.pi.write(lado["in1"], 0)
                self.pi.write(lado["in2"], 0)
            except Exception:  # pragma: no cover - solo en hardware
                log.exception("Error al detener motores")
        self._ultimo = (0.0, 0.0)

    def cerrar(self) -> None:
        self.detener()
        for lado in self._lados.values():
            try:
                self.pi.write(lado["pwm"], 0)
            except Exception:  # pragma: no cover
                pass
