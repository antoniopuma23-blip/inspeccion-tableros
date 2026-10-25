"""Persistencia de inspecciones en SQLite.

Tablas:

* ``tableros``: un registro por tablero eléctrico (identificado por su código).
* ``inspecciones``: cada hallazgo capturado, con temperaturas, ΔT,
  clasificación, notas, rutas de imágenes y la matriz térmica completa
  (32×24 float32 guardada como BLOB, ~3 KB).

Se abre una conexión por operación (``check_same_thread`` no es problema y
SQLite maneja el bloqueo entre hilos); el modo WAL permite leer mientras se
escribe.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np

ESQUEMA = """
CREATE TABLE IF NOT EXISTS tableros (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo      TEXT NOT NULL UNIQUE,
    ubicacion   TEXT DEFAULT '',
    creado      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspecciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tablero_id      INTEGER NOT NULL REFERENCES tableros(id) ON DELETE CASCADE,
    fecha           TEXT NOT NULL,
    t_max           REAL NOT NULL,
    t_min           REAL NOT NULL,
    t_media         REAL NOT NULL,
    t_referencia    REAL NOT NULL,
    modo_referencia TEXT NOT NULL,
    delta_t         REAL NOT NULL,
    nivel           INTEGER NOT NULL,
    clasificacion   TEXT NOT NULL,
    emisividad      REAL NOT NULL,
    pos_max_fila    INTEGER,
    pos_max_col     INTEGER,
    ref_fila        INTEGER,
    ref_col         INTEGER,
    notas           TEXT DEFAULT '',
    img_visible     TEXT,
    img_termica     TEXT,
    img_fusion      TEXT,
    img_miniatura   TEXT,
    img_mini_termica TEXT,
    matriz          BLOB NOT NULL,
    simulacion      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_insp_tablero ON inspecciones(tablero_id, fecha);
"""

_CAMPOS_LISTA = """i.id, i.fecha, i.t_max, i.t_min, i.t_media, i.t_referencia, i.modo_referencia,
    i.delta_t, i.nivel, i.clasificacion, i.emisividad, i.pos_max_fila, i.pos_max_col,
    i.ref_fila, i.ref_col, i.notas, i.img_visible, i.img_termica, i.img_fusion,
    i.img_miniatura, i.img_mini_termica, i.simulacion, t.codigo AS tablero, t.ubicacion"""


class BaseDatos:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._bloqueo_escritura = threading.Lock()
        with self._conectar() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(ESQUEMA)

    @contextmanager
    def _conectar(self):
        con = sqlite3.connect(self.ruta, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # --------------------------------------------------------------- tableros
    def obtener_o_crear_tablero(self, codigo: str, ubicacion: str = "") -> int:
        codigo = codigo.strip()
        if not codigo:
            raise ValueError("El nombre/ID del tablero es obligatorio")
        with self._bloqueo_escritura, self._conectar() as con:
            fila = con.execute("SELECT id, ubicacion FROM tableros WHERE codigo = ?", (codigo,)).fetchone()
            if fila:
                if ubicacion and ubicacion != fila["ubicacion"]:
                    con.execute("UPDATE tableros SET ubicacion = ? WHERE id = ?", (ubicacion, fila["id"]))
                return int(fila["id"])
            cur = con.execute("INSERT INTO tableros (codigo, ubicacion, creado) VALUES (?, ?, ?)",
                              (codigo, ubicacion, datetime.now().isoformat(timespec="seconds")))
            return int(cur.lastrowid)

    def listar_tableros(self) -> list[dict]:
        with self._conectar() as con:
            filas = con.execute("""
                SELECT t.codigo, t.ubicacion, COUNT(i.id) AS inspecciones,
                       MAX(i.fecha) AS ultima, MAX(i.nivel) AS peor_nivel
                FROM tableros t LEFT JOIN inspecciones i ON i.tablero_id = t.id
                GROUP BY t.id ORDER BY t.codigo""").fetchall()
        return [dict(f) for f in filas]

    # ----------------------------------------------------------- inspecciones
    def insertar_inspeccion(self, tablero: str, analisis: dict, matriz: np.ndarray,
                            emisividad: float, notas: str = "", ubicacion: str = "",
                            simulacion: bool = False, fecha: str | None = None) -> int:
        tablero_id = self.obtener_o_crear_tablero(tablero, ubicacion)
        ref = analisis.get("punto_referencia") or [None, None]
        clasif = analisis["clasificacion"]
        with self._bloqueo_escritura, self._conectar() as con:
            cur = con.execute("""
                INSERT INTO inspecciones (tablero_id, fecha, t_max, t_min, t_media, t_referencia,
                    modo_referencia, delta_t, nivel, clasificacion, emisividad, pos_max_fila,
                    pos_max_col, ref_fila, ref_col, notas, matriz, simulacion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                tablero_id, fecha or datetime.now().isoformat(timespec="seconds"),
                analisis["t_max"], analisis["t_min"], analisis["t_media"], analisis["t_referencia"],
                analisis["modo_referencia"], analisis["delta_t"], clasif["nivel"], clasif["etiqueta"],
                emisividad, analisis["pos_max"][0], analisis["pos_max"][1], ref[0], ref[1],
                notas.strip(), np.asarray(matriz, np.float32).tobytes(), int(simulacion)))
            return int(cur.lastrowid)

    def actualizar_imagenes(self, id_insp: int, **rutas: str) -> None:
        permitidas = {"img_visible", "img_termica", "img_fusion", "img_miniatura", "img_mini_termica"}
        rutas = {k: v for k, v in rutas.items() if k in permitidas}
        if not rutas:
            return
        asignaciones = ", ".join(f"{k} = ?" for k in rutas)
        with self._bloqueo_escritura, self._conectar() as con:
            con.execute(f"UPDATE inspecciones SET {asignaciones} WHERE id = ?", (*rutas.values(), id_insp))

    def obtener_inspeccion(self, id_insp: int, con_matriz: bool = False) -> dict | None:
        with self._conectar() as con:
            fila = con.execute(f"""SELECT {_CAMPOS_LISTA}, i.matriz FROM inspecciones i
                                   JOIN tableros t ON t.id = i.tablero_id WHERE i.id = ?""",
                               (id_insp,)).fetchone()
        if fila is None:
            return None
        d = dict(fila)
        blob = d.pop("matriz")
        if con_matriz:
            d["matriz"] = np.frombuffer(blob, dtype=np.float32).reshape(24, 32).copy()
        return d

    def listar_inspecciones(self, tablero: str | None = None, nivel: int | None = None,
                            desde: str | None = None, hasta: str | None = None,
                            limite: int = 500) -> list[dict]:
        condiciones, params = [], []
        if tablero:
            condiciones.append("t.codigo = ?")
            params.append(tablero)
        if nivel is not None:
            condiciones.append("i.nivel = ?")
            params.append(int(nivel))
        if desde:
            condiciones.append("i.fecha >= ?")
            params.append(desde)
        if hasta:
            condiciones.append("i.fecha <= ?")
            params.append(hasta + "T23:59:59" if len(hasta) == 10 else hasta)
        where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
        with self._conectar() as con:
            filas = con.execute(f"""SELECT {_CAMPOS_LISTA} FROM inspecciones i
                                    JOIN tableros t ON t.id = i.tablero_id {where}
                                    ORDER BY i.fecha DESC, i.id DESC LIMIT ?""",
                                (*params, int(limite))).fetchall()
        return [dict(f) for f in filas]

    def tendencia(self, tablero: str | None = None) -> dict[str, list[dict]]:
        """Serie temporal de T máx y ΔT por tablero (orden cronológico)."""
        condicion, params = ("WHERE t.codigo = ?", (tablero,)) if tablero else ("", ())
        with self._conectar() as con:
            filas = con.execute(f"""SELECT t.codigo, i.id, i.fecha, i.t_max, i.delta_t, i.nivel
                                    FROM inspecciones i JOIN tableros t ON t.id = i.tablero_id
                                    {condicion} ORDER BY i.fecha, i.id""", params).fetchall()
        series: dict[str, list[dict]] = {}
        for f in filas:
            series.setdefault(f["codigo"], []).append(
                {"id": f["id"], "fecha": f["fecha"], "t_max": f["t_max"],
                 "delta_t": f["delta_t"], "nivel": f["nivel"]})
        return series

    def eliminar_inspeccion(self, id_insp: int) -> dict | None:
        d = self.obtener_inspeccion(id_insp)
        if d is None:
            return None
        with self._bloqueo_escritura, self._conectar() as con:
            con.execute("DELETE FROM inspecciones WHERE id = ?", (id_insp,))
            # Elimina tableros que se quedaron sin inspecciones
            con.execute("""DELETE FROM tableros WHERE id NOT IN
                           (SELECT DISTINCT tablero_id FROM inspecciones)""")
        return d
