"""Pruebas del watchdog de 500 ms y de su efecto sobre los motores."""

import pytest

from robot.drivers.base import DriverEncoders, DriverMotores
from robot.logica.control import ControladorTraccion, ParametrosTraccion
from robot.logica.watchdog import Watchdog


class TestWatchdog:
    def test_no_expira_sin_comandos_previos(self, reloj):
        llamadas = []
        w = Watchdog(0.5, lambda: llamadas.append(1), reloj)
        reloj.avanzar(10)
        assert w.verificar() is False
        assert llamadas == []

    def test_no_expira_antes_del_limite(self, reloj):
        llamadas = []
        w = Watchdog(0.5, lambda: llamadas.append(1), reloj)
        w.alimentar()
        reloj.avanzar(0.49)
        assert w.verificar() is False
        reloj.avanzar(0.01)          # exactamente 500 ms todavía es válido
        assert w.verificar() is False
        assert llamadas == []

    def test_expira_despues_de_500_ms(self, reloj):
        llamadas = []
        w = Watchdog(0.5, lambda: llamadas.append(1), reloj)
        w.alimentar()
        reloj.avanzar(0.501)
        assert w.verificar() is True
        assert llamadas == [1]

    def test_accion_se_ejecuta_una_sola_vez(self, reloj):
        llamadas = []
        w = Watchdog(0.5, lambda: llamadas.append(1), reloj)
        w.alimentar()
        reloj.avanzar(1.0)
        w.verificar()
        reloj.avanzar(1.0)
        assert w.verificar() is False
        assert llamadas == [1]
        assert w.expiraciones == 1

    def test_alimentar_reinicia_el_plazo(self, reloj):
        w = Watchdog(0.5, None, reloj)
        for _ in range(20):          # comandos cada 100 ms durante 2 s
            w.alimentar()
            reloj.avanzar(0.1)
            assert w.verificar() is False

    def test_desarmar(self, reloj):
        w = Watchdog(0.5, None, reloj)
        w.alimentar()
        w.desarmar()
        reloj.avanzar(5)
        assert w.verificar() is False
        assert not w.armado

    def test_tiempo_limite_invalido(self):
        with pytest.raises(ValueError):
            Watchdog(0)


# ---------------------------------------------------------------------------
#  Integración watchdog + controlador de tracción
# ---------------------------------------------------------------------------
class MotoresEspia(DriverMotores):
    nombre = "espía"

    def __init__(self):
        self.historial = []
        self._ultimo = (0.0, 0.0)
        self.paros = 0

    def aplicar(self, duty_izq, duty_der):
        self._ultimo = (duty_izq, duty_der)
        self.historial.append(self._ultimo)

    def detener(self):
        self._ultimo = (0.0, 0.0)
        self.paros += 1


class EncodersQuietos(DriverEncoders):
    nombre = "quietos"
    disponible = False  # fuerza lazo abierto

    def leer_conteos(self):
        return 0, 0


@pytest.fixture
def controlador(reloj):
    motores = MotoresEspia()
    c = ControladorTraccion(motores, EncodersQuietos(), ParametrosTraccion(aceleracion=10.0),
                            cuentas_por_vuelta=1320, watchdog_s=0.5, reloj=reloj)
    return c, motores


def correr(c, reloj, segundos, dt=0.02):
    for _ in range(int(round(segundos / dt))):
        reloj.avanzar(dt)
        c.paso()


def test_motores_en_cero_al_crear(controlador):
    _, motores = controlador
    assert motores.paros >= 1
    assert motores.ultimo_duty == (0.0, 0.0)


def test_watchdog_detiene_motores_si_dejan_de_llegar_comandos(controlador, reloj):
    c, motores = controlador
    c.comando(0, 1)
    correr(c, reloj, 0.3)
    assert motores.ultimo_duty[0] > 0 and motores.ultimo_duty[1] > 0
    correr(c, reloj, 0.3)            # 600 ms sin comandos
    assert motores.ultimo_duty == (0.0, 0.0)
    assert c.watchdog.expiraciones == 1
    assert "Watchdog" in c.ultimo_paro


def test_comandos_periodicos_mantienen_el_movimiento(controlador, reloj):
    c, motores = controlador
    for _ in range(20):              # 2 s con comandos cada 100 ms
        c.comando(0, 1)
        correr(c, reloj, 0.1)
    assert motores.ultimo_duty[0] > 0
    assert c.watchdog.expiraciones == 0


def test_paro_por_watchdog_es_inmediato_sin_rampa(reloj):
    motores = MotoresEspia()
    c = ControladorTraccion(motores, EncodersQuietos(), ParametrosTraccion(aceleracion=0.2),
                            cuentas_por_vuelta=1320, watchdog_s=0.5, reloj=reloj)
    for _ in range(15):
        c.comando(0, 1)
        correr(c, reloj, 0.1)
    assert c.referencia[0] > 0.2
    correr(c, reloj, 0.52)
    assert c.referencia == [0.0, 0.0]
    assert motores.ultimo_duty == (0.0, 0.0)


def test_evento_de_watchdog_solo_si_habia_movimiento(reloj):
    eventos = []
    c = ControladorTraccion(MotoresEspia(), EncodersQuietos(), ParametrosTraccion(),
                            cuentas_por_vuelta=1320, reloj=reloj,
                            al_evento=lambda t, m: eventos.append(t))
    c.comando(0, 0)                  # comando nulo (joystick en reposo)
    correr(c, reloj, 1.0)
    assert eventos == []
    c.comando(0, 1)
    correr(c, reloj, 1.0)
    assert eventos == ["watchdog"]
