"""Procesamiento de los sensores ambientales reutilizados del proyecto TechRoach.

Sensores: DHT22 (temperatura y humedad), MQ-2 (gases inflamables), MQ-7 (CO),
PIR (presencia), piezoeléctrico (impactos/caídas) y LDR (luz).

Respuestas automáticas (mismas reglas de TechRoach):

========================  ===================================================
Condición                 Acción
========================  ===================================================
Humedad > 80 %            Velocidad reducida (factor configurable)
MQ-2 o MQ-7 en alarma     Alerta visual/acústica + detención. Mientras dure,
                          solo se permite retroceder para salir de la zona.
PIR detecta presencia     Velocidad reducida durante unos segundos
Impacto (piezoeléctrico)  Detención y registro del evento
Poca luz (LDR)            Enciende la iluminación propia del robot
========================  ===================================================

Medidas contra falsas alarmas de los MQ (el principal desafío técnico
reportado en TechRoach):

* **Calentamiento**: durante los primeros minutos no se generan alarmas.
* **Filtro de mediana** de varias muestras (elimina picos aislados).
* **Confirmación**: se requieren N lecturas seguidas sobre el umbral.
* **Histéresis**: la alarma se libera solo al bajar un % por debajo del umbral.

Conversión de voltaje a ppm (modelo de la hoja de datos de Hanwei):

    Rs = RL · (Vc − Vout) / Vout        resistencia del sensor
    R0 = Rs(aire limpio) / razón_aire    calibración
    ppm = a · (Rs / R0)^b                curva log-log del gas

Las ppm son una **estimación**: los MQ no son instrumentos certificados.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

NORMAL, ADVERTENCIA, ALARMA = "normal", "advertencia", "alarma"
ORDEN_NIVEL = {NORMAL: 0, ADVERTENCIA: 1, ALARMA: 2}


# ----------------------------------------------------------------------------
#  Funciones puras de conversión
# ----------------------------------------------------------------------------
def rs_desde_voltaje(v_salida: float, vc: float = 5.0, rl_kohm: float = 1.0) -> float | None:
    """Resistencia del sensor MQ (kΩ) a partir del voltaje en la salida AO."""
    if v_salida is None or v_salida <= 0.001:
        return None
    v_salida = min(v_salida, vc - 1e-6)
    return rl_kohm * (vc - v_salida) / v_salida


def ppm_desde_razon(razon: float | None, a: float, b: float) -> float | None:
    """Concentración estimada: ppm = a · (Rs/R0)^b."""
    if razon is None or razon <= 0:
        return None
    return a * razon ** b


def voltaje_desde_ppm(ppm: float, a: float, b: float, r0_kohm: float,
                      vc: float = 5.0, rl_kohm: float = 1.0) -> float:
    """Inversa de la cadena anterior (se usa en la simulación y en las pruebas)."""
    razon = (ppm / a) ** (1.0 / b)
    rs = razon * r0_kohm
    return vc * rl_kohm / (rl_kohm + rs)


def r0_desde_rs(rs_aire_limpio: float, razon_aire_limpio: float) -> float:
    """R0 a partir de Rs medido en aire limpio."""
    return rs_aire_limpio / razon_aire_limpio


def porcentaje_luz(v_ldr: float | None, v_ref: float = 3.3, invertir: bool = False) -> float | None:
    """Nivel de luz 0-100 % a partir del voltaje del divisor del LDR."""
    if v_ldr is None:
        return None
    pct = max(0.0, min(100.0, v_ldr / v_ref * 100.0))
    return 100.0 - pct if invertir else pct


# ----------------------------------------------------------------------------
#  Filtros y detectores
# ----------------------------------------------------------------------------
class FiltroMediana:
    def __init__(self, n: int = 5):
        self.muestras: deque[float] = deque(maxlen=max(1, int(n)))

    def agregar(self, valor: float | None) -> float | None:
        if valor is not None and math.isfinite(valor):
            self.muestras.append(valor)
        if not self.muestras:
            return None
        ordenadas = sorted(self.muestras)
        return ordenadas[len(ordenadas) // 2]

    def reiniciar(self) -> None:
        self.muestras.clear()


class DetectorUmbral:
    """Nivel normal/advertencia/alarma con confirmación e histéresis."""

    def __init__(self, advertencia: float, alarma: float, confirmaciones: int = 3,
                 histeresis_pct: float = 10.0):
        self.advertencia = advertencia
        self.alarma = alarma
        self.confirmaciones = max(1, int(confirmaciones))
        self.histeresis = histeresis_pct / 100.0
        self.nivel = NORMAL
        self._candidato = NORMAL
        self._cuenta = 0

    def _nivel_instantaneo(self, valor: float) -> str:
        # Para bajar de nivel se exige quedar por debajo del umbral menos la histéresis
        k = 1.0 - self.histeresis
        umbral_alarma = self.alarma * (k if self.nivel == ALARMA else 1.0)
        umbral_adv = self.advertencia * (k if self.nivel in (ADVERTENCIA, ALARMA) else 1.0)
        if valor >= umbral_alarma:
            return ALARMA
        if valor >= umbral_adv:
            return ADVERTENCIA
        return NORMAL

    def actualizar(self, valor: float | None) -> str:
        if valor is None:
            return self.nivel
        inst = self._nivel_instantaneo(valor)
        if ORDEN_NIVEL[inst] <= ORDEN_NIVEL[self.nivel]:
            # Bajar (o mantener) es inmediato gracias a la histéresis
            self.nivel = inst
            self._candidato, self._cuenta = inst, 0
            return self.nivel
        # Subir de nivel requiere confirmaciones consecutivas
        if inst == self._candidato:
            self._cuenta += 1
        else:
            self._candidato, self._cuenta = inst, 1
        if self._cuenta >= self.confirmaciones:
            self.nivel = inst
            self._cuenta = 0
        return self.nivel

    def reiniciar(self) -> None:
        self.nivel, self._candidato, self._cuenta = NORMAL, NORMAL, 0


# ----------------------------------------------------------------------------
#  Decodificación del DHT22 (independiente del hardware, se prueba con pytest)
# ----------------------------------------------------------------------------
def decodificar_dht22(duraciones_alto_us: list[int]) -> tuple[float, float]:
    """Convierte la duración de los 40 pulsos en alto del DHT22 en (temperatura °C, humedad %).

    Cada bit es un pulso en alto de ~26-28 µs (0) o ~70 µs (1). Se usan los
    últimos 40 pulsos (los anteriores son la respuesta del sensor).
    """
    if len(duraciones_alto_us) < 40:
        raise ValueError(f"DHT22: se recibieron {len(duraciones_alto_us)} bits de 40")
    bits = [1 if d > 50 else 0 for d in duraciones_alto_us[-40:]]
    datos = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, 40, 8)]
    if (sum(datos[:4]) & 0xFF) != datos[4]:
        raise ValueError("DHT22: error de suma de verificación")
    humedad = ((datos[0] << 8) | datos[1]) / 10.0
    temperatura = (((datos[2] & 0x7F) << 8) | datos[3]) / 10.0
    if datos[2] & 0x80:
        temperatura = -temperatura
    if not (0.0 <= humedad <= 100.0 and -40.0 <= temperatura <= 80.0):
        raise ValueError("DHT22: lectura fuera de rango")
    return temperatura, humedad


# ----------------------------------------------------------------------------
#  Procesador principal
# ----------------------------------------------------------------------------
@dataclass
class Evento:
    tipo: str          # gas_mq2, gas_mq7, humedad, presencia, impacto, oscuridad
    nivel: str         # info, advertencia, alarma, normal
    mensaje: str


@dataclass
class EstadoAmbiente:
    temperatura: float | None = None
    humedad: float | None = None
    mq2_ppm: float | None = None
    mq7_ppm: float | None = None
    mq2_nivel: str = NORMAL
    mq7_nivel: str = NORMAL
    luz_pct: float | None = None
    oscuro: bool = False
    pir: bool | None = None
    presencia: bool = False
    impactos: int = 0
    ultimo_impacto: float | None = None
    bateria_v: float | None = None
    calentando: bool = False
    calentamiento_restante_s: float = 0.0
    humedad_alta: bool = False
    factor_velocidad: float = 1.0
    bloqueo: str | None = None     # None | "avance" (solo retroceso) | "total"
    alarma: bool = False
    avisos: list[str] = field(default_factory=list)

    def a_dict(self) -> dict:
        def r(v, d=1):
            return None if v is None else round(float(v), d)
        return {
            "temperatura": r(self.temperatura), "humedad": r(self.humedad),
            "mq2_ppm": r(self.mq2_ppm, 0), "mq7_ppm": r(self.mq7_ppm, 0),
            "mq2_nivel": self.mq2_nivel, "mq7_nivel": self.mq7_nivel,
            "luz_pct": r(self.luz_pct, 0), "oscuro": self.oscuro,
            "pir": self.pir, "presencia": self.presencia, "impactos": self.impactos,
            "bateria_v": r(self.bateria_v, 2), "calentando": self.calentando,
            "calentamiento_restante_s": round(self.calentamiento_restante_s),
            "humedad_alta": self.humedad_alta, "factor_velocidad": round(self.factor_velocidad, 2),
            "bloqueo": self.bloqueo, "alarma": self.alarma, "avisos": list(self.avisos),
        }


class ProcesadorAmbiente:
    """Convierte lecturas crudas en valores, niveles de alarma y respuestas automáticas."""

    def __init__(self, cfg_sensores: dict, cfg_respuestas: dict,
                 reloj: Callable[[], float] = time.monotonic):
        self._reloj = reloj
        self.t_inicio = reloj()
        self.configurar(cfg_sensores, cfg_respuestas)
        self.estado = EstadoAmbiente()
        self._impactos_previos: int | None = None
        self._t_presencia_hasta = 0.0
        self._t_detenido_desde: float | None = None
        self._pir_previo = False

    def configurar(self, s: dict, r: dict) -> None:
        self.s, self.r = s, r
        conf = max(1, int(s.get("confirmaciones", 3)))
        hist = float(s.get("histeresis_pct", 10))
        if not hasattr(self, "detectores"):
            n = int(s.get("muestras_filtro", 5))
            self.filtros = {gas: FiltroMediana(n) for gas in ("mq2", "mq7")}
            self.detectores = {gas: DetectorUmbral(float(s[gas]["advertencia_ppm"]),
                                                   float(s[gas]["alarma_ppm"]), conf, hist)
                               for gas in ("mq2", "mq7")}
            return
        # Reconfiguración en caliente (cambios desde la página Configuración)
        for gas, det in self.detectores.items():
            det.advertencia = float(s[gas]["advertencia_ppm"])
            det.alarma = float(s[gas]["alarma_ppm"])
            det.confirmaciones, det.histeresis = conf, hist / 100.0

    def reiniciar_calentamiento(self) -> None:
        self.t_inicio = self._reloj()

    def ppm(self, gas: str, v_sensor: float | None) -> float | None:
        g = self.s[gas]
        rs = rs_desde_voltaje(v_sensor, float(g.get("vc", 5.0)), float(g.get("rl_kohm", 1.0)))
        if rs is None:
            return None
        return ppm_desde_razon(rs / float(g["r0_kohm"]), float(g["curva_a"]), float(g["curva_b"]))

    def procesar(self, crudo: dict, en_movimiento: bool = False) -> tuple[EstadoAmbiente, list[Evento]]:
        ahora = self._reloj()
        e = self.estado
        eventos: list[Evento] = []
        e.avisos = []

        # --- Temperatura y humedad (DHT22) --------------------------------
        if crudo.get("temperatura") is not None:
            e.temperatura = crudo["temperatura"]
        if crudo.get("humedad") is not None:
            e.humedad = crudo["humedad"]
        umbral_h = float(self.r.get("humedad_alta_pct", 80))
        if e.humedad is not None:
            alta = e.humedad > umbral_h if not e.humedad_alta else e.humedad > umbral_h - 3
            if alta != e.humedad_alta:
                eventos.append(Evento("humedad", "advertencia" if alta else "normal",
                                      f"Humedad {'alta' if alta else 'normal'}: {e.humedad:.0f} % HR"
                                      + (" → velocidad reducida" if alta else "")))
            e.humedad_alta = alta

        # --- Gases (MQ-2 / MQ-7) ------------------------------------------
        restante = float(self.s.get("calentamiento_s", 180)) - (ahora - self.t_inicio)
        e.calentando = restante > 0
        e.calentamiento_restante_s = max(0.0, restante)
        nombres = {"mq2": "Gas inflamable (MQ-2)", "mq7": "Monóxido de carbono (MQ-7)"}
        for gas in ("mq2", "mq7"):
            ppm = self.filtros[gas].agregar(self.ppm(gas, crudo.get(f"v_{gas}")))
            setattr(e, f"{gas}_ppm", ppm)
            previo = getattr(e, f"{gas}_nivel")
            if e.calentando:
                self.detectores[gas].reiniciar()
                nivel = NORMAL
            elif ppm is not None:
                nivel = self.detectores[gas].actualizar(ppm)
            elif crudo.get(f"{gas}_digital") is not None:
                # Respaldo sin ADC: salida DO del módulo (umbral del potenciómetro)
                det = self.detectores[gas]
                nivel = det.actualizar(det.alarma if crudo[f"{gas}_digital"] else 0.0)
            else:
                nivel = NORMAL
            setattr(e, f"{gas}_nivel", nivel)
            if nivel != previo:
                txt = f" ({ppm:.0f} ppm)" if ppm is not None else ""
                eventos.append(Evento(f"gas_{gas}", nivel if nivel != NORMAL else "normal",
                                      f"{nombres[gas]}: {nivel.upper()}{txt}"))
        if e.calentando:
            e.avisos.append(f"Sensores de gas en calentamiento ({e.calentamiento_restante_s:.0f} s)")
        alarma_gas = ALARMA in (e.mq2_nivel, e.mq7_nivel)

        # --- Luz (LDR) con histéresis -------------------------------------
        ldr = self.s.get("ldr", {})
        e.luz_pct = porcentaje_luz(crudo.get("v_ldr"), invertir=bool(ldr.get("invertir", False)))
        if e.luz_pct is not None:
            umbral = float(ldr.get("umbral_oscuridad_pct", 25))
            hist = float(ldr.get("histeresis_pct", 8))
            oscuro = e.luz_pct < umbral if not e.oscuro else e.luz_pct < umbral + hist
            if oscuro != e.oscuro:
                eventos.append(Evento("oscuridad", "info",
                                      "Poca luz: iluminación encendida" if oscuro else "Luz suficiente: iluminación apagada"))
            e.oscuro = oscuro

        # --- PIR: solo es confiable con el robot detenido -----------------
        e.pir = crudo.get("pir")
        if en_movimiento:
            self._t_detenido_desde = None
        elif self._t_detenido_desde is None:
            self._t_detenido_desde = ahora
        en_reposo = self._t_detenido_desde is not None and ahora - self._t_detenido_desde >= 1.0
        if e.pir and en_reposo:
            if not self._pir_previo and ahora >= self._t_presencia_hasta:
                eventos.append(Evento("presencia", "advertencia",
                                      "Presencia detectada (PIR) → velocidad reducida"))
            self._t_presencia_hasta = ahora + float(self.r.get("duracion_presencia_s", 10))
        self._pir_previo = bool(e.pir and en_reposo)
        e.presencia = ahora < self._t_presencia_hasta

        # --- Impactos (piezoeléctrico) ------------------------------------
        impactos = crudo.get("impactos")
        if impactos is not None:
            if self._impactos_previos is not None and impactos > self._impactos_previos:
                e.ultimo_impacto = ahora
                eventos.append(Evento("impacto", "alarma",
                                      "Impacto o caída detectado (sensor piezoeléctrico)"))
            self._impactos_previos = impactos
            e.impactos = impactos

        # --- Batería --------------------------------------------------------
        e.bateria_v = crudo.get("v_bateria")

        # --- Respuestas automáticas ----------------------------------------
        factor = 1.0
        if e.humedad_alta:
            factor = min(factor, float(self.r.get("factor_humedad", 0.5)))
            e.avisos.append(f"Humedad > {umbral_h:.0f} %: velocidad al {factor * 100:.0f} %")
        if e.presencia:
            f = float(self.r.get("factor_presencia", 0.4))
            factor = min(factor, f)
            e.avisos.append(f"Presencia detectada: velocidad al {f * 100:.0f} %")
        e.factor_velocidad = factor
        retroceso = bool(self.r.get("permitir_retroceso_en_gas", True))
        e.bloqueo = ("avance" if retroceso else "total") if alarma_gas else None
        e.alarma = alarma_gas
        if alarma_gas:
            e.avisos.insert(0, "ALARMA DE GAS: robot detenido; "
                            + ("solo se permite retroceder" if retroceso else "movimiento bloqueado"))
        return e, eventos
