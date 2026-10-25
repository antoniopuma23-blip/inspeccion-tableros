"""Mapeo del control Xbox a comandos del robot (independiente del hardware).

La misma lógica se usa con el control conectado a la Raspberry Pi (evdev) y,
replicada en ``static/js/gamepad.js``, con el control conectado al navegador.

Asignación de controles
-----------------------

==========================  =================================================
Control                     Acción
==========================  =================================================
Stick izquierdo             Modo *arcade*: arriba/abajo = avanzar/retroceder,
                            izquierda/derecha = girar
Stick izq. + stick der.     Modo *tanque*: eje vertical de cada stick mueve su
(vertical)                  oruga (izquierda / derecha) de forma independiente
B                           PARO DE EMERGENCIA
Menú (≡), mantener 1.5 s    Rearmar después de un paro
Vista (⧉)                   Cambiar modo arcade ↔ tanque
LB / RB                     Limitador de velocidad −25 % / +25 %
A                           Captura rápida (usa el último tablero capturado)
==========================  =================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MODOS = ("arcade", "tanque")
PASO_LIMITADOR = 0.25
LIMITADOR_MIN = 0.25
TIEMPO_REARME_S = 1.5


def zona_muerta(valor: float, zona: float) -> float:
    """Elimina la zona muerta y re-escala para que el rango útil siga siendo [-1, 1]."""
    valor = max(-1.0, min(1.0, float(valor)))
    if abs(valor) <= zona:
        return 0.0
    return math.copysign((abs(valor) - zona) / (1.0 - zona), valor)


def curva_expo(valor: float, expo: float) -> float:
    """Curva exponencial: control fino cerca del centro, máximo intacto en los extremos."""
    return (1.0 - expo) * valor + expo * valor ** 3


def acondicionar(valor: float, zona: float, expo: float) -> float:
    return curva_expo(zona_muerta(valor, zona), expo)


def normalizar_eje(valor: int, minimo: int, maximo: int) -> float:
    """Convierte la lectura cruda de evdev a [-1, 1]."""
    centro = (maximo + minimo) / 2.0
    medio_rango = (maximo - minimo) / 2.0 or 1.0
    return max(-1.0, min(1.0, (valor - centro) / medio_rango))


@dataclass
class EstadoMando:
    """Ejes normalizados en [-1, 1] con +Y hacia ARRIBA."""
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    botones: set = field(default_factory=set)


def comando_desde_mando(m: EstadoMando, modo: str = "arcade", zona: float = 0.15,
                        expo: float = 0.35, limitador: float = 1.0) -> tuple[str, float, float]:
    """Devuelve ``("arcade", x, y)`` o ``("tanque", izq, der)`` en [-1, 1]."""
    if modo == "tanque":
        izq = acondicionar(m.ly, zona, expo) * limitador
        der = acondicionar(m.ry, zona, expo) * limitador
        return "tanque", izq, der
    x = acondicionar(m.lx, zona, expo) * limitador
    y = acondicionar(m.ly, zona, expo) * limitador
    return "arcade", x, y


def es_neutro(comando: tuple[str, float, float]) -> bool:
    return abs(comando[1]) < 1e-3 and abs(comando[2]) < 1e-3


def ajustar_limitador(actual: float, subir: bool) -> float:
    nuevo = actual + (PASO_LIMITADOR if subir else -PASO_LIMITADOR)
    return round(max(LIMITADOR_MIN, min(1.0, nuevo)), 2)
