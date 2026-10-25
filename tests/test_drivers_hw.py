"""Pruebas de los drivers reales con hardware falso (pigpio, I2C y evdev simulados).

Permiten validar en una laptop el código que solo corre en la Raspberry Pi:
el ADS1115 por I2C, los sensores digitales con pigpio y el control Xbox.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from robot.config import Configuracion


# ---------------------------------------------------------------------------
#  pigpio y smbus2 falsos
# ---------------------------------------------------------------------------
class PiFalso:
    def __init__(self):
        self.niveles, self.modos, self.callbacks = {}, {}, []

    def set_mode(self, g, m):
        self.modos[g] = m

    def set_pull_up_down(self, g, p):
        pass

    def read(self, g):
        return self.niveles.get(g, 0)

    def write(self, g, v):
        self.niveles[g] = v

    def callback(self, g, borde, fn):
        cb = SimpleNamespace(gpio=g, fn=fn, cancel=lambda: None)
        self.callbacks.append(cb)
        return cb


class SMBusFalso:
    """ADS1115 falso: devuelve un voltaje fijo por canal según el MUX configurado."""
    voltajes = {0: 1.0, 1: 0.5, 2: 2.0, 3: 2.5}

    def __init__(self, bus):
        self.canal = 0

    def write_i2c_block_data(self, addr, reg, datos):
        config = (datos[0] << 8) | datos[1]
        self.canal = ((config >> 12) & 0x7) - 4

    def read_i2c_block_data(self, addr, reg, n):
        crudo = int(self.voltajes[self.canal] / 4.096 * 32768)
        return [(crudo >> 8) & 0xFF, crudo & 0xFF]

    def close(self):
        pass


@pytest.fixture
def hardware_falso(monkeypatch):
    pigpio = types.ModuleType("pigpio")
    pigpio.INPUT, pigpio.OUTPUT = 0, 1
    pigpio.PUD_UP, pigpio.PUD_DOWN, pigpio.PUD_OFF = 2, 1, 0
    pigpio.EITHER_EDGE, pigpio.RISING_EDGE = 2, 0
    pigpio.tickDiff = lambda a, b: b - a
    smbus2 = types.ModuleType("smbus2")
    smbus2.SMBus = SMBusFalso
    monkeypatch.setitem(sys.modules, "pigpio", pigpio)
    monkeypatch.setitem(sys.modules, "smbus2", smbus2)
    return PiFalso()


def test_sensores_rpi_lee_ads1115_y_digitales(hardware_falso):
    from robot.drivers.sensores_rpi import SensoresAmbienteRPi

    cfg = Configuracion()
    pines = cfg.seccion("pines")
    s = cfg.seccion("sensores")
    s["dht22"]["habilitado"] = False
    drv = SensoresAmbienteRPi(hardware_falso, pines, s)
    assert drv.ads is not None
    hardware_falso.niveles[pines["pir"]["salida"]] = 1
    d = drv.leer()
    assert d["v_mq2"] == pytest.approx(1.0 * 1.5, rel=1e-3)      # corrige el divisor 10k/20k
    assert d["v_mq7"] == pytest.approx(0.5 * 1.5, rel=1e-3)
    assert d["v_ldr"] == pytest.approx(2.0, rel=1e-3)
    assert d["v_bateria"] == pytest.approx(2.5 * 3.128, rel=1e-3)
    assert d["pir"] is True

    # Impactos con antirrebote: dos flancos seguidos cuentan como uno
    cb_vib = next(cb for cb in hardware_falso.callbacks if cb.gpio == pines["vibracion"]["salida"])
    cb_vib.fn(cb_vib.gpio, 1, 0)
    cb_vib.fn(cb_vib.gpio, 1, 10)
    assert drv.leer()["impactos"] == 1

    drv.salidas(buzzer=True, led_alarma=False, iluminacion=True)
    assert hardware_falso.niveles[pines["alarma"]["buzzer"]] == 1
    assert hardware_falso.niveles[pines["iluminacion"]["mosfet"]] == 1
    drv.cerrar()
    assert hardware_falso.niveles[pines["alarma"]["buzzer"]] == 0


def test_sensores_rpi_sin_ads1115_usa_salidas_digitales(hardware_falso, monkeypatch):
    from robot.drivers import sensores_rpi

    class SinADS:
        def __init__(self, *a, **k):
            raise OSError("Sin respuesta en 0x48")

    monkeypatch.setattr(sensores_rpi, "ADS1115", SinADS)
    cfg = Configuracion()
    pines, s = cfg.seccion("pines"), cfg.seccion("sensores")
    s["dht22"]["habilitado"] = False
    drv = sensores_rpi.SensoresAmbienteRPi(hardware_falso, pines, s)
    hardware_falso.niveles[pines["mq2_digital"]["salida"]] = 0     # DO activa en bajo
    hardware_falso.niveles[pines["mq7_digital"]["salida"]] = 1
    d = drv.leer()
    assert drv.ads is None and "no detectado" in drv.detalle
    assert d["mq2_digital"] is True and d["mq7_digital"] is False
    assert "v_mq2" not in d


def test_dht22_pigpio_decodifica_flancos(hardware_falso, monkeypatch):
    from robot.drivers.sensores_rpi import DHT22Pigpio
    from tests.test_ambiente import pulsos_dht22

    dht = DHT22Pigpio(hardware_falso, 4)
    cb = hardware_falso.callbacks[-1]

    def simular_trama(*_args):
        t = 0
        for dur in pulsos_dht22(61.2, 23.4):
            cb.fn(4, 1, t)
            cb.fn(4, 0, t + dur)
            t += dur + 50

    monkeypatch.setattr("robot.drivers.sensores_rpi.time.sleep", simular_trama)
    assert dht.leer() == pytest.approx((23.4, 61.2))


# ---------------------------------------------------------------------------
#  Control Xbox con evdev falso
# ---------------------------------------------------------------------------
ECODES = SimpleNamespace(EV_ABS=3, EV_KEY=1, ABS_X=0, ABS_Y=1, ABS_RX=3, ABS_RY=4,
                         BTN_SOUTH=304, BTN_EAST=305, BTN_TL=310, BTN_TR=311,
                         BTN_SELECT=314, BTN_START=315)


def ev(tipo, codigo, valor):
    return SimpleNamespace(type=tipo, code=codigo, value=valor)


@pytest.fixture
def xbox():
    from robot.drivers.control_xbox import ControlXboxEvdev

    registro = {"comandos": [], "soltar": 0, "paro": 0, "captura": 0, "modo": []}
    cfg = {"modo": "arcade", "zona_muerta": 0.15, "expo": 0.0}
    c = ControlXboxEvdev(
        al_comando=lambda t, a, b: registro["comandos"].append((t, round(a, 2), round(b, 2))),
        al_soltar=lambda: registro.__setitem__("soltar", registro["soltar"] + 1),
        al_paro=lambda: registro.__setitem__("paro", registro["paro"] + 1),
        al_rearme=lambda: None,
        al_captura=lambda: registro.__setitem__("captura", registro["captura"] + 1),
        al_desconectar=lambda: None,
        obtener_config=lambda: cfg,
        al_cambiar_modo=lambda m: registro["modo"].append(m))
    c._rangos = {ECODES.ABS_X: (-32768, 32767), ECODES.ABS_Y: (-32768, 32767),
                 ECODES.ABS_RX: (-32768, 32767), ECODES.ABS_RY: (-32768, 32767)}
    return c, registro, cfg


def test_xbox_stick_arriba_avanza_y_al_soltar_detiene(xbox):
    c, r, _ = xbox
    c._procesar_evento(ev(3, ECODES.ABS_Y, -32768), ECODES)     # arriba (negativo en evdev)
    c._enviar_comando()
    assert r["comandos"][-1] == ("arcade", 0.0, 1.0)
    c._procesar_evento(ev(3, ECODES.ABS_Y, 0), ECODES)
    c._enviar_comando()
    assert r["soltar"] == 1


def test_xbox_botones(xbox):
    c, r, _ = xbox
    c._procesar_evento(ev(1, ECODES.BTN_EAST, 1), ECODES)         # B
    c._procesar_evento(ev(1, ECODES.BTN_SOUTH, 1), ECODES)        # A
    c._procesar_evento(ev(1, ECODES.BTN_TL, 1), ECODES)           # LB
    c._procesar_evento(ev(1, ECODES.BTN_SELECT, 1), ECODES)       # Vista
    assert r["paro"] == 1 and r["captura"] == 1
    assert c.limitador == 0.75
    assert r["modo"] == ["tanque"]


def test_xbox_modo_tanque(xbox):
    c, r, cfg = xbox
    cfg["modo"] = "tanque"
    c._procesar_evento(ev(3, ECODES.ABS_Y, -32768), ECODES)
    c._procesar_evento(ev(3, ECODES.ABS_RY, 32767), ECODES)
    c._enviar_comando()
    assert r["comandos"][-1] == ("tanque", 1.0, -1.0)
