#!/usr/bin/env python3
"""Punto de entrada del robot de inspección térmica.

Uso:
    python run.py                 # En la Raspberry Pi (hardware real)
    ROBOT_SIM=1 python run.py     # En una laptop, todo simulado

Luego abrir http://robot.local:5000 (o http://localhost:5000 en simulación).
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import signal
import sys

from robot.config import Configuracion, modo_simulacion


def main() -> int:
    parser = argparse.ArgumentParser(description="Robot móvil de inspección térmica de tableros eléctricos")
    parser.add_argument("--sim", action="store_true", help="Forzar modo simulación (igual que ROBOT_SIM=1)")
    parser.add_argument("--config", default=None, help="Ruta alternativa a config.yaml")
    parser.add_argument("--puerto", type=int, default=None, help="Puerto HTTP (por defecto el de config.yaml)")
    parser.add_argument("--debug", action="store_true", help="Mensajes de depuración")
    args = parser.parse_args()

    if args.sim:
        os.environ["ROBOT_SIM"] = "1"
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    log = logging.getLogger("robot")

    # Importaciones después de configurar el registro de mensajes
    from robot.logica.robot import Robot
    from robot.web.app import crear_app

    cfg = Configuracion(args.config)
    robot = Robot(cfg, simulacion=modo_simulacion())

    # Seguridad: motores en cero al cerrar, pase lo que pase
    atexit.register(robot.cerrar)

    def manejar_senal(signum, _frame):
        log.info("Señal %s recibida: apagado seguro", signal.Signals(signum).name)
        robot.cerrar()
        sys.exit(0)

    signal.signal(signal.SIGTERM, manejar_senal)
    signal.signal(signal.SIGINT, manejar_senal)

    app, socketio = crear_app(robot)
    host = cfg.get("servidor.host", "0.0.0.0")
    puerto = args.puerto or int(cfg.get("servidor.puerto", 5000))
    log.info("Interfaz web en http://%s:%d  (%s)", "localhost" if robot.simulacion else "robot.local",
             puerto, "SIMULACIÓN" if robot.simulacion else "hardware real")
    try:
        socketio.run(app, host=host, port=puerto, debug=False, use_reloader=False,
                     allow_unsafe_werkzeug=True)
    finally:
        robot.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
