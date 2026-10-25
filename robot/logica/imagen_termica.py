"""Generación de imágenes térmicas para capturas y reportes (lado servidor).

En vivo, el coloreado lo hace el navegador (menos CPU en la Pi); estas
funciones solo se usan al guardar un hallazgo y al generar el PDF.
"""

from __future__ import annotations

import cv2
import numpy as np

from .analisis_termico import caja_punto_caliente
from .paletas import aplicar_paleta

_FUENTE = cv2.FONT_HERSHEY_SIMPLEX


def _texto(img: np.ndarray, texto: str, org: tuple[int, int], escala: float = 0.45,
           color=(255, 255, 255), grosor: int = 1, contorno: bool = True) -> None:
    """Escribe texto con contorno; dibuja el símbolo ° (no existe en las fuentes Hershey)."""
    x, y = org
    partes = texto.split("°")
    for i, parte in enumerate(partes):
        if parte:
            if contorno:
                cv2.putText(img, parte, (x, y), _FUENTE, escala, (0, 0, 0), grosor + 2, cv2.LINE_AA)
            cv2.putText(img, parte, (x, y), _FUENTE, escala, color, grosor, cv2.LINE_AA)
            x += cv2.getTextSize(parte, _FUENTE, escala, grosor)[0][0]
        if i < len(partes) - 1:
            r = max(2, int(4 * escala))
            centro = (x + r + 1, y - int(18 * escala))
            if contorno:
                cv2.circle(img, centro, r, (0, 0, 0), grosor + 2, cv2.LINE_AA)
            cv2.circle(img, centro, r, color, grosor, cv2.LINE_AA)
            x += 2 * r + 3


def barra_color(alto: int, t_min: float, t_max: float, paleta: str, ancho: int = 70) -> np.ndarray:
    """Escala de temperatura vertical con etiquetas."""
    barra = np.full((alto, ancho, 3), 30, np.uint8)
    gradiente = np.linspace(t_max, t_min, alto - 20, dtype=np.float32)[:, None].repeat(16, axis=1)
    barra[10:alto - 10, 6:22] = aplicar_paleta(gradiente, t_min, t_max, paleta)
    for k in range(5):
        frac = k / 4
        y = int(10 + frac * (alto - 21))
        t = t_max - frac * (t_max - t_min)
        cv2.line(barra, (22, y), (27, y), (220, 220, 220), 1)
        _texto(barra, f"{t:.1f}°", (29, y + 5), 0.4, contorno=False)
    return barra


def renderizar_termica(matriz: np.ndarray, paleta: str = "ironbow", ancho: int = 480,
                       t_min: float | None = None, t_max: float | None = None,
                       pos_max: list | None = None, punto_ref: list | None = None,
                       con_barra: bool = True) -> np.ndarray:
    """Imagen BGR del mapa térmico escalado, con punto caliente, referencia y escala."""
    m = np.asarray(matriz, np.float32)
    filas, columnas = m.shape
    t_min = float(m.min()) if t_min is None else t_min
    t_max = float(m.max()) if t_max is None else t_max
    alto = int(ancho * filas / columnas)
    interpolada = cv2.resize(m, (ancho, alto), interpolation=cv2.INTER_CUBIC)
    img = aplicar_paleta(interpolada, t_min, t_max, paleta)
    sx, sy = ancho / columnas, alto / filas

    if pos_max is not None:
        c0, f0, c1, f1 = caja_punto_caliente(pos_max, filas, columnas)
        p0 = (int(c0 * sx), int(f0 * sy))
        p1 = (int((c1 + 1) * sx) - 1, int((f1 + 1) * sy) - 1)
        cv2.rectangle(img, p0, p1, (0, 0, 0), 4)
        cv2.rectangle(img, p0, p1, (255, 255, 255), 2)
        t = float(m[int(pos_max[0]), int(pos_max[1])])
        etiqueta_y = p0[1] - 8 if p0[1] > 24 else p1[1] + 20
        _texto(img, f"MAX {t:.1f}°C", (max(2, min(p0[0], ancho - 120)), etiqueta_y), 0.55)

    if punto_ref is not None:
        cx = int((int(punto_ref[1]) + 0.5) * sx)
        cy = int((int(punto_ref[0]) + 0.5) * sy)
        for color, g in (((0, 0, 0), 4), ((80, 255, 80), 2)):
            cv2.drawMarker(img, (cx, cy), color, cv2.MARKER_CROSS, 22, g, cv2.LINE_AA)
        _texto(img, "REF", (cx + 10, cy - 8), 0.45, color=(80, 255, 80))

    if con_barra:
        img = np.hstack([img, barra_color(alto, t_min, t_max, paleta)])
    return img


def fusionar(visible: np.ndarray, matriz: np.ndarray, paleta: str = "ironbow",
             superposicion: dict | None = None, alfa: float = 0.55) -> np.ndarray:
    """Superpone el térmico sobre el visible respetando la alineación configurada."""
    s = superposicion or {}
    h, w = visible.shape[:2]
    tw = int(w * s.get("escala_x", 0.88))
    th = int(h * s.get("escala_y", 0.72))
    cx = int(w * (0.5 + s.get("desplazamiento_x", 0.0)))
    cy = int(h * (0.5 + s.get("desplazamiento_y", 0.0)))
    x0, y0 = cx - tw // 2, cy - th // 2
    m = np.asarray(matriz, np.float32)
    termica = aplicar_paleta(cv2.resize(m, (tw, th), interpolation=cv2.INTER_CUBIC),
                             float(m.min()), float(m.max()), paleta)
    salida = visible.copy()
    # Recorte por si la región sale del cuadro visible
    vx0, vy0 = max(0, x0), max(0, y0)
    vx1, vy1 = min(w, x0 + tw), min(h, y0 + th)
    if vx1 > vx0 and vy1 > vy0:
        region = salida[vy0:vy1, vx0:vx1]
        parche = termica[vy0 - y0:vy1 - y0, vx0 - x0:vx1 - x0]
        salida[vy0:vy1, vx0:vx1] = cv2.addWeighted(parche, alfa, region, 1 - alfa, 0)
        cv2.rectangle(salida, (vx0, vy0), (vx1 - 1, vy1 - 1), (255, 255, 255), 1)
    return salida


def miniatura(img: np.ndarray, ancho: int = 160) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.resize(img, (ancho, int(h * ancho / w)), interpolation=cv2.INTER_AREA)


def imagen_sin_senal(ancho: int = 640, alto: int = 480, texto: str = "SIN SENAL") -> np.ndarray:
    """Cuadro de reemplazo cuando no hay cámara."""
    img = np.full((alto, ancho, 3), 24, np.uint8)
    for x in range(-alto, ancho, 40):
        cv2.line(img, (x, alto), (x + alto, 0), (34, 34, 38), 12)
    tam = cv2.getTextSize(texto, _FUENTE, 1.0, 2)[0]
    _texto(img, texto, ((ancho - tam[0]) // 2, (alto + tam[1]) // 2), 1.0, (90, 180, 255), 2)
    return img
