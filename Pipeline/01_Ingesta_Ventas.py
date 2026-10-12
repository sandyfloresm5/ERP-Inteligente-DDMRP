"""
Módulo 1: Histórico de Ventas de PT
"""

import pandas as pd
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import EXCEL_VENTAS, SHEET_VENTAS, DB_PATH

# --------------------------------------------------
# Mapeo columnas
# --------------------------------------------------

COLUMN_MAP = {
    "Código SAP": "sku",
    "Descripción": "sku_descripcion",
    "Forma": "forma",
    "Etapa": "etapa",
    "Calibre": "calibre",
    "Plantas / Bodegas": "centro",
    "Centros": "centro_sap",
    "Segmento Cliente": "segmento_cliente",
    "Holding": "holding",
    "Código Cliente": "codigo_cliente",
    "Cliente": "cliente",
    "Fecha": "fecha",
    "Ventas [TM]": "ventas_tm",
    "Precio Unitario [$/Sacos]": "precio_sacos",
    "Precio Unitario [$/TM]": "precio_tm",
    "Costo Unitario [$/TM]": "costo_tm",
    "Año": "anio",
    "Mes": "mes",
}

# Alias tolerados para la columna de centro: el encabezado cambia entre

ALIAS_CENTRO = ["Plantas / Bodegas", "Plantas / Bodegas", "Plantas / Bodegas", "Planta / Bodegas", "planta / bodegas"]

def load_raw(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(
        excel_path,
        sheet_name=sheet_name,
        dtype={"Código SAP": str, "Descripción": str, "Código Cliente": str,
               "Holding": str},
    )

    df.columns = (
        df.columns.astype(str)
        .str.replace("\n", " ", regex=False)
        .str.strip()
    )

    df = df.rename(columns=COLUMN_MAP)

    # Si el encabezado del centro vino con otro nombre, se normaliza acá.
    if "centro" not in df.columns:
        for alias in ALIAS_CENTRO:
            if alias in df.columns:
                df = df.rename(columns={alias: "centro"})
                break

    print("Columnas cargadas:")
    print(df.columns.tolist())

    if "centro" not in df.columns:
        print("[WARN] No se encontró la columna de centro. El buffer no se va a "
              "poder partir por planta y el ADU quedará agregado a nivel nacional.")

    return df

def clean_ventas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce", dayfirst=True)

    n_bad_dates = df["fecha"].isna().sum()
    if n_bad_dates:
        print(f"[WARN] {n_bad_dates} filas con fechas inválidas.")
        # Se exportan en vez de descartarlas en silencio: sin verlas no se
        # puede distinguir basura real (celda vacía) de un problema de
        # formato en el origen que convendría corregir.
        ruta_invalidas = Path(__file__).resolve().parent / "fechas_invalidas_ventas.csv"
        df[df["fecha"].isna()].to_csv(ruta_invalidas, index=False)
        print(f"[OK] Filas con fecha inválida exportadas a: {ruta_invalidas}")

    df = df.dropna(subset=["fecha"])

    df["sku"] = df["sku"].astype(str).str.strip()
    df["sku_descripcion"] = df["sku_descripcion"].astype(str).str.strip()
    df["codigo_cliente"] = df["codigo_cliente"].astype(str).str.strip()
    df["holding"] = df["holding"].astype(str).str.strip()

    if "centro" in df.columns:
        df["centro"] = df["centro"].astype(str).str.strip()
        # Un centro vacío no se puede asignar a ninguna planta. Se marca en
        # vez de imputarlo: inventar el centro repartiría demanda a una
        # planta que quizá nunca produjo ese SKU.
        df.loc[df["centro"].isin(["", "nan", "None"]), "centro"] = "SIN_CENTRO"
        n_sin = int((df["centro"] == "SIN_CENTRO").sum())
        if n_sin:
            print(f"[WARN] {n_sin} filas sin centro asignado -> 'SIN_CENTRO'. "
                  f"No entran en el reparto por planta.")
    else:
        df["centro"] = "SIN_CENTRO"

    for col in ["ventas_tm", "precio_sacos", "precio_tm", "costo_tm"]:
        if df[col].dtype == object:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
            )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["es_devolucion"] = df["ventas_tm"] < 0
    df["serie_id"] = df["sku"] + "__" + df["codigo_cliente"]
    return df

