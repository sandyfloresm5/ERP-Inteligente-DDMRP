"""
Bootstrap de errores históricos para generar bandas P10/P50/P90.
Compartido entre SBA y TSB (ninguno tiene intervalos analíticos nativos).

Lógica:
  1. Se toma la serie de errores históricos un-paso-adelante del método.
  2. Se simulan N trayectorias futuras: en cada mes del horizonte, se le
     suma al forecast puntual un error muestreado con reemplazo de la
     distribución empírica de errores históricos.
  3. Se acumulan las N trayectorias y se sacan percentiles 10/50/90 mes a mes.
"""

import numpy as np


def bootstrap_intervalos(
    forecast_puntual: np.ndarray,
    errores_historicos: np.ndarray,
    n_simulaciones: int,
    horizonte: int,
    semilla: int = None,
) -> dict:
    """
    forecast_puntual: array de longitud `horizonte` (el pronóstico central, SBA/TSB).
    errores_historicos: array de errores un-paso-adelante (ver forecast_engines.error_historico).
    n_simulaciones: cuántas trayectorias simular.
    horizonte: meses a proyectar.

    Devuelve dict con arrays 'p10', 'p50', 'p90', cada uno de longitud `horizonte`.
    """
    rng = np.random.default_rng(semilla)

    if len(errores_historicos) == 0:
        # Sin errores históricos que muestrear (serie muy corta):
        # no hay banda, se devuelve el puntual en las tres.
        return {
            "p10": forecast_puntual.copy(),
            "p50": forecast_puntual.copy(),
            "p90": forecast_puntual.copy(),
        }

    trayectorias = np.zeros((n_simulaciones, horizonte))

    for sim in range(n_simulaciones):
        errores_muestreados = rng.choice(errores_historicos, size=horizonte, replace=True)
        trayectoria = forecast_puntual + errores_muestreados
        trayectoria = np.clip(trayectoria, a_min=0, a_max=None)  # demanda no negativa
        trayectorias[sim, :] = trayectoria

    p10 = np.percentile(trayectorias, 10, axis=0)
    p50 = np.percentile(trayectorias, 50, axis=0)
    p90 = np.percentile(trayectorias, 90, axis=0)

    return {"p10": p10, "p50": p50, "p90": p90}