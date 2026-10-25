"""Pruebas del control de tracción: mezcla skid steering, límite de velocidad, rampa y paro."""

import math

import pytest

from robot.drivers.simulados import EncodersSimulados, ModeloTraccionSimulado, MotoresSimulados
from robot.logica.control import (ControladorTraccion, ControlPI, ParametrosTraccion,
                                  aplicar_rampa, mezclar_skid)

from .test_watchdog import EncodersQuietos, MotoresEspia


class TestMezcla:
    def test_avance_recto(self):
        assert mezclar_skid(0, 1) == (1.0, 1.0)

    def test_retroceso(self):
        assert mezclar_skid(0, -1) == (-1.0, -1.0)

    def test_giro_en_sitio(self):
        izq, der = mezclar_skid(1, 0, factor_giro=0.6)
        assert izq == pytest.approx(0.6) and der == pytest.approx(-0.6)

    def test_normaliza_sin_exceder_1(self):
        izq, der = mezclar_skid(1, 1, factor_giro=1.0)
        assert max(abs(izq), abs(der)) == pytest.approx(1.0)
        assert izq > der

    def test_satura_entradas_fuera_de_rango(self):
        assert mezclar_skid(0, 5) == (1.0, 1.0)


class TestRampa:
    def test_limita_el_cambio(self):
        assert aplicar_rampa(0.0, 1.0, 0.1) == pytest.approx(0.1)
        assert aplicar_rampa(0.5, 0.0, 0.1) == pytest.approx(0.4)

    def test_llega_al_objetivo(self):
        assert aplicar_rampa(0.95, 1.0, 0.1) == 1.0


def test_pi_antiwindup():
    pi = ControlPI(kp=0.0, ki=10.0, limite_integral=0.3)
    for _ in range(100):
        pi.paso(1.0, 0.1)
    assert pi.integral == pytest.approx(0.3)


def correr(c, reloj, segundos, dt=0.02, x=None, y=None):
    pasos = int(round(segundos / dt))
    for k in range(pasos):
        if x is not None and k % 5 == 0:
            c.comando(x, y)
        reloj.avanzar(dt)
        c.paso()


def test_referencia_respeta_velocidad_maxima(reloj):
    c = ControladorTraccion(MotoresEspia(), EncodersQuietos(), ParametrosTraccion(velocidad_maxima=0.3),
                            1320, reloj=reloj)
    correr(c, reloj, 3.0, x=0, y=1)
    assert c.referencia[0] == pytest.approx(0.3)
    assert c.referencia[1] == pytest.approx(0.3)


def test_rampa_de_aceleracion(reloj):
    p = ParametrosTraccion(velocidad_maxima=0.3, aceleracion=0.4)
    c = ControladorTraccion(MotoresEspia(), EncodersQuietos(), p, 1320, reloj=reloj)
    correr(c, reloj, 0.5, x=0, y=1)
    assert c.referencia[0] == pytest.approx(0.2, abs=0.01)  # 0.4 m/s² × 0.5 s


def test_paro_de_emergencia_enclava(reloj):
    motores = MotoresEspia()
    c = ControladorTraccion(motores, EncodersQuietos(), ParametrosTraccion(), 1320, reloj=reloj)
    correr(c, reloj, 0.5, x=0, y=1)
    c.paro_emergencia()
    assert motores.ultimo_duty == (0.0, 0.0)
    assert c.comando(0, 1) is False
    correr(c, reloj, 0.5)
    assert motores.ultimo_duty == (0.0, 0.0)
    c.rearmar()
    assert c.comando(0, 1) is True


def test_rechaza_comandos_no_finitos(reloj):
    c = ControladorTraccion(MotoresEspia(), EncodersQuietos(), ParametrosTraccion(), 1320, reloj=reloj)
    assert c.comando(math.nan, 1) is False


def test_duty_limitado_aunque_falle_el_encoder(reloj):
    """Con lazo cerrado y encoder que no cuenta, el duty no debe irse al 100 %."""
    class EncoderRoto(EncodersQuietos):
        disponible = True

    motores = MotoresEspia()
    p = ParametrosTraccion(velocidad_maxima=0.3, lazo_cerrado=True, ki=50.0)
    c = ControladorTraccion(motores, EncoderRoto(), p, 1320, reloj=reloj)
    correr(c, reloj, 5.0, x=0, y=1)
    limite = abs(p.duty_prealimentado(0.3 * 1.3))
    assert max(abs(d) for d in motores.ultimo_duty) <= limite + 1e-9


def test_lazo_cerrado_alcanza_la_velocidad_en_simulacion(reloj):
    p = ParametrosTraccion(velocidad_maxima=0.3, aceleracion=1.0, diametro_rueda=0.04)
    modelo = ModeloTraccionSimulado(diametro_m=0.04, cuentas_por_vuelta=1320, asimetria=0.08, reloj=reloj)
    c = ControladorTraccion(MotoresSimulados(modelo), EncodersSimulados(modelo), p, 1320, reloj=reloj)
    correr(c, reloj, 4.0, x=0, y=0.5)
    # 0.5 × 0.3 m/s = 0.15 m/s en ambas orugas pese a la asimetría de motores
    assert c.medida[0] == pytest.approx(0.15, abs=0.015)
    assert c.medida[1] == pytest.approx(0.15, abs=0.015)
