"""Orquestador del robot: une drivers, control, análisis térmico y persistencia.

Hilos en ejecución (todos ``daemon``):

=====================  ============  ==============================================
Hilo                   Frecuencia    Función
=====================  ============  ==============================================
control-traccion       50 Hz         Watchdog, rampa, PI y PWM de los motores
camara-termica         4 Hz          Lectura MLX90640, emisividad, estadísticas
camara-visible         10-15 fps     Captura de cuadros (JPEG bajo demanda)
sensores               2 Hz          Sensores de TechRoach y respuestas automáticas
estado                 2 Hz          CPU, temperatura, batería, velocidades
control-xbox           20 Hz         Control Xbox conectado a la Pi (opcional)
=====================  ============  ==============================================

La capa web se suscribe a los eventos (``termico``, ``estado``,
``seguridad``, ``ambiente``) mediante :meth:`Robot.suscribir` y los reenvía
por Socket.IO.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ..config import Configuracion, modo_simulacion
from ..datos.db import BaseDatos
from ..datos.reporte_pdf import generar_reporte
from ..drivers.control_xbox import ControlXboxEvdev
from ..drivers.esp32_uart import Esp32Uart
from ..drivers.fabrica import crear_drivers, cuentas_por_vuelta
from ..drivers.sistema import MonitorSistema
from . import imagen_termica as imagen
from .ambiente import ALARMA, ProcesadorAmbiente, r0_desde_rs, rs_desde_voltaje
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

        # Sensores ambientales de TechRoach
        self.procesador = ProcesadorAmbiente(self._cfg_sensores(), cfg.seccion("respuestas"))
        self.ambiente: dict = self.procesador.estado.a_dict()
        self.historial_ambiente: deque = deque(maxlen=600)     # 10 min a 1 muestra/s
        self._voltajes_mq: dict[str, deque] = {"mq2": deque(maxlen=20), "mq7": deque(maxlen=20)}
        self.ultimo_tablero: str | None = None
        self.ultima_ubicacion = ""

        self.clientes = 0
        self.bateria_v: float | None = None
        self.esp32: Esp32Uart | None = None
        self.xbox: ControlXboxEvdev | None = None
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
        self.registrar_evento(tipo, "alarma" if tipo in ("paro", "watchdog") else "info", mensaje)
        self._publicar("seguridad", {"tipo": tipo, "mensaje": mensaje,
                                     "hora": datetime.now().strftime("%H:%M:%S")})

    def registrar_evento(self, tipo: str, nivel: str, mensaje: str) -> None:
        try:
            self.db.registrar_evento(tipo, nivel, mensaje)
        except Exception:
            log.exception("No se pudo registrar el evento")

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        self._activo.set()
        self.control.iniciar()
        for nombre, objetivo in (("camara-termica", self._bucle_termico),
                                 ("camara-visible", self._bucle_visible),
                                 ("sensores", self._bucle_sensores),
                                 ("estado", self._bucle_estado)):
            hilo = threading.Thread(target=objetivo, name=nombre, daemon=True)
            hilo.start()
            self._hilos.append(hilo)
        if self.cfg.get("esp32.habilitado", False):
            self._iniciar_esp32()
        if self.cfg.get("control_xbox.habilitado_pi", True):
            self._iniciar_xbox()
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
        if self.xbox:
            self.xbox.detener()
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
                                         self.temperatura_reflejada())
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

    def temperatura_reflejada(self) -> float:
        """Temperatura reflejada: la del DHT22 si está habilitado y hay lectura."""
        ct = self.cfg.seccion("camara_termica")
        t_dht = self.ambiente.get("temperatura")
        if ct.get("usar_dht22_como_reflejada", True) and t_dht is not None:
            return float(t_dht)
        return float(ct.get("temp_reflejada_c", 25.0))

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
        # 1) ADS1115 (canal A3)  2) ESP32 opcional
        if self.ambiente.get("bateria_v") is not None:
            return self.ambiente["bateria_v"]
        if self.esp32 and self.esp32.conectado and self.bateria_v is not None:
            return self.bateria_v
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
            "ambiente": self.ambiente,
            "xbox_pi": self.xbox.estado() if self.xbox else None,
            "hora": datetime.now().strftime("%H:%M:%S"),
        }

    def diagnostico(self) -> dict:
        d = self.drivers
        return {
            "simulacion": self.simulacion,
            "componentes": {
                "motores": d.motores.estado(), "encoders": d.encoders.estado(),
                "camara_termica": d.termica.estado(), "camara_visible": d.visible.estado(),
                "sensores_ambiente": d.ambiente.estado(),
                "control_xbox": {"nombre": "Control Xbox en la Pi (evdev)",
                                 "disponible": bool(self.xbox and self.xbox.conectado),
                                 "detalle": (self.xbox.nombre or self.xbox.error or "Esperando control…")
                                 if self.xbox else "Deshabilitado"},
            },
            "esp32": self.esp32.estado() if self.esp32 else {"habilitado": False},
            "parametros_control": self.control.parametros(),
            "cuentas_por_vuelta": self.control.cpr,
            "watchdog_ms": round(self.control.watchdog.tiempo_limite * 1000),
            "expiraciones_watchdog": self.control.watchdog.expiraciones,
        }

    # ------------------------------------------------------------ sensores ambientales
    def _bucle_sensores(self) -> None:
        t_historial = 0.0
        parpadeo = False
        while self._activo.is_set():
            inicio = time.monotonic()
            periodo = float(self.cfg.get("sensores.periodo_s", 0.5))
            drv = self.drivers.ambiente
            if not drv.disponible:
                time.sleep(1.0)
                continue
            try:
                crudo = drv.leer()
            except Exception:
                log.exception("Error leyendo sensores ambientales")
                time.sleep(1.0)
                continue
            for gas, voltajes in self._voltajes_mq.items():
                if crudo.get(f"v_{gas}") is not None:
                    voltajes.append(crudo[f"v_{gas}"])

            estado, eventos = self.procesador.procesar(crudo, self.control.en_movimiento())
            self.control.fijar_restricciones(estado.factor_velocidad, estado.bloqueo)
            for ev in eventos:
                if ev.tipo.startswith("gas_") and ev.nivel == ALARMA:
                    self.control.detener(f"Alarma de gas: {ev.mensaje}")
                elif ev.tipo == "impacto" and self.cfg.get("respuestas.detener_por_impacto", True):
                    self.control.detener("Impacto o caída detectado")
                self.registrar_evento(ev.tipo, ev.nivel, ev.mensaje)
                self._publicar("ambiente_evento", {"tipo": ev.tipo, "nivel": ev.nivel, "mensaje": ev.mensaje,
                                                   "hora": datetime.now().strftime("%H:%M:%S")})

            # Alerta visual/acústica: parpadeo a 1 Hz mientras dure la alarma
            parpadeo = not parpadeo
            ahora = time.monotonic()
            impacto_reciente = estado.ultimo_impacto is not None and ahora - estado.ultimo_impacto < 2.0
            alarma = estado.alarma or impacto_reciente
            advertencia = "advertencia" in (estado.mq2_nivel, estado.mq7_nivel)
            buzzer = bool(self.cfg.get("respuestas.buzzer", True)) and alarma and parpadeo
            led = (alarma or advertencia) and parpadeo
            try:
                drv.salidas(buzzer=buzzer, led_alarma=led, iluminacion=estado.oscuro)
            except Exception:
                log.exception("Error en salidas de alarma")
            amb = estado.a_dict()
            amb["salidas"] = {"buzzer": buzzer, "led_alarma": led, "iluminacion": estado.oscuro}
            amb["impacto_reciente"] = impacto_reciente
            self.ambiente = amb

            if ahora - t_historial >= 1.0:
                t_historial = ahora
                self.historial_ambiente.append({
                    "t": round(time.time(), 1), "temperatura": amb["temperatura"],
                    "humedad": amb["humedad"], "mq2_ppm": amb["mq2_ppm"], "mq7_ppm": amb["mq7_ppm"],
                    "luz_pct": amb["luz_pct"]})
            espera = periodo - (time.monotonic() - inicio)
            if espera > 0:
                time.sleep(espera)

    def calibrar_gases(self) -> dict:
        """Calcula R0 de los MQ con las últimas lecturas (el robot debe estar en aire limpio)."""
        if self.ambiente.get("calentando"):
            raise ValueError("Espera a que termine el calentamiento de los sensores de gas")
        sens = self.cfg.seccion("sensores")
        cambios = {}
        for gas, voltajes in self._voltajes_mq.items():
            g = sens[gas]
            rs = [rs_desde_voltaje(v, float(g.get("vc", 5.0)), float(g.get("rl_kohm", 1.0))) for v in voltajes]
            rs = [r for r in rs if r is not None]
            if len(rs) < 5:
                raise ValueError(f"No hay lecturas suficientes del {gas.upper()} (¿ADS1115 conectado?)")
            r0 = r0_desde_rs(sum(rs) / len(rs), float(g.get("razon_aire_limpio", 9.83)))
            cambios[f"sensores.{gas}.r0_kohm"] = round(r0, 3)
        self.aplicar_configuracion(cambios)
        for filtro in self.procesador.filtros.values():
            filtro.reiniciar()
        self.registrar_evento("calibracion", "info",
                              "Calibración en aire limpio: " + ", ".join(f"{k.split('.')[1].upper()} R0 = {v} kΩ"
                                                                       for k, v in cambios.items()))
        return cambios

    def inyectar_evento_simulado(self, evento: str) -> str:
        """Provoca un evento en los sensores simulados (solo en modo simulación)."""
        drv = self.drivers.ambiente
        if not self.simulacion or not hasattr(drv, "inyectar"):
            raise RuntimeError("Los eventos simulados solo existen en modo simulación")
        drv.inyectar(evento)
        descripcion = drv.EVENTOS[evento][1]
        self.registrar_evento("simulacion", "info", f"Evento simulado: {descripcion}")
        return descripcion

    # ------------------------------------------------------------ control Xbox en la Pi
    def _iniciar_xbox(self) -> None:
        self.xbox = ControlXboxEvdev(
            al_comando=self.comando_mando,
            al_soltar=self.detener,
            al_paro=self.paro_emergencia,
            al_rearme=self.rearmar,
            al_captura=self._captura_desde_mando,
            al_desconectar=lambda: self.detener("Control Xbox desconectado"),
            obtener_config=lambda: self.cfg.seccion("control_xbox"),
            al_cambiar_modo=lambda modo: self.aplicar_configuracion({"control_xbox.modo": modo}),
        )
        self.xbox.iniciar()

    def comando_mando(self, tipo: str, a: float, b: float) -> bool:
        """Comando de un control: ``arcade`` (x, y) o ``tanque`` (oruga izq., oruga der.)."""
        if tipo == "tanque":
            return self.control.comando_orugas(a, b)
        return self.control.comando(a, b)

    def captura_rapida(self) -> dict:
        """Captura con el último tablero usado (botón A del control)."""
        if not self.ultimo_tablero:
            raise ValueError("Para la captura rápida, primero captura un hallazgo con el nombre del tablero")
        return self.capturar(self.ultimo_tablero, "Captura rápida desde el control Xbox", self.ultima_ubicacion)

    def _captura_desde_mando(self) -> None:
        try:
            insp = self.captura_rapida()
            self._publicar("aviso", {"tipo": "ok", "mensaje": f"Captura rápida INS-{insp['id']:05d} ({insp['tablero']})"})
        except (ValueError, RuntimeError) as e:
            self._publicar("aviso", {"tipo": "aviso", "mensaje": str(e)})

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
        self.procesador.configurar(self._cfg_sensores(), self.cfg.seccion("respuestas"))
        if "control_xbox.habilitado_pi" in aplicados:
            if self.xbox and not self.cfg.get("control_xbox.habilitado_pi"):
                self.xbox.detener()
                self.xbox = None
            elif not self.xbox and self.cfg.get("control_xbox.habilitado_pi"):
                self._iniciar_xbox()
        if hasattr(self.drivers.termica, "superposicion"):
            self.drivers.termica.superposicion = self.cfg.seccion("superposicion")
        if "esp32.habilitado" in aplicados or "esp32.puerto" in aplicados:
            if self.esp32:
                self.esp32.detener()
                self.esp32 = None
                self.bateria_v = None
            if self.cfg.get("esp32.habilitado"):
                self._iniciar_esp32()
        self._publicar("config", self.cfg.editables())
        return aplicados

    def _cfg_sensores(self) -> dict:
        cfg_sens = self.cfg.seccion("sensores")
        if self.simulacion:
            # En simulación el calentamiento de los MQ se acorta para la demostración
            cfg_sens["calentamiento_s"] = min(float(cfg_sens.get("calentamiento_s", 180)), 10.0)
        return cfg_sens

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
                                              ubicacion, self.simulacion, ambiente=self.ambiente)
        self.ultimo_tablero, self.ultima_ubicacion = tablero, ubicacion
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
        self._publicar("nueva_inspeccion", {"id": id_insp, "tablero": tablero})
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
