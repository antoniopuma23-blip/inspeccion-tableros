"""Pruebas de la clasificación ΔT según ANSI/NETA MTS."""

import math

import pytest

from robot.logica.analisis_termico import CLASIFICACIONES, Umbrales, clasificar_delta_t


@pytest.mark.parametrize("delta_t, clave", [
    (-2.0, "sin_anomalia"),     # componente más frío que la referencia
    (0.0, "sin_anomalia"),
    (0.99, "sin_anomalia"),
    (1.0, "posible"),           # 1–3 °C: posible deficiencia (amarillo)
    (2.0, "posible"),
    (3.0, "posible"),
    (3.9, "posible"),
    (4.0, "reparar"),           # 4–15 °C: reparar en próxima oportunidad (naranja)
    (10.0, "reparar"),
    (15.0, "reparar"),
    (15.01, "inmediata"),       # > 15 °C: reparación inmediata (rojo)
    (40.0, "inmediata"),
])
def test_rangos_ansi_neta(delta_t, clave):
    assert clasificar_delta_t(delta_t).clave == clave


def test_etiquetas_y_colores():
    assert clasificar_delta_t(2).etiqueta == "Posible deficiencia"
    assert clasificar_delta_t(2).color == "#f1c40f"          # amarillo
    assert clasificar_delta_t(8).etiqueta == "Reparar en próxima oportunidad"
    assert clasificar_delta_t(8).color == "#e67e22"          # naranja
    assert clasificar_delta_t(20).etiqueta == "Reparación inmediata"
    assert clasificar_delta_t(20).color == "#e74c3c"         # rojo


def test_niveles_ordenados_y_con_recomendacion():
    assert [c.nivel for c in CLASIFICACIONES] == [0, 1, 2, 3]
    assert all(len(c.recomendacion) > 30 for c in CLASIFICACIONES)


def test_umbrales_personalizados():
    u = Umbrales(posible=2.0, reparar=6.0, inmediata=20.0)
    assert clasificar_delta_t(1.5, u).clave == "sin_anomalia"
    assert clasificar_delta_t(5.0, u).clave == "posible"
    assert clasificar_delta_t(18.0, u).clave == "reparar"
    assert clasificar_delta_t(21.0, u).clave == "inmediata"


@pytest.mark.parametrize("p, r, i", [(0, 4, 15), (4, 4, 15), (5, 4, 15), (1, 16, 15)])
def test_umbrales_invalidos(p, r, i):
    with pytest.raises(ValueError):
        Umbrales(p, r, i)


@pytest.mark.parametrize("valor", [math.nan, math.inf])
def test_delta_t_no_finito(valor):
    with pytest.raises(ValueError):
        clasificar_delta_t(valor)
