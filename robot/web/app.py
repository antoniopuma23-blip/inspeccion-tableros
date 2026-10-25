"""Servidor web: Flask + Flask-SocketIO (modo ``threading``, sin eventlet).

Rutas principales
-----------------
Páginas:   ``/`` (en vivo), ``/captura``, ``/historial``, ``/reporte``, ``/configuracion``
Video:     ``/video.mjpg``  (MJPEG multipart, 640×480)
API REST:  ``/api/...``     (JSON)
Socket.IO: eventos de movimiento, paro, térmico y estado (ver ``socket_eventos.py``)
"""

from __future__ import annotations

import csv
import io
import logging
import time
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, render_template, request,
                   send_file, send_from_directory, stream_with_context)
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename

from .. import __version__
from ..config import ErrorConfiguracion
from ..logica import paletas
from ..logica.analisis_termico import CLASIFICACIONES
from ..logica.robot import Robot
from .socket_eventos import registrar_eventos

log = logging.getLogger(__name__)

# (id, ruta, nombre, nombre corto para celular, icono)
PAGINAS = [
    ("en_vivo", "/", "Inspección en vivo", "En vivo", "visor"),
    ("captura", "/captura", "Captura", "Captura", "camara"),
    ("ambiente", "/ambiente", "Ambiente", "Ambiente", "gas"),
    ("historial", "/historial", "Historial", "Historial", "lista"),
    ("reporte", "/reporte", "Reporte", "Reporte", "documento"),
    ("configuracion", "/configuracion", "Configuración", "Ajustes", "engrane"),
]


