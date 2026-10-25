"""Estimación del estado de carga de una batería LiPo a partir de su voltaje."""

from __future__ import annotations

import numpy as np

# Curva típica de descarga de una celda LiPo en reposo (V, %)
_CURVA_CELDA = ((3.30, 0), (3.50, 5), (3.60, 15), (3.70, 30), (3.75, 40), (3.80, 50),
                (3.90, 65), (4.00, 80), (4.10, 90), (4.20, 100))


def porcentaje_lipo(voltaje: float | None, celdas: int = 2) -> float | None:
    """Porcentaje aproximado de carga (0-100) o None si no hay lectura."""
    if voltaje is None or celdas <= 0:
        return None
    v_celda = voltaje / celdas
    xs = [v for v, _ in _CURVA_CELDA]
    ys = [p for _, p in _CURVA_CELDA]
    return float(np.interp(v_celda, xs, ys))


def nivel_alerta(porcentaje: float | None) -> str:
    """'ok', 'baja' o 'critica' (una LiPo no debe bajar de 3.3 V por celda)."""
    if porcentaje is None:
        return "sin_dato"
    if porcentaje < 10:
        return "critica"
    if porcentaje < 25:
        return "baja"
    return "ok"
