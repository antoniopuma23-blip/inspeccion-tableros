#!/usr/bin/env python3
"""Prueba de los encoders de cuadratura.

Muestra en vivo los conteos, las rpm y la velocidad lineal de cada oruga.
Gira las orugas a mano (o usa --mover para moverlas a un duty bajo) y
verifica que:

* el conteo AUMENTE al avanzar y DISMINUYA al retroceder,
* una vuelta completa de la rueda dé aproximadamente las cuentas esperadas.

Uso:
    python scripts/pruebas/probar_encoders.py
    python scripts/pruebas/probar_encoders.py --mover 0.35
    ROBOT_SIM=1 python scripts/pruebas/probar_encoders.py --mover 0.5
"""

import argparse
import math
import time

from _comun import cargar_config, conectar_pigpio, simulacion, titulo


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mover", type=float, default=0.0, help="Duty para mover ambos motores (0 = a mano)")
    parser.add_argument("--sim", action="store_true", help="Usar encoders simulados")
    args = parser.parse_args()

    cfg = cargar_config()
    from robot.drivers.fabrica import cuentas_por_vuelta
    cpr = cuentas_por_vuelta(cfg)
    diametro = cfg.get("traccion.diametro_rueda_m", 0.04)
    titulo("PRUEBA DE ENCODERS")
    print(f"Cuentas por vuelta esperadas: {cpr}   Diámetro configurado: {diametro * 1000:.1f} mm")

    pi = None
    motores = None
    if simulacion():
        from robot.drivers.simulados import EncodersSimulados, ModeloTraccionSimulado, MotoresSimulados
        modelo = ModeloTraccionSimulado(cuentas_por_vuelta=cpr, diametro_m=diametro)
        encoders = EncodersSimulados(modelo)
        motores = MotoresSimulados(modelo)
    else:
        from robot.drivers.encoders_pigpio import EncodersPigpio
        pi = conectar_pigpio()
        e = cfg.seccion("encoders")
        encoders = EncodersPigpio(pi, cfg.seccion("pines"), e.get("modo_cuadratura", 4),
                                  e.get("filtro_glitch_us", 20), e.get("invertir_izquierdo", False),
                                  e.get("invertir_derecho", False))
        if args.mover:
            from robot.drivers.motores_l298n import MotoresL298N
            m = cfg.seccion("motores")
            motores = MotoresL298N(pi, cfg.seccion("pines"), m.get("pwm_frecuencia_hz", 2000),
                                   m.get("invertir_izquierdo", False), m.get("invertir_derecho", False))

    if args.mover and motores:
        motores.aplicar(args.mover, args.mover)
        encoders.indicar_sentido(1, 1)

    print("Ctrl+C para terminar\n")
    print(f"{'Izq. cuentas':>13} {'rpm':>7} {'m/s':>6}  |  {'Der. cuentas':>13} {'rpm':>7} {'m/s':>6}")
    previo = encoders.leer_conteos()
    t_prev = time.monotonic()
    try:
        while True:
            time.sleep(0.25)
            actual = encoders.leer_conteos()
            ahora = time.monotonic()
            dt = ahora - t_prev
            filas = []
            for i in (0, 1):
                vueltas_s = (actual[i] - previo[i]) / cpr / dt
                filas.append((actual[i], vueltas_s * 60, vueltas_s * math.pi * diametro))
            print(f"\r{filas[0][0]:>13d} {filas[0][1]:>7.1f} {filas[0][2]:>6.3f}  |  "
                  f"{filas[1][0]:>13d} {filas[1][1]:>7.1f} {filas[1][2]:>6.3f}", end="", flush=True)
            previo, t_prev = actual, ahora
    except KeyboardInterrupt:
        print("\n")
    finally:
        if motores:
            motores.detener()
            motores.cerrar()
        encoders.cerrar()
        if pi is not None:
            pi.stop()


if __name__ == "__main__":
    main()
