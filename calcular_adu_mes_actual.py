"""
calcular_adu_mes_actual
Cargill Ecuador - Aquanutrition
"""

import os
import sqlite3
import calendar
from datetime import date, timedelta


import pandas as pd

from config import DB_PATH, EXCEL_PT, SHEET_PT

HOY = date.today()

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

MESES_ES_INV = {v: k for k, v in MESES_ES.items()}


def nombre_mes_es(fecha: date) -> str:
    return f"{MESES_ES[fecha.month]} {fecha.year}"


def dias_del_mes(anio: int, mes: int) -> int:
    return calendar.monthrange(anio, mes)[1]


def cargar_ventas_mes_actual_desde_excel() -> tuple:
    df = pd.read_excel(EXCEL_PT, sheet_name=SHEET_PT)
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

    col_sku = "Código"
    col_ventas_tm = "Ventas del Mes (TM)"
    col_anio = "Año"
    col_mes = "Mes"

    faltantes = [c for c in [col_sku, col_ventas_tm, col_anio, col_mes] if c not in df.columns]
    if faltantes:
        raise ValueError(f"Columnas no encontradas en {SHEET_PT}: {faltantes}. "
                         f"Columnas disponibles: {list(df.columns)}")

    df["_mes_num"] = df[col_mes].astype(str).str.strip().str.lower().map(MESES_ES_INV)
    df = df[(df[col_anio] == HOY.year) & (df["_mes_num"] == HOY.month)].copy()

    fecha_corte = HOY - pd.Timedelta(days=1)

    df[col_sku] = df[col_sku].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df[col_ventas_tm] = pd.to_numeric(df[col_ventas_tm], errors="coerce").fillna(0)

    agg = df.groupby(col_sku, as_index=False)[col_ventas_tm].sum()
    agg = agg.rename(columns={col_sku: "sku", col_ventas_tm: "venta_real_mtd"})
    return agg, fecha_corte


def calcular_adu_hibrido(ventas_mes: pd.DataFrame, forecast_sku: pd.DataFrame, fecha_corte: date) -> pd.DataFrame:
    df = forecast_sku.copy()
    df["sku"] = df["sku"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    mes0 = df[df["mes_offset"] == 0].copy()
    if mes0.empty:
        return pd.DataFrame()

    mes0 = mes0.merge(ventas_mes, on="sku", how="left")
    mes0["venta_real_mtd"] = mes0["venta_real_mtd"].fillna(0.0)

    dias_totales = dias_del_mes(HOY.year, HOY.month)
    dias_transcurridos = fecha_corte.day
    dias_restantes = max(dias_totales - dias_transcurridos, 0)

    mes0["adu_original_forecast"] = mes0["p50"] / dias_totales
    mes0["estimado_dias_restantes"] = mes0["adu_original_forecast"] * dias_restantes
    mes0["total_mes_hibrido"] = mes0["venta_real_mtd"] + mes0["estimado_dias_restantes"]
    mes0["adu_hibrido"] = mes0["total_mes_hibrido"] / dias_totales
    mes0["dias_transcurridos"] = dias_transcurridos
    mes0["dias_restantes"] = dias_restantes
    mes0["pct_mes_transcurrido"] = round(dias_transcurridos / dias_totales, 3)

    cols = ["sku", "adu_original_forecast", "venta_real_mtd", "dias_transcurridos",
            "dias_restantes", "adu_hibrido", "pct_mes_transcurrido"]
    return mes0[cols].round(3)


def main():
    conn = sqlite3.connect(DB_PATH)

    print(f"Leyendo ventas de {nombre_mes_es(HOY)} desde {EXCEL_PT} / hoja {SHEET_PT}...")
    try:
        ventas_mes, fecha_corte = cargar_ventas_mes_actual_desde_excel()
    except FileNotFoundError:
        print(f"[ERROR] No se encontró {EXCEL_PT}. Verificar ruta en config.py.")
        conn.close()
        return
    except ValueError as e:
        print(f"[ERROR] {e}")
        conn.close()
        return

    print(f"  {len(ventas_mes)} SKU con ventas registradas en {nombre_mes_es(HOY)}")
    print(f"  Corte de datos real: {fecha_corte}")

    dias_transcurridos_mes = fecha_corte.day
    ventas_completo = ventas_mes.copy()
    ventas_completo["dias_transcurridos"] = dias_transcurridos_mes
    ventas_completo.to_sql("ventas_mes_actual_completo", conn, if_exists="replace", index=False)
    print(f"  [OK] {len(ventas_completo)} SKU (universo completo) guardados en "
          f"'ventas_mes_actual_completo' -- TM total: "
          f"{ventas_completo['venta_real_mtd'].sum():,.1f}")

    forecast_sku = pd.read_sql(
        "SELECT sku, mes_offset, p50 FROM forecast_sku_calibrado WHERE mes_offset = 0",
        conn,
    )
    if forecast_sku.empty:
        print("[AVISO] Sin forecast_sku_calibrado para mes_offset=0.")
        conn.close()
        return

    adu_hibrido = calcular_adu_hibrido(ventas_mes, forecast_sku, fecha_corte)
    if adu_hibrido.empty:
        print("[AVISO] No se pudo calcular ADU híbrido.")
        conn.close()
        return

    adu_hibrido.to_sql("adu_mes_actual_hibrido", conn, if_exists="replace", index=False)
    conn.commit()

    dt = int(adu_hibrido["dias_transcurridos"].iloc[0])
    dtot = dias_del_mes(HOY.year, HOY.month)
    print(f"\n[OK] {len(adu_hibrido)} SKU con ADU híbrido "
          f"({dt} de {dtot} días de {nombre_mes_es(HOY)} transcurridos, "
          f"{dt/dtot:.0%} del mes ya real).")
    print(adu_hibrido.sort_values("venta_real_mtd", ascending=False).head(10).to_string(index=False))

    conn.close()

if __name__ == "__main__":
    main()