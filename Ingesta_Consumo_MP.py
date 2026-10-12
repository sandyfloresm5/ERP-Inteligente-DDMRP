"""
Ingesta_Consumo_MP.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP: consumo diario de MP (histórico ADU)
"""

import sys
from pathlib import Path
from datetime import date

import pandas as pd
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import (
    DB_PATH,
    EXCEL_MP_CONSUMOS_1, SHEET_MP_CONSUMOS_1,
    EXCEL_MP_CONSUMOS_2, SHEET_MP_CONSUMOS_2,
)

HOY = date.today().isoformat()


def _col(df, nombre_esperado):
    def _limpiar(s):
        return " ".join(str(s).strip().lower().split())
    mapa = {_limpiar(c): c for c in df.columns}
    real = mapa.get(_limpiar(nombre_esperado))
    if real is None:
        raise KeyError(f"No se encontró '{nombre_esperado}'. "
                       f"Columnas disponibles: {list(df.columns)}")
    return real


MAX_FILAS_BUSCAR_HEADER = 40


def _norm(s):
    return " ".join(str(s).strip().lower().split()) if pd.notna(s) else ""


def cargar_consumo(ruta: Path, hoja: str, etiqueta: str) -> pd.DataFrame:
    print(f"  {Path(ruta).name} / hoja '{hoja}' ({etiqueta})")

    crudo = pd.read_excel(ruta, sheet_name=hoja, header=None, nrows=MAX_FILAS_BUSCAR_HEADER)
    claves = ["material", "fecha contabiliz", "consumos"]
    fila_header = None
    for i in range(len(crudo)):
        celdas = [_norm(v) for v in crudo.iloc[i].tolist() if _norm(v)]
        if sum(1 for k in claves if any(k in c for c in celdas)) >= 2:
            fila_header = i
            break
    if fila_header is None:
        raise KeyError(f"No se encontró fila de encabezado con {claves} "
                       f"en las primeras {MAX_FILAS_BUSCAR_HEADER} filas de '{hoja}'.")
    if fila_header > 0:
        print(f"    Encabezados detectados en la fila {fila_header + 1} del Excel")

    df = pd.read_excel(ruta, sheet_name=hoja, header=fila_header)
    df.columns = [str(c).strip() for c in df.columns]

    c_material = _col(df, "Material")
    c_fecha = _col(df, "Fecha contabiliz.")
    c_consumo = _col(df, "Consumos [TM]")

    out = pd.DataFrame()
    out["sku_mp"] = df[c_material].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out["fecha_contabilizacion"] = pd.to_datetime(df[c_fecha], errors="coerce", dayfirst=True)
    out["consumo_tm"] = pd.to_numeric(df[c_consumo], errors="coerce")

    n_antes = len(out)
    out = out.dropna(subset=["sku_mp", "fecha_contabilizacion", "consumo_tm"])
    n_desc = n_antes - len(out)
    if n_desc:
        print(f"    {n_desc} filas descartadas (sin material, fecha o consumo válido)")

    print(f"    {len(out)} filas válidas · rango "
          f"{out['fecha_contabilizacion'].min().date()} a {out['fecha_contabilizacion'].max().date()}")
    return out


def main():
    print("=== Consumo de MP (histórico + en curso) ===")

    partes = []
    try:
        h1 = cargar_consumo(EXCEL_MP_CONSUMOS_1, SHEET_MP_CONSUMOS_1, "histórico 2025")
        h1["fuente"] = "historico"
        partes.append(h1)
    except Exception as e:
        print(f"  [ERROR] Archivo histórico: {type(e).__name__}: {e}")

    try:
        h2 = cargar_consumo(EXCEL_MP_CONSUMOS_2, SHEET_MP_CONSUMOS_2, "en curso 2026")
        h2["fuente"] = "en_curso"
        partes.append(h2)
    except Exception as e:
        print(f"  [ERROR] Archivo en curso: {type(e).__name__}: {e}")

    if not partes:
        print("\n[ERROR] Ningún archivo se pudo leer -- no se actualiza consumo_mp.")
        return

    consumo = pd.concat(partes, ignore_index=True)
    n_antes_dedup = len(consumo)

    # Paso 1: dentro de CADA archivo, sumar transacciones legítimas del
    # mismo material el mismo día (esto es lo que hacía el groupby original,
    # pero ahora separado por fuente para no mezclar histórico con en_curso).
    consumo = (consumo.groupby(["sku_mp", "fecha_contabilizacion", "fuente"], as_index=False)
                    .agg(consumo_tm=("consumo_tm", "sum")))

    # Paso 2: ENTRE archivos, prioridad a "en_curso" (más actualizado).
    # Si el mismo (material, fecha) aparece en ambos, se descarta el
    # histórico -- NO se suman, porque es el mismo consumo reportado dos veces.
    consumo["_prioridad"] = consumo["fuente"].map({"en_curso": 1, "historico": 0})
    consumo = (consumo.sort_values("_prioridad", ascending=False)
                    .drop_duplicates(subset=["sku_mp", "fecha_contabilizacion"], keep="first")
                    .drop(columns=["_prioridad", "fuente"]))

    dup = n_antes_dedup - len(consumo)
    if dup:
        print(f"\n {dup} filas se combinaron: transacciones del mismo día dentro de un "
            f"archivo se sumaron; choques entre archivos se resolvieron a favor del "
            f"más actualizado (sin sumar).")

    consumo["fecha_contabilizacion"] = consumo["fecha_contabilizacion"].dt.strftime("%Y-%m-%d")
    consumo["fecha_ingesta"] = HOY



    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS consumo_mp")
    consumo.to_sql("consumo_mp", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()

    print(f"\n[OK] consumo_mp: {len(consumo)} filas · {consumo['sku_mp'].nunique()} MP distintas")
    print(f"  Rango total: {consumo['fecha_contabilizacion'].min()} a "
          f"{consumo['fecha_contabilizacion'].max()}")


if __name__ == "__main__":
    main()