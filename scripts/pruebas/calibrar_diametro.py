#!/usr/bin/env python3
"""Calibración del diámetro EFECTIVO de la rueda motriz de las orugas.

Si no conoces el diámetro del sprocket (o la oruga añade grosor), este script
lo calcula a partir de una distancia medida con cinta métrica:

    diámetro = distancia_recorrida / (π × vueltas_medidas_por_encoder)

Procedimiento:
  1. Coloca el robot en el piso, en línea recta, y marca la posición inicial
     (por ejemplo, la punta delantera de la oruga) con cinta.
  2. Ejecuta el script. El robot avanza en línea recta a baja velocidad
     durante unos segundos (o empújalo tú con --empujar).
  3. Mide con cinta métrica la distancia recorrida y escríbela.
  4. El script calcula el diámetro y te ofrece guardarlo en
     data/config_usuario.yaml (lo verás en la página Configuración).

Repite 2-3 veces y usa el promedio para mayor exactitud.

Uso:
    python scripts/pruebas/calibrar_diametro.py                 # avanza con motores
    python scripts/pruebas/calibrar_diametro.py --tiempo 4 --duty 0.4
    python scripts/pruebas/calibrar_diametro.py --empujar       # motores apagados, empujar a mano
    ROBOT_SIM=1 python scripts/pruebas/calibrar_diametro.py     # demostración sin hardware
"""

import argparse
import math
import time

from _comun import cargar_config, conectar_pigpio, confirmar, simulacion, titulo


def pedir_distancia() -> float:
    while True:
        texto = input("Distancia recorrida medida con cinta (en centímetros): ").strip().replace(",", ".")
        try:
            valor = float(texto)
            if valor > 0:
                return valor / 100.0
        except ValueError:
            pass
        print("  Escribe un número positivo, por ejemplo 85.5")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duty", type=float, default=0.4, help="Ciclo de trabajo durante el avance")
    parser.add_argument("--tiempo", type=float, default=3.0, help="Segundos de avance")
    parser.add_argument("--empujar", action="store_true", help="No usar motores: empuja el robot a mano")
    parser.add_argument("--sim", action="store_true", help="Modo simulación")
    args = parser.parse_args()

    cfg = cargar_config()
    from robot.drivers.fabrica import cuentas_por_vuelta
    cpr = cuentas_por_vuelta(cfg)
    actual = cfg.get("traccion.diametro_rueda_m", 0.04)
    titulo("CALIBRACIÓN DEL DIÁMETRO DE LA RUEDA MOTRIZ")
    print(f"Cuentas por vuelta: {cpr}  |  Diámetro configurado actualmente: {actual * 1000:.1f} mm\n")

    pi = None
    motores = None
    if simulacion():
        from robot.drivers.simulados import EncodersSimulados, ModeloTraccionSimulado, MotoresSimulados
        modelo = ModeloTraccionSimulado(cuentas_por_vuelta=cpr, diametro_m=0.0435)  # "real" desconocido
        encoders, motores = EncodersSimulados(modelo), MotoresSimulados(modelo)
        print("Modo simulación: el diámetro 'real' simulado es 43.5 mm.")
    else:
        from robot.drivers.encoders_pigpio import EncodersPigpio
        pi = conectar_pigpio()
        e = cfg.seccion("encoders")
        encoders = EncodersPigpio(pi, cfg.seccion("pines"), e.get("modo_cuadratura", 4),
                                  e.get("filtro_glitch_us", 20), e.get("invertir_izquierdo", False),
                                  e.get("invertir_derecho", False))
        if not args.empujar:
            from robot.drivers.motores_l298n import MotoresL298N
            m = cfg.seccion("motores")
            motores = MotoresL298N(pi, cfg.seccion("pines"), m.get("pwm_frecuencia_hz", 2000),
                                   m.get("invertir_izquierdo", False), m.get("invertir_derecho", False))

    inicio = encoders.leer_conteos()
    try:
        if args.empujar:
            input("Empuja el robot en línea recta hacia adelante y presiona Enter al terminar…")
        else:
            if not simulacion() and not confirmar(f"El robot avanzará {args.tiempo:.1f} s. ¿El camino está despejado?"):
                return
            print("Avanzando… (Ctrl+C para detener)")
            encoders.indicar_sentido(1, 1)
            motores.aplicar(args.duty, args.duty)
            time.sleep(args.tiempo)
            motores.detener()
            time.sleep(0.5)  # esperar a que el robot se detenga por completo
    except KeyboardInterrupt:
        print("\nDetenido por el usuario")
    finally:
        if motores:
            motores.detener()

    fin = encoders.leer_conteos()
    cuentas = [fin[0] - inicio[0], fin[1] - inicio[1]]
    print(f"\nCuentas: izquierda {cuentas[0]}, derecha {cuentas[1]}")
    if min(abs(c) for c in cuentas) < cpr * 0.25:
        print("ADVERTENCIA: menos de 1/4 de vuelta en algún encoder; revisa el cableado o aumenta el tiempo.")
    if cuentas[0] * cuentas[1] < 0:
        print("ADVERTENCIA: los encoders cuentan en sentidos opuestos; ajusta encoders.invertir_* en config.yaml.")
    vueltas = (abs(cuentas[0]) + abs(cuentas[1])) / 2 / cpr
    if vueltas <= 0:
        raise SystemExit("No se registró movimiento en los encoders.")
    print(f"Vueltas promedio de la rueda motriz: {vueltas:.3f}")

    if simulacion():
        distancia = vueltas * math.pi * 0.0435
        print(f"Distancia simulada recorrida: {distancia * 100:.1f} cm")
    else:
        distancia = pedir_distancia()

    diametro = distancia / (math.pi * vueltas)
    print(f"\n>>> Diámetro efectivo calculado: {diametro * 1000:.1f} mm  (antes: {actual * 1000:.1f} mm)")
    if not (0.010 <= diametro <= 0.300):
        raise SystemExit("El valor está fuera del rango razonable (10-300 mm); repite la medición.")
    if confirmar("¿Guardar este diámetro en la configuración?"):
        cfg.actualizar({"traccion.diametro_rueda_m": round(diametro, 4)})
        print(f"Guardado en {cfg.ruta_usuario}. Reinicia el servicio si el robot está en ejecución:\n"
              "    sudo systemctl restart robot-inspeccion")

    if motores:
        motores.cerrar()
    encoders.cerrar()
    if pi is not None:
        pi.stop()


if __name__ == "__main__":
    main()
