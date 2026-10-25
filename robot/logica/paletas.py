"""Paletas de color para la representación térmica.

Cada paleta se define con puntos de control (posición 0-1, color RGB) y se
interpola a una tabla de 256 colores. La misma definición se envía al
navegador (``/api/paletas``), de modo que la interfaz web y el reporte PDF
usan exactamente los mismos colores.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

PUNTOS_CONTROL: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    # Ironbow: estándar de facto en termografía industrial
    "ironbow": [(0.00, (0, 0, 0)), (0.10, (30, 0, 90)), (0.25, (110, 0, 150)),
                (0.40, (180, 20, 140)), (0.55, (225, 60, 70)), (0.70, (245, 120, 10)),
                (0.85, (255, 190, 30)), (1.00, (255, 255, 220))],
    "arcoiris": [(0.00, (0, 0, 130)), (0.15, (0, 0, 255)), (0.35, (0, 200, 255)),
                 (0.50, (0, 255, 100)), (0.65, (230, 255, 0)), (0.80, (255, 140, 0)),
                 (1.00, (255, 0, 0))],
    "grises": [(0.00, (0, 0, 0)), (1.00, (255, 255, 255))],
    "lava": [(0.00, (10, 10, 30)), (0.30, (80, 20, 120)), (0.55, (200, 40, 40)),
             (0.80, (255, 160, 20)), (1.00, (255, 250, 200))],
}

NOMBRES = {"ironbow": "Ironbow", "arcoiris": "Arcoíris", "grises": "Escala de grises", "lava": "Lava"}


@lru_cache(maxsize=None)
def tabla_rgb(nombre: str) -> np.ndarray:
    """Tabla (256, 3) uint8 en orden RGB."""
    puntos = PUNTOS_CONTROL.get(nombre, PUNTOS_CONTROL["ironbow"])
    pos = np.array([p for p, _ in puntos])
    colores = np.array([c for _, c in puntos], dtype=np.float64)
    x = np.linspace(0, 1, 256)
    tabla = np.stack([np.interp(x, pos, colores[:, k]) for k in range(3)], axis=1)
    return np.round(tabla).astype(np.uint8)


def tabla_bgr(nombre: str) -> np.ndarray:
    return tabla_rgb(nombre)[:, ::-1].copy()


def aplicar_paleta(matriz: np.ndarray, t_min: float, t_max: float, nombre: str = "ironbow",
                   bgr: bool = True) -> np.ndarray:
    """Convierte temperaturas en una imagen de color (alto, ancho, 3) uint8."""
    rango = max(t_max - t_min, 0.1)
    idx = np.clip((np.asarray(matriz, np.float32) - t_min) / rango * 255.0, 0, 255).astype(np.uint8)
    tabla = tabla_bgr(nombre) if bgr else tabla_rgb(nombre)
    return tabla[idx]


def para_cliente() -> dict:
    """Tablas en formato JSON para el navegador."""
    return {n: {"nombre": NOMBRES[n], "tabla": tabla_rgb(n).tolist()} for n in PUNTOS_CONTROL}
