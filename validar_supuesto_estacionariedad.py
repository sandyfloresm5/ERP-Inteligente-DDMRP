"""
validar_supuesto_estacionariedad.py
Cargill Ecuador - Aquanutrition
"""

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import adfuller


def validar_estacionariedad(serie_diaria: pd.Series, sku: str) -> dict:
    y = serie_diaria.values
    n = len(y)
    mitad = n // 2

    # 1) ADF sobre la serie completa
    try:
        adf_stat, adf_pvalue, *_ = adfuller(y, autolag="AIC")
        adf_estacionaria = adf_pvalue < 0.05
    except Exception as e:
        adf_stat, adf_pvalue, adf_estacionaria = np.nan, np.nan, None

    # 2) Varianza por mitad de historia (temprana vs reciente)
    var_temprana = y[:mitad].var(ddof=1)
    var_reciente = y[mitad:].var(ddof=1)
    ratio_varianza = var_reciente / var_temprana if var_temprana > 0 else np.nan

    # 3) Test de Levene: compara formalmente si las varianzas de las
    # dos mitades son estadísticamente distintas (no solo distintas
    # en el número, sino con significancia).
    try:
        levene_stat, levene_pvalue = stats.levene(y[:mitad], y[mitad:])
        varianzas_iguales = levene_pvalue >= 0.05
    except Exception:
        levene_stat, levene_pvalue, varianzas_iguales = np.nan, np.nan, None

    apto_para_bootstrap = bool(adf_estacionaria) and bool(varianzas_iguales) \
        if (adf_estacionaria is not None and varianzas_iguales is not None) else None

    return {
        "sku": sku, "n_dias": n,
        "adf_pvalue": round(adf_pvalue, 4) if not np.isnan(adf_pvalue) else None,
        "adf_estacionaria": adf_estacionaria,
        "var_temprana": round(var_temprana, 2),
        "var_reciente": round(var_reciente, 2),
        "ratio_varianza_reciente_vs_temprana": round(ratio_varianza, 2) if not np.isnan(ratio_varianza) else None,
        "levene_pvalue": round(levene_pvalue, 4) if not np.isnan(levene_pvalue) else None,
        "varianzas_iguales": varianzas_iguales,
        "apto_para_bootstrap_completo": apto_para_bootstrap,
    }