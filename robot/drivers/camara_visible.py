"""Drivers reales de la cámara visible.

La cámara principal es una **webcam USB** (reemplaza a la ESP32-CAM del
proyecto TechRoach, ya que la Raspberry Pi tiene puertos USB), leída con
OpenCV (``cv2.VideoCapture``). Con ``indice_usb: -1`` se busca la primera
webcam disponible entre /dev/video0 y /dev/video3.

También se soporta un módulo Pi Camera por CSI (``picamera2``), con
``camara_visible.tipo: picamera2`` o ``auto``.
"""

from __future__ import annotations

import logging

import numpy as np

from .base import DriverCamaraVisible

log = logging.getLogger(__name__)


class CamaraPicamera2(DriverCamaraVisible):
    nombre = "Pi Camera (picamera2)"

    def __init__(self, ancho: int = 640, alto: int = 480, fps: int = 12):
        from picamera2 import Picamera2

        if not Picamera2.global_camera_info():
            raise RuntimeError("No se detectó ningún módulo Pi Camera")
        self._cam = Picamera2()
        # "RGB888" en picamera2 entrega los bytes en orden BGR (igual que OpenCV)
        cfg = self._cam.create_video_configuration(
            main={"size": (ancho, alto), "format": "RGB888"},
            controls={"FrameDurationLimits": (int(1e6 / fps), int(1e6 / fps))},
            buffer_count=2,
        )
        self._cam.configure(cfg)
        self._cam.start()
        modelo = Picamera2.global_camera_info()[0].get("Model", "desconocido")
        self.detalle = f"Sensor {modelo}, {ancho}x{alto} @ {fps} fps"
        log.info("Pi Camera inicializada (%s)", self.detalle)

    def leer_cuadro(self) -> np.ndarray | None:
        try:
            return self._cam.capture_array("main")
        except Exception as e:  # pragma: no cover - solo en hardware
            log.warning("Error capturando de Pi Camera: %s", e)
            return None

    def cerrar(self) -> None:
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:  # pragma: no cover
            pass


class CamaraUSB(DriverCamaraVisible):
    nombre = "Webcam USB (OpenCV)"

    def __init__(self, indice: int = 0, ancho: int = 640, alto: int = 480, fps: int = 12):
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(indice, cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else cv2.VideoCapture(indice)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(indice)
        if not self._cap.isOpened():
            raise RuntimeError(f"No se pudo abrir la webcam /dev/video{indice}")
        # MJPG reduce el ancho de banda USB y el uso de CPU en la Pi
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.ancho, self.alto = ancho, alto
        ok, cuadro = self._cap.read()
        if not ok or cuadro is None:
            self._cap.release()
            raise RuntimeError("La webcam no entregó imagen")
        h, w = cuadro.shape[:2]
        self.detalle = f"/dev/video{indice}, {w}x{h}"
        log.info("Webcam USB inicializada (%s)", self.detalle)

    def leer_cuadro(self) -> np.ndarray | None:
        ok, cuadro = self._cap.read()
        if not ok:
            return None
        if cuadro.shape[1] != self.ancho or cuadro.shape[0] != self.alto:
            cuadro = self._cv2.resize(cuadro, (self.ancho, self.alto))
        return cuadro

    def cerrar(self) -> None:
        self._cap.release()


def _abrir_usb(indice: int, ancho: int, alto: int, fps: int) -> "CamaraUSB":
    """Abre la webcam indicada o, con índice negativo, la primera que responda."""
    if indice >= 0:
        return CamaraUSB(indice, ancho, alto, fps)
    errores = []
    for i in range(4):
        try:
            return CamaraUSB(i, ancho, alto, fps)
        except RuntimeError as e:
            errores.append(str(e))
    raise RuntimeError("No se encontró ninguna webcam USB (" + "; ".join(errores) + ")")


def detectar_camara(tipo: str = "usb", indice_usb: int = -1, ancho: int = 640,
                    alto: int = 480, fps: int = 12) -> DriverCamaraVisible:
    """Crea la cámara indicada; con ``auto`` prueba la webcam USB y luego la Pi Camera."""
    errores = []
    if tipo in ("auto", "usb"):
        try:
            return _abrir_usb(int(indice_usb), ancho, alto, fps)
        except Exception as e:
            errores.append(f"USB: {e}")
            log.info("Webcam USB no disponible: %s", e)
    if tipo in ("auto", "picamera2"):
        try:
            return CamaraPicamera2(ancho, alto, fps)
        except Exception as e:
            errores.append(f"Pi Camera: {e}")
            log.info("Pi Camera no disponible: %s", e)
    raise RuntimeError("; ".join(errores) or f"Tipo de cámara desconocido: {tipo}")
