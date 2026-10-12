"""
migrar_config_lineas_produccion.py
Cargill Ecuador - Aquanutrition
"""
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))
from config import DB_PATH

# (bu, tipo_producto, forma, planta, linea, tamano_mm, horas_dia, rate_tm_hora)
# horas_dia y forma copiados literalmente de HORAS_DIA_LINEA_REAL /
# FORMA_POR_RECURSO en optimizar_produccion.py -- no se inventa nada.
SEED = [
    ("AQUA", "Pellet",   "AP", "DURAN",     "L1", None, 22.0, None),
    ("AQUA", "Extruído", "AX", "DURAN",     "L2", None, 20.0, None),
    ("AQUA", "Extruído", "AX", "DURAN",     "L3", None, 20.0, None),
    ("AQUA", "Extruído", "AX", "GUAYAQUIL", "L1", None, 20.0, None),
    ("AQUA", "Extruído", "AX", "GUAYAQUIL", "L3", None, 20.0, None),
    ("AQUA", "Extruído", "AP", "GUAYAQUIL", "L4", None, 15.0, None),
    ("AQUA", "Extruído", "AX", "GUAYAQUIL", "L5", None, 22.0, None),
    ("AQUA", "Extruído", "AX", "GUAYAQUIL", "L6", None, 22.0, None),
]

def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='config_lineas_produccion'"
        ).fetchone()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_lineas_produccion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bu TEXT NOT NULL,
                tipo_producto TEXT NOT NULL,
                forma TEXT NOT NULL,
                planta TEXT NOT NULL,
                linea TEXT NOT NULL,
                tamano_mm REAL,
                horas_dia REAL NOT NULL,
                rate_tm_hora REAL,
                activo INTEGER NOT NULL DEFAULT 1,
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)

        if existe:
            n = conn.execute("SELECT COUNT(*) FROM config_lineas_produccion").fetchone()[0]
            print(f"[OK] La tabla ya existía con {n} fila(s) -- no se modifica nada.")
            return

        conn.executemany(
            "INSERT INTO config_lineas_produccion "
            "(bu, tipo_producto, forma, planta, linea, tamano_mm, horas_dia, rate_tm_hora) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            SEED,
        )
        conn.commit()
        print(f"[OK] Tabla config_lineas_produccion creada con {len(SEED)} línea(s) "
              f"(las mismas que hoy están fijas en el código).")
        print("     Para agregar la planta/líneas nuevas: Configuración -> Líneas de "
              "producción en la app, una vez esté conectada al backend.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()