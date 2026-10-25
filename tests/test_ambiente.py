"""Pruebas de los sensores ambientales de TechRoach: conversión MQ, filtros, alarmas y respuestas."""

import pytest

from robot.config import Configuracion
from robot.logica.ambiente import (ALARMA, ADVERTENCIA, NORMAL, DetectorUmbral, FiltroMediana,
                                   ProcesadorAmbiente, decodificar_dht22, porcentaje_luz,
                                   ppm_desde_razon, r0_desde_rs, rs_desde_voltaje,
                                   voltaje_desde_ppm)


# ---------------------------------------------------------------------------
#  Conversión voltaje -> ppm
# ---------------------------------------------------------------------------
def test_rs_desde_voltaje():
    # Vout = Vc/2 -> Rs = RL
    assert rs_desde_voltaje(2.5, 5.0, 1.0) == pytest.approx(1.0)
    assert rs_desde_voltaje(1.0, 5.0, 10.0) == pytest.approx(40.0)
    assert rs_desde_voltaje(0.0) is None
    assert rs_desde_voltaje(None) is None


@pytest.mark.parametrize("ppm", [50, 300, 1000, 5000])
def test_ida_y_vuelta_ppm(ppm):
    a, b, r0 = 574.25, -2.222, 10.0
    v = voltaje_desde_ppm(ppm, a, b, r0)
    rs = rs_desde_voltaje(v)
    assert ppm_desde_razon(rs / r0, a, b) == pytest.approx(ppm, rel=1e-6)


def test_mas_gas_mas_voltaje():
    """En un MQ, a mayor concentración baja Rs y sube el voltaje de salida."""
    v = [voltaje_desde_ppm(p, 574.25, -2.222, 10.0) for p in (100, 1000, 5000)]
    assert v[0] < v[1] < v[2]


def test_r0_aire_limpio():
    assert r0_desde_rs(98.3, 9.83) == pytest.approx(10.0)


def test_porcentaje_luz():
    assert porcentaje_luz(3.3) == pytest.approx(100)
    assert porcentaje_luz(0.0) == pytest.approx(0)
    assert porcentaje_luz(3.3, invertir=True) == pytest.approx(0)
    assert porcentaje_luz(None) is None


# ---------------------------------------------------------------------------
#  Filtros contra falsas alarmas
# ---------------------------------------------------------------------------
def test_filtro_mediana_elimina_picos():
    f = FiltroMediana(5)
    for v in (100, 102, 9999, 101, 99):
        salida = f.agregar(v)
    assert salida == 101


def test_detector_requiere_confirmaciones():
    d = DetectorUmbral(500, 1000, confirmaciones=3)
    assert d.actualizar(1500) == NORMAL
    assert d.actualizar(1500) == NORMAL
    assert d.actualizar(1500) == ALARMA


def test_detector_pico_aislado_no_alarma():
    d = DetectorUmbral(500, 1000, confirmaciones=3)
    for v in (100, 1500, 100, 1500, 100):
        assert d.actualizar(v) == NORMAL


def test_detector_histeresis():
    d = DetectorUmbral(500, 1000, confirmaciones=1, histeresis_pct=10)
    assert d.actualizar(1000) == ALARMA
    assert d.actualizar(950) == ALARMA          # dentro de la histéresis (900)
    assert d.actualizar(890) == ADVERTENCIA
    assert d.actualizar(460) == ADVERTENCIA     # advertencia se libera bajo 450
    assert d.actualizar(440) == NORMAL


# ---------------------------------------------------------------------------
#  DHT22
# ---------------------------------------------------------------------------
def pulsos_dht22(humedad: float, temperatura: float, checksum_ok: bool = True) -> list[int]:
    h = int(round(humedad * 10))
    t = int(round(abs(temperatura) * 10)) | (0x8000 if temperatura < 0 else 0)
    datos = [h >> 8, h & 0xFF, t >> 8, t & 0xFF]
    datos.append((sum(datos) + (0 if checksum_ok else 1)) & 0xFF)
    bits = [int(b) for byte in datos for b in f"{byte:08b}"]
    return [30, 80] + [70 if b else 27 for b in bits]   # respuesta + 40 bits


def test_dht22_decodifica():
    assert decodificar_dht22(pulsos_dht22(55.3, 24.7)) == pytest.approx((24.7, 55.3))


def test_dht22_temperatura_negativa():
    assert decodificar_dht22(pulsos_dht22(40.0, -5.5))[0] == pytest.approx(-5.5)


def test_dht22_errores():
    with pytest.raises(ValueError, match="suma"):
        decodificar_dht22(pulsos_dht22(55.3, 24.7, checksum_ok=False))
    with pytest.raises(ValueError, match="bits"):
        decodificar_dht22([27] * 30)


# ---------------------------------------------------------------------------
#  Procesador: respuestas automáticas (reglas de TechRoach)
# ---------------------------------------------------------------------------
@pytest.fixture
def procesador(reloj):
    cfg = Configuracion()
    s = cfg.seccion("sensores")
    s["calentamiento_s"] = 0
    return ProcesadorAmbiente(s, cfg.seccion("respuestas"), reloj), s


