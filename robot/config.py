"""Carga, validación y persistencia de la configuración del robot.

La configuración se compone de dos capas:

1. ``config.yaml`` (en la raíz del proyecto): valores de fábrica, pines y
   comentarios explicativos. Nunca se sobrescribe desde el programa.
2. ``data/config_usuario.yaml``: solo los parámetros modificados desde la
   interfaz web. Se aplica encima de la capa anterior.

Solo los parámetros listados en :data:`EDITABLES` pueden modificarse desde la
web; cada uno tiene tipo y rango válidos para evitar valores peligrosos.
"""

from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Any

import yaml

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

PALETAS_VALIDAS = ("ironbow", "arcoiris", "grises", "lava")
REFERENCIAS_VALIDAS = ("mediana", "punto", "manual")
MODOS_CONTROL = ("arcade", "tanque")

# Parámetros editables desde la interfaz: ruta -> (tipo, mínimo/opciones, máximo)
EDITABLES: dict[str, tuple] = {
    "traccion.velocidad_maxima_m_s": (float, 0.05, 1.0),
    "traccion.aceleracion_m_s2": (float, 0.05, 3.0),
    "traccion.diametro_rueda_m": (float, 0.010, 0.300),
    "traccion.separacion_orugas_m": (float, 0.05, 1.0),
    "traccion.velocidad_giro_factor": (float, 0.1, 1.0),
    "traccion.lazo_cerrado": (bool, None, None),
    "camara_termica.emisividad": (float, 0.10, 1.0),
    "camara_termica.temp_reflejada_c": (float, -20.0, 80.0),
    "analisis.umbral_posible_c": (float, 0.1, 100.0),
    "analisis.umbral_reparar_c": (float, 0.1, 100.0),
    "analisis.umbral_inmediata_c": (float, 0.1, 200.0),
    "analisis.referencia_por_defecto": (str, REFERENCIAS_VALIDAS, None),
    "interfaz.paleta": (str, PALETAS_VALIDAS, None),
    "superposicion.escala_x": (float, 0.2, 2.0),
    "superposicion.escala_y": (float, 0.2, 2.0),
    "superposicion.desplazamiento_x": (float, -0.5, 0.5),
    "superposicion.desplazamiento_y": (float, -0.5, 0.5),
    "esp32.habilitado": (bool, None, None),
    "esp32.puerto": (str, None, None),
    "reporte.empresa": (str, None, None),
    "reporte.titulo": (str, None, None),
    "reporte.responsable": (str, None, None),
    "reporte.logo": (str, None, None),
    "camara_termica.usar_dht22_como_reflejada": (bool, None, None),
    # Sensores ambientales
    "sensores.mq2.r0_kohm": (float, 0.01, 1000.0),
    "sensores.mq2.advertencia_ppm": (float, 1.0, 100000.0),
    "sensores.mq2.alarma_ppm": (float, 1.0, 100000.0),
    "sensores.mq7.r0_kohm": (float, 0.01, 1000.0),
    "sensores.mq7.advertencia_ppm": (float, 1.0, 10000.0),
    "sensores.mq7.alarma_ppm": (float, 1.0, 10000.0),
    "sensores.ldr.umbral_oscuridad_pct": (float, 0.0, 100.0),
    "sensores.calentamiento_s": (float, 0.0, 3600.0),
    "sensores.confirmaciones": (float, 1.0, 20.0),
    # Respuestas automáticas
    "respuestas.humedad_alta_pct": (float, 10.0, 100.0),
    "respuestas.factor_humedad": (float, 0.1, 1.0),
    "respuestas.permitir_retroceso_en_gas": (bool, None, None),
    "respuestas.factor_presencia": (float, 0.1, 1.0),
    "respuestas.duracion_presencia_s": (float, 0.0, 300.0),
    "respuestas.detener_por_impacto": (bool, None, None),
    "respuestas.buzzer": (bool, None, None),
    # Control Xbox
    "control_xbox.habilitado_pi": (bool, None, None),
    "control_xbox.modo": (str, MODOS_CONTROL, None),
    "control_xbox.zona_muerta": (float, 0.0, 0.6),
    "control_xbox.expo": (float, 0.0, 1.0),
}


class ErrorConfiguracion(ValueError):
    """Valor de configuración inválido."""


def _fusionar(base: dict, encima: dict) -> dict:
    """Fusiona diccionarios anidados (``encima`` tiene prioridad)."""
    resultado = copy.deepcopy(base)
    for clave, valor in encima.items():
        if isinstance(valor, dict) and isinstance(resultado.get(clave), dict):
            resultado[clave] = _fusionar(resultado[clave], valor)
        else:
            resultado[clave] = copy.deepcopy(valor)
    return resultado


