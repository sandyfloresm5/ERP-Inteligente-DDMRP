"""
bootstrap_tor_newsvendor.py
Cargill Ecuador - Aquanutrition
"""

import numpy as np
import pandas as pd

N_SIMULACIONES_DLT = 2000


def bootstrap_tor_newsvendor(serie_diaria: pd.Series, dlt_dias: float,
                              cuantil_objetivo: float,
                              apto_bootstrap_completo,
                              n_sim: int = N_SIMULACIONES_DLT,
                              random_state: int = 42) -> dict:
    """TOR = cuantil_objetivo de la distribución EMPÍRICA de demanda
    acumulada a DLT días. Reemplaza red_base+red_safety(var_factor):
    en vez de un factor de seguridad multiplicando el promedio, se
    lee directo el percentil que Cu/Co exige sobre lo que REALMENTE
    ocurrió en ventanas de DLT días -- sin asumir forma de distribución,
    sin conversión de escala."""
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