"""
Motores de forecast intermitente: SBA y TSB.
Funciones puras — reciben arrays de numpy, devuelven arrays de numpy.
No tocan la base de datos ni el disco. Facilita testing unitario y
optimización de hiperparámetros por grid search.
"""

import numpy as np


def sba_forecast(demanda: np.ndarray, alpha: float, horizonte: int) -> np.ndarray:
    """
    Syntetos-Boylan Approximation (SBA).

    demanda: serie mensual completa, incluyendo ceros (orden cronológico).
    alpha: parámetro de suavizado exponencial (0 < alpha < 1).
    horizonte: número de periodos futuros a proyectar.

    Devuelve: array de longitud `horizonte` con el mismo pronóstico
    repetido (SBA proyecta plano, es la naturaleza del método).
    """
    n = len(demanda)
    if n == 0:
        return np.zeros(horizonte)

    # Inicialización: primer valor no-nulo observado
    idx_no_nulos = np.where(demanda > 0)[0]
    if len(idx_no_nulos) == 0:
        return np.zeros(horizonte)

    z = demanda[idx_no_nulos[0]]       # nivel de demanda (tamaño)
    p = 1.0 / max(idx_no_nulos[0] + 1, 1)  # intervalo inicial estimado

    intervalo_desde_ultima = 0

    for t in range(idx_no_nulos[0] + 1, n):
        intervalo_desde_ultima += 1
        if demanda[t] > 0:
            z = alpha * demanda[t] + (1 - alpha) * z
            p = alpha * intervalo_desde_ultima + (1 - alpha) * p
            intervalo_desde_ultima = 0

    # Corrección de sesgo SBA (vs Croston clásico)
    factor_correccion = 1 - (alpha / 2)
    forecast_periodo = factor_correccion * (z / p) if p > 0 else 0.0

    return np.full(horizonte, forecast_periodo)


def tsb_forecast(demanda: np.ndarray, alpha: float, beta: float, horizonte: int) -> np.ndarray:
    """
    Teunter-Syntetos-Babai (TSB).

    Modela por separado:
      - prob: probabilidad de ocurrencia de demanda en un periodo (suavizado con beta)
      - z: tamaño de la demanda dado que ocurre (suavizado con alpha)

    A diferencia de SBA, la probabilidad decae si la serie deja de tener
    demanda (protege contra sobre-pronóstico de clientes que dejaron de comprar).

    demanda: serie mensual completa, incluyendo ceros.
    alpha: suavizado del tamaño de demanda.
    beta: suavizado de la probabilidad de ocurrencia.
    horizonte: periodos futuros a proyectar.
    """
    n = len(demanda)
    if n == 0:
        return np.zeros(horizonte)

    idx_no_nulos = np.where(demanda > 0)[0]
    if len(idx_no_nulos) == 0:
        return np.zeros(horizonte)

    z = demanda[idx_no_nulos[0]]
    prob = 1.0 / max(idx_no_nulos[0] + 1, 1)

    for t in range(idx_no_nulos[0] + 1, n):
        ocurrencia = 1.0 if demanda[t] > 0 else 0.0
        prob = beta * ocurrencia + (1 - beta) * prob
        if demanda[t] > 0:
            z = alpha * demanda[t] + (1 - alpha) * z
        # si no hay demanda ese periodo, z no se actualiza (a diferencia de SBA
        # que solo actualiza en periodos con demanda, aquí es igual, pero prob
        # sí decae cada periodo, haya o no demanda)

    forecast_periodo = prob * z

    return np.full(horizonte, forecast_periodo)


def error_historico(demanda: np.ndarray, alpha: float, metodo: str, beta: float = None) -> np.ndarray:
    """
    Calcula el error de pronóstico un-paso-adelante en cada punto histórico,
    corriendo el método de forma incremental. Se usa como insumo para el
    bootstrap de intervalos P10/P50/P90 (ver utils/bootstrap.py).

    Devuelve array de errores (demanda_real - forecast_en_ese_punto),
    de longitud n-1 (no se puede calcular error en el primer periodo).
    """
    n = len(demanda)
    errores = []

    for t in range(2, n + 1):  # necesita al menos 2 periodos de historia
        historia = demanda[:t - 1]
        real = demanda[t - 1]

        if metodo == "SBA":
            pred = sba_forecast(historia, alpha, horizonte=1)[0]
        elif metodo == "TSB":
            pred = tsb_forecast(historia, alpha, beta, horizonte=1)[0]
        else:
            raise ValueError(f"Método desconocido: {metodo}")

        errores.append(real - pred)

    return np.array(errores)