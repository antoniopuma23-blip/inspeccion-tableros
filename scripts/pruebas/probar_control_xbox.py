#!/usr/bin/env python3
"""Prueba del control Xbox conectado a la Raspberry Pi (USB o Bluetooth).

Lista los dispositivos de entrada, detecta el control y muestra en vivo los
sticks, los botones y el comando que recibiría el robot. NO mueve los motores.

Emparejar por Bluetooth (una sola vez):
    bluetoothctl
      power on
      agent on
      scan on            # mantener presionado el botón de emparejar del control
      pair  XX:XX:XX:XX:XX:XX
      trust XX:XX:XX:XX:XX:XX
      connect XX:XX:XX:XX:XX:XX

Uso:
    python scripts/pruebas/probar_control_xbox.py
    python scripts/pruebas/probar_control_xbox.py --modo tanque
"""

import argparse
import sys

from _comun import cargar_config, titulo


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modo", choices=["arcade", "tanque"], default=None)
    args = parser.parse_args()

    try:
        import evdev
        from evdev import ecodes
    except ImportError:
        sys.exit("Falta evdev. En la Pi: sudo apt install python3-evdev")

    from robot.logica.gamepad import EstadoMando, comando_desde_mando, normalizar_eje

    cfg = cargar_config().seccion("control_xbox")
    modo = args.modo or cfg.get("modo", "arcade")
    titulo("PRUEBA DEL CONTROL XBOX")
    dispositivos = [evdev.InputDevice(r) for r in evdev.list_devices()]
    if not dispositivos:
        sys.exit("No hay dispositivos de entrada. ¿El usuario está en el grupo 'input'? (sudo usermod -aG input $USER)")
    control = None
    for d in dispositivos:
        teclas = d.capabilities().get(ecodes.EV_KEY, [])
        es_mando = ecodes.BTN_SOUTH in teclas
        print(f"  {d.path:<20} {d.name}{'   <-- control' if es_mando and control is None else ''}")
        if es_mando and control is None:
            control = d
    if control is None:
        sys.exit("\nNo se encontró un control. Conéctalo por USB o empareja por Bluetooth.")

    rangos = {}
    for codigo in (ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY):
        try:
            info = control.absinfo(codigo)
            rangos[codigo] = (info.min, info.max)
        except Exception:
            pass
    nombres = {ecodes.BTN_SOUTH: "A", ecodes.BTN_EAST: "B", ecodes.BTN_TL: "LB", ecodes.BTN_TR: "RB",
               ecodes.BTN_START: "Menú", ecodes.BTN_SELECT: "Vista"}
    print(f"\nModo: {modo}. Mueve los sticks y presiona botones (Ctrl+C para salir)\n")
    m = EstadoMando()
    try:
        for ev in control.read_loop():
            if ev.type == ecodes.EV_ABS and ev.code in rangos:
                v = normalizar_eje(ev.value, *rangos[ev.code])
                if ev.code == ecodes.ABS_X:
                    m.lx = v
                elif ev.code == ecodes.ABS_Y:
                    m.ly = -v
                elif ev.code == ecodes.ABS_RX:
                    m.rx = v
                elif ev.code == ecodes.ABS_RY:
                    m.ry = -v
            elif ev.type == ecodes.EV_KEY and ev.value == 1:
                print(f"\n  Botón: {nombres.get(ev.code, ecodes.BTN.get(ev.code, ev.code))}")
            tipo, a, b = comando_desde_mando(m, modo, float(cfg.get("zona_muerta", 0.15)), float(cfg.get("expo", 0.35)))
            etiquetas = ("x (giro)", "y (avance)") if tipo == "arcade" else ("oruga izq.", "oruga der.")
            print(f"\r  LX {m.lx:+.2f} LY {m.ly:+.2f} RX {m.rx:+.2f} RY {m.ry:+.2f}  ->  "
                  f"{etiquetas[0]} {a:+.2f}  {etiquetas[1]} {b:+.2f}   ", end="", flush=True)
    except KeyboardInterrupt:
        print()
    except OSError:
        print("\nControl desconectado (en el robot esto provoca un paro inmediato).")


if __name__ == "__main__":
    main()
