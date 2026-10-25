"""Control de tracción diferencial (skid steering) con límites de seguridad.

Flujo de un comando del joystick ``(x, y)`` en el rango [-1, 1]:

1. **Mezcla skid steering**: ``izq = y + x·k_giro``, ``der = y − x·k_giro``,
   normalizado para no exceder 1.
2. **Límite de velocidad**: se escala por la velocidad máxima configurada
   (0.3 m/s por defecto, ISO 3691-4) → velocidad objetivo de cada oruga.
3. **Rampa**: la velocidad de referencia cambia como máximo
   ``aceleracion × dt`` por ciclo (arranques y frenados suaves).
4. **Lazo de velocidad** (opcional, con encoders): prealimentación +
   controlador PI por oruga → ciclo de trabajo del PWM.

Los paros (watchdog, desconexión, paro de emergencia) NO usan rampa: ponen
los motores en cero de inmediato.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from ..drivers.base import DriverEncoders, DriverMotores
from .watchdog import Watchdog

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
#  Funciones puras (probadas en tests/test_control.py)
# ----------------------------------------------------------------------------
def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


def mezclar_skid(x: float, y: float, factor_giro: float = 1.0) -> tuple[float, float]:
    """Convierte joystick (x = giro, y = avance) a fracciones de velocidad por oruga."""
    x = _limitar(float(x), -1.0, 1.0)
    y = _limitar(float(y), -1.0, 1.0)
    giro = x * factor_giro
    izq, der = y + giro, y - giro
    escala = max(1.0, abs(izq), abs(der))
    return izq / escala, der / escala


def aplicar_rampa(actual: float, objetivo: float, delta_max: float) -> float:
    """Acerca ``actual`` a ``objetivo`` sin cambiar más de ``delta_max``."""
    diferencia = objetivo - actual
    if abs(diferencia) <= delta_max:
        return objetivo
    return actual + math.copysign(delta_max, diferencia)


class ControlPI:
    """Controlador proporcional-integral con anti-windup por saturación."""

    def __init__(self, kp: float, ki: float, limite_integral: float = 0.5):
        self.kp, self.ki = kp, ki
        self.limite_integral = limite_integral
        self.integral = 0.0

    def paso(self, error: float, dt: float) -> float:
        self.integral = _limitar(self.integral + error * dt * self.ki,
                                 -self.limite_integral, self.limite_integral)
        return self.kp * error + self.integral

    def reiniciar(self) -> None:
        self.integral = 0.0


@dataclass
class ParametrosTraccion:
    velocidad_maxima: float = 0.30       # m/s
    aceleracion: float = 0.40            # m/s²
    diametro_rueda: float = 0.040        # m
    separacion_orugas: float = 0.18      # m
    factor_giro: float = 0.6
    lazo_cerrado: bool = True
    kp: float = 0.6
    ki: float = 2.5
    rpm_nominales: float = 330.0
    duty_minimo: float = 0.22

    @property
    def velocidad_libre(self) -> float:
        """Velocidad lineal de la oruga con duty = 1 (m/s)."""
        return self.rpm_nominales / 60.0 * math.pi * self.diametro_rueda

    def duty_prealimentado(self, velocidad: float) -> float:
        """Duty estimado para una velocidad (modelo lineal con zona muerta)."""
        if abs(velocidad) < 1e-4:
            return 0.0
        fraccion = min(1.0, abs(velocidad) / self.velocidad_libre)
        return math.copysign(self.duty_minimo + (1.0 - self.duty_minimo) * fraccion, velocidad)


# ----------------------------------------------------------------------------
#  Controlador principal
# ----------------------------------------------------------------------------
class ControladorTraccion:
    def __init__(self, motores: DriverMotores, encoders: DriverEncoders,
                 parametros: ParametrosTraccion, cuentas_por_vuelta: int,
                 watchdog_s: float = 0.5, periodo_s: float = 0.02,
                 reloj: Callable[[], float] = time.monotonic,
                 al_evento: Callable[[str, str], None] | None = None):
        self.motores = motores
        self.encoders = encoders
        self.p = parametros
        self.cpr = max(1, int(cuentas_por_vuelta))
        self.periodo = periodo_s
        self._reloj = reloj
        self.al_evento = al_evento
        self._bloqueo = threading.RLock()
        self.watchdog = Watchdog(watchdog_s, self._por_watchdog, reloj)
        self.paro_emergencia_activo = False
        self.objetivo = [0.0, 0.0]       # m/s solicitado
        self.referencia = [0.0, 0.0]     # m/s tras la rampa
        self.medida = [0.0, 0.0]         # m/s medido por encoders
        self.duty = [0.0, 0.0]
        self._pi = [ControlPI(parametros.kp, parametros.ki), ControlPI(parametros.kp, parametros.ki)]
        self._conteos_prev = self.encoders.leer_conteos()
        self._t_prev = reloj()
        self._hilo: threading.Thread | None = None
        self._activo = threading.Event()
        self.ultimo_paro = ""
        self.motores.detener()  # Seguridad: motores en cero al crear el controlador

    # ------------------------------------------------------------- comandos
    def comando(self, x: float, y: float) -> bool:
        """Comando del operador (joystick/teclado). Devuelve False si se ignoró."""
        with self._bloqueo:
            if self.paro_emergencia_activo:
                return False
            if not all(math.isfinite(v) for v in (x, y)):
                return False
            izq, der = mezclar_skid(x, y, self.p.factor_giro)
            vmax = self.p.velocidad_maxima
            self.objetivo = [izq * vmax, der * vmax]
            self.watchdog.alimentar()
            return True

    def detener(self, motivo: str = "") -> None:
        """Paro inmediato (sin rampa). No bloquea comandos posteriores."""
        with self._bloqueo:
            self.objetivo = [0.0, 0.0]
            self.referencia = [0.0, 0.0]
            self.duty = [0.0, 0.0]
            for pi in self._pi:
                pi.reiniciar()
            self.watchdog.desarmar()
            self.motores.detener()
            if motivo:
                self.ultimo_paro = motivo
                log.info("Paro: %s", motivo)

    def paro_emergencia(self) -> None:
        """Paro enclavado: ignora comandos hasta que se llame a :meth:`rearmar`."""
        with self._bloqueo:
            self.paro_emergencia_activo = True
            self.detener("Paro de emergencia")
        self._emitir("paro", "PARO DE EMERGENCIA activado")

    def rearmar(self) -> None:
        with self._bloqueo:
            self.detener()
            self.paro_emergencia_activo = False
        self._emitir("rearme", "Sistema rearmado")

    def _por_watchdog(self) -> None:
        en_movimiento = any(abs(v) > 1e-3 for v in self.objetivo + self.referencia)
        self.detener("Watchdog: sin comandos en %d ms" % round(self.watchdog.tiempo_limite * 1000)
                     if en_movimiento else "")
        if en_movimiento:
            self._emitir("watchdog", "Paro por watchdog: se perdieron los comandos de movimiento")

    def _emitir(self, tipo: str, mensaje: str) -> None:
        if self.al_evento:
            try:
                self.al_evento(tipo, mensaje)
            except Exception:
                log.exception("Error notificando evento")

    def actualizar_parametros(self, **cambios) -> None:
        with self._bloqueo:
            for clave, valor in cambios.items():
                if hasattr(self.p, clave):
                    setattr(self.p, clave, valor)
            for pi in self._pi:
                pi.kp, pi.ki = self.p.kp, self.p.ki

    # ---------------------------------------------------------- lazo de control
    def paso(self) -> None:
        """Un ciclo de control. Se llama periódicamente (50 Hz)."""
        self.watchdog.verificar()
        with self._bloqueo:
            ahora = self._reloj()
            dt = ahora - self._t_prev
            if dt <= 0:
                return
            self._t_prev = ahora

            # 1) Velocidad medida por encoders (m/s), con filtro exponencial
            conteos = self.encoders.leer_conteos()
            metros_por_cuenta = math.pi * self.p.diametro_rueda / self.cpr
            for i in (0, 1):
                v = (conteos[i] - self._conteos_prev[i]) * metros_por_cuenta / dt
                self.medida[i] += 0.5 * (v - self.medida[i])
            self._conteos_prev = conteos

            if self.paro_emergencia_activo:
                if any(self.duty):
                    self.motores.detener()
                    self.duty = [0.0, 0.0]
                return

            # 2) Rampa de aceleración y límite de velocidad
            vmax = self.p.velocidad_maxima
            delta = self.p.aceleracion * dt
            for i in (0, 1):
                objetivo = _limitar(self.objetivo[i], -vmax, vmax)
                self.referencia[i] = aplicar_rampa(self.referencia[i], objetivo, delta)

            # 3) Ciclo de trabajo: prealimentación + PI
            lazo = self.p.lazo_cerrado and self.encoders.disponible
            limite = min(1.0, abs(self.p.duty_prealimentado(vmax * 1.3)))
            duty = [0.0, 0.0]
            for i in (0, 1):
                ref = self.referencia[i]
                if abs(ref) < 1e-4:
                    self._pi[i].reiniciar()
                    continue
                d = self.p.duty_prealimentado(ref)
                if lazo:
                    error = (ref - self.medida[i]) / self.p.velocidad_libre
                    d += self._pi[i].paso(error, dt)
                    # No invertir el sentido por acción del PI (evita oscilaciones)
                    if d * ref < 0:
                        d = 0.0
                duty[i] = _limitar(d, -limite, limite)
            self.duty = duty
            self.encoders.indicar_sentido(int(math.copysign(1, duty[0])) if duty[0] else 0,
                                          int(math.copysign(1, duty[1])) if duty[1] else 0)
            self.motores.aplicar(duty[0], duty[1])

    def _bucle(self) -> None:
        siguiente = time.monotonic()
        while self._activo.is_set():
            try:
                self.paso()
            except Exception:
                log.exception("Error en el lazo de control; deteniendo motores")
                self.detener("Error interno del control")
            siguiente += self.periodo
            espera = siguiente - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            else:
                siguiente = time.monotonic()

    def iniciar(self) -> None:
        if self._hilo and self._hilo.is_alive():
            return
        self._activo.set()
        self._hilo = threading.Thread(target=self._bucle, name="control-traccion", daemon=True)
        self._hilo.start()

    def finalizar(self) -> None:
        self._activo.clear()
        if self._hilo:
            self._hilo.join(timeout=1.0)
        self.detener("Cierre del programa")

    # ----------------------------------------------------------------- estado
    def estado(self) -> dict:
        with self._bloqueo:
            k = 60.0 / (math.pi * self.p.diametro_rueda)
            return {
                "v_izq": round(self.medida[0], 3),
                "v_der": round(self.medida[1], 3),
                "rpm_izq": round(self.medida[0] * k, 1),
                "rpm_der": round(self.medida[1] * k, 1),
                "ref_izq": round(self.referencia[0], 3),
                "ref_der": round(self.referencia[1], 3),
                "duty_izq": round(self.duty[0], 3),
                "duty_der": round(self.duty[1], 3),
                "paro_emergencia": self.paro_emergencia_activo,
                "lazo_cerrado": self.p.lazo_cerrado and self.encoders.disponible,
                "velocidad_maxima": self.p.velocidad_maxima,
                "ultimo_paro": self.ultimo_paro,
            }

    def parametros(self) -> dict:
        return asdict(self.p)
