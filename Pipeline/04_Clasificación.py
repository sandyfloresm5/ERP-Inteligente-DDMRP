"""
Módulo 1 - Clasificación ABC y ADI/CV² por serie (SKU x Cliente) y por SKU
Cargill Ecuador - Aquanutrition
"""

import pandas as pd
import numpy as np
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH, ADI_UMBRAL, CV2_UMBRAL, MIN_OBS_PROPHET, MESES_MIN_HIST

MIN_PERIODOS_PARA_FORECAST = 3
MIN_PERIODOS_SKU = 6
UMBRAL_INACTIVIDAD_RECIENTE = 0.80

ANIO_ACTUAL = datetime.now().year  # dinámico, se evalúa en tiempo de ejecución


def load_demand(conn) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM ventas_demanda", conn, parse_dates=["fecha"])


def to_monthly_series(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["periodo"] = df["fecha"].dt.to_period("M")
    return (df.groupby(["serie_id", "sku", "codigo_cliente", "periodo"], as_index=False)
            .agg(ventas_tm=("ventas_tm", "sum")))


def _inactivo_periodo_actual(g: pd.DataFrame) -> bool:
    """True si la última venta con ventas_tm > 0 fue antes del año en
    curso, o si nunca hubo venta. 'g' debe tener columnas periodo/ventas_tm."""
    con_venta = g[g["ventas_tm"] > 0]
    if con_venta.empty:
        return True
    ultimo_anio = con_venta["periodo"].max().year
    return ultimo_anio < ANIO_ACTUAL


def compute_adi_cv2(monthly: pd.DataFrame) -> pd.DataFrame:
    results = []
    for serie_id, g in monthly.groupby("serie_id"):
        periodo_min, periodo_max = g["periodo"].min(), g["periodo"].max()
        total_periodos = (periodo_max - periodo_min).n + 1

        demanda_no_nula = g.loc[g["ventas_tm"] > 0, "ventas_tm"]
        n_no_nula = len(demanda_no_nula)
        if n_no_nula == 0:
            continue

        adi = total_periodos / n_no_nula
        if n_no_nula > 1 and demanda_no_nula.mean() > 0:
            cv2 = (demanda_no_nula.std(ddof=1) / demanda_no_nula.mean()) ** 2
        else:
            cv2 = np.nan

        results.append({
            "serie_id": serie_id,
            "sku": g["sku"].iloc[0],
            "codigo_cliente": g["codigo_cliente"].iloc[0],
            "total_periodos": total_periodos,
            "n_periodos_con_demanda": n_no_nula,
            "meses_historia": total_periodos,
            "adi": adi,
            "cv2": cv2,
            "demanda_media_no_nula": demanda_no_nula.mean(),
            "inactivo_periodo_actual": _inactivo_periodo_actual(g),
        })

    return pd.DataFrame(results)


def _cuadrante(adi, cv2) -> str:
    if pd.isna(cv2):
        return "insuficiente_data"
    if adi < ADI_UMBRAL and cv2 < CV2_UMBRAL:
        return "smooth"
    if adi < ADI_UMBRAL and cv2 >= CV2_UMBRAL:
        return "erratic"
    if adi >= ADI_UMBRAL and cv2 < CV2_UMBRAL:
        return "intermittent"
    return "lumpy"


def classify_demand_type(row):
    # Inactividad reciente pesa más que tener historia suficiente:
    # un SKU con 20 meses de historia pero sin vender desde el año
    # pasado no debe recibir un método de forecast estadístico.
    if row.get("inactivo_periodo_actual", False):
        return "descontinuado"
    if row["n_periodos_con_demanda"] <= MIN_PERIODOS_PARA_FORECAST:
        return "sin_historia"
    return _cuadrante(row["adi"], row["cv2"])


def compute_adi_cv2_sku(conn) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT serie_id, fecha, ventas_tm FROM ventas_demanda",
        conn, parse_dates=["fecha"],
    )
    if df.empty:
        return pd.DataFrame()

    df["sku"] = df["serie_id"].astype(str).str.split("__").str[0]
    df["periodo"] = df["fecha"].dt.to_period("M")
    agg = df.groupby(["sku", "periodo"], as_index=False)["ventas_tm"].sum()

    periodos = pd.period_range(agg["periodo"].min(), agg["periodo"].max(), freq="M")
    idx = pd.MultiIndex.from_product([agg["sku"].unique(), periodos],
                                     names=["sku", "periodo"])
    denso = (agg.set_index(["sku", "periodo"])["ventas_tm"]
             .reindex(idx, fill_value=0.0).reset_index()
             .sort_values(["sku", "periodo"]))

    total_periodos = len(periodos)
    mitad = total_periodos // 2

    filas = []
    for sku, g in denso.groupby("sku"):
        g = g.sort_values("periodo")
        no_nula = g.loc[g["ventas_tm"] > 0, "ventas_tm"]
        n_no_nula = len(no_nula)

        inactivo_actual = _inactivo_periodo_actual(g)

        if n_no_nula == 0:
            filas.append({
                "sku": sku, "total_periodos": total_periodos,
                "n_periodos_con_demanda": 0, "adi": np.nan, "cv2": np.nan,
                "demanda_media_no_nula": 0.0, "demanda_mensual_promedio": 0.0,
                "tipo_demanda": "sin_demanda",
                "pct_meses_recientes_sin_demanda": 1.0,
                "probable_descontinuado": True,
                "inactivo_periodo_actual": True,
            })
            continue

        adi = total_periodos / n_no_nula
        cv2 = ((no_nula.std(ddof=1) / no_nula.mean()) ** 2
               if n_no_nula > 1 and no_nula.mean() > 0 else np.nan)

        reciente = g.iloc[mitad:]
        pct_sin = float((reciente["ventas_tm"] <= 0).mean()) if len(reciente) else 0.0

        # Descontinuado si cae en CUALQUIERA de los dos criterios:
        # (a) >=80% de la ventana reciente sin demanda (criterio original), o
        # (b) no vendió nada en el año en curso (criterio nuevo, dinámico).
        descontinuado = pct_sin >= UMBRAL_INACTIVIDAD_RECIENTE or inactivo_actual

        if n_no_nula <= MIN_PERIODOS_SKU:
            tipo = "sin_historia"
        elif descontinuado:
            tipo = "descontinuado"
        else:
            tipo = _cuadrante(adi, cv2)

        filas.append({
            "sku": sku,
            "total_periodos": total_periodos,
            "n_periodos_con_demanda": n_no_nula,
            "adi": adi,
            "cv2": cv2,
            "demanda_media_no_nula": float(no_nula.mean()),
            "demanda_mensual_promedio": float(g["ventas_tm"].mean()),
            "tipo_demanda": tipo,
            "pct_meses_recientes_sin_demanda": round(pct_sin, 3),
            "probable_descontinuado": bool(descontinuado),
            "inactivo_periodo_actual": inactivo_actual,
        })

    return pd.DataFrame(filas)


