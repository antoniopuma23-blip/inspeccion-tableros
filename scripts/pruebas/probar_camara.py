#!/usr/bin/env python3
"""Prueba de la cámara visible (Pi Camera con picamera2 o webcam USB).

Detecta automáticamente qué cámara hay, mide los cuadros por segundo y el
tamaño del JPEG, y guarda una foto en data/prueba_camara.jpg.

Uso:
    python scripts/pruebas/probar_camara.py
    python scripts/pruebas/probar_camara.py --tipo usb --indice 0
    ROBOT_SIM=1 python scripts/pruebas/probar_camara.py
"""

import argparse
import time

import cv2

from _comun import RAIZ, cargar_config, simulacion, titulo


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tipo", choices=["auto", "picamera2", "usb"], default=None)
    parser.add_argument("--indice", type=int, default=None, help="Índice de la webcam USB")
    parser.add_argument("--cuadros", type=int, default=40)
    parser.add_argument("--sim", action="store_true", help="Usar la cámara simulada")
    args = parser.parse_args()

    cfg = cargar_config()
    cv = cfg.seccion("camara_visible")
    titulo("PRUEBA DE CÁMARA VISIBLE")
    if simulacion():
        from robot.drivers.simulados import CamaraVisibleSimulada, EscenaTablero, ModeloTraccionSimulado
        camara = CamaraVisibleSimulada(EscenaTablero(), ModeloTraccionSimulado(), cv.get("ancho", 640),
                                       cv.get("alto", 480), cv.get("fps", 12))
    else:
        from robot.drivers.camara_visible import detectar_camara
        try:
            camara = detectar_camara(args.tipo or cv.get("tipo", "auto"),
                                     cv.get("indice_usb", 0) if args.indice is None else args.indice,
                                     cv.get("ancho", 640), cv.get("alto", 480), cv.get("fps", 12))
        except RuntimeError as e:
            raise SystemExit(f"ERROR: no se encontró ninguna cámara.\n{e}\n"
                             "Pi Camera: revisa el cable plano y ejecuta 'rpicam-hello --list-cameras'.\n"
                             "USB: revisa 'ls /dev/video*'.")
    print(f"Cámara detectada: {camara.nombre} — {camara.detalle}")

    cuadro = None
    t0 = time.monotonic()
    for _ in range(args.cuadros):
        c = camara.leer_cuadro()
        if c is not None:
            cuadro = c
    duracion = time.monotonic() - t0
    camara.cerrar()
    if cuadro is None:
        raise SystemExit("La cámara no entregó cuadros.")

    ok, jpeg = cv2.imencode(".jpg", cuadro, [cv2.IMWRITE_JPEG_QUALITY, cv.get("calidad_jpeg", 70)])
    print(f"Resolución: {cuadro.shape[1]}x{cuadro.shape[0]}")
    print(f"Captura: {args.cuadros / duracion:.1f} cuadros/s (objetivo {cv.get('fps', 12)} fps)")
    print(f"Tamaño JPEG (calidad {cv.get('calidad_jpeg', 70)}): {len(jpeg) / 1024:.1f} KB "
          f"→ ~{len(jpeg) * cv.get('fps', 12) * 8 / 1e6:.1f} Mbit/s por espectador")
    salida = RAIZ / "data" / "prueba_camara.jpg"
    salida.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(salida), cuadro)
    print(f"Foto guardada en {salida}")


if __name__ == "__main__":
    main()
