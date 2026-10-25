#!/usr/bin/env python3
"""Prueba de la cámara térmica MLX90640.

* Verifica la comunicación I2C (dirección 0x33).
* Mide la frecuencia real de cuadros.
* Imprime un mapa ASCII y las temperaturas mín/máx/mediana.
* Guarda un termograma en PNG (data/prueba_mlx90640.png).

Antes de usarla en la Pi:
    sudo i2cdetect -y 1        # debe aparecer "33"

Uso:
    python scripts/pruebas/probar_mlx90640.py --cuadros 20
    ROBOT_SIM=1 python scripts/pruebas/probar_mlx90640.py
"""

import argparse
import time

import numpy as np

from _comun import RAIZ, cargar_config, simulacion, titulo

GRADIENTE = " .:-=+*#%@"


def ascii(matriz: np.ndarray) -> str:
    t_min, t_max = float(matriz.min()), float(matriz.max())
    idx = ((matriz - t_min) / max(0.1, t_max - t_min) * (len(GRADIENTE) - 1)).astype(int)
    return "\n".join("".join(GRADIENTE[v] * 2 for v in fila) for fila in idx)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cuadros", type=int, default=12, help="Número de cuadros a leer")
    parser.add_argument("--sim", action="store_true", help="Usar el sensor simulado")
    args = parser.parse_args()

    cfg = cargar_config()
    ct = cfg.seccion("camara_termica")
    titulo("PRUEBA DE CÁMARA TÉRMICA MLX90640")
    if simulacion():
        from robot.drivers.simulados import CamaraTermicaSimulada, EscenaTablero, ModeloTraccionSimulado
        sensor = CamaraTermicaSimulada(EscenaTablero(), ModeloTraccionSimulado(), ct.get("frecuencia_hz", 4))
    else:
        from robot.drivers.mlx90640 import CamaraMLX90640
        try:
            sensor = CamaraMLX90640(ct.get("frecuencia_hz", 4), ct.get("i2c_frecuencia_hz", 400000),
                                    ct.get("rotar_180", False), ct.get("espejo_horizontal", False))
        except Exception as e:
            raise SystemExit(f"ERROR: no se pudo iniciar el MLX90640: {e}\n"
                             "Revisa el cableado (SDA=GPIO2, SCL=GPIO3, 3.3V, GND) y que I2C esté habilitado.")
    print(sensor.detalle)

    from robot.logica.analisis_termico import corregir_emisividad, estadisticas
    matriz = None
    t0 = time.monotonic()
    leidos = 0
    for k in range(args.cuadros):
        cuadro = sensor.leer_cuadro()
        if cuadro is None:
            print(f"  cuadro {k + 1}: error de lectura")
            continue
        leidos += 1
        matriz = corregir_emisividad(cuadro, ct.get("emisividad", 0.95), ct.get("temp_reflejada_c", 25))
        est = estadisticas(matriz)
        print(f"  cuadro {k + 1:>2}: mín {est['t_min']:5.1f} °C  máx {est['t_max']:5.1f} °C  "
              f"mediana {est['t_mediana']:5.1f} °C  punto caliente {est['pos_max']}")
    duracion = time.monotonic() - t0
    sensor.cerrar()
    if matriz is None:
        raise SystemExit("No se obtuvo ningún cuadro válido.")

    print(f"\nFrecuencia real: {leidos / duracion:.2f} cuadros/s (objetivo {ct.get('frecuencia_hz', 4)} Hz)")
    print("\nMapa ASCII del último cuadro:\n" + ascii(matriz))

    import cv2
    from robot.logica.imagen_termica import renderizar_termica
    salida = RAIZ / "data" / "prueba_mlx90640.png"
    salida.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(salida), renderizar_termica(matriz, cfg.get("interfaz.paleta", "ironbow"), 480,
                                                pos_max=estadisticas(matriz)["pos_max"]))
    print(f"\nTermograma guardado en {salida}")


if __name__ == "__main__":
    main()