def compute_abc(conn, series_excluidas: set) -> pd.DataFrame:
    ventas = pd.read_sql("SELECT * FROM ventas_raw", conn)

    for col in ["serie_id", "sku", "codigo_cliente", "ventas_tm", "costo_tm"]:
        if col not in ventas.columns:
            raise ValueError(f"No existe la columna '{col}' en ventas_raw")

    precios = pd.read_sql("SELECT * FROM precio_ponderado_sku_cliente", conn)
    if "precio_ponderado_tm" not in precios.columns:
        raise ValueError("No existe 'precio_ponderado_tm' en precio_ponderado_sku_cliente")

    ventas = ventas.merge(
        precios[["sku", "codigo_cliente", "precio_ponderado_tm"]],
        on=["sku", "codigo_cliente"], how="left",
    )

    ventas["ingreso"] = ventas["ventas_tm"] * ventas["precio_ponderado_tm"]
    ventas["costo"] = ventas["ventas_tm"] * ventas["costo_tm"]

    merged = (ventas.groupby(["serie_id", "sku", "codigo_cliente"], as_index=False)
              .agg(ventas_tm_total=("ventas_tm", "sum"),
                   ingreso_total=("ingreso", "sum"),
                   costo_total=("costo", "sum")))

    merged["rentabilidad_total"] = merged["ingreso_total"] - merged["costo_total"]
    merged["margen_unitario_tm"] = np.where(
        merged["ventas_tm_total"] > 0,
        merged["rentabilidad_total"] / merged["ventas_tm_total"],
        np.nan,
    )

    es_excluida = merged["serie_id"].isin(series_excluidas)
    rankeable = (merged.loc[~es_excluida].copy()
                 .sort_values("rentabilidad_total", ascending=False)
                 .reset_index(drop=True))
    no_rankeable = merged.loc[es_excluida].copy()
    no_rankeable["clase_abc"] = None

    total = rankeable["rentabilidad_total"].sum()
    if total <= 0:
        raise ValueError("La rentabilidad total (series con forecast) es <= 0. "
                         "Revisar precios y costos.")

    rankeable["rentabilidad_acumulada_pct"] = (
        rankeable["rentabilidad_total"].cumsum() / total
    )

    def abc_label(pct):
        if pct <= 0.80:
            return "A"
        if pct <= 0.95:
            return "B"
        return "C"

    rankeable["clase_abc"] = rankeable["rentabilidad_acumulada_pct"].apply(abc_label)

    return pd.concat([rankeable, no_rankeable], ignore_index=True, sort=False)


