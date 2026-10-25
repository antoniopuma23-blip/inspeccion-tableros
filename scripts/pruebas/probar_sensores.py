#!/usr/bin/env python3
"""Prueba de los sensores ambientales de TechRoach conectados a la Raspberry Pi.

Muestra en vivo:
  * ADS1115: voltajes de MQ-2, MQ-7, LDR y batería, y las ppm estimadas
  * DHT22: temperatura y humedad
  * PIR y contador de impactos del piezoeléctrico
Al inicio prueba las salidas: zumbador, LED de alarma e iluminación.

Antes de usarlo en la Pi:
    sudo i2cdetect -y 1      # deben aparecer 33 (MLX90640) y 48 (ADS1115)

Uso:
    python scripts/pruebas/probar_sensores.py
    python scripts/pruebas/probar_sensores.py --sin-salidas
    ROBOT_SIM=1 python scripts/pruebas/probar_sensores.py
"""

import argparse
import time

from _comun import cargar_config, conectar_pigpio, simulacion, titulo


def fmt(v, d=2, unidad=""):
    return "   —  " if v is None else f"{v:6.{d}f}{unidad}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sin-salidas", action="store_true", help="No probar zumbador/LED/iluminación")
    parser.add_argument("--sim", action="store_true", help="Usar sensores simulados")
    args = parser.parse_args()

    cfg = cargar_config()
    s = cfg.seccion("sensores")
    titulo("PRUEBA DE SENSORES AMBIENTALES (TechRoach)")
    pi = None
    if simulacion():
        from robot.drivers.simulados import BateriaSimulada, SensoresAmbienteSimulados
        sensores = SensoresAmbienteSimulados(lambda: cfg.seccion("sensores"), BateriaSimulada())
    else:
        from robot.drivers.sensores_rpi import SensoresAmbienteRPi
        pi = conectar_pigpio()
        sensores = SensoresAmbienteRPi(pi, cfg.seccion("pines"), s)
    print(sensores.detalle)

    if not args.sin_salidas:
        for nombre, kwargs in (("Zumbador", {"buzzer": True}), ("LED de alarma", {"led_alarma": True}),
                               ("Iluminación", {"iluminacion": True})):
            print(f"-> {nombre} encendido 1 s")
            sensores.salidas(**kwargs)
            time.sleep(1.0)
            sensores.salidas()
            time.sleep(0.3)

    from robot.logica.ambiente import ProcesadorAmbiente, porcentaje_luz
    proc = ProcesadorAmbiente(s, cfg.seccion("respuestas"))
    print("\nLos MQ necesitan calentarse (~3 min, y 24-48 h la primera vez). Ctrl+C para salir.\n")
    print(f"{'V MQ-2':>8} {'ppm':>7} | {'V MQ-7':>8} {'ppm':>6} | {'Luz %':>6} | {'Bat V':>6} | "
          f"{'T °C':>6} {'HR %':>6} | PIR | Impactos")
    try:
        while True:
            c = sensores.leer()
            ppm2 = proc.ppm("mq2", c.get("v_mq2"))
            ppm7 = proc.ppm("mq7", c.get("v_mq7"))
            luz = porcentaje_luz(c.get("v_ldr"), invertir=bool(s.get("ldr", {}).get("invertir")))
            pir = "SÍ" if c.get("pir") else ("no" if c.get("pir") is not None else "—")
            extra = ""
            if c.get("mq2_digital") is not None:
                extra = f"  DO MQ-2={'ALARMA' if c['mq2_digital'] else 'ok'} MQ-7={'ALARMA' if c['mq7_digital'] else 'ok'}"
            print(f"\r{fmt(c.get('v_mq2'))}V {fmt(ppm2, 0)} | {fmt(c.get('v_mq7'))}V {fmt(ppm7, 0)} | "
                  f"{fmt(luz, 0)} | {fmt(c.get('v_bateria'))} | {fmt(c.get('temperatura'), 1)} "
                  f"{fmt(c.get('humedad'), 1)} | {pir:>3} | {c.get('impactos', 0):>8}{extra}   ",
                  end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
    finally:
        sensores.cerrar()
        if pi is not None:
            pi.stop()


if __name__ == "__main__":
    main()
