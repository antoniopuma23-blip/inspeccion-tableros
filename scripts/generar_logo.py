#!/usr/bin/env python3
"""Genera el logo por defecto (robot/web/static/img/logo.png).

Se usa en la cabecera de la interfaz y en el reporte PDF cuando no se ha
subido un logo propio. Uso: ``python scripts/generar_logo.py``.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from robot.logica.paletas import tabla_bgr  # noqa: E402

AMBAR = (17, 163, 252)
FONDO = (28, 20, 13)


def generar(ruta: Path, tam: int = 256) -> None:
    S = 512
    img = np.zeros((S, S, 3), np.uint8)
    img[:] = FONDO
    # Máscara de esquinas redondeadas (canal alfa)
    mascara = np.zeros((S, S), np.uint8)
    cv2.rectangle(mascara, (40, 0), (S - 41, S - 1), 255, -1)
    cv2.rectangle(mascara, (0, 40), (S - 1, S - 41), 255, -1)
    for c in ((40, 40), (S - 41, 40), (40, S - 41), (S - 41, S - 41)):
        cv2.circle(mascara, c, 40, 255, -1)

    # Disco "térmico" con la paleta ironbow
    centro = (S // 2, int(S * 0.43))
    yy, xx = np.mgrid[0:S, 0:S]
    d = np.hypot(xx - centro[0], yy - centro[1])
    t = np.clip(1 - d / 170, 0, 1) ** 0.9
    disco = tabla_bgr("ironbow")[(t * 255).astype(np.uint8)]
    img[d < 170] = disco[d < 170]
    cv2.circle(img, centro, 170, AMBAR, 10, cv2.LINE_AA)

    # Retícula
    cx, cy = centro
    for a, b in (((cx - 120, cy), (cx - 45, cy)), ((cx + 45, cy), (cx + 120, cy)),
                 ((cx, cy - 120), (cx, cy - 45)), ((cx, cy + 45), (cx, cy + 120))):
        cv2.line(img, a, b, (255, 255, 255), 10, cv2.LINE_AA)
    cv2.circle(img, centro, 18, (255, 255, 255), -1, cv2.LINE_AA)

    # Oruga
    cv2.rectangle(img, (145, 400), (367, 470), AMBAR, -1)
    for x in (145, 367):
        cv2.circle(img, (x, 435), 35, AMBAR, -1, cv2.LINE_AA)
    for x in range(150, 380, 55):
        cv2.circle(img, (x, 435), 17, FONDO, -1, cv2.LINE_AA)

    salida = np.dstack([img, mascara])
    ruta.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(ruta), cv2.resize(salida, (tam, tam), interpolation=cv2.INTER_AREA))


if __name__ == "__main__":
    destino = RAIZ / "robot" / "web" / "static" / "img" / "logo.png"
    generar(destino)
    print(f"Logo generado en {destino}")
