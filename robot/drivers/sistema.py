"""Lectura de recursos del sistema: CPU, temperatura del SoC y memoria."""

from __future__ import annotations

import os
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - psutil es opcional
    psutil = None

_RUTA_TEMP = Path("/sys/class/thermal/thermal_zone0/temp")


class MonitorSistema:
    def __init__(self):
        self._proceso = psutil.Process(os.getpid()) if psutil else None
        self._cpu_previo = self._leer_proc_stat()
        if psutil:
            psutil.cpu_percent(None)  # primera lectura de referencia

    @staticmethod
    def _leer_proc_stat() -> tuple[int, int] | None:
        try:
            with open("/proc/stat", "r") as f:
                valores = [int(x) for x in f.readline().split()[1:]]
            inactivo = valores[3] + (valores[4] if len(valores) > 4 else 0)
            return inactivo, sum(valores)
        except (OSError, ValueError, IndexError):
            return None

    def cpu_porcentaje(self) -> float | None:
        if psutil:
            return float(psutil.cpu_percent(None))
        actual = self._leer_proc_stat()
        if actual is None or self._cpu_previo is None:
            return None
        d_inactivo = actual[0] - self._cpu_previo[0]
        d_total = actual[1] - self._cpu_previo[1]
        self._cpu_previo = actual
        return round(100.0 * (1 - d_inactivo / d_total), 1) if d_total > 0 else 0.0

    @staticmethod
    def temperatura_soc() -> float | None:
        """Temperatura del procesador en °C (None si no existe el sensor)."""
        try:
            return round(int(_RUTA_TEMP.read_text().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            return None

    def memoria_proceso_mb(self) -> float | None:
        if self._proceso is None:
            return None
        return round(self._proceso.memory_info().rss / 1_048_576, 1)

    def leer(self) -> dict:
        return {
            "cpu": self.cpu_porcentaje(),
            "temp_soc": self.temperatura_soc(),
            "ram_mb": self.memoria_proceso_mb(),
        }
