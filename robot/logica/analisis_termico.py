"""Análisis termográfico de los cuadros de la cámara MLX90640.

Incluye:

* Estadísticas del cuadro (máxima, mínima, media, mediana) y ubicación del
  punto más caliente.
* Temperatura de referencia: mediana del cuadro (por defecto), un punto
  elegido con un clic o un valor manual.
* ΔT = T(punto más caliente) − T(referencia).
* Clasificación según el criterio ΔT de **ANSI/NETA MTS** para comparación
  entre componentes similares bajo carga similar:

  ========================  =====================================  ========
  ΔT                        Clasificación                          Color
  ========================  =====================================  ========
  < 1 °C                    Sin anomalía                           verde
  1 – 3 °C                  Posible deficiencia                    amarillo
  4 – 15 °C                 Reparar en próxima oportunidad         naranja
  > 15 °C                   Reparación inmediata                   rojo
  ========================  =====================================  ========

  Los umbrales son configurables. Con valores continuos se usa:
  ``ΔT < posible`` → sin anomalía; ``posible ≤ ΔT < reparar`` → posible
  deficiencia; ``reparar ≤ ΔT ≤ inmediata`` → reparar; ``ΔT > inmediata`` →
  reparación inmediata.

* Corrección de emisividad (modelo de cuerpo gris, ley de Stefan-Boltzmann).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

KELVIN = 273.15
EMISIVIDAD_BASE_MLX = 0.95   # emisividad fija usada por la biblioteca de Adafruit


@dataclass(frozen=True)
class Umbrales:
    posible: float = 1.0
    reparar: float = 4.0
    inmediata: float = 15.0

    def __post_init__(self):
        if not (0 < self.posible < self.reparar <= self.inmediata):
            raise ValueError("Umbrales ΔT inválidos: se requiere 0 < posible < reparar ≤ inmediata")


@dataclass(frozen=True)
class Clasificacion:
    nivel: int            # 0 = sin anomalía … 3 = reparación inmediata
    clave: str
    etiqueta: str
    color: str            # color hexadecimal para la interfaz y el PDF
    recomendacion: str

    def a_dict(self) -> dict:
        return asdict(self)


CLASIFICACIONES = (
    Clasificacion(0, "sin_anomalia", "Sin anomalía", "#2ecc71",
                  "No se detectan diferencias de temperatura significativas. Continuar con el "
                  "programa de mantenimiento preventivo y la inspección termográfica periódica."),
    Clasificacion(1, "posible", "Posible deficiencia", "#f1c40f",
                  "Posible deficiencia: se justifica investigación adicional. Verificar condiciones "
                  "de carga, apriete y limpieza de conexiones, y dar seguimiento en la próxima "
                  "inspección."),
    Clasificacion(2, "reparar", "Reparar en próxima oportunidad", "#e67e22",
                  "Indica una probable deficiencia. Programar la reparación en la próxima "
                  "oportunidad (paro programado): revisar torque de terminales, estado de "
                  "conductores y balance de cargas."),
    Clasificacion(3, "inmediata", "Reparación inmediata", "#e74c3c",
                  "Deficiencia mayor. Reparar de inmediato: notificar al responsable de "
                  "mantenimiento, evaluar reducir la carga del circuito, restringir el acceso y "
                  "aplicar procedimientos de bloqueo/etiquetado (LOTO) antes de intervenir."),
)


def validar_matriz(matriz, filas: int = 24, columnas: int = 32) -> np.ndarray:
    """Convierte a ``float32 (filas, columnas)`` y reemplaza valores no finitos por la mediana."""
    m = np.asarray(matriz, dtype=np.float32)
    if m.size != filas * columnas:
        raise ValueError(f"La matriz térmica debe tener {filas}x{columnas} valores (tiene {m.size})")
    m = m.reshape(filas, columnas).copy()
    invalidos = ~np.isfinite(m)
    if invalidos.all():
        raise ValueError("La matriz térmica no contiene valores válidos")
    if invalidos.any():
        m[invalidos] = float(np.median(m[~invalidos]))
    return m


def estadisticas(matriz: np.ndarray) -> dict:
    """Máxima, mínima, media, mediana y posición (fila, columna) del punto más caliente."""
    m = validar_matriz(matriz)
    idx_max = np.unravel_index(int(np.argmax(m)), m.shape)
    idx_min = np.unravel_index(int(np.argmin(m)), m.shape)
    return {
        "t_max": float(m[idx_max]),
        "t_min": float(m[idx_min]),
        "t_media": float(m.mean()),
        "t_mediana": float(np.median(m)),
        "pos_max": [int(idx_max[0]), int(idx_max[1])],
        "pos_min": [int(idx_min[0]), int(idx_min[1])],
    }


def temperatura_referencia(matriz: np.ndarray, modo: str = "mediana",
                           punto: tuple[int, int] | list | None = None,
                           valor: float | None = None) -> float:
    """Temperatura de referencia para el cálculo de ΔT.

    * ``mediana``: mediana de todo el cuadro (representa el componente "sano").
    * ``punto``: promedio 3×3 alrededor de ``punto = (fila, columna)``.
    * ``manual``: ``valor`` dado por el usuario.
    """
    m = validar_matriz(matriz)
    if modo == "mediana":
        return float(np.median(m))
    if modo == "punto":
        if punto is None:
            raise ValueError("El modo 'punto' requiere la fila y columna de referencia")
        f, c = int(punto[0]), int(punto[1])
        if not (0 <= f < m.shape[0] and 0 <= c < m.shape[1]):
            raise ValueError("El punto de referencia está fuera de la imagen térmica")
        vecindad = m[max(0, f - 1):f + 2, max(0, c - 1):c + 2]
        return float(vecindad.mean())
    if modo == "manual":
        if valor is None or not np.isfinite(valor):
            raise ValueError("El modo 'manual' requiere un valor de temperatura")
        return float(valor)
    raise ValueError(f"Modo de referencia desconocido: {modo}")


def clasificar_delta_t(delta_t: float, umbrales: Umbrales = Umbrales()) -> Clasificacion:
    """Clasifica un ΔT (°C) con el criterio ANSI/NETA MTS."""
    if not np.isfinite(delta_t):
        raise ValueError("ΔT debe ser un número finito")
    if delta_t > umbrales.inmediata:
        return CLASIFICACIONES[3]
    if delta_t >= umbrales.reparar:
        return CLASIFICACIONES[2]
    if delta_t >= umbrales.posible:
        return CLASIFICACIONES[1]
    return CLASIFICACIONES[0]


def analizar(matriz: np.ndarray, umbrales: Umbrales = Umbrales(), modo_referencia: str = "mediana",
             punto: tuple[int, int] | list | None = None, valor: float | None = None) -> dict:
    """Análisis completo de un cuadro: estadísticas, referencia, ΔT y clasificación."""
    est = estadisticas(matriz)
    t_ref = temperatura_referencia(matriz, modo_referencia, punto, valor)
    delta_t = est["t_max"] - t_ref
    clasif = clasificar_delta_t(delta_t, umbrales)
    return {
        **est,
        "t_referencia": t_ref,
        "modo_referencia": modo_referencia,
        "punto_referencia": list(punto) if (modo_referencia == "punto" and punto is not None) else None,
        "delta_t": delta_t,
        "clasificacion": clasif.a_dict(),
    }


def corregir_emisividad(matriz: np.ndarray, emisividad: float, temp_reflejada_c: float = 25.0,
                        emisividad_base: float = EMISIVIDAD_BASE_MLX) -> np.ndarray:
    """Recalcula temperaturas para otra emisividad (modelo de cuerpo gris).

    La radiación medida equivale a ``ε·T_obj⁴ + (1−ε)·T_refl⁴``. Primero se
    obtiene esa radiación a partir de la temperatura calculada con
    ``emisividad_base`` y luego se despeja ``T_obj`` con la nueva emisividad.
    """
    if not (0.05 <= emisividad <= 1.0):
        raise ValueError("La emisividad debe estar entre 0.05 y 1.0")
    m = np.asarray(matriz, dtype=np.float64)
    if abs(emisividad - emisividad_base) < 1e-6:
        return m.astype(np.float32)
    tr4 = (temp_reflejada_c + KELVIN) ** 4
    radiacion = emisividad_base * (m + KELVIN) ** 4 + (1 - emisividad_base) * tr4
    t4 = (radiacion - (1 - emisividad) * tr4) / emisividad
    t4 = np.maximum(t4, 1.0)
    return (t4 ** 0.25 - KELVIN).astype(np.float32)


def caja_punto_caliente(pos: tuple[int, int] | list, filas: int = 24, columnas: int = 32,
                        radio: int = 1) -> tuple[int, int, int, int]:
    """Rectángulo (col0, fila0, col1, fila1) inclusivo alrededor del punto más caliente."""
    f, c = int(pos[0]), int(pos[1])
    return (max(0, c - radio), max(0, f - radio), min(columnas - 1, c + radio), min(filas - 1, f + radio))
