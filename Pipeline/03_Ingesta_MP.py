"""
Módulo 1 - Ingesta de Materia Prima (Stock + Consumo)
Cargill Ecuador - Aquanutrition
"""

import pandas as pd
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import EXCEL_MP_STOCK, EXCEL_MP_CONSUMOS, SHEET_MP_STOCK, SHEET_MP_CONSUMO, DB_PATH

STOCK_COLUMN_MAP = {
    "Año": "anio",
    "Mes":"mes",
    "Planta": "planta",
    "Centro": "centro",
    "Localidad": "localidad",
    "Bodegas": "bodega",
    "Segmento": "segmento",
    "Origen": "origen",
    "Status": "status",
    "Material": "sku_mp",
    "Descripción": "mp_descripcion",
    "Capacidad [TM]": "capacidad_tm",
    "Stock Total [TM]": "stock_tm",
}

CONSUMO_COLUMN_MAP = {
    "Planta": "planta",
    "Material": "sku_mp",
    "Descripción": "mp_descripcion",
    "Segmento": "segmento",
    "Ctd.en UM entrada": "cantidad_um_entrada",
    "Fecha contabiliz.": "fecha_contabilizacion",
    "Mes": "mes",
    "Consumos [TM]": "consumo_tm",
}


def load_stock(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype={"Material": str})

    df.columns = (
        df.columns.astype(str)
        .str.replace("\n", " ", regex=False)
        .str.strip()
    )

    df = df.rename(columns=STOCK_COLUMN_MAP)
    df["sku_mp"] = df["sku_mp"].astype(str).str.strip()

    return df


def load_consumo(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype={"Material": str})

    df.columns = (
        df.columns.astype(str)
        .str.replace("\n", " ", regex=False)
        .str.strip()
    )

    df = df.rename(columns=CONSUMO_COLUMN_MAP)

    return df


def save_to_sqlite(df_stock: pd.DataFrame, df_consumo: pd.DataFrame, db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    df_stock.to_sql("stock_mp", conn, if_exists="replace", index=False)
    df_consumo.to_sql("consumo_mp", conn, if_exists="replace", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_mp ON stock_mp(sku_mp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_consumo_mp ON consumo_mp(sku_mp, fecha_contabilizacion)")
    conn.commit()
    conn.close()
    print(f"[OK] Stock y Consumo MP cargados en {db_path}")


def main():
    print("Cargando Stock MP (hoja 'Análisis MP')...")
    stock = load_stock(EXCEL_MP_STOCK, SHEET_MP_STOCK)

    print("Cargando Consumo MP (hoja 'Consumo MP')...")
    consumo = load_consumo(EXCEL_MP_CONSUMOS, SHEET_MP_CONSUMO)

    save_to_sqlite(stock, consumo, DB_PATH)

    print(f"SKUs MP en stock: {stock['sku_mp'].nunique()}")
    print(f"SKUs MP en consumo: {consumo['sku_mp'].nunique()}")
    print(f"Rango de fechas consumo: {consumo['fecha_contabilizacion'].min()} a {consumo['fecha_contabilizacion'].max()}")
    print("\n[PENDIENTE] Faltan por revisar las hojas: Inventario MP, RMB - MP, Cobertura Histórica")


if __name__ == "__main__":
    main()
