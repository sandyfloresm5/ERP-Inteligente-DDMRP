"""
bootstrap_var_factor_dlt.py
Cargill Ecuador - Aquanutrition
"""

import numpy as np
import pandas as pd

N_SIMULACIONES = 2000


def bootstrap_var_factor_dlt(serie_diaria: pd.Series, dlt_dias: int,
                              apto_bootstrap_completo: bool,
                              n_sim: int = N_SIMULACIONES,
                              random_state: int = 42) -> dict:
    """Si apto_bootstrap_completo=False, usa solo la segunda mitad de
    la serie (ventana post-quiebre de varianza) -- consistente con el
    corte que usó validar_estacionariedad() para detectar el cambio."""
    y_completa = serie_diaria.values
    n = len(y_completa)

    if apto_bootstrap_completo:
        y = y_completa
        ventana_usada = "completa"
    else:
        mitad = n // 2
        y = y_completa[mitad:]
        ventana_usada = "reciente_post_quiebre"

    if len(y) < dlt_dias * 3:
        return {"p10": np.nan, "p50": np.nan, "p90": np.nan,
                "var_factor": np.nan, "ventana_usada": ventana_usada,
                "n_dias_usados": len(y), "estado": "datos_insuficientes"}

    rng = np.random.default_rng(random_state)
    inicios_posibles = len(y) - dlt_dias
    sumas = np.array([
        y[i:i + dlt_dias].sum()
        for i in rng.integers(0, inicios_posibles + 1, size=n_sim)
    ])

    p10, p50, p90 = np.percentile(sumas, [10, 50, 90])
    media, std = sumas.mean(), sumas.std(ddof=1)
    var_factor = std / media if media > 0 else np.nan

    return {"p10": round(p10, 2), "p50": round(p50, 2), "p90": round(p90, 2),
            "var_factor": round(var_factor, 4), "ventana_usada": ventana_usada,
            "n_dias_usados": len(y), "estado": "ok"}