def build_demanda_series(df: pd.DataFrame) -> pd.DataFrame:
    """
    Serie de demanda mensual por serie_id.

    --- demanda BRUTA, no neta de devoluciones ---
    Un pedido de 5 TM con devolución de 2 TM es demanda de 5 TM: el
    forecast debe predecir cuánto pide el cliente, no cuánto queda después
    de la logística de devoluciones. Las filas de devolución quedan en
    ventas_raw para análisis aparte.

    --- NUEVO: se conserva el centro ---
    El centro se arrastra a la serie para poder calcular la participación
    de cada planta en la demanda del SKU. Se agrega POR CENTRO, no se
    colapsa: un mismo SKU-cliente puede despacharse desde dos plantas y
    esas son dos filas distintas.
    """
    demanda_bruta = df[df["ventas_tm"] > 0].copy()

    n_filas_devolucion = (~(df["ventas_tm"] > 0)).sum()
    if n_filas_devolucion:
        print(f"[INFO] {n_filas_devolucion} filas de devolución/ventas<=0 excluidas "
              f"de la serie de demanda (demanda bruta, no neta).")

    demanda = (
        demanda_bruta.groupby(
            [
                "serie_id", "sku", "sku_descripcion", "codigo_cliente",
                "cliente", "segmento_cliente", "holding", "centro", "fecha",
            ],
            as_index=False,
        )
        .agg(
            ventas_tm=("ventas_tm", "sum"),
            costos_tm=("costo_tm", "mean"),
        )
    )
    return demanda

def compute_weighted_price(df: pd.DataFrame, level: list) -> pd.DataFrame:
    base = df[df["ventas_tm"] > 0].copy()
    base["_num"] = base["precio_tm"] * base["ventas_tm"]

    grouped = (
        base.groupby(level, as_index=False)
        .agg(_num_sum=("_num", "sum"), ventas_tm_sum=("ventas_tm", "sum"))
    )

    grouped["precio_ponderado_tm"] = grouped["_num_sum"] / grouped["ventas_tm_sum"]

    return grouped.drop(columns=["_num_sum"])