def crear_app(robot: Robot) -> tuple[Flask, SocketIO]:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = "robot-inspeccion-termica"
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # subida de logo
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 300
    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*",
                        ping_interval=2, ping_timeout=4)
    app.extensions["robot"] = robot
    registrar_eventos(socketio, robot)

    @app.context_processor
    def contexto_global():
        return {"paginas": PAGINAS, "simulacion": robot.simulacion, "version": __version__,
                "nombre_robot": robot.cfg.get("robot.nombre", "Robot de Inspección Térmica")}

    # ------------------------------------------------------------- páginas
    def _pagina(plantilla: str, activa: str):
        return render_template(plantilla, activa=activa)

    app.add_url_rule("/", "en_vivo", lambda: _pagina("en_vivo.html", "en_vivo"))
    app.add_url_rule("/captura", "captura", lambda: _pagina("captura.html", "captura"))
    app.add_url_rule("/ambiente", "ambiente", lambda: _pagina("ambiente.html", "ambiente"))
    app.add_url_rule("/historial", "historial", lambda: _pagina("historial.html", "historial"))
    app.add_url_rule("/reporte", "reporte", lambda: _pagina("reporte.html", "reporte"))
    app.add_url_rule("/configuracion", "configuracion",
                     lambda: _pagina("configuracion.html", "configuracion"))

    # ------------------------------------------------------------- video MJPEG
    @app.route("/video.mjpg")
    def video():
        fps = max(1, int(robot.cfg.get("camara_visible.fps", 12)))

        def generar():
            robot.espectadores_video += 1
            id_previo = -1
            try:
                while True:
                    inicio = time.monotonic()
                    jpeg, id_previo = robot.esperar_jpeg(id_previo)
                    if jpeg is None:
                        time.sleep(0.2)
                        continue
                    yield (b"--cuadro\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                    espera = 1.0 / fps - (time.monotonic() - inicio)
                    if espera > 0:
                        time.sleep(espera)
            finally:
                robot.espectadores_video -= 1

        return Response(stream_with_context(generar()),
                        mimetype="multipart/x-mixed-replace; boundary=cuadro",
                        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})

    @app.route("/foto.jpg")
    def foto():
        jpeg, _ = robot.esperar_jpeg(-1, timeout=0.1)
        if jpeg is None:
            abort(503)
        return Response(jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- API: seguridad (respaldo HTTP)
    @app.post("/api/paro")
    def api_paro():
        robot.paro_emergencia()
        return jsonify({"ok": True})

    @app.post("/api/rearmar")
    def api_rearmar():
        robot.rearmar()
        return jsonify({"ok": True})

    # ------------------------------------------------------------- API: estado y config
    @app.get("/api/estado")
    def api_estado():
        return jsonify(robot.estado())

    @app.get("/api/diagnostico")
    def api_diagnostico():
        return jsonify(robot.diagnostico())

    @app.get("/api/paletas")
    def api_paletas():
        return jsonify(paletas.para_cliente())

    @app.get("/api/config")
    def api_config():
        return jsonify({
            "valores": robot.cfg.editables(),
            "clasificaciones": [c.a_dict() for c in CLASIFICACIONES],
            "pines": robot.cfg.seccion("pines"),
            "simulacion": robot.simulacion,
        })

    @app.post("/api/config")
    def api_config_guardar():
        cambios = request.get_json(silent=True) or {}
        try:
            aplicados = robot.aplicar_configuracion(cambios)
        except ErrorConfiguracion as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "aplicados": aplicados, "valores": robot.cfg.editables()})

    @app.post("/api/config/restablecer")
    def api_config_restablecer():
        robot.cfg.restablecer()
        robot.aplicar_configuracion({})
        return jsonify({"ok": True, "valores": robot.cfg.editables()})

    @app.post("/api/logo")
    def api_logo():
        archivo = request.files.get("logo")
        if archivo is None or not archivo.filename:
            return jsonify({"ok": False, "error": "No se recibió ningún archivo"}), 400
        extension = Path(secure_filename(archivo.filename)).suffix.lower()
        if extension not in (".png", ".jpg", ".jpeg"):
            return jsonify({"ok": False, "error": "El logo debe ser PNG o JPG"}), 400
        destino = robot.cfg.dir_datos / f"logo{extension}"
        archivo.save(destino)
        robot.aplicar_configuracion({"reporte.logo": str(destino)})
        return jsonify({"ok": True, "ruta": str(destino)})

    @app.get("/api/logo")
    def api_logo_ver():
        logo = robot.cfg.get("reporte.logo") or ""
        if logo and Path(logo).is_file():
            return send_file(Path(logo), max_age=0)
        return send_from_directory(app.static_folder, "img/logo.png")

    # ------------------------------------------------------------- API: captura e historial
    @app.post("/api/capturar")
    def api_capturar():
        d = request.get_json(silent=True) or {}
        try:
            insp = robot.capturar(d.get("tablero", ""), d.get("notas", ""), d.get("ubicacion", ""),
                                  d.get("referencia"), d.get("matriz"))
        except (ValueError, RuntimeError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "inspeccion": insp})

    # ------------------------------------------------------------- API: sensores ambientales
    @app.get("/api/ambiente")
    def api_ambiente():
        return jsonify({"actual": robot.ambiente, "historial": list(robot.historial_ambiente),
                        "simulacion": robot.simulacion,
                        "eventos_simulables": {k: v[1] for k, v in getattr(
                            robot.drivers.ambiente, "EVENTOS", {}).items()}})

    @app.get("/api/eventos")
    def api_eventos():
        return jsonify(robot.db.listar_eventos(int(request.args.get("limite", 200)),
                                               request.args.get("tipo") or None))

    @app.post("/api/sensores/calibrar")
    def api_calibrar_gases():
        try:
            cambios = robot.calibrar_gases()
        except (ValueError, ErrorConfiguracion) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "valores": cambios})

    @app.post("/api/sim/evento")
    def api_sim_evento():
        evento = (request.get_json(silent=True) or {}).get("evento", "")
        try:
            descripcion = robot.inyectar_evento_simulado(evento)
        except (ValueError, RuntimeError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "descripcion": descripcion})

    @app.get("/api/tableros")
    def api_tableros():
        return jsonify(robot.db.listar_tableros())

    @app.get("/api/inspecciones")
    def api_inspecciones():
        nivel = request.args.get("nivel", "")
        return jsonify(robot.db.listar_inspecciones(
            tablero=request.args.get("tablero") or None,
            nivel=int(nivel) if nivel.isdigit() else None,
            desde=request.args.get("desde") or None,
            hasta=request.args.get("hasta") or None))

    @app.get("/api/inspecciones/<int:id_insp>")
    def api_inspeccion(id_insp: int):
        insp = robot.db.obtener_inspeccion(id_insp, con_matriz=True)
        if insp is None:
            abort(404)
        insp["matriz"] = [[round(float(v), 2) for v in fila] for fila in insp["matriz"]]
        return jsonify(insp)

    @app.delete("/api/inspecciones/<int:id_insp>")
    def api_eliminar(id_insp: int):
        if not robot.eliminar_inspeccion(id_insp):
            abort(404)
        return jsonify({"ok": True})

    @app.get("/api/inspecciones/<int:id_insp>/matriz.csv")
    def api_matriz_csv(id_insp: int):
        insp = robot.db.obtener_inspeccion(id_insp, con_matriz=True)
        if insp is None:
            abort(404)
        salida = io.StringIO()
        escritor = csv.writer(salida)
        for fila in insp["matriz"]:
            escritor.writerow([f"{v:.2f}" for v in fila])
        return Response(salida.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition": f"attachment; filename=INS-{id_insp:05d}_matriz.csv"})

    @app.get("/api/inspecciones/<int:id_insp>/pdf")
    def api_pdf(id_insp: int):
        try:
            ruta = robot.generar_pdf(id_insp)
        except KeyError:
            abort(404)
        descargar = request.args.get("descargar") == "1"
        return send_file(ruta, mimetype="application/pdf", as_attachment=descargar,
                         download_name=ruta.name, max_age=0)

    @app.get("/api/tendencia")
    def api_tendencia():
        return jsonify(robot.db.tendencia(request.args.get("tablero") or None))

    @app.get("/capturas/<path:nombre>")
    def capturas(nombre: str):
        return send_from_directory(robot.dir_capturas, nombre, max_age=3600)

    @app.errorhandler(404)
    def no_encontrado(_e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "No encontrado"}), 404
        return render_template("404.html", activa=""), 404

    return app, socketio
