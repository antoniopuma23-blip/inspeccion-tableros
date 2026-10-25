"""Eventos Socket.IO (tiempo real) entre el navegador y el robot.

Navegador → servidor
    ``mover``      {x, y} en [-1, 1]. Se envía cada 100 ms mientras hay
                   movimiento; también alimenta el watchdog.
    ``orugas``     {izq, der} en [-1, 1]: modo tanque del control Xbox.
    ``captura_rapida``  botón A del control Xbox.
    ``detener``    suelta el joystick (paro normal).
    ``paro``       PARO DE EMERGENCIA (enclavado).
    ``rearmar``    libera el paro de emergencia.

Servidor → navegador
    ``termico``    matriz 32×24 + estadísticas (4 Hz).
    ``estado``     batería, velocidades, CPU, temperatura (2 Hz).
    ``seguridad``  avisos de paro, watchdog, desconexión.
    ``config``     configuración actualizada.
    ``ambiente_evento``  alarmas y eventos de los sensores de TechRoach.
    ``aviso``      mensajes informativos (p. ej. captura rápida).

Seguridad ante desconexión: si se desconecta el navegador que estaba
manejando el robot (el "piloto"), los motores se detienen de inmediato. El
watchdog de 500 ms cubre además cualquier pérdida de red que Socket.IO aún no
haya detectado.
"""

from __future__ import annotations

import logging
import threading

from flask import request
from flask_socketio import SocketIO, emit

from ..logica import paletas

log = logging.getLogger(__name__)


def registrar_eventos(socketio: SocketIO, robot) -> None:
    estado = {"piloto": None}
    bloqueo = threading.Lock()

    # Reenvío de eventos del robot a todos los navegadores
    robot.suscribir("termico", lambda datos: socketio.emit("termico", datos))
    robot.suscribir("estado", lambda datos: socketio.emit("estado", datos))
    robot.suscribir("seguridad", lambda datos: socketio.emit("seguridad", datos))
    for evento in ("ambiente_evento", "config", "nueva_inspeccion", "aviso"):
        robot.suscribir(evento, lambda datos, ev=evento: socketio.emit(ev, datos))

    @socketio.on("connect")
    def conectar(_auth=None):
        with bloqueo:
            robot.clientes += 1
        log.info("Navegador conectado (%s). Clientes: %d", request.sid, robot.clientes)
        emit("bienvenida", {
            "config": robot.cfg.editables(),
            "paletas": paletas.para_cliente(),
            "simulacion": robot.simulacion,
            "estado": robot.estado(),
        })

    @socketio.on("disconnect")
    def desconectar(*_args):
        with bloqueo:
            robot.clientes = max(0, robot.clientes - 1)
            era_piloto = estado["piloto"] == request.sid
            if era_piloto:
                estado["piloto"] = None
        if era_piloto:
            robot.detener("Se perdió la conexión con el navegador")
            socketio.emit("seguridad", {"tipo": "desconexion",
                                        "mensaje": "Paro inmediato: se desconectó el navegador que controlaba el robot"})
        log.info("Navegador desconectado (%s). Clientes: %d", request.sid, robot.clientes)

    @socketio.on("mover")
    def mover(datos):
        try:
            x, y = float(datos.get("x", 0)), float(datos.get("y", 0))
        except (AttributeError, TypeError, ValueError):
            return {"ok": False}
        with bloqueo:
            estado["piloto"] = request.sid
        return {"ok": robot.comando_movimiento(x, y)}

    @socketio.on("orugas")
    def orugas(datos):
        try:
            izq, der = float(datos.get("izq", 0)), float(datos.get("der", 0))
        except (AttributeError, TypeError, ValueError):
            return {"ok": False}
        with bloqueo:
            estado["piloto"] = request.sid
        return {"ok": robot.comando_mando("tanque", izq, der)}

    @socketio.on("captura_rapida")
    def captura_rapida(_datos=None):
        try:
            insp = robot.captura_rapida()
        except (ValueError, RuntimeError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "id": insp["id"], "tablero": insp["tablero"]}

    @socketio.on("detener")
    def detener(_datos=None):
        robot.detener()

    @socketio.on("paro")
    def paro(_datos=None):
        log.warning("PARO DE EMERGENCIA solicitado desde %s", request.sid)
        robot.paro_emergencia()

    @socketio.on("rearmar")
    def rearmar(_datos=None):
        robot.rearmar()
