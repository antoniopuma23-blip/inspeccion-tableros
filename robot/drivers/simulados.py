"""Drivers simulados para ejecutar el sistema completo en una laptop (ROBOT_SIM=1).

La simulación es coherente entre sus partes:

* :class:`ModeloTraccionSimulado` integra la dinámica de los dos motores
  (retardo de primer orden, zona muerta y una pequeña asimetría entre motores
  para que el control en lazo cerrado tenga algo que corregir). De ahí salen
  los conteos de los encoders y la pose del robot frente al tablero.
* :class:`EscenaTablero` dibuja una vez un tablero eléctrico (imagen visible)
  y su mapa de temperaturas base con la misma geometría.
* Las cámaras simuladas recortan la misma "ventana" de la escena según la pose
  del robot: al avanzar el robot se acerca (zoom), al girar la vista se
  desplaza, y el térmico siempre queda alineado con el visible.
* Un punto caliente recorre los interruptores y su intensidad varía en el
  tiempo para que la clasificación ΔT pase por todos los niveles.
"""

from __future__ import annotations

import math
import threading
import time

import numpy as np

from ..logica.ambiente import voltaje_desde_ppm
from .base import (DriverCamaraTermica, DriverCamaraVisible, DriverEncoders, DriverMotores,
                   DriverSensoresAmbiente)

try:
    import cv2
except ImportError:  # pragma: no cover - OpenCV es requisito en simulación
    cv2 = None


# ----------------------------------------------------------------------------
#  Dinámica de tracción
# ----------------------------------------------------------------------------
class ModeloTraccionSimulado:
    """Modelo simple de dos motores DC + orugas."""

    def __init__(self, rpm_nominales: float = 330, duty_minimo: float = 0.22,
                 diametro_m: float = 0.04, separacion_m: float = 0.18,
                 cuentas_por_vuelta: int = 1320, tau_s: float = 0.12,
                 asimetria: float = 0.05, reloj=time.monotonic):
        self.w_max = rpm_nominales * 2 * math.pi / 60.0   # rad/s de la rueda
        self.duty_minimo = duty_minimo
        self.diametro = diametro_m
        self.separacion = separacion_m
        self.cpr = cuentas_por_vuelta
        self.tau = tau_s
        self.ganancia = (1.0, 1.0 - asimetria)            # motor derecho más "débil"
        self._reloj = reloj
        self._bloqueo = threading.Lock()
        self.duty = [0.0, 0.0]
        self.w = [0.0, 0.0]
        self.conteos = [0.0, 0.0]
        self.theta = 0.0          # rumbo (rad) respecto al tablero
        self.distancia = 0.0      # avance hacia el tablero (m)
        self._t = reloj()

    def fijar_duty(self, izq: float, der: float) -> None:
        with self._bloqueo:
            self._actualizar()
            self.duty = [max(-1.0, min(1.0, izq)), max(-1.0, min(1.0, der))]

    def _w_objetivo(self, duty: float, lado: int) -> float:
        magnitud = abs(duty)
        if magnitud < self.duty_minimo:
            return 0.0
        fraccion = (magnitud - self.duty_minimo) / (1.0 - self.duty_minimo)
        return math.copysign(fraccion * self.w_max * self.ganancia[lado], duty)

    def _actualizar(self) -> None:
        ahora = self._reloj()
        transcurrido = min(ahora - self._t, 30.0)
        self._t = ahora
        # Integración en subpasos de ≤ 20 ms aunque pase tiempo sin lecturas
        while transcurrido > 1e-9:
            dt = min(transcurrido, 0.02)
            transcurrido -= dt
            self._integrar(dt)

    def _integrar(self, dt: float) -> None:
        alfa = 1.0 - math.exp(-dt / self.tau)
        for i in (0, 1):
            self.w[i] += (self._w_objetivo(self.duty[i], i) - self.w[i]) * alfa
            if abs(self.w[i]) < 1e-4:
                self.w[i] = 0.0
            self.conteos[i] += self.w[i] * dt / (2 * math.pi) * self.cpr
        v_izq = self.w[0] * self.diametro / 2
        v_der = self.w[1] * self.diametro / 2
        v = (v_izq + v_der) / 2
        omega = (v_der - v_izq) / self.separacion
        self.theta = max(-0.7, min(0.7, self.theta + omega * dt))
        self.distancia = max(-0.8, min(0.55, self.distancia + v * math.cos(self.theta) * dt))

    def leer(self) -> tuple[int, int]:
        with self._bloqueo:
            self._actualizar()
            return int(self.conteos[0]), int(self.conteos[1])

    def pose(self) -> tuple[float, float]:
        with self._bloqueo:
            self._actualizar()
            return self.theta, self.distancia


