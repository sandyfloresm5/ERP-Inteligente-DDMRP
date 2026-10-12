"""
Ingesta_LeadTime_MP.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP

Lee el Excel de lead time de materias primas (EXCEL_MP_LEAD_TIME_MP,
hoja SHEET_MP_LEAD_TIME) y crea la tabla 'lead_time_mp' en simulador.db.

--- POR QUÉ VALIDACIÓN EXPLÍCITA DE COLUMNAS ---
No se conoce con certeza el nombre exacto de las columnas en el Excel
real. En vez de asumir y fallar en silencio (o peor, cruzar mal por
un typo), el script imprime las columnas encontradas y aborta con un
mensaje claro si no puede mapear alguna requerida -- mismo principio
que el resto del pipeline: nunca inventar, nunca ocultar una falla.

Columnas esperadas en el Excel (ajustar MAPEO_COLUMNAS abajo si el
nombre real difiere):
  - sku_mp : código de la materia prima
  - mp_descripcion : descripción de la MP
  - lead_time_total_dias: lead time del proveedor, en días
"""

import sys
import sqlite3
from pathlib import Path
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH, EXCEL_MP_LEAD_TIME_MP, SHEET_MP_LEAD_TIME

# --- Mapeo de columnas: nombre_en_excel -> nombre_estandar_en_db ---
# AJUSTAR ESTO si al correr el script salen columnas distintas a las
# que aparecen aquí a la izquierda. El script imprime las columnas
# reales del Excel al inicio para facilitar el ajuste.
# Match flexible: se normaliza (mayúsculas, sin saltos de línea/espacios
# múltiples) para no depender de si el Excel tiene \n, espacio, o nada
# entre "OPERACIÓN" y "[Días]".
# LEAD TIME OPERACIÓN = proveedor -> bodega (externo, real lead time de
# reposición). LEAD TIME PLANTA = bodega -> planta (interno, NO es lead
# time de proveedor) -- no se usa para el buffer DDMRP.
PATRONES_COLUMNAS = {
    "sku_mp": ["CÓDIGO SAP", "CODIGO SAP"],
    "mp_descripcion": ["DESCRIPCION", "DESCRIPCIÓN"],
    "lead_time_total_dias": ["LEAD TIME OPERACION", "LEAD TIME OPERACIÓN"],
}

COLUMNAS_REQUERIDAS_DB = {"sku_mp", "mp_descripcion", "lead_time_total_dias"}


def _normalizar(texto: str) -> str:
    return " ".join(str(texto).upper().replace("\n", " ").split())


def cargar_excel() -> pd.DataFrame:
    if not EXCEL_MP_LEAD_TIME_MP.exists():
        print(f"[ERROR] No se encuentra el archivo: {EXCEL_MP_LEAD_TIME_MP}")
        sys.exit(1)

    df = pd.read_excel(EXCEL_MP_LEAD_TIME_MP, sheet_name=SHEET_MP_LEAD_TIME)
    print(f"[OK] Excel leído: {len(df)} filas, hoja '{SHEET_MP_LEAD_TIME}'")
    return df


def mapear_columnas(df: pd.DataFrame) -> pd.DataFrame:
    normalizadas = {_normalizar(c): c for c in df.columns}

    mapeo_aplicable = {}
    for col_db, patrones in PATRONES_COLUMNAS.items():
        encontrada = None
        for patron in patrones:
            for norm, original in normalizadas.items():
                if norm.startswith(_normalizar(patron)):
                    encontrada = original
                    break
            if encontrada:
                break
        if encontrada:
            mapeo_aplicable[encontrada] = col_db

    faltantes = COLUMNAS_REQUERIDAS_DB - set(mapeo_aplicable.values())
    if faltantes:
        print(f"\n[ERROR] No se pudo mapear estas columnas requeridas: {faltantes}")
        print("Columnas reales en el Excel:", list(df.columns))
        print("\nAjusta PATRONES_COLUMNAS en este script con los nombres exactos "
              "de arriba y vuelve a correr.")
        sys.exit(1)

    print(f"Mapeo aplicado: {mapeo_aplicable}")
    df = df.rename(columns=mapeo_aplicable)
    df = df[list(COLUMNAS_REQUERIDAS_DB)]
    return df


def limpiar(df: pd.DataFrame) -> pd.DataFrame:
    # sku_mp como texto, sin espacios -- causa más común de mismatch
    # en el merge contra 'bom' (ceros a la izquierda, espacios, tipo).
    df["sku_mp"] = df["sku_mp"].astype(str).str.strip()
    df["mp_descripcion"] = df["mp_descripcion"].astype(str).str.strip()
    df["lead_time_total_dias"] = pd.to_numeric(
        df["lead_time_total_dias"], errors="coerce"
    )

    n_lt_invalido = df["lead_time_total_dias"].isna().sum()
    if n_lt_invalido:
        print(f"[WARN] {n_lt_invalido} filas con lead time no numérico -- quedan NaN, "
              f"no se descartan (para no perder el sku_mp de la tabla).")

    n_dup = df["sku_mp"].duplicated().sum()
    if n_dup:
        print(f"[WARN] {n_dup} sku_mp duplicados en el Excel -- se conserva la última "
              f"aparición. Revisar el Excel si esto no es esperado.")
        df = df.drop_duplicates(subset="sku_mp", keep="last")

    return df


def main():
    print("=== Ingesta Lead Time MP ===")
    df = cargar_excel()
    df = mapear_columnas(df)
    df = limpiar(df)

    conn = sqlite3.connect(DB_PATH)
    df.to_sql("lead_time_mp", conn, if_exists="replace", index=False)
    conn.commit()

    print(f"\n[OK] {len(df)} materias primas guardadas en tabla 'lead_time_mp'")
    print(f"Lead time (días) -- min: {df['lead_time_total_dias'].min():.0f}, "
          f"promedio: {df['lead_time_total_dias'].mean():.1f}, "
          f"max: {df['lead_time_total_dias'].max():.0f}")

    conn.close()


if __name__ == "__main__":
    main()