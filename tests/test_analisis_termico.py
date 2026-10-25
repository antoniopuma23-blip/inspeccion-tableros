"""Pruebas del análisis térmico: estadísticas, referencia, ΔT y emisividad."""

import numpy as np
import pytest

from robot.logica.analisis_termico import (Umbrales, analizar, caja_punto_caliente,
                                           corregir_emisividad, estadisticas,
                                           temperatura_referencia, validar_matriz)


def cuadro_con_punto_caliente(fondo=30.0, pico=55.0, fila=10, col=20):
    m = np.full((24, 32), fondo, dtype=np.float32)
    m[fila, col] = pico
    return m


class TestEstadisticas:
    def test_maximo_minimo_y_posicion(self):
        m = cuadro_con_punto_caliente()
        m[3, 4] = 21.0
        est = estadisticas(m)
        assert est["t_max"] == pytest.approx(55.0)
        assert est["t_min"] == pytest.approx(21.0)
        assert est["pos_max"] == [10, 20]
        assert est["pos_min"] == [3, 4]

    def test_mediana_resiste_punto_caliente(self):
        est = estadisticas(cuadro_con_punto_caliente())
        assert est["t_mediana"] == pytest.approx(30.0)
        assert est["t_media"] > 30.0  # la media sí se desplaza

    def test_acepta_lista_plana_de_768(self):
        datos = list(range(768))
        est = estadisticas(datos)
        assert est["t_max"] == 767
        assert est["pos_max"] == [23, 31]

    def test_tamano_incorrecto(self):
        with pytest.raises(ValueError):
            estadisticas(np.zeros((10, 10)))

    def test_reemplaza_valores_no_finitos(self):
        m = cuadro_con_punto_caliente()
        m[0, 0] = np.nan
        m[0, 1] = np.inf
        limpia = validar_matriz(m)
        assert np.isfinite(limpia).all()
        assert limpia[0, 0] == pytest.approx(30.0)

    def test_todo_invalido(self):
        with pytest.raises(ValueError):
            validar_matriz(np.full((24, 32), np.nan))


class TestReferencia:
    def test_mediana(self):
        assert temperatura_referencia(cuadro_con_punto_caliente(), "mediana") == pytest.approx(30.0)

    def test_punto_promedia_vecindad_3x3(self):
        m = np.full((24, 32), 30.0, dtype=np.float32)
        m[5, 5] = 39.0  # 8 vecinos de 30 y el centro de 39 -> promedio 31
        assert temperatura_referencia(m, "punto", (5, 5)) == pytest.approx(31.0)

    def test_punto_en_esquina(self):
        m = np.full((24, 32), 25.0, dtype=np.float32)
        assert temperatura_referencia(m, "punto", (0, 0)) == pytest.approx(25.0)

    def test_punto_fuera_de_rango(self):
        with pytest.raises(ValueError):
            temperatura_referencia(cuadro_con_punto_caliente(), "punto", (24, 0))

    def test_punto_sin_coordenadas(self):
        with pytest.raises(ValueError):
            temperatura_referencia(cuadro_con_punto_caliente(), "punto")

    def test_manual(self):
        assert temperatura_referencia(cuadro_con_punto_caliente(), "manual", valor=28.5) == 28.5

    def test_modo_desconocido(self):
        with pytest.raises(ValueError):
            temperatura_referencia(cuadro_con_punto_caliente(), "promedio")


class TestAnalisisCompleto:
    def test_delta_t_con_mediana(self):
        r = analizar(cuadro_con_punto_caliente(30.0, 50.0))
        assert r["delta_t"] == pytest.approx(20.0)
        assert r["clasificacion"]["clave"] == "inmediata"
        assert r["punto_referencia"] is None

    def test_delta_t_con_punto(self):
        m = cuadro_con_punto_caliente(30.0, 40.0, fila=10, col=20)
        m[2:5, 2:5] = 36.0
        r = analizar(m, Umbrales(), "punto", (3, 3))
        assert r["t_referencia"] == pytest.approx(36.0)
        assert r["delta_t"] == pytest.approx(4.0)
        assert r["clasificacion"]["clave"] == "reparar"
        assert r["punto_referencia"] == [3, 3]

    def test_cuadro_uniforme_sin_anomalia(self):
        r = analizar(np.full((24, 32), 27.0))
        assert r["delta_t"] == pytest.approx(0.0)
        assert r["clasificacion"]["nivel"] == 0


class TestEmisividad:
    def test_misma_emisividad_no_cambia(self):
        m = cuadro_con_punto_caliente()
        np.testing.assert_allclose(corregir_emisividad(m, 0.95), m, atol=1e-4)

    def test_emisividad_menor_eleva_objetos_calientes(self):
        m = cuadro_con_punto_caliente(30.0, 60.0)
        c = corregir_emisividad(m, 0.80, temp_reflejada_c=25.0)
        assert c[10, 20] > 60.0
        assert c[0, 0] > 30.0

    def test_objeto_a_temperatura_reflejada_no_cambia(self):
        m = np.full((24, 32), 25.0, dtype=np.float32)
        np.testing.assert_allclose(corregir_emisividad(m, 0.6, 25.0), m, atol=1e-3)

    def test_emisividad_invalida(self):
        with pytest.raises(ValueError):
            corregir_emisividad(np.zeros((24, 32)), 0.0)


def test_caja_punto_caliente_limitada_a_bordes():
    assert caja_punto_caliente((0, 0)) == (0, 0, 1, 1)
    assert caja_punto_caliente((23, 31)) == (30, 22, 31, 23)
    assert caja_punto_caliente((10, 10)) == (9, 9, 11, 11)
