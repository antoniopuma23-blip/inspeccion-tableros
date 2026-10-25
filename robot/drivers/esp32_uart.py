"""Comunicación opcional con un ESP32-S3 por UART (JSON por línea).

DESACTIVADO por defecto (``esp32.habilitado: false``). El robot funciona
completo sin el ESP32; este módulo solo agrega funciones extra.

Protocolo (cada mensaje es un objeto JSON terminado en ``\\n``, 115200 baud):

Raspberry Pi → ESP32
    {"t": "estado", "paro": false, "v_izq": 0.12, "v_der": 0.12}   (2 Hz)
    {"t": "paro"}                                                  (inmediato)

ESP32 → Raspberry Pi
    {"t": "bateria", "v": 7.62}        voltaje del paquete LiPo
    {"t": "paro"}                      botón de paro físico conectado al ESP32
    {"t": "latido"}                    mantiene la conexión viva
    {"t": "log", "msg": "texto"}       mensaje de diagnóstico

Conexión: TX Pi (GPIO14) → RX ESP32, RX Pi (GPIO15) ← TX ESP32, GND común.
Ambos trabajan con lógica de 3.3 V, no se requiere convertidor de nivel.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


def decodificar_linea(linea: bytes | str) -> dict | None:
    """Convierte una línea recibida en un diccionario; None si es inválida."""
    if isinstance(linea, bytes):
        linea = linea.decode("utf-8", errors="replace")
    linea = linea.strip()
    if not linea:
        return None
    try:
        msg = json.loads(linea)
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) and "t" in msg else None


def codificar_mensaje(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


class Esp32Uart:
    """Hilo lector/escritor del puerto serie hacia el ESP32."""

    def __init__(self, puerto: str = "/dev/serial0", baudios: int = 115200,
                 al_recibir: Callable[[dict], None] | None = None):
        self.puerto = puerto
        self.baudios = baudios
        self.al_recibir = al_recibir
        self._serial = None
        self._hilo: threading.Thread | None = None
        self._activo = threading.Event()
        self._bloqueo_tx = threading.Lock()
        self.ultimo_mensaje = 0.0
        self.error = ""

    @property
    def conectado(self) -> bool:
        return self._serial is not None and (time.monotonic() - self.ultimo_mensaje) < 3.0

    def iniciar(self) -> None:
        import serial  # pyserial, importación diferida

        try:
            self._serial = serial.Serial(self.puerto, self.baudios, timeout=0.2)
        except Exception as e:
            self.error = str(e)
            self._serial = None
            log.warning("No se pudo abrir el puerto del ESP32 %s: %s", self.puerto, e)
            return
        self.error = ""
        self._activo.set()
        self._hilo = threading.Thread(target=self._leer, name="esp32-uart", daemon=True)
        self._hilo.start()
        log.info("Comunicación con ESP32 iniciada en %s", self.puerto)

    def _leer(self) -> None:
        while self._activo.is_set():
            try:
                linea = self._serial.readline()
            except Exception as e:  # pragma: no cover - solo en hardware
                self.error = str(e)
                time.sleep(0.5)
                continue
            msg = decodificar_linea(linea)
            if msg is None:
                continue
            self.ultimo_mensaje = time.monotonic()
            if self.al_recibir:
                try:
                    self.al_recibir(msg)
                except Exception:
                    log.exception("Error procesando mensaje del ESP32")

    def enviar(self, msg: dict) -> bool:
        if self._serial is None:
            return False
        try:
            with self._bloqueo_tx:
                self._serial.write(codificar_mensaje(msg))
            return True
        except Exception as e:  # pragma: no cover - solo en hardware
            self.error = str(e)
            return False

    def detener(self) -> None:
        self._activo.clear()
        if self._hilo:
            self._hilo.join(timeout=1.0)
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # pragma: no cover
                pass
        self._serial = None

    def estado(self) -> dict:
        return {"puerto": self.puerto, "conectado": self.conectado, "error": self.error}