class MotoresSimulados(DriverMotores):
    nombre = "Motores simulados"
    detalle = "Modelo dinámico de primer orden"

    def __init__(self, modelo: ModeloTraccionSimulado):
        self.modelo = modelo
        self._ultimo = (0.0, 0.0)
        self.detener()

    def aplicar(self, duty_izq: float, duty_der: float) -> None:
        self.modelo.fijar_duty(duty_izq, duty_der)
        self._ultimo = (float(duty_izq), float(duty_der))

    def detener(self) -> None:
        self.modelo.fijar_duty(0.0, 0.0)
        self._ultimo = (0.0, 0.0)


class EncodersSimulados(DriverEncoders):
    nombre = "Encoders simulados"

    def __init__(self, modelo: ModeloTraccionSimulado):
        self.modelo = modelo
        self.detalle = f"{modelo.cpr} cuentas/vuelta"

    def leer_conteos(self) -> tuple[int, int]:
        return self.modelo.leer()


# ----------------------------------------------------------------------------
#  Escena: tablero eléctrico visible + mapa de temperaturas
# ----------------------------------------------------------------------------
class EscenaTablero:
    """Imagen sintética de un tablero de distribución y sus temperaturas."""

    ANCHO, ALTO = 1280, 800
    REDUCCION = 4  # el mapa de temperaturas se guarda a 1/4 de resolución

    def __init__(self, semilla: int = 7):
        if cv2 is None:
            raise RuntimeError("La simulación requiere OpenCV (pip install opencv-python-headless)")
        rng = np.random.default_rng(semilla)
        W, H = self.ANCHO, self.ALTO
        img = np.zeros((H, W, 3), np.uint8)
        temp = np.full((H, W), 23.5, np.float32)

        # Pared con degradado y piso
        grad = np.linspace(48, 70, H, dtype=np.float32)[:, None]
        img[:] = np.dstack([grad + 6, grad + 3, grad]).astype(np.uint8)
        cv2.rectangle(img, (0, 770), (W, H), (52, 55, 58), -1)

        # Gabinete (gris RAL 7035) y placa de montaje
        cv2.rectangle(img, (230, 50), (1050, 770), (40, 40, 42), -1)
        cv2.rectangle(img, (238, 58), (1042, 762), (186, 190, 188), -1)
        cv2.rectangle(temp, (238, 58), (1042, 762), 25.0, -1)
        cv2.rectangle(img, (268, 88), (1012, 732), (212, 214, 210), -1)
        cv2.rectangle(temp, (268, 88), (1012, 732), 26.0, -1)
        for y in (120, 700):  # bisagras
            cv2.rectangle(img, (232, y), (246, y + 40), (90, 90, 92), -1)

        # Placa de identificación
        cv2.rectangle(img, (420, 100), (860, 140), (25, 25, 25), -1)
        cv2.putText(img, "TABLERO TD-01   220/127 V  3F-4H", (435, 128),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (235, 235, 235), 1, cv2.LINE_AA)

        # Señal de advertencia
        pts = np.array([[960, 100], [995, 160], [925, 160]], np.int32)
        cv2.fillPoly(img, [pts], (0, 200, 255))
        cv2.polylines(img, [pts], True, (20, 20, 20), 2, cv2.LINE_AA)
        cv2.putText(img, "!", (953, 152), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 3, cv2.LINE_AA)

        # Interruptor principal
        cv2.rectangle(img, (300, 170), (420, 380), (55, 55, 58), -1)
        cv2.rectangle(img, (340, 240), (380, 300), (25, 25, 25), -1)
        cv2.rectangle(img, (348, 225), (372, 262), (200, 200, 200), -1)
        cv2.putText(img, "PRINCIPAL", (303, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1, cv2.LINE_AA)
        cv2.rectangle(temp, (300, 170), (420, 380), 30.5, -1)

        # Barras de cobre
        for i, y in enumerate((175, 205, 235)):
            cv2.rectangle(img, (460, y), (990, y + 16), (60, 120, 190), -1)
            cv2.line(img, (460, y + 3), (990, y + 3), (120, 180, 230), 2)
            cv2.rectangle(temp, (460, y), (990, y + 16), 30.0 + i * 0.3, -1)

        # Dos filas de interruptores derivados
        self.terminales = []
        n = 1
        for fila_y in (300, 500):
            for i in range(10):
                x = 462 + i * 53
                cv2.rectangle(img, (x, fila_y), (x + 48, fila_y + 120), (236, 236, 232), -1)
                cv2.rectangle(img, (x, fila_y), (x + 48, fila_y + 120), (120, 120, 120), 1)
                cv2.rectangle(img, (x + 16, fila_y + 45), (x + 32, fila_y + 75), (35, 35, 35), -1)
                cv2.rectangle(img, (x + 18, fila_y + 47), (x + 30, fila_y + 58), (80, 80, 210), -1)
                for tx in (x + 12, x + 36):
                    cv2.circle(img, (tx, fila_y + 12), 5, (150, 150, 150), -1)
                    cv2.circle(img, (tx, fila_y + 108), 5, (150, 150, 150), -1)
                cv2.putText(img, f"C{n}", (x + 8, fila_y + 98), cv2.FONT_HERSHEY_SIMPLEX,
                            0.42, (60, 60, 60), 1, cv2.LINE_AA)
                cv2.rectangle(temp, (x, fila_y), (x + 48, fila_y + 120),
                              float(28.0 + rng.normal(0, 0.5)), -1)
                self.terminales.append((x + 24, fila_y + 12))
                n += 1

        # Cables y canaleta
        colores = [(40, 40, 200), (30, 30, 30), (190, 90, 30), (40, 40, 200), (30, 30, 30)]
        for i in range(20):
            fila_y = 300 if i < 10 else 500
            x = 462 + (i % 10) * 53 + 24
            pts = np.array([[x, fila_y + 120], [x, fila_y + 150 + (i % 3) * 6],
                            [x - 10 + (i % 4) * 5, 660]], np.int32)
            cv2.polylines(img, [pts], False, colores[i % 5], 6, cv2.LINE_AA)
            cv2.polylines(temp, [pts], False, 27.0, 6)
        cv2.rectangle(img, (300, 655), (990, 705), (150, 152, 150), -1)
        cv2.rectangle(img, (300, 655), (990, 705), (90, 90, 90), 2)
        for x in range(310, 990, 28):
            cv2.line(img, (x, 655), (x, 670), (110, 110, 110), 2)
        cv2.rectangle(img, (300, 715), (520, 728), (40, 140, 40), -1)  # barra de tierra

        self.imagen = img
        r = self.REDUCCION
        temp = cv2.GaussianBlur(temp, (0, 0), 6)
        self.temp_base = cv2.resize(temp, (W // r, H // r), interpolation=cv2.INTER_AREA)
        self._t0 = time.monotonic()

    # -------------------------------------------------------------- geometría
    def ventana(self, theta: float, distancia: float) -> tuple[float, float, float, float]:
        """Rectángulo visible (x0, y0, ancho, alto) en coordenadas de la escena."""
        zoom = max(0.45, min(1.6, 1.0 - 0.9 * distancia))
        w = 900.0 * zoom
        h = w * 0.75
        cx = self.ANCHO / 2 - theta * 700.0
        cy = self.ALTO / 2 - 20
        cx = max(w / 2, min(self.ANCHO - w / 2, cx)) if w < self.ANCHO else self.ANCHO / 2
        cy = max(h / 2, min(self.ALTO - h / 2, cy)) if h < self.ALTO else self.ALTO / 2
        return cx - w / 2, cy - h / 2, w, h

    def punto_caliente(self, t: float) -> tuple[float, float, float]:
        """Posición (x, y) e incremento de temperatura del punto caliente."""
        x = 486 + 478 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 40.0))
        y = 312 + 200 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 23.0))
        fase = 0.5 + 0.5 * math.sin(2 * math.pi * t / 50.0 - math.pi / 2)
        amplitud = 1.5 + 26.0 * fase ** 1.4
        return x, y, amplitud

    def temperaturas(self, x0: float, y0: float, w: float, h: float,
                     columnas: int = 32, filas: int = 24, ruido: float = 0.12,
                     t: float | None = None) -> np.ndarray:
        """Muestrea la escena térmica en una cuadrícula de ``filas × columnas``."""
        r = self.REDUCCION
        # Matriz afín que lleva la ventana (en escala reducida) a la cuadrícula
        sx, sy = columnas / (w / r), filas / (h / r)
        M = np.array([[sx, 0, -x0 / r * sx], [0, sy, -y0 / r * sy]], np.float32)
        base = cv2.warpAffine(self.temp_base, M, (columnas, filas),
                              flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        t = (time.monotonic() - self._t0) if t is None else t
        hx, hy, amp = self.punto_caliente(t)
        xs = x0 + (np.arange(columnas) + 0.5) * w / columnas
        ys = y0 + (np.arange(filas) + 0.5) * h / filas
        gx, gy = np.meshgrid(xs, ys)
        sigma = 26.0
        caliente = amp * np.exp(-((gx - hx) ** 2 + (gy - hy) ** 2) / (2 * sigma ** 2))
        matriz = base + caliente.astype(np.float32)
        if ruido > 0:
            matriz += np.random.normal(0, ruido, matriz.shape).astype(np.float32)
        return matriz


class _Marcapasos:
    """Hace que ``leer_cuadro`` bloquee como un sensor real a frecuencia fija."""

    def __init__(self, frecuencia_hz: float):
        self.periodo = 1.0 / max(0.5, frecuencia_hz)
        self._siguiente = time.monotonic()

    def esperar(self) -> None:
        ahora = time.monotonic()
        if self._siguiente > ahora:
            time.sleep(self._siguiente - ahora)
            self._siguiente += self.periodo
        else:
            self._siguiente = ahora + self.periodo


class CamaraVisibleSimulada(DriverCamaraVisible):
    nombre = "Cámara visible simulada"

    def __init__(self, escena: EscenaTablero, modelo: ModeloTraccionSimulado,
                 ancho: int = 640, alto: int = 480, fps: int = 12):
        self.escena, self.modelo = escena, modelo
        self.ancho, self.alto = ancho, alto
        self._ritmo = _Marcapasos(fps)
        self.detalle = f"Imagen sintética de tablero, {ancho}x{alto} @ {fps} fps"

    def leer_cuadro(self) -> np.ndarray | None:
        self._ritmo.esperar()
        theta, dist = self.modelo.pose()
        x0, y0, w, h = self.escena.ventana(theta, dist)
        M = np.array([[self.ancho / w, 0, -x0 * self.ancho / w],
                      [0, self.alto / h, -y0 * self.alto / h]], np.float32)
        cuadro = cv2.warpAffine(self.escena.imagen, M, (self.ancho, self.alto),
                                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        cv2.putText(cuadro, "SIMULACION  " + time.strftime("%H:%M:%S"), (10, self.alto - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(cuadro, "SIMULACION  " + time.strftime("%H:%M:%S"), (10, self.alto - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1, cv2.LINE_AA)
        return cuadro


class CamaraTermicaSimulada(DriverCamaraTermica):
    nombre = "MLX90640 simulada"

    def __init__(self, escena: EscenaTablero, modelo: ModeloTraccionSimulado,
                 frecuencia_hz: float = 4, superposicion: dict | None = None):
        self.escena, self.modelo = escena, modelo
        self.superposicion = superposicion or {}
        self._ritmo = _Marcapasos(frecuencia_hz)
        self.detalle = f"Mapa sintético 32x24 @ {frecuencia_hz:g} Hz con punto caliente móvil"

    def leer_cuadro(self) -> np.ndarray | None:
        self._ritmo.esperar()
        theta, dist = self.modelo.pose()
        x0, y0, w, h = self.escena.ventana(theta, dist)
        # El térmico cubre solo la fracción central del visible (FOV menor)
        s = self.superposicion
        ex, ey = s.get("escala_x", 0.88), s.get("escala_y", 0.72)
        cx = x0 + w * (0.5 + s.get("desplazamiento_x", 0.0))
        cy = y0 + h * (0.5 + s.get("desplazamiento_y", 0.0))
        tw, th = w * ex, h * ey
        return self.escena.temperaturas(cx - tw / 2, cy - th / 2, tw, th)


class SensoresAmbienteSimulados(DriverSensoresAmbiente):
    """Sensores de TechRoach simulados, con eventos que se pueden provocar desde la interfaz.

    Las lecturas pasan por la misma cadena que en el robot real: se generan
    VOLTAJES (como los del ADS1115) a partir de las ppm deseadas, y el
    procesador los convierte de nuevo a ppm con la calibración configurada.
    """

    nombre = "Sensores TechRoach simulados"
    detalle = "DHT22, MQ-2, MQ-7, PIR, piezoeléctrico, LDR y batería"

    # Eventos que se pueden provocar: nombre -> (duración s, descripción)
    EVENTOS = {
        "gas": (25.0, "Fuga de gas inflamable (MQ-2)"),
        "co": (25.0, "Monóxido de carbono (MQ-7)"),
        "humedad": (30.0, "Humedad alta (> 80 %)"),
        "presencia": (4.0, "Persona frente al robot (PIR)"),
        "impacto": (0.0, "Impacto / caída (piezoeléctrico)"),
        "oscuridad": (30.0, "Zona oscura (LDR)"),
    }

    def __init__(self, obtener_cfg, bateria: "BateriaSimulada | None" = None,
                 modelo: ModeloTraccionSimulado | None = None):
        # Función que devuelve la sección "sensores" vigente (sigue la calibración)
        self.obtener_cfg = obtener_cfg
        self.bateria = bateria
        self.modelo = modelo
        self._t0 = time.monotonic()
        self._activos: dict[str, float] = {}     # evento -> instante de inicio
        self.impactos = 0
        self.estado_salidas = {"buzzer": False, "led_alarma": False, "iluminacion": False}

    def inyectar(self, evento: str) -> None:
        if evento not in self.EVENTOS:
            raise ValueError(f"Evento simulado desconocido: {evento}")
        if evento == "impacto":
            self.impactos += 1
        else:
            self._activos[evento] = time.monotonic()

    def _intensidad(self, evento: str) -> float:
        """0 → 1 → 0: subida de 3 s, meseta y bajada de 3 s."""
        inicio = self._activos.get(evento)
        if inicio is None:
            return 0.0
        dur = self.EVENTOS[evento][0]
        t = time.monotonic() - inicio
        if t > dur:
            del self._activos[evento]
            return 0.0
        return max(0.0, min(1.0, t / 3.0, (dur - t) / 3.0))

    def _voltaje_mq(self, gas: str, ppm: float) -> float:
        g = self.obtener_cfg()[gas]
        return voltaje_desde_ppm(ppm, float(g["curva_a"]), float(g["curva_b"]), float(g["r0_kohm"]),
                                 float(g.get("vc", 5.0)), float(g.get("rl_kohm", 1.0)))

    def leer(self) -> dict:
        t = time.monotonic() - self._t0
        ruido = np.random.normal
        mq2 = 120 + 25 * math.sin(t / 40) + ruido(0, 6) + 1500 * self._intensidad("gas")
        mq7 = 8 + 3 * math.sin(t / 55) + ruido(0, 0.8) + 160 * self._intensidad("co")
        humedad = 52 + 4 * math.sin(t / 90) + 36 * self._intensidad("humedad")
        temperatura = 26.5 + 0.8 * math.sin(t / 120) + ruido(0, 0.05)
        luz = 0.62 * 3.3 * (1 - 0.85 * self._intensidad("oscuridad")) + ruido(0, 0.01)
        carga = 0.0
        if self.modelo is not None:
            carga = sum(abs(d) for d in self.modelo.duty) / 2
        return {
            "v_mq2": self._voltaje_mq("mq2", max(1.0, mq2)),
            "v_mq7": self._voltaje_mq("mq7", max(0.5, mq7)),
            "v_ldr": max(0.0, luz),
            "v_bateria": self.bateria.voltaje(carga) if self.bateria else None,
            "temperatura": round(temperatura, 1),
            "humedad": round(min(99.0, humedad), 1),
            "pir": self._intensidad("presencia") > 0,
            "impactos": self.impactos,
        }

    def salidas(self, buzzer: bool = False, led_alarma: bool = False, iluminacion: bool = False) -> None:
        self.estado_salidas = {"buzzer": buzzer, "led_alarma": led_alarma, "iluminacion": iluminacion}

    def eventos_activos(self) -> list[str]:
        return [e for e in list(self._activos) if self._intensidad(e) > 0]


class BateriaSimulada:
    """Paquete LiPo 2S que se descarga lentamente (solo para la demostración)."""

    def __init__(self, celdas: int = 2):
        self.celdas = celdas
        self._t0 = time.monotonic()

    def voltaje(self, carga: float = 0.0) -> float:
        horas = (time.monotonic() - self._t0) / 3600.0
        por_celda = 4.15 - 0.35 * horas - 0.06 * carga + 0.005 * math.sin(time.monotonic())
        return round(max(3.3, por_celda) * self.celdas, 2)