def compute_participacion_centro(df: pd.DataFrame, meses_ventana: int = 6) -> pd.DataFrame:
    """
    Participación de cada centro en la demanda de un SKU.

    POR QUÉ EXISTE
    --------------
    El forecast está a nivel SKU nacional. El buffer DDMRP necesita el ADU
    por planta para que sea comparable con la capacidad de esa planta. En
    vez de re-pronosticar por SKU-centro (partir la señal en más pedazos,
    cada uno con menos historia), se reparte el forecast del SKU según la
    participación observada de cada planta.

    Es el mismo criterio que la reconciliación por cliente: el nivel
    agregado tiene el error validado, el detalle define el reparto.

    VENTANA CORTA A PROPÓSITO
    -------------------------
    Se usan los últimos meses, no toda la historia: la asignación de un SKU
    a una planta cambia cuando se mueve producción, y una ventana larga
    arrastra plantas que ya no lo hacen.

    ESTABILIDAD
    -----------
    Se reporta la desviación de la participación mes a mes. Un SKU que va
    90/10 en un mes y 30/70 en el siguiente no tiene un reparto confiable, y
    el buffer derivado de él tampoco. Eso queda marcado, no escondido.
    """
    base = df[df["ventas_tm"] > 0].copy()
    if base.empty or "centro" not in base.columns:
        return pd.DataFrame()

    base["periodo"] = base["fecha"].dt.to_period("M")
    ultimo = base["periodo"].max()
    desde = ultimo - (meses_ventana - 1)
    ventana = base[base["periodo"] >= desde]

    print(f"\n--- Participación por centro (ventana: {desde} a {ultimo}) ---")

    # Participación acumulada en la ventana
    por_sku_centro = (ventana.groupby(["sku", "centro"], as_index=False)["ventas_tm"]
                      .sum().rename(columns={"ventas_tm": "tm_centro"}))
    por_sku = (ventana.groupby("sku", as_index=False)["ventas_tm"]
               .sum().rename(columns={"ventas_tm": "tm_sku"}))

    part = por_sku_centro.merge(por_sku, on="sku")
    part["participacion"] = part["tm_centro"] / part["tm_sku"].replace(0, pd.NA)

    # Estabilidad: desviación de la participación entre meses
    mensual = (ventana.groupby(["sku", "centro", "periodo"], as_index=False)["ventas_tm"]
               .sum())
    total_mes = (ventana.groupby(["sku", "periodo"], as_index=False)["ventas_tm"]
                 .sum().rename(columns={"ventas_tm": "tm_mes"}))
    mensual = mensual.merge(total_mes, on=["sku", "periodo"])
    mensual["part_mes"] = mensual["ventas_tm"] / mensual["tm_mes"].replace(0, pd.NA)

    estab = (mensual.groupby(["sku", "centro"])["part_mes"]
             .agg(desv_participacion="std", meses_con_venta="count")
             .reset_index())

    part = part.merge(estab, on=["sku", "centro"], how="left")
    part["reparto_estable"] = part["desv_participacion"].fillna(0) <= 0.15

    n_sku = part["sku"].nunique()
    n_multi = part.groupby("sku")["centro"].nunique()
    n_multi = int((n_multi > 1).sum())
    inestables = part[(~part["reparto_estable"]) & (part["participacion"] > 0.10)]

    print(f"  {n_sku} SKU con participación calculable")
    print(f"  {n_multi} SKU se producen en más de un centro")
    if len(inestables):
        print(f"  ⚠️  {inestables['sku'].nunique()} SKU con reparto INESTABLE entre "
              f"meses (desviación > 0.15).")
        print(f"      Su buffer por planta va a ser poco confiable -- revisar si "
              f"hubo movimiento de producción entre centros.")

    return part[["sku", "centro", "tm_centro", "tm_sku", "participacion",
                 "desv_participacion", "meses_con_venta", "reparto_estable"]]

def save_to_sqlite(df_raw, df_series, df_precio_sku, df_precio_sku_cliente,
                   df_participacion, db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)

    for t in ["ventas_raw", "ventas_demanda", "precio_ponderado_sku",
              "precio_ponderado_sku_cliente", "participacion_centro"]:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()

    df_raw.to_sql("ventas_raw", conn, index=False)
    df_series.to_sql("ventas_demanda", conn, index=False)
    df_precio_sku.to_sql("precio_ponderado_sku", conn, index=False)
    df_precio_sku_cliente.to_sql("precio_ponderado_sku_cliente", conn, index=False)
    if not df_participacion.empty:
        df_participacion.to_sql("participacion_centro", conn, index=False)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_demanda_serie
        ON ventas_demanda(serie_id, fecha)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_demanda_centro
        ON ventas_demanda(sku, centro, fecha)
    """)

    conn.commit()
    conn.close()

    print(f"[OK] Datos cargados en {db_path}")

def main():
    print("Cargando Histórico de Ventas...")

    raw = load_raw(EXCEL_VENTAS, SHEET_VENTAS)
    clean = clean_ventas(raw)
    series = build_demanda_series(clean)

    precio_sku = compute_weighted_price(clean, ["sku"])
    precio_sku_cliente = compute_weighted_price(clean, ["sku", "codigo_cliente"])
    participacion = compute_participacion_centro(clean)

    save_to_sqlite(clean, series, precio_sku, precio_sku_cliente,
                   participacion, DB_PATH)

    print(f"\nSeries únicas: {series['serie_id'].nunique()}")
    print(f"SKU únicos: {series['sku'].nunique()}")
    print(f"Rango fechas: {series['fecha'].min()} a {series['fecha'].max()}")

    if "centro" in series.columns:
        print(f"Centros: {sorted(series['centro'].unique())}")
        # Es el número que explica por qué el ADU nacional no era comparable
        # con la capacidad de una planta.
        por_centro = (series.groupby("centro")["ventas_tm"].sum()
                      .sort_values(ascending=False))
        print("\nVolumen histórico por centro (TM):")
        print(por_centro.round(0).to_string()) 

main()