"""Watchdog de comandos de movimiento.

Si no llega un comando de movimiento dentro del tiempo límite (500 ms por
defecto), se ejecuta la acción de seguridad (detener los motores). La
interfaz web envía comandos cada 100 ms mientras el operador mueve el
joystick o mantiene presionada una tecla, así que una pérdida de red, un
navegador congelado o una pestaña en segundo plano detienen el robot.

El reloj es inyectable para poder probar el comportamiento sin esperas
reales (ver ``tests/test_watchdog.py``).
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class Watchdog:
    def __init__(self, tiempo_limite_s: float = 0.5,
                 al_expirar: Callable[[], None] | None = None,
                 reloj: Callable[[], float] = time.monotonic):
        if tiempo_limite_s <= 0:
            raise ValueError("El tiempo límite del watchdog debe ser positivo")
        self.tiempo_limite = tiempo_limite_s
        self.al_expirar = al_expirar
        self._reloj = reloj
        self._bloqueo = threading.Lock()
        self._ultimo: float | None = None   # None = sin comando pendiente
        self.expiraciones = 0

    def alimentar(self) -> None:
        """Registra la llegada de un comando de movimiento."""
        with self._bloqueo:
            self._ultimo = self._reloj()

    def desarmar(self) -> None:
        """Olvida el último comando (p. ej. tras un paro explícito)."""
        with self._bloqueo:
            self._ultimo = None

    @property
    def armado(self) -> bool:
        return self._ultimo is not None

    def tiempo_desde_ultimo(self) -> float | None:
        with self._bloqueo:
            return None if self._ultimo is None else self._reloj() - self._ultimo

    def verificar(self) -> bool:
        """Revisa el plazo. Devuelve True (y ejecuta la acción una sola vez) si expiró."""
        with self._bloqueo:
            if self._ultimo is None or self._reloj() - self._ultimo <= self.tiempo_limite:
                return False
            self._ultimo = None
            self.expiraciones += 1
        if self.al_expirar:
            self.al_expirar()
        return True
