"""
Ingesta_Forecast_Comercial.py
Cargill Ecuador - Aquanutrition
"""

import sys
from pathlib import Path
from datetime import date

import pandas as pd
import numpy as np
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH, EXCEL_PT_FORECAST, SHEET_PT_FORECAST

HOY = date.today()

MESES_ES_INV = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

COLUMN_MAP = {
    "Año": "anio", "Mes": "mes", "Cod SAP": "sku",
    "SOLD TO": "codigo_cliente", "Cliente": "cliente",
    "Forecast [TM]": "forecast_tm",
}


def _parsear_mes(valor) -> int:
    """Admite mes numérico (1-12) o nombre en español."""
    if pd.isna(valor):
        return None
    try:
        n = int(valor)
        if 1 <= n <= 12:
            return n
    except (ValueError, TypeError):
        pass
    return MESES_ES_INV.get(str(valor).strip().lower())


def cargar_forecast_comercial() -> pd.DataFrame:
    crudo = pd.read_excel(EXCEL_PT_FORECAST, sheet_name=SHEET_PT_FORECAST, header=None)

    fila_header = None
    for i in range(min(40, len(crudo))):
        valores = [str(v).strip().lower() for v in crudo.iloc[i].tolist()]
        tiene_sap = any("cod sap" in v for v in valores)
        tiene_fcst = any("forecast" in v and "tm" in v for v in valores)
        if tiene_sap and tiene_fcst:
            fila_header = i
            break
    if fila_header is None:
        muestra = crudo.iloc[:15].to_string()
        raise ValueError("No se encontró la fila de encabezado ('Cod SAP' + "
                          f"'Forecast...TM') en las primeras 40 filas.\n"
                          f"Primeras 15 filas crudas:\n{muestra}")

    df = pd.read_excel(EXCEL_PT_FORECAST, sheet_name=SHEET_PT_FORECAST, header=fila_header,
                        dtype={"Cod SAP": str, "SOLD TO": str})
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

    faltantes = [c for c in COLUMN_MAP if c not in df.columns]
    if faltantes:
        raise ValueError(f"Columnas no encontradas en el forecast de Comercial: "
                          f"{faltantes}. Disponibles: {list(df.columns)}")

    df = df.rename(columns=COLUMN_MAP)
    df["sku"] = df["sku"].astype(str).str.strip()
    df["codigo_cliente"] = df["codigo_cliente"].astype(str).str.strip()
    df["cliente"] = df["cliente"].astype(str).str.strip()
    df["mes_num"] = df["mes"].apply(_parsear_mes)
    df["forecast_tm"] = pd.to_numeric(df["forecast_tm"], errors="coerce").fillna(0.0)

    n_mes_invalido = df["mes_num"].isna().sum()
    if n_mes_invalido:
        print(f"[AVISO] {n_mes_invalido} filas con mes no reconocido -- excluidas.")
    df = df.dropna(subset=["mes_num", "anio"])
    df["mes_num"] = df["mes_num"].astype(int)
    df["anio"] = df["anio"].astype(int)

    # mes_offset relativo al mes calendario actual (0 = mes en curso).
    df["mes_offset"] = ((df["anio"] - HOY.year) * 12 + (df["mes_num"] - HOY.month))
    df = df[df["mes_offset"] >= 0]  # Comercial no entrega meses ya cerrados

    return df


def construir_forecast_sku_cliente(df: pd.DataFrame) -> pd.DataFrame:
    """Nivel serie (sku__codigo_cliente), para detalle del frontend."""
    agg = (df.groupby(["sku", "codigo_cliente", "cliente", "mes_offset"], as_index=False)
             .agg(p50=("forecast_tm", "sum")))
    agg["serie_id"] = agg["sku"] + "__" + agg["codigo_cliente"]
    agg["p10"] = np.nan
    agg["p90"] = np.nan
    agg["metodo_ganador"] = "Comercial (Excel mensual)"
    agg["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    return agg


def construir_forecast_sku(df: pd.DataFrame) -> pd.DataFrame:
    """Nivel SKU agregado (todos los clientes) -- alimenta el buffer PT
    sin ningún cambio en calcular_buffer_ddmrp_dinamico.py."""
    agg = (df.groupby(["sku", "mes_offset"], as_index=False)
             .agg(p50=("forecast_tm", "sum")))
    agg["p10"] = np.nan
    agg["p90"] = np.nan
    agg["metodo_ganador"] = "Comercial (Excel mensual)"
    agg["rango_calibrado"] = np.nan
    agg["wape_holdout"] = np.nan
    agg["fva_holdout"] = np.nan
    # Sin WAPE/holdout propio (Comercial entrega la data, no un modelo
    # validado): se marca SIN_VALIDAR para que el buffer y el dashboard
    # sepan que este número no tiene error histórico medido detrás,
    # nada más -- no implica que falte hacer algo.
    agg["semaforo"] = "SIN_VALIDAR"
    return agg


def main():
    print(f"Cargando forecast de Comercial — {EXCEL_PT_FORECAST.name}")
    try:
        df = cargar_forecast_comercial()
    except FileNotFoundError:
        print(f"[ERROR] No se encontró {EXCEL_PT_FORECAST}. Verificar ruta en config.py.")
        return
    except ValueError as e:
        print(f"[ERROR] {e}")
        return

    if df.empty:
        print("[AVISO] Sin filas válidas en el forecast de Comercial.")
        return

    forecast_sku_cliente = construir_forecast_sku_cliente(df)
    forecast_sku = construir_forecast_sku(df)

    conn = sqlite3.connect(DB_PATH)
    forecast_sku.to_sql("forecast_sku_calibrado", conn, if_exists="replace", index=False)
    forecast_sku_cliente.to_sql("forecast_sku_cliente_comercial", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()

    n_meses = sorted(df["mes_offset"].unique())
    print(f"\n[OK] {forecast_sku['sku'].nunique()} SKU · "
          f"{forecast_sku_cliente['serie_id'].nunique()} series sku-cliente")
    print(f"  mes_offset disponibles: {n_meses}")
    print(f"  Tablas: forecast_sku_calibrado, forecast_sku_cliente_comercial")


if __name__ == "__main__":
    main()