#!/usr/bin/env python3
"""Prueba de los motores DC y el driver L298N.

Mueve cada motor por separado, hacia adelante y hacia atrás, a un ciclo de
trabajo bajo. Sirve para verificar el cableado (ENA/IN1/IN2, ENB/IN3/IN4),
el sentido de giro y la zona muerta.

    ¡LEVANTA EL ROBOT para que las orugas no toquen el piso!

Uso:
    python scripts/pruebas/probar_motores.py            # hardware real
    python scripts/pruebas/probar_motores.py --duty 0.5
    ROBOT_SIM=1 python scripts/pruebas/probar_motores.py   # sin hardware
"""

import argparse
import time

from _comun import cargar_config, conectar_pigpio, confirmar, simulacion, titulo


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duty", type=float, default=0.4, help="Ciclo de trabajo de prueba (0-1)")
    parser.add_argument("--tiempo", type=float, default=1.5, help="Segundos por movimiento")
    parser.add_argument("--zona-muerta", action="store_true", help="Barrido para encontrar el duty mínimo")
    parser.add_argument("--sim", action="store_true", help="Usar motores simulados")
    args = parser.parse_args()

    cfg = cargar_config()
    titulo("PRUEBA DE MOTORES (L298N)")
    pi = None
    if simulacion():
        from robot.drivers.simulados import ModeloTraccionSimulado, MotoresSimulados
        modelo = ModeloTraccionSimulado()
        motores = MotoresSimulados(modelo)
        print("Modo simulación: no se moverá ningún motor real.")
    else:
        from robot.drivers.motores_l298n import MotoresL298N
        pi = conectar_pigpio()
        m = cfg.seccion("motores")
        motores = MotoresL298N(pi, cfg.seccion("pines"), m.get("pwm_frecuencia_hz", 2000),
                               m.get("invertir_izquierdo", False), m.get("invertir_derecho", False))
        print(motores.detalle)
        if not confirmar("¿El robot está levantado y las orugas giran libres?"):
            motores.cerrar()
            pi.stop()
            return

    secuencia = [
        ("Motor IZQUIERDO hacia ADELANTE", args.duty, 0),
        ("Motor IZQUIERDO hacia ATRÁS", -args.duty, 0),
        ("Motor DERECHO hacia ADELANTE", 0, args.duty),
        ("Motor DERECHO hacia ATRÁS", 0, -args.duty),
        ("Ambos hacia ADELANTE", args.duty, args.duty),
        ("Giro en sitio a la DERECHA", args.duty, -args.duty),
    ]
    try:
        for nombre, izq, der in secuencia:
            print(f"-> {nombre} (duty izq={izq:+.2f}, der={der:+.2f})")
            motores.aplicar(izq, der)
            time.sleep(args.tiempo)
            motores.detener()
            time.sleep(0.6)

        if args.zona_muerta:
            titulo("BARRIDO DE ZONA MUERTA")
            print("Observa en qué duty empieza a girar cada motor y anótalo en motores.duty_minimo")
            for d in [x / 100 for x in range(5, 61, 5)]:
                print(f"   duty = {d:.2f}")
                motores.aplicar(d, d)
                time.sleep(1.0)
            motores.detener()
        print("\nSi algún motor giró al revés, cambia invertir_izquierdo / invertir_derecho en config.yaml.")
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario")
    finally:
        motores.detener()
        motores.cerrar()
        if pi is not None:
            pi.stop()
        print("Motores detenidos.")


if __name__ == "__main__":
    main()
