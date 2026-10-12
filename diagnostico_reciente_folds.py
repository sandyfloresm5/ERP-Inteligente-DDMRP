"""
diagnostico_recencia_folds.py
Cargill Ecuador - Aquanutrition
"""

import sqlite3
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config import DB_PATH, N_FOLDS_TORNEO, TEST_PERIODS_TORNEO, MIN_HIST_TORNEO
from Torneo_Forecast import (
    FUNCIONES_MODELO, MIN_TRAIN_POR_MODELO, _folds_disponibles,
)
from forecast_sku import cargar_demanda_sku, _wape

HORIZON = TEST_PERIODS_TORNEO
MAX_FOLDS = N_FOLDS_TORNEO

# Umbral para marcar "el modelo se deteriora en lo reciente":
# WAPE del fold más nuevo >= este múltiplo del WAPE promedio de los
# folds viejos. 1.5x es conservador -- cualquier valor por debajo no
# se reporta como caso sospechoso.
UMBRAL_DETERIORO_RECIENTE = 1.5


def wape_por_fold(serie: pd.Series, modelo_fn, min_train: int,
                   horizon: int = HORIZON, max_folds: int = MAX_FOLDS) -> list:
    """Devuelve lista de WAPE por fold, ordenada de más vieja a más
    reciente (a diferencia de evaluar_candidato, que los agrupa)."""
    n_meses = len(serie)
    n_folds = _folds_disponibles(n_meses, horizon, min_train, max_folds)
    wapes = []

    for fold in range(n_folds, 0, -1):
        corte = n_meses - fold * horizon
        train = serie.iloc[:corte]
        test = serie.iloc[corte:corte + horizon]
        if len(train) < min_train or len(test) == 0:
            continue
        try:
            pred = np.maximum(modelo_fn(train, len(test)), 0)
            if np.any(np.isnan(pred)):
                continue
        except Exception:
            continue
        wapes.append(_wape(test.values, pred))

    return wapes


def main():
    conn = sqlite3.connect(DB_PATH)

    demanda = cargar_demanda_sku(conn)
    val = pd.read_sql(
        "SELECT sku, meses_historia, modelo_produccion, wape_holdout, semaforo "
        "FROM forecast_sku_validacion", conn,
    )

    resultados = []

    for _, row in val.iterrows():
        sku = row["sku"]
        modelo = row["modelo_produccion"]

        # Modelos con wrapper propio (Prophet/SARIMAX) o blends no se
        # evalúan aquí -- este diagnóstico es sobre los candidatos base
        # de FUNCIONES_MODELO, que es donde vimos el patrón (Naive, SBA).
        if modelo not in FUNCIONES_MODELO:
            continue

        serie = (demanda[demanda["sku"] == sku]
                 .sort_values("periodo")["ventas_tm"].reset_index(drop=True))

        min_train = MIN_TRAIN_POR_MODELO.get(modelo, 5)
        if len(serie) < MIN_HIST_TORNEO:
            continue

        wapes = wape_por_fold(serie, FUNCIONES_MODELO[modelo], min_train)
        if len(wapes) < 2:
            continue  # necesita al menos 2 folds para comparar reciente vs viejo

        wape_reciente = wapes[-1]
        wape_viejos = np.mean(wapes[:-1])

        ratio = wape_reciente / wape_viejos if wape_viejos > 0 else np.nan

        resultados.append({
            "sku": sku,
            "modelo_produccion": modelo,
            "semaforo": row["semaforo"],
            "n_folds": len(wapes),
            "wape_folds_viejos": round(wape_viejos, 3),
            "wape_fold_reciente": round(wape_reciente, 3),
            "ratio_reciente_vs_viejo": round(ratio, 2) if not np.isnan(ratio) else None,
            "deteriora_en_reciente": bool(ratio >= UMBRAL_DETERIORO_RECIENTE) if not np.isnan(ratio) else False,
        })

    df = pd.DataFrame(resultados)
    ruta = os.path.join(ROOT_DIR, "diagnostico_recencia_folds.csv")
    df.to_csv(ruta, index=False)

    print(f"[OK] {len(df)} SKU evaluados -> {ruta}\n")

    n_deteriora = df["deteriora_en_reciente"].sum()
    print(f"SKU donde el fold más reciente empeora >= {UMBRAL_DETERIORO_RECIENTE}x "
          f"vs. folds viejos: {n_deteriora} de {len(df)} "
          f"({n_deteriora/len(df):.1%})\n" if len(df) else "Sin datos evaluables.\n")

    if n_deteriora > 0:
        print("--- Casos con deterioro reciente, ordenados por ratio ---")
        cols = ["sku", "modelo_produccion", "semaforo", "wape_folds_viejos",
                "wape_fold_reciente", "ratio_reciente_vs_viejo"]
        print(df[df["deteriora_en_reciente"]]
              .sort_values("ratio_reciente_vs_viejo", ascending=False)[cols]
              .to_string(index=False))

    print("\n--- Distribución por modelo ganador (todos los evaluados) ---")
    print(df.groupby("modelo_produccion")["deteriora_en_reciente"].agg(["sum", "count"]))

    conn.close()


if __name__ == "__main__":
    main()