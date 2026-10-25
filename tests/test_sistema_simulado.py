"""Pruebas de integración del sistema completo en modo simulación.

Arrancan el robot simulado (drivers, hilos, base de datos) y el servidor web
con el cliente de pruebas de Flask y de Socket.IO.
"""

import time

import numpy as np
import pytest

from robot.config import Configuracion, ErrorConfiguracion
from robot.drivers.esp32_uart import codificar_mensaje, decodificar_linea
from robot.logica.bateria import porcentaje_lipo


def esperar(condicion, timeout=5.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def cliente(robot_sim):
    from robot.web.app import crear_app

    app, socketio = crear_app(robot_sim)
    app.testing = True
    return app.test_client(), socketio, app, robot_sim


def test_paginas_responden(cliente):
    http, *_ = cliente
    for ruta in ("/", "/captura", "/ambiente", "/historial", "/reporte", "/configuracion"):
        r = http.get(ruta)
        assert r.status_code == 200, ruta
        assert "PARO" in r.get_data(as_text=True)
    assert http.get("/no-existe").status_code == 404


def test_simulacion_produce_datos_termicos_y_video(robot_sim):
    assert esperar(lambda: robot_sim.matriz_termica() is not None)
    m = robot_sim.matriz_termica()
    assert m.shape == (24, 32)
    assert 15 < float(m.min()) < float(m.max()) < 80
    jpeg, _ = robot_sim.esperar_jpeg(-1, timeout=3)
    assert jpeg[:2] == b"\xff\xd8"  # cabecera JPEG


def test_captura_historial_y_pdf(cliente):
    http, _, _, robot = cliente
    assert esperar(lambda: robot.matriz_termica() is not None and robot.cuadro_visible() is not None)

    r = http.post("/api/capturar", json={"tablero": "TD-01", "ubicacion": "Lab", "notas": "Prueba",
                                         "referencia": {"modo": "mediana"}})
    assert r.status_code == 200, r.get_json()
    insp = r.get_json()["inspeccion"]
    assert insp["tablero"] == "TD-01"
    assert insp["delta_t"] == pytest.approx(insp["t_max"] - insp["t_referencia"])
    for clave in ("img_visible", "img_termica", "img_fusion", "img_miniatura"):
        assert (robot.dir_capturas / insp[clave]).exists()

    # Referencia por punto
    r = http.post("/api/capturar", json={"tablero": "TD-01",
                                         "referencia": {"modo": "punto", "fila": 2, "columna": 3}})
    assert r.get_json()["inspeccion"]["modo_referencia"] == "punto"

    # Validaciones
    assert http.post("/api/capturar", json={"tablero": ""}).status_code == 400
    assert http.post("/api/capturar", json={"tablero": "X", "referencia": {"modo": "punto"}}).status_code == 400

    lista = http.get("/api/inspecciones?tablero=TD-01").get_json()
    assert len(lista) == 2
    assert http.get("/api/tendencia").get_json()["TD-01"][0]["t_max"] > 0
    assert http.get("/api/tableros").get_json()[0]["inspecciones"] == 2

    detalle = http.get(f"/api/inspecciones/{insp['id']}").get_json()
    assert len(detalle["matriz"]) == 24 and len(detalle["matriz"][0]) == 32
    csv = http.get(f"/api/inspecciones/{insp['id']}/matriz.csv").get_data(as_text=True)
    assert len(csv.strip().splitlines()) == 24

    pdf = http.get(f"/api/inspecciones/{insp['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.data[:4] == b"%PDF"

    assert http.delete(f"/api/inspecciones/{insp['id']}").status_code == 200
    assert len(http.get("/api/inspecciones").get_json()) == 1


def test_captura_con_matriz_congelada(cliente):
    http, _, _, robot = cliente
    m = np.full((24, 32), 30.0)
    m[5, 5] = 50.0
    r = http.post("/api/capturar", json={"tablero": "TD-02", "matriz": m.ravel().tolist()})
    insp = r.get_json()["inspeccion"]
    assert insp["t_max"] == pytest.approx(50.0)
    assert insp["nivel"] == 3


def test_configuracion_valida_y_aplica(cliente):
    http, _, _, robot = cliente
    r = http.post("/api/config", json={"traccion.velocidad_maxima_m_s": 0.2, "traccion.diametro_rueda_m": 0.05})
    assert r.status_code == 200
    assert robot.control.p.velocidad_maxima == pytest.approx(0.2)
    assert robot.control.p.diametro_rueda == pytest.approx(0.05)
    assert robot.cfg.ruta_usuario.exists()

    assert http.post("/api/config", json={"traccion.velocidad_maxima_m_s": 5}).status_code == 400
    assert http.post("/api/config", json={"pines.motor_izquierdo.pwm": 4}).status_code == 400
    assert http.post("/api/config", json={"analisis.umbral_reparar_c": 0.5}).status_code == 400

    http.post("/api/config/restablecer")
    assert robot.control.p.velocidad_maxima == pytest.approx(0.3)


def test_movimiento_y_paro_por_desconexion(cliente):
    _, socketio, app, robot = cliente
    sio = socketio.test_client(app)
    assert sio.is_connected()
    assert any(m["name"] == "bienvenida" for m in sio.get_received())

    for _ in range(8):
        sio.emit("mover", {"x": 0, "y": 1})
        time.sleep(0.1)
    assert robot.control.referencia[0] > 0
    assert esperar(lambda: robot.control.medida[0] > 0.02, 2)

    sio.disconnect()                 # el piloto se desconecta
    assert robot.control.referencia == [0.0, 0.0]
    assert robot.drivers.motores.ultimo_duty == (0.0, 0.0)
    assert "conexión" in robot.control.ultimo_paro


def test_paro_de_emergencia_por_socket(cliente):
    _, socketio, app, robot = cliente
    sio = socketio.test_client(app)
    sio.emit("paro")
    assert robot.control.paro_emergencia_activo
    sio.emit("mover", {"x": 0, "y": 1})
    time.sleep(0.1)
    assert robot.control.referencia == [0.0, 0.0]
    sio.emit("rearmar")
    assert not robot.control.paro_emergencia_activo
    sio.disconnect()


def test_cierre_deja_motores_en_cero(robot_sim):
    robot_sim.comando_movimiento(0, 1)
    time.sleep(0.3)
    robot_sim.cerrar()
    assert robot_sim.drivers.motores.ultimo_duty == (0.0, 0.0)


def test_estado_incluye_bateria_simulada_y_sistema(robot_sim):
    assert esperar(lambda: robot_sim.estado()["bateria_v"] is not None)
    e = robot_sim.estado()
    assert e["simulacion"] is True
    assert 6.0 < e["bateria_v"] < 8.5
    assert {"cpu", "temp_soc", "v_izq", "v_der"} <= set(e)


def test_protocolo_esp32():
    assert decodificar_linea(b'{"t":"bateria","v":7.6}\n') == {"t": "bateria", "v": 7.6}
    assert decodificar_linea(b"basura") is None
    assert decodificar_linea(b'{"sin_tipo":1}') is None
    assert codificar_mensaje({"t": "paro"}) == b'{"t":"paro"}\n'


def test_porcentaje_lipo():
    assert porcentaje_lipo(None) is None
    assert porcentaje_lipo(8.4) == pytest.approx(100)
    assert porcentaje_lipo(6.6) == pytest.approx(0)
    assert 40 < porcentaje_lipo(7.6) < 60


def test_config_base_no_se_modifica(tmp_path):
    cfg = Configuracion(sobrescribir={"datos": {"directorio": str(tmp_path)}})
    original = cfg.ruta_base.read_text(encoding="utf-8")
    cfg.actualizar({"camara_termica.emisividad": 0.9})
    assert cfg.ruta_base.read_text(encoding="utf-8") == original
    with pytest.raises(ErrorConfiguracion):
        cfg.actualizar({"camara_termica.emisividad": 2})
    recargada = Configuracion(sobrescribir={"datos": {"directorio": str(tmp_path)}})
    assert recargada.get("camara_termica.emisividad") == pytest.approx(0.9)


# ---------------------------------------------------------------------------
#  Sensores de TechRoach y control Xbox (simulación)
# ---------------------------------------------------------------------------
def test_api_ambiente_y_lecturas(cliente):
    http, _, _, robot = cliente
    assert esperar(lambda: robot.ambiente.get("temperatura") is not None)
    d = http.get("/api/ambiente").get_json()
    assert d["simulacion"] is True
    assert {"gas", "co", "humedad", "presencia", "impacto", "oscuridad"} <= set(d["eventos_simulables"])
    a = d["actual"]
    assert 20 < a["temperatura"] < 35 and 30 < a["humedad"] < 70
    assert 50 < a["mq2_ppm"] < 250            # la cadena voltaje -> ppm recupera el valor simulado
    assert a["mq2_nivel"] == "normal" and a["bloqueo"] is None


def test_alarma_de_gas_detiene_y_solo_permite_retroceso(cliente):
    http, socketio, app, robot = cliente
    sio = socketio.test_client(app)
    for _ in range(5):
        sio.emit("mover", {"x": 0, "y": 1})
        time.sleep(0.1)
    assert robot.control.referencia[0] > 0

    assert http.post("/api/sim/evento", json={"evento": "gas"}).status_code == 200

    def avanzar_hasta_alarma():
        sio.emit("mover", {"x": 0, "y": 1})
        return robot.ambiente.get("mq2_nivel") == "alarma"
    assert esperar(avanzar_hasta_alarma, timeout=10)
    time.sleep(0.2)
    sio.emit("mover", {"x": 0, "y": 1})
    time.sleep(0.2)
    assert robot.control.bloqueo == "avance"
    assert all(v <= 0 for v in robot.control.referencia)
    assert "gas" in robot.control.ultimo_paro.lower()

    for _ in range(5):                        # retroceder sí se permite
        sio.emit("mover", {"x": 0, "y": -1})
        time.sleep(0.1)
    assert robot.control.referencia[0] < 0
    sio.disconnect()

    eventos = http.get("/api/eventos").get_json()
    assert any(e["tipo"] == "gas_mq2" and e["nivel"] == "alarma" for e in eventos)
    assert robot.drivers.ambiente.estado_salidas is not None


def test_humedad_alta_reduce_velocidad(cliente):
    http, _, _, robot = cliente
    http.post("/api/sim/evento", json={"evento": "humedad"})
    assert esperar(lambda: robot.ambiente.get("humedad_alta"), timeout=8)
    assert robot.control.factor_externo == pytest.approx(0.5)


def test_impacto_detiene(cliente):
    http, _, _, robot = cliente
    assert esperar(lambda: robot.ambiente.get("temperatura") is not None)
    robot.comando_movimiento(0, 1)
    time.sleep(0.3)
    http.post("/api/sim/evento", json={"evento": "impacto"})
    assert esperar(lambda: "impacto" in robot.control.ultimo_paro.lower(), timeout=3)


def test_evento_simulado_invalido(cliente):
    http, *_ = cliente
    assert http.post("/api/sim/evento", json={"evento": "terremoto"}).status_code == 400


def test_captura_guarda_condiciones_ambientales(cliente):
    http, _, _, robot = cliente
    assert esperar(lambda: robot.matriz_termica() is not None and robot.ambiente.get("humedad") is not None)
    insp = http.post("/api/capturar", json={"tablero": "TD-05"}).get_json()["inspeccion"]
    assert insp["t_ambiente"] is not None and insp["humedad"] is not None
    assert http.get(f"/api/inspecciones/{insp['id']}/pdf").data[:4] == b"%PDF"


def test_control_xbox_por_socket(cliente):
    http, socketio, app, robot = cliente
    sio = socketio.test_client(app)
    for _ in range(6):                        # modo tanque: orugas en sentidos opuestos
        sio.emit("orugas", {"izq": 1.0, "der": -1.0})
        time.sleep(0.1)
    assert robot.control.referencia[0] > 0 > robot.control.referencia[1]

    r = sio.emit("captura_rapida", callback=True)
    assert r["ok"] is False                   # aún no hay un tablero capturado
    assert esperar(lambda: robot.matriz_termica() is not None)
    http.post("/api/capturar", json={"tablero": "TD-07"})
    r = sio.emit("captura_rapida", callback=True)
    assert r["ok"] is True and r["tablero"] == "TD-07"
    sio.disconnect()


def test_calibracion_de_gases(cliente):
    http, _, _, robot = cliente
    assert esperar(lambda: len(robot._voltajes_mq["mq2"]) >= 10)
    r0_previo = robot.cfg.get("sensores.mq2.r0_kohm")
    r = http.post("/api/sensores/calibrar").get_json()
    assert r["ok"] and r["valores"]["sensores.mq2.r0_kohm"] != r0_previo
    assert robot.cfg.get("sensores.mq2.r0_kohm") == r["valores"]["sensores.mq2.r0_kohm"]


def test_temperatura_reflejada_usa_dht22(robot_sim):
    assert esperar(lambda: robot_sim.ambiente.get("temperatura") is not None)
    assert robot_sim.temperatura_reflejada() == pytest.approx(robot_sim.ambiente["temperatura"])