def main():
    conn = sqlite3.connect(DB_PATH)

    print(f"Año en curso (corte de inactividad): {ANIO_ACTUAL}")
    print("Calculando ADI/CV² por serie (sobre demanda bruta)...")
    monthly = to_monthly_series(load_demand(conn))
    adi_cv2 = compute_adi_cv2(monthly)
    adi_cv2["tipo_demanda"] = adi_cv2.apply(classify_demand_type, axis=1)
    adi_cv2["excluir_forecast"] = adi_cv2["tipo_demanda"].isin(["sin_historia", "descontinuado"])

    print("Calculando ABC (sobre venta neta, solo series con forecast)...")
    series_excluidas = set(adi_cv2.loc[adi_cv2["excluir_forecast"], "serie_id"])
    abc = compute_abc(conn, series_excluidas)

    clasificacion = adi_cv2.merge(
        abc[["serie_id", "rentabilidad_total", "clase_abc", "margen_unitario_tm"]],
        on="serie_id", how="left",
    )
    clasificacion.to_sql("sku_clasificacion", conn, if_exists="replace", index=False)

    print("Calculando ADI/CV² a NIVEL SKU (demanda agregada, rejilla densa)...")
    clas_sku = compute_adi_cv2_sku(conn)
    if not clas_sku.empty:
        clas_sku.to_sql("clasificacion_nivel_sku", conn, if_exists="replace", index=False)

    conn.commit()

    print("\n--- Resumen clasificación demanda (nivel SERIE / SKU-Cliente) ---")
    print(clasificacion["tipo_demanda"].value_counts())

    n_desc_serie = int((clasificacion["tipo_demanda"] == "descontinuado").sum())
    print(f"\n{n_desc_serie} series SKU-Cliente marcadas 'descontinuado' "
          f"(sin venta desde antes de {ANIO_ACTUAL}).")

    print("\n--- Resumen ABC (solo series con forecast; 'None' = excluida) ---")
    print(clasificacion["clase_abc"].value_counts(dropna=False))

    n_excluidas = int(clasificacion["excluir_forecast"].sum())
    n_total = len(clasificacion)
    print(f"\n{n_excluidas} de {n_total} series ({n_excluidas/n_total*100:.1f}%) excluidas "
          f"del forecast (sin_historia o descontinuado).")

    if n_excluidas:
        rent = clasificacion.loc[clasificacion["excluir_forecast"], "rentabilidad_total"].sum()
        print(f"Rentabilidad total asociada a series excluidas: {rent:,.0f}")

    if not clas_sku.empty:
        print("\n" + "=" * 58)
        print("CLASIFICACIÓN A NIVEL SKU")
        print("=" * 58)
        print(clas_sku["tipo_demanda"].value_counts())

        n_desc_sku = int((clas_sku["tipo_demanda"] == "descontinuado").sum())
        n_inactivo_actual = int(clas_sku["inactivo_periodo_actual"].sum())
        print(f"\n{n_desc_sku} SKU marcados 'descontinuado'.")
        print(f"{n_inactivo_actual} SKU sin ninguna venta en {ANIO_ACTUAL} "
              f"(criterio dinámico, se recalcula cada corrida).")

        desc = clas_sku[clas_sku["tipo_demanda"] == "descontinuado"]
        if len(desc):
            print(desc[["sku", "n_periodos_con_demanda",
                        "pct_meses_recientes_sin_demanda",
                        "inactivo_periodo_actual",
                        "demanda_mensual_promedio"]]
                  .sort_values("demanda_mensual_promedio", ascending=False)
                  .to_string(index=False))

    conn.close()
    print(f"\n[OK] Tablas guardadas: 'sku_clasificacion' (por serie) y "
          f"'clasificacion_nivel_sku' (por SKU) en {DB_PATH}")


if __name__ == "__main__":
    main()