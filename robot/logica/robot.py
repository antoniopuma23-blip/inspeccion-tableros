"""Orquestador del robot: une drivers, control, análisis térmico y persistencia.

Hilos en ejecución (todos ``daemon``):

=====================  ============  ==============================================
Hilo                   Frecuencia    Función
=====================  ============  ==============================================
control-traccion       50 Hz         Watchdog, rampa, PI y PWM de los motores
camara-termica         4 Hz          Lectura MLX90640, emisividad, estadísticas
camara-visible         10-15 fps     Captura de cuadros (JPEG bajo demanda)
estado                 2 Hz          CPU, temperatura, batería, velocidades
=====================  ============  ==============================================

La capa web se suscribe a los eventos (``termico``, ``estado``,
``seguridad``) mediante :meth:`Robot.suscribir` y los reenvía por Socket.IO.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ..config import Configuracion, modo_simulacion
from ..datos.db import BaseDatos
from ..datos.reporte_pdf import generar_reporte
from ..drivers.esp32_uart import Esp32Uart
from ..drivers.fabrica import crear_drivers, cuentas_por_vuelta
from ..drivers.sistema import MonitorSistema
from . import imagen_termica as imagen
from .analisis_termico import (Umbrales, analizar, corregir_emisividad, estadisticas,
                               validar_matriz)
from .bateria import nivel_alerta, porcentaje_lipo
from .control import ControladorTraccion, ParametrosTraccion

log = logging.getLogger(__name__)


def parametros_desde_config(cfg: Configuracion) -> ParametrosTraccion:
    t, m = cfg.seccion("traccion"), cfg.seccion("motores")
    return ParametrosTraccion(
        velocidad_maxima=float(t.get("velocidad_maxima_m_s", 0.3)),
        aceleracion=float(t.get("aceleracion_m_s2", 0.4)),
        diametro_rueda=float(t.get("diametro_rueda_m", 0.04)),
        separacion_orugas=float(t.get("separacion_orugas_m", 0.18)),
        factor_giro=float(t.get("velocidad_giro_factor", 0.6)),
        lazo_cerrado=bool(t.get("lazo_cerrado", True)),
        kp=float(t.get("kp", 0.6)),
        ki=float(t.get("ki", 2.5)),
        rpm_nominales=float(m.get("rpm_nominales", 330)),
        duty_minimo=float(m.get("duty_minimo", 0.22)),
    )


class Robot:
    def __init__(self, cfg: Configuracion, simulacion: bool | None = None, iniciar: bool = True):
        self.cfg = cfg
        self.simulacion = modo_simulacion() if simulacion is None else simulacion
        self.drivers = crear_drivers(cfg, self.simulacion)
        self.drivers.motores.detener()  # Seguridad: motores en cero al arrancar

        self.dir_capturas = cfg.dir_datos / "capturas"
        self.dir_reportes = cfg.dir_datos / "reportes"
        self.dir_capturas.mkdir(parents=True, exist_ok=True)
        self.dir_reportes.mkdir(parents=True, exist_ok=True)
        self.db = BaseDatos(cfg.dir_datos / "inspecciones.db")
        self.sistema = MonitorSistema()

        self._suscriptores: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self.control = ControladorTraccion(
            self.drivers.motores, self.drivers.encoders, parametros_desde_config(cfg),
            cuentas_por_vuelta(cfg), watchdog_s=cfg.get("seguridad.watchdog_ms", 500) / 1000.0,
            periodo_s=cfg.get("seguridad.periodo_control_ms", 20) / 1000.0,
            al_evento=self._evento_seguridad)

        # Estado térmico
        self._bloqueo_termico = threading.Lock()
        self._matriz: np.ndarray | None = None
        self._est_termico: dict = {}
        self._fps_termico = 0.0

        # Estado visible
        self._cond_visible = threading.Condition()
        self._cuadro: np.ndarray | None = None
        self._id_cuadro = 0
        self._jpeg: bytes | None = None
        self._id_jpeg = -1
        self._bloqueo_jpeg = threading.Lock()
        self.espectadores_video = 0
        self._ultimo_pedido_video = 0.0

        self.clientes = 0
        self.bateria_v: float | None = None
        self.esp32: Esp32Uart | None = None
        self._activo = threading.Event()
        self._hilos: list[threading.Thread] = []
        self._cerrado = False
        if iniciar:
            self.iniciar()

    # ------------------------------------------------------------ eventos
    def suscribir(self, evento: str, funcion: Callable[[Any], None]) -> None:
        self._suscriptores[evento].append(funcion)

    def _publicar(self, evento: str, datos: Any) -> None:
        for funcion in list(self._suscriptores.get(evento, ())):
            try:
                funcion(datos)
            except Exception:
                log.exception("Error en suscriptor de '%s'", evento)

    def _evento_seguridad(self, tipo: str, mensaje: str) -> None:
        if tipo == "paro" and self.esp32:
            self.esp32.enviar({"t": "paro"})
        self._publicar("seguridad", {"tipo": tipo, "mensaje": mensaje,
                                     "hora": datetime.now().strftime("%H:%M:%S")})

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        self._activo.set()
        self.control.iniciar()
        for nombre, objetivo in (("camara-termica", self._bucle_termico),
                                 ("camara-visible", self._bucle_visible),
                                 ("estado", self._bucle_estado)):
            hilo = threading.Thread(target=objetivo, name=nombre, daemon=True)
            hilo.start()
            self._hilos.append(hilo)
        if self.cfg.get("esp32.habilitado", False):
            self._iniciar_esp32()
        log.info("Robot iniciado (%s)", "SIMULACIÓN" if self.simulacion else "hardware real")

    def cerrar(self) -> None:
        """Apagado seguro: motores en cero primero, luego el resto."""
        if self._cerrado:
            return
        self._cerrado = True
        log.info("Cerrando robot: deteniendo motores")
        try:
            self.control.finalizar()
        finally:
            self.drivers.motores.detener()
        self._activo.clear()
        with self._cond_visible:
            self._cond_visible.notify_all()
        for hilo in self._hilos:
            hilo.join(timeout=1.5)
        if self.esp32:
            self.esp32.detener()
        self.drivers.cerrar()
        log.info("Robot cerrado")

    # ------------------------------------------------------------ movimiento
    def comando_movimiento(self, x: float, y: float) -> bool:
        return self.control.comando(float(x), float(y))

    def detener(self, motivo: str = "") -> None:
        self.control.detener(motivo)

    def paro_emergencia(self) -> None:
        self.control.paro_emergencia()

    def rearmar(self) -> None:
        self.control.rearmar()

    # ------------------------------------------------------------ cámara térmica
    def _bucle_termico(self) -> None:
        t_previo = None
        while self._activo.is_set():
            drv = self.drivers.termica
            if not drv.disponible:
                time.sleep(1.0)
                continue
            try:
                cuadro = drv.leer_cuadro()
            except Exception:
                log.exception("Error leyendo cámara térmica")
                time.sleep(0.5)
                continue
            if cuadro is None:
                time.sleep(0.05)
                continue
            ct = self.cfg.seccion("camara_termica")
            matriz = corregir_emisividad(cuadro, float(ct.get("emisividad", 0.95)),
                                         float(ct.get("temp_reflejada_c", 25.0)))
            est = estadisticas(matriz)
            ahora = time.monotonic()
            if t_previo is not None:
                fps = 1.0 / max(1e-3, ahora - t_previo)
                self._fps_termico = fps if self._fps_termico == 0 else 0.8 * self._fps_termico + 0.2 * fps
            t_previo = ahora
            with self._bloqueo_termico:
                self._matriz = matriz
                self._est_termico = est
            self._publicar("termico", {
                "datos": np.round(matriz, 1).ravel().tolist(),
                "filas": int(matriz.shape[0]), "columnas": int(matriz.shape[1]),
                "t_max": round(est["t_max"], 1), "t_min": round(est["t_min"], 1),
                "t_mediana": round(est["t_mediana"], 1), "pos_max": est["pos_max"],
                "fps": round(self._fps_termico, 1),
            })

    def matriz_termica(self) -> np.ndarray | None:
        with self._bloqueo_termico:
            return None if self._matriz is None else self._matriz.copy()

    # ------------------------------------------------------------ cámara visible
    def _bucle_visible(self) -> None:
        sin_senal = None
        while self._activo.is_set():
            drv = self.drivers.visible
            if not drv.disponible:
                if sin_senal is None:
                    cv = self.cfg.seccion("camara_visible")
                    sin_senal = imagen.imagen_sin_senal(cv.get("ancho", 640), cv.get("alto", 480),
                                                        "CAMARA NO DISPONIBLE")
                self._nuevo_cuadro(sin_senal)
                time.sleep(1.0)
                continue
            # Sin espectadores se baja a ~2 fps para ahorrar CPU (se conserva un
            # cuadro reciente para las capturas)
            if self.espectadores_video <= 0 and time.monotonic() - self._ultimo_pedido_video > 10:
                time.sleep(0.5)
            try:
                cuadro = drv.leer_cuadro()
            except Exception:
                log.exception("Error leyendo cámara visible")
                time.sleep(0.5)
                continue
            if cuadro is not None:
                self._nuevo_cuadro(cuadro)
            else:
                time.sleep(0.05)

    def _nuevo_cuadro(self, cuadro: np.ndarray) -> None:
        with self._cond_visible:
            self._cuadro = cuadro
            self._id_cuadro += 1
            self._cond_visible.notify_all()

    def esperar_jpeg(self, id_previo: int = -1, timeout: float = 2.0) -> tuple[bytes | None, int]:
        """Espera un cuadro más nuevo que ``id_previo`` y lo devuelve en JPEG (compartido)."""
        self._ultimo_pedido_video = time.monotonic()
        with self._cond_visible:
            self._cond_visible.wait_for(
                lambda: (self._cuadro is not None and self._id_cuadro != id_previo)
                or not self._activo.is_set(), timeout)
            cuadro, id_cuadro = self._cuadro, self._id_cuadro
        if cuadro is None:
            return None, id_cuadro
        with self._bloqueo_jpeg:
            if self._id_jpeg != id_cuadro:
                calidad = int(self.cfg.get("camara_visible.calidad_jpeg", 70))
                ok, buf = cv2.imencode(".jpg", cuadro, [cv2.IMWRITE_JPEG_QUALITY, calidad])
                if ok:
                    self._jpeg, self._id_jpeg = buf.tobytes(), id_cuadro
            return self._jpeg, id_cuadro

    def cuadro_visible(self) -> np.ndarray | None:
        with self._cond_visible:
            return None if self._cuadro is None else self._cuadro.copy()

    # ------------------------------------------------------------ estado
    def _bucle_estado(self) -> None:
        while self._activo.is_set():
            estado = self.estado()
            self._publicar("estado", estado)
            if self.esp32:
                self.esp32.enviar({"t": "estado", "paro": estado["paro_emergencia"],
                                   "v_izq": estado["v_izq"], "v_der": estado["v_der"]})
            time.sleep(0.5)

    def _leer_bateria(self) -> float | None:
        if self.esp32 and self.esp32.conectado and self.bateria_v is not None:
            return self.bateria_v
        if self.drivers.bateria_sim is not None:
            carga = sum(abs(d) for d in self.drivers.motores.ultimo_duty) / 2
            return self.drivers.bateria_sim.voltaje(carga)
        return None

    def estado(self) -> dict:
        v = self._leer_bateria()
        pct = porcentaje_lipo(v, int(self.cfg.get("bateria.celdas", 2)))
        return {
            **self.control.estado(),
            **self.sistema.leer(),
            "bateria_v": v,
            "bateria_pct": None if pct is None else round(pct),
            "bateria_alerta": nivel_alerta(pct),
            "clientes": self.clientes,
            "fps_termico": round(self._fps_termico, 1),
            "termica_ok": self.drivers.termica.disponible,
            "visible_ok": self.drivers.visible.disponible,
            "motores_ok": self.drivers.motores.disponible,
            "simulacion": self.simulacion,
            "esp32": self.esp32.estado() if self.esp32 else None,
            "hora": datetime.now().strftime("%H:%M:%S"),
        }

    def diagnostico(self) -> dict:
        d = self.drivers
        return {
            "simulacion": self.simulacion,
            "componentes": {
                "motores": d.motores.estado(), "encoders": d.encoders.estado(),
                "camara_termica": d.termica.estado(), "camara_visible": d.visible.estado(),
            },
            "esp32": self.esp32.estado() if self.esp32 else {"habilitado": False},
            "parametros_control": self.control.parametros(),
            "cuentas_por_vuelta": self.control.cpr,
            "watchdog_ms": round(self.control.watchdog.tiempo_limite * 1000),
            "expiraciones_watchdog": self.control.watchdog.expiraciones,
        }

    # ------------------------------------------------------------ ESP32
    def _iniciar_esp32(self) -> None:
        e = self.cfg.seccion("esp32")
        self.esp32 = Esp32Uart(e.get("puerto", "/dev/serial0"), int(e.get("baudios", 115200)),
                               self._mensaje_esp32)
        try:
            self.esp32.iniciar()
        except ImportError:
            self.esp32.error = "Falta pyserial (pip install pyserial)"
            log.error(self.esp32.error)

    def _mensaje_esp32(self, msg: dict) -> None:
        tipo = msg.get("t")
        if tipo == "bateria":
            try:
                self.bateria_v = float(msg["v"])
            except (KeyError, TypeError, ValueError):
                pass
        elif tipo == "paro":
            self.paro_emergencia()
        elif tipo == "log":
            log.info("ESP32: %s", msg.get("msg", ""))

    # ------------------------------------------------------------ configuración
    def aplicar_configuracion(self, cambios: dict) -> dict:
        aplicados = self.cfg.actualizar(cambios)
        self.control.actualizar_parametros(**vars(parametros_desde_config(self.cfg)))
        if hasattr(self.drivers.termica, "superposicion"):
            self.drivers.termica.superposicion = self.cfg.seccion("superposicion")
        if "esp32.habilitado" in aplicados or "esp32.puerto" in aplicados:
            if self.esp32:
                self.esp32.detener()
                self.esp32 = None
                self.bateria_v = None
            if self.cfg.get("esp32.habilitado"):
                self._iniciar_esp32()
        return aplicados

    def umbrales(self) -> Umbrales:
        a = self.cfg.seccion("analisis")
        return Umbrales(float(a["umbral_posible_c"]), float(a["umbral_reparar_c"]),
                        float(a["umbral_inmediata_c"]))

    # ------------------------------------------------------------ captura de hallazgos
    def capturar(self, tablero: str, notas: str = "", ubicacion: str = "",
                 referencia: dict | None = None, matriz_congelada=None) -> dict:
        """Guarda un hallazgo: imágenes, matriz térmica, análisis ΔT y metadatos.

        ``matriz_congelada`` permite guardar exactamente el cuadro que el
        operador congeló en la interfaz para elegir la referencia.
        """
        tablero = (tablero or "").strip()
        if not tablero:
            raise ValueError("Escribe el nombre o ID del tablero antes de capturar")
        matriz = validar_matriz(matriz_congelada) if matriz_congelada is not None else self.matriz_termica()
        if matriz is None:
            raise RuntimeError("No hay datos de la cámara térmica para capturar")
        visible = self.cuadro_visible()
        ref = referencia or {}
        modo = ref.get("modo") or self.cfg.get("analisis.referencia_por_defecto", "mediana")
        punto = (ref.get("fila"), ref.get("columna")) if modo == "punto" else None
        if modo == "punto" and None in punto:
            raise ValueError("Haz clic en la imagen térmica para elegir el punto de referencia")
        valor = float(ref["valor"]) if modo == "manual" and ref.get("valor") not in (None, "") else None
        analisis = analizar(matriz, self.umbrales(), modo, punto, valor)

        emisividad = float(self.cfg.get("camara_termica.emisividad", 0.95))
        id_insp = self.db.insertar_inspeccion(tablero, analisis, matriz, emisividad, notas,
                                              ubicacion, self.simulacion)
        paleta = self.cfg.get("interfaz.paleta", "ironbow")
        base = f"INS-{id_insp:05d}"
        termica = imagen.renderizar_termica(matriz, paleta, 480, pos_max=analisis["pos_max"],
                                            punto_ref=analisis["punto_referencia"])
        rutas = {"img_termica": f"{base}_termica.png",
                 "img_mini_termica": f"{base}_mini_termica.jpg"}
        cv2.imwrite(str(self.dir_capturas / rutas["img_termica"]), termica)
        cv2.imwrite(str(self.dir_capturas / rutas["img_mini_termica"]),
                    imagen.miniatura(imagen.renderizar_termica(matriz, paleta, 160, con_barra=False,
                                                               pos_max=analisis["pos_max"])))
        if visible is not None:
            rutas["img_visible"] = f"{base}_visible.jpg"
            rutas["img_fusion"] = f"{base}_fusion.jpg"
            rutas["img_miniatura"] = f"{base}_mini.jpg"
            cv2.imwrite(str(self.dir_capturas / rutas["img_visible"]), visible,
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
            cv2.imwrite(str(self.dir_capturas / rutas["img_fusion"]),
                        imagen.fusionar(visible, matriz, paleta, self.cfg.seccion("superposicion")),
                        [cv2.IMWRITE_JPEG_QUALITY, 88])
            cv2.imwrite(str(self.dir_capturas / rutas["img_miniatura"]), imagen.miniatura(visible))
        self.db.actualizar_imagenes(id_insp, **rutas)
        log.info("Hallazgo %s guardado: %s, ΔT=%.1f °C (%s)", base, tablero, analisis["delta_t"],
                 analisis["clasificacion"]["etiqueta"])
        return self.db.obtener_inspeccion(id_insp)

    def generar_pdf(self, id_insp: int) -> Path:
        insp = self.db.obtener_inspeccion(id_insp)
        if insp is None:
            raise KeyError(f"No existe la inspección {id_insp}")
        u = self.umbrales()
        return generar_reporte(insp, self.cfg.seccion("reporte"),
                               {"posible": u.posible, "reparar": u.reparar, "inmediata": u.inmediata},
                               self.dir_reportes / f"INS-{id_insp:05d}.pdf", self.dir_capturas,
                               self.cfg.get("robot.nombre", "Robot de inspección térmica"))

    def eliminar_inspeccion(self, id_insp: int) -> bool:
        insp = self.db.eliminar_inspeccion(id_insp)
        if insp is None:
            return False
        for clave in ("img_visible", "img_termica", "img_fusion", "img_miniatura", "img_mini_termica"):
            if insp.get(clave):
                (self.dir_capturas / insp[clave]).unlink(missing_ok=True)
        (self.dir_reportes / f"INS-{id_insp:05d}.pdf").unlink(missing_ok=True)
        return True
