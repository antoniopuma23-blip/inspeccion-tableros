"""Pruebas del mapeo del control Xbox y de los comandos por oruga."""

import pytest

from robot.logica.control import ControladorTraccion, ParametrosTraccion
from robot.logica.gamepad import (EstadoMando, ajustar_limitador, comando_desde_mando,
                                  curva_expo, es_neutro, normalizar_eje, zona_muerta)

from .test_watchdog import EncodersQuietos, MotoresEspia


def test_zona_muerta():
    assert zona_muerta(0.1, 0.15) == 0.0
    assert zona_muerta(1.0, 0.15) == pytest.approx(1.0)
    assert zona_muerta(-1.0, 0.15) == pytest.approx(-1.0)
    assert zona_muerta(0.575, 0.15) == pytest.approx(0.5)


def test_expo_conserva_extremos_y_suaviza_centro():
    assert curva_expo(1.0, 0.35) == pytest.approx(1.0)
    assert curva_expo(-1.0, 0.35) == pytest.approx(-1.0)
    assert abs(curva_expo(0.3, 0.35)) < 0.3


def test_normalizar_eje_evdev():
    assert normalizar_eje(-32768, -32768, 32767) == pytest.approx(-1.0)
    assert normalizar_eje(32767, -32768, 32767) == pytest.approx(1.0)
    assert normalizar_eje(0, 0, 255) == pytest.approx(-1.0)


def test_modo_arcade():
    tipo, x, y = comando_desde_mando(EstadoMando(lx=0.0, ly=1.0), "arcade", 0.15, 0.0)
    assert tipo == "arcade" and x == 0.0 and y == pytest.approx(1.0)


def test_modo_tanque_cada_stick_su_oruga():
    tipo, izq, der = comando_desde_mando(EstadoMando(ly=1.0, ry=-1.0), "tanque", 0.15, 0.0)
    assert tipo == "tanque" and izq == pytest.approx(1.0) and der == pytest.approx(-1.0)


def test_limitador():
    _, _, y = comando_desde_mando(EstadoMando(ly=1.0), "arcade", 0.15, 0.0, limitador=0.5)
    assert y == pytest.approx(0.5)
    assert ajustar_limitador(1.0, subir=True) == 1.0
    assert ajustar_limitador(0.25, subir=False) == 0.25
    assert ajustar_limitador(0.5, subir=True) == 0.75


def test_sticks_en_reposo_son_neutros():
    assert es_neutro(comando_desde_mando(EstadoMando(lx=0.05, ly=-0.08), "arcade", 0.15, 0.35))


# ---------------------------------------------------------------------------
#  Controlador: modo tanque y restricciones de las respuestas automáticas
# ---------------------------------------------------------------------------
def correr(c, reloj, segundos, comando=None, dt=0.02):
    for k in range(int(round(segundos / dt))):
        if comando and k % 5 == 0:
            comando()
        reloj.avanzar(dt)
        c.paso()


@pytest.fixture
def controlador(reloj):
    return ControladorTraccion(MotoresEspia(), EncodersQuietos(),
                               ParametrosTraccion(velocidad_maxima=0.3, aceleracion=10), 1320, reloj=reloj)


def test_comando_orugas_independientes(controlador, reloj):
    correr(controlador, reloj, 1.0, lambda: controlador.comando_orugas(1.0, -0.5))
    assert controlador.referencia == pytest.approx([0.3, -0.15])


def test_factor_externo_reduce_velocidad(controlador, reloj):
    controlador.fijar_restricciones(factor=0.5)
    correr(controlador, reloj, 1.0, lambda: controlador.comando(0, 1))
    assert controlador.referencia == pytest.approx([0.15, 0.15])


def test_bloqueo_por_gas_solo_permite_retroceder(controlador, reloj):
    correr(controlador, reloj, 0.5, lambda: controlador.comando(0, 1))
    controlador.fijar_restricciones(bloqueo="avance")
    correr(controlador, reloj, 0.5, lambda: controlador.comando(0, 1))
    assert controlador.referencia == [0.0, 0.0]
    correr(controlador, reloj, 1.0, lambda: controlador.comando(0, -1))
    assert controlador.referencia == pytest.approx([-0.3, -0.3])


def test_bloqueo_total(controlador, reloj):
    controlador.fijar_restricciones(bloqueo="total")
    correr(controlador, reloj, 1.0, lambda: controlador.comando(0, -1))
    assert controlador.referencia == [0.0, 0.0]
