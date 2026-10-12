"""
Ingesta_RMB_MP.py
Cargill Ecuador - Aquanutrition
Carga el RMB (forecast mensual de consumo de MP) -- equivalente al
forecast de Comercial para PT, pero para materia prima.
"""
import sys
from pathlib import Path
import pandas as pd
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))
from config import DB_PATH, EXCEL_RMB_MP, SHEET_RMB_MP

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}


def cargar_rmb_mp() -> pd.DataFrame:
    df = pd.read_excel(EXCEL_RMB_MP, sheet_name=SHEET_RMB_MP, header=2)
    df = df.rename(columns={
        "Código MP": "sku_mp", "Descipción": "mp_descripcion",
        "RMB\n[TM]": "rmb_tm", "Fecha": "fecha",
    })
    df["sku_mp"] = df["sku_mp"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["rmb_tm"] = pd.to_numeric(df["rmb_tm"], errors="coerce")
    n_sin_rmb = int(df["rmb_tm"].isna().sum())
    df = df.dropna(subset=["sku_mp", "rmb_tm"])
    df = df[df["sku_mp"] != "nan"]

    df["mes_num"] = df["Mes"].astype(str).str.strip().str.lower().map(MESES_ES)
    df["anio"] = pd.to_numeric(df["Año"], errors="coerce")
    n_sin_fecha = int((df["mes_num"].isna() | df["anio"].isna()).sum())
    df = df.dropna(subset=["mes_num", "anio"])
    df["mes_num"] = df["mes_num"].astype(int)
    df["anio"] = df["anio"].astype(int)

    # RMB puede venir desglosado por planta: se suma por CÓDIGO + año + mes.
    agregado = (df.groupby(["sku_mp", "anio", "mes_num"], as_index=False)
                  .agg(mp_descripcion=("mp_descripcion", "first"),
                       rmb_tm=("rmb_tm", "sum")))

    print(f"[OK] RMB MP: {len(agregado)} filas (MP x mes) · "
          f"{agregado['sku_mp'].nunique()} MP distintas")
    if n_sin_rmb:
        print(f"  [AVISO] {n_sin_rmb} filas sin RMB numérico -- descartadas, "
              f"no se inventa el dato.")
    if n_sin_fecha:
        print(f"  [AVISO] {n_sin_fecha} filas con mes o año no reconocido -- descartadas.")
    return agregado


def main():
    conn = sqlite3.connect(DB_PATH)
    df = cargar_rmb_mp()
    df.to_sql("rmb_mp", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()