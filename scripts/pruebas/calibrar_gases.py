#!/usr/bin/env python3
"""Calibración de R0 de los sensores MQ-2 y MQ-7 en aire limpio.

Los MQ entregan una resistencia Rs que depende del gas. Para convertirla a
ppm se necesita R0, la resistencia en aire limpio de CADA sensor:

    R0 = Rs(aire limpio) / razón_aire_limpio      (MQ-2: 9.83, MQ-7: 27.5)

Procedimiento:
  1. Coloca el robot en un lugar ventilado, lejos de gas, humo o combustión.
  2. Deja los sensores encendidos al menos 3 minutos (la primera vez se
     recomienda un "quemado" de 24-48 h).
  3. Ejecuta este script: promedia lecturas durante el tiempo indicado y
     guarda R0 en data/config_usuario.yaml.

También se puede calibrar desde la interfaz: Ambiente → "Calibrar MQ en aire limpio".

Uso:
    sudo systemctl stop robot-inspeccion
    python scripts/pruebas/calibrar_gases.py --segundos 60
"""

import argparse
import statistics
import time

from _comun import cargar_config, conectar_pigpio, confirmar, simulacion, titulo


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--segundos", type=float, default=60.0, help="Duración del promedio")
    parser.add_argument("--sim", action="store_true", help="Sensores simulados")
    args = parser.parse_args()

    cfg = cargar_config()
    s = cfg.seccion("sensores")
    titulo("CALIBRACIÓN DE SENSORES MQ EN AIRE LIMPIO")
    pi = None
    if simulacion():
        from robot.drivers.simulados import SensoresAmbienteSimulados
        sensores = SensoresAmbienteSimulados(lambda: cfg.seccion("sensores"))
    else:
        from robot.drivers.sensores_rpi import SensoresAmbienteRPi
        pi = conectar_pigpio()
        sensores = SensoresAmbienteRPi(pi, cfg.seccion("pines"), s)
        if sensores.ads is None:
            raise SystemExit("No se detectó el ADS1115: la calibración requiere las salidas analógicas AO.")

    from robot.logica.ambiente import r0_desde_rs, rs_desde_voltaje
    rs = {"mq2": [], "mq7": []}
    fin = time.monotonic() + args.segundos
    print(f"Promediando durante {args.segundos:.0f} s…")
    while time.monotonic() < fin:
        c = sensores.leer()
        for gas in rs:
            valor = rs_desde_voltaje(c.get(f"v_{gas}"), float(s[gas].get("vc", 5.0)), float(s[gas].get("rl_kohm", 1.0)))
            if valor is not None:
                rs[gas].append(valor)
        print(f"\r  muestras: {len(rs['mq2'])}   restante: {fin - time.monotonic():4.0f} s ", end="", flush=True)
        time.sleep(0.5)
    print()
    sensores.cerrar()
    if pi is not None:
        pi.stop()

    cambios = {}
    for gas, valores in rs.items():
        if len(valores) < 5:
            raise SystemExit(f"Muy pocas lecturas válidas del {gas.upper()}; revisa el cableado.")
        media = statistics.mean(valores)
        variacion = statistics.pstdev(valores) / media * 100
        r0 = r0_desde_rs(media, float(s[gas]["razon_aire_limpio"]))
        print(f"  {gas.upper()}: Rs = {media:.2f} kΩ (variación {variacion:.1f} %)  ->  R0 = {r0:.3f} kΩ "
              f"(antes {s[gas]['r0_kohm']})")
        if variacion > 10:
            print("    ADVERTENCIA: lectura inestable; ¿el sensor terminó de calentarse?")
        cambios[f"sensores.{gas}.r0_kohm"] = round(r0, 3)

    if confirmar("¿Guardar estos valores de R0?"):
        cfg.actualizar(cambios)
        print(f"Guardado en {cfg.ruta_usuario}. Reinicia el servicio: sudo systemctl restart robot-inspeccion")


if __name__ == "__main__":
    main()