def _a_bool(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on", "yes")
    return bool(valor)


def modo_simulacion() -> bool:
    """Devuelve True si la variable de entorno ROBOT_SIM está activa."""
    return _a_bool(os.environ.get("ROBOT_SIM", "0"))


class Configuracion:
    """Acceso seguro (con bloqueo) a la configuración del robot."""

    def __init__(self, ruta_base: str | Path | None = None,
                 ruta_usuario: str | Path | None = None,
                 sobrescribir: dict | None = None):
        self._bloqueo = threading.RLock()
        self.ruta_base = Path(ruta_base) if ruta_base else RAIZ_PROYECTO / "config.yaml"
        with open(self.ruta_base, "r", encoding="utf-8") as f:
            self._base = yaml.safe_load(f) or {}
        if sobrescribir:
            self._base = _fusionar(self._base, sobrescribir)

        dir_datos = Path(self._base.get("datos", {}).get("directorio", "data"))
        if not dir_datos.is_absolute():
            dir_datos = RAIZ_PROYECTO / dir_datos
        self.dir_datos = dir_datos
        self.dir_datos.mkdir(parents=True, exist_ok=True)

        self.ruta_usuario = Path(ruta_usuario) if ruta_usuario else dir_datos / "config_usuario.yaml"
        self._usuario: dict = {}
        if self.ruta_usuario.exists():
            with open(self.ruta_usuario, "r", encoding="utf-8") as f:
                self._usuario = yaml.safe_load(f) or {}
        self._datos = _fusionar(self._base, self._usuario)

    # ------------------------------------------------------------------ lectura
    def get(self, ruta: str, defecto: Any = None) -> Any:
        """Obtiene un valor con notación de puntos, p. ej. ``"pines.motor_izquierdo.pwm"``."""
        with self._bloqueo:
            nodo: Any = self._datos
            for parte in ruta.split("."):
                if not isinstance(nodo, dict) or parte not in nodo:
                    return defecto
                nodo = nodo[parte]
            return copy.deepcopy(nodo)

    def seccion(self, nombre: str) -> dict:
        return self.get(nombre, {}) or {}

    def todo(self) -> dict:
        with self._bloqueo:
            return copy.deepcopy(self._datos)

    def editables(self) -> dict:
        """Valores actuales de los parámetros editables (plano: ruta -> valor)."""
        return {ruta: self.get(ruta) for ruta in EDITABLES}

    # --------------------------------------------------------------- escritura
    @staticmethod
    def validar(ruta: str, valor: Any) -> Any:
        """Convierte y valida un valor; lanza :class:`ErrorConfiguracion`."""
        if ruta not in EDITABLES:
            raise ErrorConfiguracion(f"El parámetro '{ruta}' no es editable")
        tipo, minimo, maximo = EDITABLES[ruta]
        try:
            if tipo is bool:
                return _a_bool(valor)
            if tipo is float:
                v = float(valor)
                if v != v:  # NaN
                    raise ValueError
                if not (minimo <= v <= maximo):
                    raise ErrorConfiguracion(
                        f"'{ruta}' debe estar entre {minimo} y {maximo} (recibido {v})")
                return v
            v = str(valor).strip()
            if isinstance(minimo, tuple) and v not in minimo:
                raise ErrorConfiguracion(f"'{ruta}' debe ser uno de {', '.join(minimo)}")
            return v
        except ErrorConfiguracion:
            raise
        except (TypeError, ValueError):
            raise ErrorConfiguracion(f"Valor inválido para '{ruta}': {valor!r}") from None

    def actualizar(self, cambios: dict[str, Any], guardar: bool = True) -> dict:
        """Valida y aplica cambios (``{ruta: valor}``). Devuelve los valores aplicados."""
        validados = {ruta: self.validar(ruta, valor) for ruta, valor in cambios.items()}
        with self._bloqueo:
            propuesta = copy.deepcopy(self._datos)
            for ruta, valor in validados.items():
                _asignar(propuesta, ruta, valor)
            a = propuesta["analisis"]
            if not (a["umbral_posible_c"] < a["umbral_reparar_c"] <= a["umbral_inmediata_c"]):
                raise ErrorConfiguracion(
                    "Los umbrales ΔT deben cumplir: posible < reparar ≤ inmediata")
            for gas in ("mq2", "mq7"):
                g = propuesta.get("sensores", {}).get(gas, {})
                if "advertencia_ppm" in g and g["advertencia_ppm"] >= g["alarma_ppm"]:
                    raise ErrorConfiguracion(
                        f"{gas.upper()}: el umbral de advertencia debe ser menor que el de alarma")
            for ruta, valor in validados.items():
                _asignar(self._usuario, ruta, valor)
            self._datos = propuesta
            if guardar:
                self.ruta_usuario.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.ruta_usuario.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    yaml.safe_dump(self._usuario, f, allow_unicode=True, sort_keys=True)
                os.replace(tmp, self.ruta_usuario)
        return validados

    def restablecer(self) -> None:
        """Elimina los cambios del usuario y vuelve a los valores de config.yaml."""
        with self._bloqueo:
            self._usuario = {}
            self._datos = copy.deepcopy(self._base)
            if self.ruta_usuario.exists():
                self.ruta_usuario.unlink()


def _asignar(datos: dict, ruta: str, valor: Any) -> None:
    partes = ruta.split(".")
    nodo = datos
    for parte in partes[:-1]:
        nodo = nodo.setdefault(parte, {})
    nodo[partes[-1]] = valor
