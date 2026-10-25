"""Fábrica de drivers: elige la implementación real o simulada de cada componente.

* ``ROBOT_SIM=1`` → todos los componentes simulados.
* En la Raspberry Pi → componentes reales. Si alguno falla al iniciar (sensor
  desconectado, pigpiod detenido, etc.) se sustituye por un driver "nulo" y el
  motivo se muestra en la página de Configuración (sección Diagnóstico). Así
  el robot nunca queda en un estado indefinido.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import Configuracion
from .base import (CamaraTermicaNula, CamaraVisibleNula, DriverCamaraTermica,
                   DriverCamaraVisible, DriverEncoders, DriverMotores,
                   DriverSensoresAmbiente, EncodersNulos, MotoresNulos,
                   SensoresAmbienteNulos)

log = logging.getLogger(__name__)


def cuentas_por_vuelta(cfg: Configuracion) -> int:
    e = cfg.seccion("encoders")
    modo = 4 if int(e.get("modo_cuadratura", 4)) == 4 else 1
    return int(e.get("pulsos_por_vuelta_motor", 11)) * int(e.get("relacion_reduccion", 30)) * modo


@dataclass
class ConjuntoDrivers:
    motores: DriverMotores
    encoders: DriverEncoders
    termica: DriverCamaraTermica
    visible: DriverCamaraVisible
    simulacion: bool
    ambiente: DriverSensoresAmbiente = field(default_factory=lambda: SensoresAmbienteNulos("No configurados"))
    extras: dict = field(default_factory=dict)

    def cerrar(self) -> None:
        """Cierra todo; los motores primero y en cero."""
        for nombre in ("motores", "encoders", "termica", "visible", "ambiente"):
            drv = getattr(self, nombre)
            try:
                if nombre == "motores":
                    drv.detener()
                drv.cerrar()
            except Exception:
                log.exception("Error cerrando %s", nombre)
        pi = self.extras.get("pi")
        if pi is not None:
            try:
                pi.stop()
            except Exception:  # pragma: no cover
                pass


def crear_simulados(cfg: Configuracion) -> ConjuntoDrivers:
    from .simulados import (BateriaSimulada, CamaraTermicaSimulada,
                            CamaraVisibleSimulada, EncodersSimulados,
                            EscenaTablero, ModeloTraccionSimulado,
                            MotoresSimulados, SensoresAmbienteSimulados)

    t, m, cv = cfg.seccion("traccion"), cfg.seccion("motores"), cfg.seccion("camara_visible")
    modelo = ModeloTraccionSimulado(
        rpm_nominales=m.get("rpm_nominales", 330),
        duty_minimo=m.get("duty_minimo", 0.22),
        diametro_m=t.get("diametro_rueda_m", 0.04),
        separacion_m=t.get("separacion_orugas_m", 0.18),
        cuentas_por_vuelta=cuentas_por_vuelta(cfg),
    )
    escena = EscenaTablero()
    bateria = BateriaSimulada(cfg.get("bateria.celdas", 2)) if cfg.get("simulacion.bateria_simulada", True) else None
    return ConjuntoDrivers(
        motores=MotoresSimulados(modelo),
        encoders=EncodersSimulados(modelo),
        termica=CamaraTermicaSimulada(escena, modelo, cfg.get("camara_termica.frecuencia_hz", 4),
                                      cfg.seccion("superposicion")),
        visible=CamaraVisibleSimulada(escena, modelo, cv.get("ancho", 640), cv.get("alto", 480),
                                      cv.get("fps", 12)),
        simulacion=True,
        ambiente=SensoresAmbienteSimulados(lambda: cfg.seccion("sensores"), bateria, modelo),
        extras={"modelo": modelo, "escena": escena},
    )


def crear_reales(cfg: Configuracion) -> ConjuntoDrivers:
    pines, m, e = cfg.seccion("pines"), cfg.seccion("motores"), cfg.seccion("encoders")
    extras: dict = {}

    # --- Motores y encoders (pigpio) ---------------------------------------
    motores: DriverMotores
    encoders: DriverEncoders
    try:
        import pigpio

        pi = pigpio.pi()
        if not pi.connected:
            raise RuntimeError("pigpiod no está en ejecución (sudo systemctl start pigpiod)")
        extras["pi"] = pi
    except Exception as ex:
        log.error("pigpio no disponible: %s", ex)
        pi = None
        motores, encoders = MotoresNulos(str(ex)), EncodersNulos(str(ex))

    if pi is not None:
        from .encoders_pigpio import EncodersPigpio
        from .motores_l298n import MotoresL298N
        try:
            motores = MotoresL298N(pi, pines, m.get("pwm_frecuencia_hz", 2000),
                                   m.get("invertir_izquierdo", False), m.get("invertir_derecho", False))
        except Exception as ex:
            log.exception("Falla al iniciar motores")
            motores = MotoresNulos(str(ex))
        try:
            encoders = EncodersPigpio(pi, pines, e.get("modo_cuadratura", 4), e.get("filtro_glitch_us", 20),
                                      e.get("invertir_izquierdo", False), e.get("invertir_derecho", False))
        except Exception as ex:
            log.exception("Falla al iniciar encoders")
            encoders = EncodersNulos(str(ex))

    # --- Cámara térmica ----------------------------------------------------
    ct = cfg.seccion("camara_termica")
    termica: DriverCamaraTermica
    if ct.get("habilitada", True):
        try:
            from .mlx90640 import CamaraMLX90640
            termica = CamaraMLX90640(ct.get("frecuencia_hz", 4), ct.get("i2c_frecuencia_hz", 400000),
                                     ct.get("rotar_180", False), ct.get("espejo_horizontal", False))
        except Exception as ex:
            log.error("MLX90640 no disponible: %s", ex)
            termica = CamaraTermicaNula(str(ex))
    else:
        termica = CamaraTermicaNula("Deshabilitada en config.yaml")

    # --- Cámara visible ----------------------------------------------------
    cv = cfg.seccion("camara_visible")
    visible: DriverCamaraVisible
    try:
        from .camara_visible import detectar_camara
        visible = detectar_camara(cv.get("tipo", "usb"), cv.get("indice_usb", -1),
                                  cv.get("ancho", 640), cv.get("alto", 480), cv.get("fps", 12))
    except Exception as ex:
        log.error("Cámara visible no disponible: %s", ex)
        visible = CamaraVisibleNula(str(ex))

    # --- Sensores ambientales de TechRoach -----------------------------------
    ambiente: DriverSensoresAmbiente
    if not cfg.get("sensores.habilitados", True):
        ambiente = SensoresAmbienteNulos("Deshabilitados en config.yaml")
    elif pi is None:
        ambiente = SensoresAmbienteNulos("Requieren pigpiod")
    else:
        try:
            from .sensores_rpi import SensoresAmbienteRPi
            ambiente = SensoresAmbienteRPi(pi, pines, cfg.seccion("sensores"))
        except Exception as ex:
            log.exception("Falla al iniciar sensores ambientales")
            ambiente = SensoresAmbienteNulos(str(ex))

    return ConjuntoDrivers(motores, encoders, termica, visible, simulacion=False,
                           ambiente=ambiente, extras=extras)


def crear_drivers(cfg: Configuracion, simulacion: bool) -> ConjuntoDrivers:
    if simulacion:
        log.info("MODO SIMULACIÓN: se usan drivers simulados")
        return crear_simulados(cfg)
    return crear_reales(cfg)
