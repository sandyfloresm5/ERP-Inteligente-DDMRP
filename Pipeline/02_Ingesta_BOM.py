"""
Módulo 1 - Ingesta de BOM (explosión Producto Terminado -> Materia Prima)
Cargill Ecuador - Aquanutrition
"""

import pandas as pd
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import EXCEL_BOM, SHEET_BOM, DB_PATH

COLUMN_MAP = {
    "CENTRO": "centro",
    "CODIGO NIS": "codigo_nis",
    "CODIGO SAP": "sku_pt",
    "CANTIDAD": "cantidad_pt",
    "FECHA PLANEADA": "fecha_planeada",
    "pt_centro": "pt_centro",
    "MP": "sku_mp",
    "%": "pct_formula",
    "DEMANDA": "demanda_mp",
    "DESCRIPCION": "mp_descripcion",
}


def load_and_clean(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype={
        "CODIGO SAP": str, "MP": str, "CODIGO NIS": str
    })
    df = df.rename(columns=COLUMN_MAP)

    df["fecha_planeada"] = pd.to_datetime(
        df["fecha_planeada"], format="%d/%m/%Y", errors="coerce", dayfirst=True
    )

    df["sku_pt"] = df["sku_pt"].astype(str).str.strip()
    df["sku_mp"] = df["sku_mp"].astype(str).str.strip()

    if df["pct_formula"].dtype == object:
        df["pct_formula"] = (
            df["pct_formula"].astype(str).str.replace("%", "", regex=False).astype(float) / 100
        )
    else:
        if df["pct_formula"].max() > 1:
            df["pct_formula"] = df["pct_formula"] / 100

    for col in ["cantidad_pt", "demanda_mp"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["demanda_calculada"] = df["cantidad_pt"] * df["pct_formula"]
    df["delta_validacion"] = (df["demanda_mp"] - df["demanda_calculada"]).abs()
    inconsistentes = (df["delta_validacion"] > 0.5).sum()
    if inconsistentes:
        print(f"[WARN] {inconsistentes} filas donde DEMANDA no cuadra con CANTIDAD*% (tolerancia 0.5 TM)")

    return df


def save_to_sqlite(df: pd.DataFrame, db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    df.to_sql("bom", conn, if_exists="replace", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bom_pt ON bom(sku_pt)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bom_mp ON bom(sku_mp)")
    conn.commit()
    conn.close()
    print(f"[OK] BOM cargado en {db_path}")


def main():
    print("Cargando BOM...")
    df = load_and_clean(EXCEL_BOM, SHEET_BOM)
    save_to_sqlite(df, DB_PATH)
    print(f"Combinaciones PT-MP únicas: {df[['sku_pt','sku_mp']].drop_duplicates().shape[0]}")
    print(f"SKUs PT en BOM: {df['sku_pt'].nunique()} | SKUs MP en BOM: {df['sku_mp'].nunique()}")


if __name__ == "__main__":
    main()
