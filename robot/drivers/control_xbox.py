"""Control Xbox conectado directamente a la Raspberry Pi (USB o Bluetooth), con evdev.

* **USB**: funciona de inmediato con el driver ``xpad`` del kernel.
* **Bluetooth**: emparejar con ``bluetoothctl``. Los controles Xbox One S /
  Series funcionan con el driver ``hid-microsoft``; si los botones salen
  cambiados se recomienda el driver ``xpadneo``.

Seguridad:

* Mientras el stick está fuera de la zona muerta se envían comandos a 20 Hz
  (alimentan el watchdog de 500 ms del robot).
* Al soltar el stick se envía un paro normal una sola vez.
* Si el control se desconecta (batería, alcance, cable) el robot se detiene
  de inmediato.
* El hilo busca el control cada 2 s, así que se puede conectar en cualquier
  momento (conexión en caliente).
"""

from __future__ import annotations

import logging
import select
import threading
import time
from typing import Callable

from ..logica.gamepad import (TIEMPO_REARME_S, EstadoMando, ajustar_limitador,
                              comando_desde_mando, es_neutro, normalizar_eje)

log = logging.getLogger(__name__)


class ControlXboxEvdev:
    """Hilo que lee el control y llama a las funciones del robot."""

    def __init__(self, al_comando: Callable[[str, float, float], None],
                 al_soltar: Callable[[], None], al_paro: Callable[[], None],
                 al_rearme: Callable[[], None], al_captura: Callable[[], None],
                 al_desconectar: Callable[[], None], obtener_config: Callable[[], dict],
                 al_cambiar_modo: Callable[[str], None] | None = None):
        self.al_comando, self.al_soltar = al_comando, al_soltar
        self.al_paro, self.al_rearme, self.al_captura = al_paro, al_rearme, al_captura
        self.al_desconectar = al_desconectar
        self.al_cambiar_modo = al_cambiar_modo
        self.obtener_config = obtener_config
        self.dispositivo = None
        self.nombre = ""
        self.limitador = 1.0
        self.mando = EstadoMando()
        self.error = ""
        self._activo = threading.Event()
        self._hilo: threading.Thread | None = None
        self._en_movimiento = False
        self._t_menu: float | None = None
        self._t_envio = 0.0
        self._rangos: dict[int, tuple[int, int]] = {}

    # --------------------------------------------------------------- estado
    @property
    def conectado(self) -> bool:
        return self.dispositivo is not None

    def estado(self) -> dict:
        cfg = self.obtener_config()
        return {"conectado": self.conectado, "nombre": self.nombre, "modo": cfg.get("modo", "arcade"),
                "limitador": self.limitador, "error": self.error}

    # --------------------------------------------------------------- ciclo
    def iniciar(self) -> None:
        try:
            import evdev  # noqa: F401
        except ImportError:
            self.error = "Falta python3-evdev (sudo apt install python3-evdev)"
            log.warning(self.error)
            return
        self._activo.set()
        self._hilo = threading.Thread(target=self._bucle, name="control-xbox", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._activo.clear()
        if self._hilo:
            self._hilo.join(timeout=1.5)
        self._cerrar_dispositivo()

    def _buscar(self):
        import evdev
        from evdev import ecodes

        for ruta in evdev.list_devices():
            try:
                disp = evdev.InputDevice(ruta)
            except OSError:
                continue
            caps = disp.capabilities()
            teclas = caps.get(ecodes.EV_KEY, [])
            ejes = [c[0] if isinstance(c, tuple) else c for c in caps.get(ecodes.EV_ABS, [])]
            if ecodes.BTN_SOUTH in teclas and ecodes.ABS_X in ejes and ecodes.ABS_Y in ejes:
                return disp
            disp.close()
        return None

    def _bucle(self) -> None:
        from evdev import ecodes

        while self._activo.is_set():
            if self.dispositivo is None:
                try:
                    self.dispositivo = self._buscar()
                except Exception as e:  # pragma: no cover - solo en hardware
                    self.error = str(e)
                if self.dispositivo is None:
                    time.sleep(2.0)
                    continue
                self.nombre = self.dispositivo.name
                self.error = ""
                self._rangos = {}
                for codigo in (ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY):
                    try:
                        info = self.dispositivo.absinfo(codigo)
                        self._rangos[codigo] = (info.min, info.max)
                    except Exception:
                        self._rangos[codigo] = (-32768, 32767)
                self.mando = EstadoMando()
                log.info("Control conectado a la Pi: %s", self.nombre)

            try:
                listo, _, _ = select.select([self.dispositivo.fd], [], [], 0.05)
                if listo:
                    for ev in self.dispositivo.read():
                        self._procesar_evento(ev, ecodes)
                self._enviar_comando()
            except OSError:
                log.warning("Control Xbox desconectado")
                self._cerrar_dispositivo()
                self.al_desconectar()

    def _cerrar_dispositivo(self) -> None:
        if self.dispositivo is not None:
            try:
                self.dispositivo.close()
            except Exception:  # pragma: no cover
                pass
        self.dispositivo = None
        self.nombre = ""
        self._en_movimiento = False

    # --------------------------------------------------------------- eventos
    def _procesar_evento(self, ev, ecodes) -> None:
        if ev.type == ecodes.EV_ABS and ev.code in self._rangos:
            v = normalizar_eje(ev.value, *self._rangos[ev.code])
            if ev.code == ecodes.ABS_X:
                self.mando.lx = v
            elif ev.code == ecodes.ABS_Y:
                self.mando.ly = -v          # en evdev arriba es negativo
            elif ev.code == ecodes.ABS_RX:
                self.mando.rx = v
            elif ev.code == ecodes.ABS_RY:
                self.mando.ry = -v
        elif ev.type == ecodes.EV_KEY:
            presionado = ev.value == 1
            if ev.code == ecodes.BTN_EAST and presionado:          # B
                self._en_movimiento = False
                self.al_paro()
            elif ev.code == ecodes.BTN_SOUTH and presionado:       # A
                self.al_captura()
            elif ev.code == ecodes.BTN_TL and presionado:          # LB
                self.limitador = ajustar_limitador(self.limitador, subir=False)
            elif ev.code == ecodes.BTN_TR and presionado:          # RB
                self.limitador = ajustar_limitador(self.limitador, subir=True)
            elif ev.code == ecodes.BTN_SELECT and presionado:      # Vista
                modo = "tanque" if self.obtener_config().get("modo") == "arcade" else "arcade"
                if self.al_cambiar_modo:
                    self.al_cambiar_modo(modo)
            elif ev.code == ecodes.BTN_START:                      # Menú (mantener)
                self._t_menu = time.monotonic() if presionado else None

    def _enviar_comando(self) -> None:
        if self._t_menu is not None and time.monotonic() - self._t_menu >= TIEMPO_REARME_S:
            self._t_menu = None
            self.al_rearme()
        cfg = self.obtener_config()
        cmd = comando_desde_mando(self.mando, cfg.get("modo", "arcade"), float(cfg.get("zona_muerta", 0.15)),
                                  float(cfg.get("expo", 0.35)), self.limitador)
        ahora = time.monotonic()
        if not es_neutro(cmd):
            # 20 Hz como máximo
            if ahora - self._t_envio >= 0.05:
                self._t_envio = ahora
                self.al_comando(*cmd)
                self._en_movimiento = True
        elif self._en_movimiento:
            self._en_movimiento = False
            self.al_soltar()
