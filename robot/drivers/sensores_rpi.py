"""Driver real de los sensores ambientales de TechRoach en la Raspberry Pi.

La Raspberry Pi no tiene entradas analógicas (el ESP32 de TechRoach sí), por
lo que los sensores analógicos se leen con un **ADS1115** (ADC de 16 bits,
I2C 0x48) en el mismo bus I2C-1 que el MLX90640:

=======  ==================================  ===============================
Canal    Señal                               Acondicionamiento
=======  ==================================  ===============================
A0       MQ-2 (AO)                           divisor 10 kΩ / 20 kΩ (5 V→3.3 V)
A1       MQ-7 (AO)                           divisor 10 kΩ / 20 kΩ
A2       LDR                                 divisor con resistencia de 10 kΩ
A3       Batería LiPo 2S                     divisor 10 kΩ / 4.7 kΩ
=======  ==================================  ===============================

Sensores digitales (con pigpio, que ya está en uso para motores y encoders):

* DHT22 en GPIO4: protocolo de un hilo decodificado midiendo los pulsos con
  las marcas de tiempo de pigpio (µs), sin bibliotecas adicionales.
* PIR en GPIO25 (nivel lógico).
* Módulo piezoeléctrico/vibración en GPIO20: cada flanco activo cuenta un
  impacto (con antirrebote de 300 ms).
* Salidas: zumbador (GPIO21), LED de alarma (GPIO19) e iluminación (GPIO26).

Si no hay ADS1115, las alarmas de gas usan las salidas digitales DO de los
módulos MQ (GPIO7 y GPIO8).
"""

from __future__ import annotations

import logging
import threading
import time

from ..logica.ambiente import decodificar_dht22
from .base import DriverSensoresAmbiente

log = logging.getLogger(__name__)


class ADS1115:
    """Lectura de un solo disparo del ADS1115 con smbus2 (rango ±4.096 V, 128 muestras/s)."""

    REG_CONVERSION, REG_CONFIG = 0x00, 0x01

    def __init__(self, bus: int = 1, direccion: int = 0x48):
        from smbus2 import SMBus

        self.direccion = direccion
        self._bus = SMBus(bus)
        self._bloqueo = threading.Lock()
        self.leer_voltaje(0)  # verifica que responda (lanza OSError si no)

    def leer_voltaje(self, canal: int) -> float:
        config = (0x8000                     # OS: iniciar conversión
                  | ((4 + canal) << 12)      # MUX: AINx contra GND
                  | (0b001 << 9)             # PGA: ±4.096 V
                  | (1 << 8)                 # modo de un solo disparo
                  | (0b100 << 5)             # 128 muestras/s
                  | 0b11)                    # comparador deshabilitado
        with self._bloqueo:
            self._bus.write_i2c_block_data(self.direccion, self.REG_CONFIG, [config >> 8, config & 0xFF])
            time.sleep(0.009)
            alto, bajo = self._bus.read_i2c_block_data(self.direccion, self.REG_CONVERSION, 2)
        crudo = (alto << 8) | bajo
        if crudo & 0x8000:
            crudo -= 1 << 16
        return crudo * 4.096 / 32768.0

    def cerrar(self) -> None:
        self._bus.close()


class DHT22Pigpio:
    """Lee el DHT22 con callbacks de pigpio (resolución de 1 µs)."""

    def __init__(self, pi, gpio: int):
        import pigpio

        self._pigpio = pigpio
        self.pi, self.gpio = pi, gpio
        pi.set_pull_up_down(gpio, pigpio.PUD_UP)
        pi.set_mode(gpio, pigpio.INPUT)
        self._t_alto: int | None = None
        self._pulsos: list[int] = []
        self._cb = pi.callback(gpio, pigpio.EITHER_EDGE, self._flanco)

    def _flanco(self, _gpio: int, nivel: int, tick: int) -> None:
        if nivel == 1:
            self._t_alto = tick
        elif nivel == 0 and self._t_alto is not None:
            self._pulsos.append(self._pigpio.tickDiff(self._t_alto, tick))
            self._t_alto = None

    def leer(self) -> tuple[float, float]:
        self._pulsos, self._t_alto = [], None
        # Señal de inicio: nivel bajo ≥ 1 ms y luego liberar la línea
        self.pi.set_mode(self.gpio, self._pigpio.OUTPUT)
        self.pi.write(self.gpio, 0)
        time.sleep(0.002)
        self.pi.set_mode(self.gpio, self._pigpio.INPUT)
        time.sleep(0.03)  # la trama completa dura ~5 ms
        return decodificar_dht22(list(self._pulsos))

    def cerrar(self) -> None:
        self._cb.cancel()


