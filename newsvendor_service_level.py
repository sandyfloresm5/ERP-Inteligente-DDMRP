"""
newsvendor_service_level.py
Cargill Ecuador - Aquanutrition

Cu: margen_unitario_tm (sku_clasificacion) -- dato real, confirmado.
Co: costo_tm x tasa mensual ESTIMADA (config.CO_TASA_ANUAL_ESTIMADA) --
    declarado como estimado hasta que finanzas confirme la tasa real.
"""

import pandas as pd
import numpy as np
from config import CO_TASA_ANUAL_ESTIMADA, CO_ES_ESTIMADO

N_SIMULACIONES_DLT = 2000  # definido aquí, no importado de Buffer_DDMRP.py (evita import circular)


def cargar_cu_co_por_sku(conn) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT sku, margen_unitario_tm, demanda_media_no_nula "
        "FROM sku_clasificacion WHERE excluir_forecast = 0", conn,
    )
    df = df.dropna(subset=["margen_unitario_tm"])

    ventas = pd.read_sql("SELECT sku, ventas_tm, costo_tm FROM ventas_raw", conn)
    costo_prom = (ventas.groupby("sku")
                  .apply(lambda g: np.average(g["costo_tm"], weights=g["ventas_tm"].clip(lower=0.01)))
                  .rename("costo_tm_ponderado"))

    agg = df.groupby("sku").apply(
        lambda g: pd.Series({
            "cu": np.average(g["margen_unitario_tm"], weights=g["demanda_media_no_nula"]),
        })
    ).reset_index()

    agg = agg.merge(costo_prom, on="sku", how="left")
    agg["co"] = agg["costo_tm_ponderado"] * (CO_TASA_ANUAL_ESTIMADA / 12)
    agg["cu"] = agg["cu"].clip(lower=0)

    agg["cuantil_objetivo"] = agg["cu"] / (agg["cu"] + agg["co"])
    agg["cuantil_objetivo"] = agg["cuantil_objetivo"].clip(0.50, 0.99)
    agg["co_estimado"] = CO_ES_ESTIMADO

    return agg[["sku", "cu", "co", "cuantil_objetivo", "co_estimado"]]


def bootstrap_tor_newsvendor(serie_diaria: pd.Series, dlt_dias: float,
                              cuantil_objetivo: float,
                              apto_bootstrap_completo,
                              n_sim: int = N_SIMULACIONES_DLT,
                              random_state: int = 42) -> dict:
    if pd.isna(dlt_dias) or dlt_dias <= 0 or pd.isna(cuantil_objetivo):
        return {"tor_newsvendor": np.nan, "estado": "sin_datos"}

    dlt_dias = max(1, round(dlt_dias))
    y_completa = serie_diaria.values
    n = len(y_completa)

    if apto_bootstrap_completo is True:
        y = y_completa
    elif apto_bootstrap_completo is False:
        y = y_completa[n // 2:]
    else:
        y = y_completa

    if len(y) < dlt_dias * 3:
        return {"tor_newsvendor": np.nan, "estado": "datos_insuficientes"}

    rng = np.random.default_rng(random_state)
    inicios = len(y) - dlt_dias
    sumas = np.array([y[i:i + dlt_dias].sum()
                       for i in rng.integers(0, inicios + 1, size=n_sim)])

    tor = float(np.percentile(sumas, cuantil_objetivo * 100))
    return {"tor_newsvendor": round(tor, 1), "estado": "ok",
            "p50_referencia": round(float(np.median(sumas)), 1)}