def crudo_base(s, mq2=100.0, mq7=5.0, **extra):
    v = lambda gas, ppm: voltaje_desde_ppm(ppm, s[gas]["curva_a"], s[gas]["curva_b"], s[gas]["r0_kohm"])
    d = {"v_mq2": v("mq2", mq2), "v_mq7": v("mq7", mq7), "v_ldr": 2.0, "temperatura": 25.0,
         "humedad": 50.0, "pir": False, "impactos": 0}
    d.update(extra)
    return d


def procesar_n(p, crudo, n, reloj, en_movimiento=False):
    for _ in range(n):
        reloj.avanzar(0.5)
        estado, eventos = p.procesar(crudo, en_movimiento)
    return estado, eventos


def test_condiciones_normales(procesador, reloj):
    p, s = procesador
    e, _ = procesar_n(p, crudo_base(s), 6, reloj)
    assert e.mq2_ppm == pytest.approx(100, rel=0.01)
    assert e.factor_velocidad == 1.0
    assert e.bloqueo is None and not e.alarma


def test_gas_inflamable_detiene_y_bloquea_avance(procesador, reloj):
    p, s = procesador
    procesar_n(p, crudo_base(s), 5, reloj)
    eventos_totales = []
    for _ in range(8):
        reloj.avanzar(0.5)
        e, ev = p.procesar(crudo_base(s, mq2=2000), False)
        eventos_totales += ev
    assert e.mq2_nivel == ALARMA
    assert e.bloqueo == "avance"
    assert any(ev.tipo == "gas_mq2" and ev.nivel == ALARMA for ev in eventos_totales)


def test_co_sin_retroceso_bloquea_todo(reloj):
    cfg = Configuracion()
    s, r = cfg.seccion("sensores"), cfg.seccion("respuestas")
    s["calentamiento_s"] = 0
    r["permitir_retroceso_en_gas"] = False
    p = ProcesadorAmbiente(s, r, reloj)
    e, _ = procesar_n(p, crudo_base(s, mq7=300), 10, reloj)
    assert e.mq7_nivel == ALARMA and e.bloqueo == "total"


def test_calentamiento_suprime_alarmas(reloj):
    cfg = Configuracion()
    s = cfg.seccion("sensores")
    s["calentamiento_s"] = 60
    p = ProcesadorAmbiente(s, cfg.seccion("respuestas"), reloj)
    e, _ = procesar_n(p, crudo_base(s, mq2=5000), 10, reloj)
    assert e.calentando and e.mq2_nivel == NORMAL and e.bloqueo is None
    e, _ = procesar_n(p, crudo_base(s, mq2=5000), 130, reloj)   # pasa el calentamiento
    assert not e.calentando and e.mq2_nivel == ALARMA


def test_humedad_alta_reduce_velocidad_con_histeresis(procesador, reloj):
    p, s = procesador
    e, ev = procesar_n(p, crudo_base(s, humedad=85), 1, reloj)
    assert e.humedad_alta and e.factor_velocidad == pytest.approx(0.5)
    assert ev[0].tipo == "humedad"
    e, _ = procesar_n(p, crudo_base(s, humedad=79), 1, reloj)
    assert e.humedad_alta                      # histéresis de 3 %
    e, _ = procesar_n(p, crudo_base(s, humedad=76), 1, reloj)
    assert not e.humedad_alta and e.factor_velocidad == 1.0


def test_pir_solo_con_robot_detenido(procesador, reloj):
    p, s = procesador
    e, _ = procesar_n(p, crudo_base(s, pir=True), 6, reloj, en_movimiento=True)
    assert not e.presencia                     # en movimiento el PIR no es confiable
    e, ev = procesar_n(p, crudo_base(s, pir=True), 4, reloj, en_movimiento=False)
    assert e.presencia and e.factor_velocidad == pytest.approx(0.4)
    e, _ = procesar_n(p, crudo_base(s, pir=False), 24, reloj)   # 12 s después
    assert not e.presencia


def test_impacto_genera_evento(procesador, reloj):
    p, s = procesador
    procesar_n(p, crudo_base(s, impactos=0), 1, reloj)
    _, ev = procesar_n(p, crudo_base(s, impactos=1), 1, reloj)
    assert [x.tipo for x in ev] == ["impacto"]


def test_oscuridad_enciende_iluminacion(procesador, reloj):
    p, s = procesador
    e, _ = procesar_n(p, crudo_base(s, v_ldr=0.3), 1, reloj)
    assert e.oscuro
    e, _ = procesar_n(p, crudo_base(s, v_ldr=1.0), 1, reloj)   # 30 %: dentro de la histéresis
    assert e.oscuro
    e, _ = procesar_n(p, crudo_base(s, v_ldr=2.0), 1, reloj)
    assert not e.oscuro


def test_respaldo_digital_sin_ads1115(procesador, reloj):
    p, _ = procesador
    crudo = {"mq2_digital": True, "mq7_digital": False, "temperatura": None, "humedad": None}
    e, _ = procesar_n(p, crudo, 4, reloj)
    assert e.mq2_nivel == ALARMA and e.mq2_ppm is None and e.bloqueo == "avance"