class SensoresAmbienteRPi(DriverSensoresAmbiente):
    nombre = "Sensores TechRoach (ADS1115 + GPIO)"

    def __init__(self, pi, pines: dict, cfg: dict):
        import pigpio

        self._pigpio = pigpio
        self.pi = pi
        self.cfg = cfg
        self.pines = pines
        detalles: list[str] = []

        # --- ADS1115 -----------------------------------------------------
        self.ads: ADS1115 | None = None
        a = cfg.get("ads1115", {})
        if a.get("habilitado", True):
            try:
                self.ads = ADS1115(int(a.get("bus_i2c", 1)), int(a.get("direccion", 0x48)))
                detalles.append(f"ADS1115 0x{int(a.get('direccion', 0x48)):02X} OK")
            except Exception as e:
                detalles.append(f"ADS1115 no detectado ({e}); MQ por salida digital")
                log.warning("ADS1115 no disponible: %s", e)
        self.canales = a.get("canales", {"mq2": 0, "mq7": 1, "ldr": 2, "bateria": 3})
        self.divisor_mq = float(a.get("divisor_mq", 1.5))
        self.divisor_bat = float(a.get("divisor_bateria", 3.128))

        # --- DHT22 ---------------------------------------------------------
        self.dht: DHT22Pigpio | None = None
        self._dht_periodo = float(cfg.get("dht22", {}).get("periodo_s", 3.0))
        self._t_dht = 0.0
        self.temperatura: float | None = None
        self.humedad: float | None = None
        self.errores_dht = 0
        if cfg.get("dht22", {}).get("habilitado", True) and "dht22" in pines:
            self.dht = DHT22Pigpio(pi, pines["dht22"]["datos"])
            detalles.append(f"DHT22 GPIO{pines['dht22']['datos']}")

        # --- Entradas digitales -------------------------------------------
        for nombre in ("pir", "vibracion", "mq2_digital", "mq7_digital"):
            if nombre in pines:
                # Las DO de los MQ son activas en bajo; PIR y vibración, en alto
                pud = pigpio.PUD_UP if nombre.endswith("_digital") else pigpio.PUD_DOWN
                pi.set_mode(pines[nombre]["salida"], pigpio.INPUT)
                pi.set_pull_up_down(pines[nombre]["salida"], pud)
        self.impactos = 0
        self._t_impacto = 0.0
        self._cb_vib = None
        if "vibracion" in pines:
            self._cb_vib = pi.callback(pines["vibracion"]["salida"], pigpio.RISING_EDGE, self._vibracion)

        # --- Salidas ---------------------------------------------------------
        self._salidas = {}
        for clave, pin in (("buzzer", pines.get("alarma", {}).get("buzzer")),
                           ("led_alarma", pines.get("alarma", {}).get("led")),
                           ("iluminacion", pines.get("iluminacion", {}).get("mosfet"))):
            if pin is not None:
                pi.set_mode(pin, pigpio.OUTPUT)
                pi.write(pin, 0)
                self._salidas[clave] = pin
        self.detalle = "; ".join(detalles)

    def _vibracion(self, _gpio: int, _nivel: int, _tick: int) -> None:
        ahora = time.monotonic()
        if ahora - self._t_impacto > 0.3:   # antirrebote
            self._t_impacto = ahora
            self.impactos += 1

    def leer(self) -> dict:
        d: dict = {"impactos": self.impactos}
        if "pir" in self.pines:
            d["pir"] = bool(self.pi.read(self.pines["pir"]["salida"]))

        if self.ads is not None:
            try:
                c = self.canales
                d["v_mq2"] = self.ads.leer_voltaje(c["mq2"]) * self.divisor_mq
                d["v_mq7"] = self.ads.leer_voltaje(c["mq7"]) * self.divisor_mq
                d["v_ldr"] = self.ads.leer_voltaje(c["ldr"])
                d["v_bateria"] = self.ads.leer_voltaje(c["bateria"]) * self.divisor_bat
                if d["v_bateria"] < 3.0:     # canal sin conectar
                    d["v_bateria"] = None
            except OSError as e:
                log.debug("Error de I2C con el ADS1115: %s", e)
        else:
            # Salida DO de los módulos MQ: activa en BAJO al superar el umbral
            for gas in ("mq2", "mq7"):
                if f"{gas}_digital" in self.pines:
                    d[f"{gas}_digital"] = self.pi.read(self.pines[f"{gas}_digital"]["salida"]) == 0

        ahora = time.monotonic()
        if self.dht is not None and ahora - self._t_dht >= self._dht_periodo:
            self._t_dht = ahora
            try:
                self.temperatura, self.humedad = self.dht.leer()
            except ValueError as e:
                self.errores_dht += 1
                log.debug("%s", e)
        d["temperatura"], d["humedad"] = self.temperatura, self.humedad
        return d

    def salidas(self, buzzer: bool = False, led_alarma: bool = False, iluminacion: bool = False) -> None:
        for clave, valor in (("buzzer", buzzer), ("led_alarma", led_alarma), ("iluminacion", iluminacion)):
            if clave in self._salidas:
                self.pi.write(self._salidas[clave], 1 if valor else 0)

    def estado(self) -> dict:
        d = super().estado()
        d["errores_dht22"] = self.errores_dht
        return d

    def cerrar(self) -> None:
        self.salidas(False, False, False)
        if self._cb_vib is not None:
            self._cb_vib.cancel()
        if self.dht is not None:
            self.dht.cerrar()
        if self.ads is not None:
            self.ads.cerrar()
