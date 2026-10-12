"""
calibrar_bandas.py
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

from config import DB_PATH, MIN_HIST_TORNEO, TEST_PERIODS_TORNEO

try:
    from Torneo_Forecast import (
        correr_torneo_serie, generar_forecast_final, generar_forecast_blend,
    )
except ImportError:
    from Torneo_Forecast import (
        correr_torneo_serie, generar_forecast_final, generar_forecast_blend,
    )

H = TEST_PERIODS_TORNEO
NIVEL_NOMINAL = 0.80
MIN_OBS_GRUPO = 30          # mínimo para calibrar un grupo por separado
Q_MAX = 3.0                 # tope de seguridad del factor conformal

# Ventanas: la 0 (más reciente) se reserva para VERIFICAR.
# Las demás se usan para calibrar.
VENTANA_VERIFICACION = 0
VENTANAS_CALIBRACION = [1, 2, 3]


# ---------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------

def cargar_series(conn) -> dict:
    df = pd.read_sql(
        "SELECT serie_id, fecha, ventas_tm FROM ventas_demanda",
        conn, parse_dates=["fecha"],
    )
    df["periodo"] = df["fecha"].dt.to_period("M")
    agg = (df.groupby(["serie_id", "periodo"], as_index=False)["ventas_tm"].sum()
           .sort_values(["serie_id", "periodo"]))
    return {sid: g["ventas_tm"].reset_index(drop=True)
            for sid, g in agg.groupby("serie_id")}


def cargar_skus(conn) -> dict:
    df = pd.read_sql(
        "SELECT serie_id, fecha, ventas_tm FROM ventas_demanda",
        conn, parse_dates=["fecha"],
    )
    df["sku"] = df["serie_id"].astype(str).str.split("__").str[0]
    df["periodo"] = df["fecha"].dt.to_period("M")
    agg = df.groupby(["sku", "periodo"], as_index=False)["ventas_tm"].sum()

    periodos = pd.period_range(agg["periodo"].min(), agg["periodo"].max(), freq="M")
    idx = pd.MultiIndex.from_product([agg["sku"].unique(), periodos],
                                     names=["sku", "periodo"])
    denso = (agg.set_index(["sku", "periodo"])["ventas_tm"]
             .reindex(idx, fill_value=0.0).reset_index()
             .sort_values(["sku", "periodo"]))
    return {sku: g["ventas_tm"].reset_index(drop=True)
            for sku, g in denso.groupby("sku")}


def cargar_tipos(conn) -> tuple:
    """tipo_demanda por serie y por SKU, para calibrar por grupo."""
    try:
        s = pd.read_sql("SELECT serie_id, tipo_demanda FROM sku_clasificacion", conn)
        mapa_serie = dict(zip(s["serie_id"], s["tipo_demanda"]))
    except Exception:
        mapa_serie = {}
    try:
        k = pd.read_sql("SELECT sku, tipo_demanda FROM clasificacion_nivel_sku", conn)
        mapa_sku = dict(zip(k["sku"].astype(str), k["tipo_demanda"]))
    except Exception:
        mapa_sku = {}
    return mapa_serie, mapa_sku


# ---------------------------------------------------------------------
# Generación de observaciones de holdout
# ---------------------------------------------------------------------

def observaciones_holdout(series: dict, corte: int, nivel: str,
                          mapa_tipo: dict) -> pd.DataFrame:
    """
    Una fila por (id, mes del holdout) con el real y la banda predicha.
    El torneo elige usando solo los datos previos al corte.
    """
    filas = []

    for sid, serie in series.items():
        n = len(serie)
        fin = n - corte
        if fin - H < MIN_HIST_TORNEO:
            continue

        train = serie.iloc[:fin - H].reset_index(drop=True)
        test = serie.iloc[fin - H:fin].reset_index(drop=True)
        if len(test) < H or float(np.sum(np.abs(test.values))) == 0:
            continue

        try:
            res = correr_torneo_serie(train, df_hist=None)
            if res.get("blend_componentes") is not None:
                n1, n2, w1, w2 = res["blend_componentes"]
                pred = generar_forecast_blend(train, n1, n2, w1, w2,
                                              errores_blend=res["errores_cv"], periods=H)
            else:
                pred = generar_forecast_final(train, res["modelo"],
                                              res["errores_cv"], periods=H)
        except Exception:
            continue

        p10 = np.maximum(pred["p10"].values[:H], 0)
        p50 = np.maximum(pred["p50"].values[:H], 0)
        p90 = np.maximum(pred["p90"].values[:H], 0)

        for j in range(H):
            filas.append({
                "id": sid, "nivel": nivel,
                "tipo_demanda": mapa_tipo.get(sid, "desconocido"),
                "mes": j, "real": float(test.values[j]),
                "p10": float(p10[j]), "p50": float(p50[j]), "p90": float(p90[j]),
            })

    return pd.DataFrame(filas)


def score_conformal(df: pd.DataFrame) -> pd.Series:
    """
    E = max(p10 - y, y - p90) / p50

    Negativo si el real cayó dentro de la banda, positivo si quedó afuera.
    Normalizado por p50 para que el ajuste escale con el tamaño de la serie.
    """
    p50 = df["p50"].replace(0, np.nan)
    exceso = np.maximum(df["p10"] - df["real"], df["real"] - df["p90"])
    return exceso / p50


# ---------------------------------------------------------------------
# Calibración
# ---------------------------------------------------------------------

def calibrar(df_cal: pd.DataFrame) -> tuple:
    """
    Devuelve (q_por_grupo, q_global). Grupo = (nivel, tipo_demanda).
    Grupos con pocas observaciones caen al q global.
    """
    df = df_cal.copy()
    df["score"] = score_conformal(df)
    df = df.dropna(subset=["score"])

    q_global = float(np.clip(np.quantile(df["score"], NIVEL_NOMINAL), 0.0, Q_MAX))

    q_grupo = {}
    for (nivel, tipo), g in df.groupby(["nivel", "tipo_demanda"]):
        if len(g) < MIN_OBS_GRUPO:
            continue
        q_grupo[(nivel, tipo)] = float(
            np.clip(np.quantile(g["score"], NIVEL_NOMINAL), 0.0, Q_MAX)
        )

    return q_grupo, q_global


def aplicar(df: pd.DataFrame, q_grupo: dict, q_global: float) -> pd.DataFrame:
    out = df.copy()
    out["q"] = [
        q_grupo.get((n, t), q_global)
        for n, t in zip(out["nivel"], out["tipo_demanda"])
    ]
    out["p10_cal"] = np.maximum(out["p10"] - out["q"] * out["p50"], 0)
    out["p90_cal"] = out["p90"] + out["q"] * out["p50"]
    return out


def picp(df: pd.DataFrame, col_lo: str, col_hi: str) -> float:
    dentro = (df["real"] >= df[col_lo]) & (df["real"] <= df[col_hi])
    return float(dentro.mean()) if len(df) else np.nan


def ancho_relativo(df: pd.DataFrame, col_lo: str, col_hi: str) -> float:
    p50 = df["p50"].replace(0, np.nan)
    return float(((df[col_hi] - df[col_lo]) / p50).median())


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    conn = sqlite3.connect(DB_PATH)

    series = cargar_series(conn)
    skus = cargar_skus(conn)
    mapa_serie, mapa_sku = cargar_tipos(conn)

    n_meses = max(len(s) for s in skus.values())

    print("=" * 66)
    print("CALIBRACIÓN CONFORMAL DE BANDAS")
    print("=" * 66)
    print(f"Objetivo de cobertura: {NIVEL_NOMINAL:.0%}")
    print(f"Ventanas de calibración: {VENTANAS_CALIBRACION} (más antiguas)")
    print(f"Ventana de verificación: {VENTANA_VERIFICACION} (la más reciente, "
          f"NO participa de la calibración)\n")

    def recolectar(indices):
        partes = []
        for i in indices:
            corte = i * H
            if n_meses - corte - H < MIN_HIST_TORNEO:
                continue
            print(f"  Procesando ventana {i} ...", flush=True)
            partes.append(observaciones_holdout(series, corte, "serie", mapa_serie))
            partes.append(observaciones_holdout(skus, corte, "sku", mapa_sku))
        return (pd.concat(partes, ignore_index=True)
                if partes else pd.DataFrame())

    print("Recolectando observaciones de CALIBRACIÓN...")
    df_cal = recolectar(VENTANAS_CALIBRACION)
    print("\nRecolectando observaciones de VERIFICACIÓN...")
    df_ver = recolectar([VENTANA_VERIFICACION])

    if df_cal.empty or df_ver.empty:
        print("\n[ERROR] No hay observaciones suficientes.")
        conn.close()
        return

    print(f"\nObservaciones de calibración: {len(df_cal)}")
    print(f"Observaciones de verificación: {len(df_ver)}")

    q_grupo, q_global = calibrar(df_cal)

    print(f"\n--- Factor conformal q ---")
    print(f"  Global: {q_global:.3f}")
    print(f"  (la banda se ensancha q x p50 hacia cada lado)")
    if q_grupo:
        print("  Por grupo:")
        for (nivel, tipo), q in sorted(q_grupo.items()):
            print(f"    {nivel:6s} / {tipo:15s}: {q:.3f}")
    else:
        print("  Ningún grupo alcanzó el mínimo de observaciones; se usa el global.")

    # -----------------------------------------------------------------
    # Verificación sobre la ventana reservada
    # -----------------------------------------------------------------
    ver = aplicar(df_ver, q_grupo, q_global)

    print("\n" + "=" * 66)
    print("VERIFICACIÓN -- ventana que NO participó de la calibración")
    print("=" * 66)

    resumen = []
    for nivel in ["sku", "serie"]:
        sub = ver[ver["nivel"] == nivel]
        if sub.empty:
            continue

        antes = picp(sub, "p10", "p90")
        despues = picp(sub, "p10_cal", "p90_cal")
        ancho_antes = ancho_relativo(sub, "p10", "p90")
        ancho_despues = ancho_relativo(sub, "p10_cal", "p90_cal")

        print(f"\n--- Nivel {nivel.upper()} ({len(sub)} observaciones) ---")
        print(f"  Cobertura ANTES:    {antes:.1%}")
        print(f"  Cobertura DESPUÉS:  {despues:.1%}   (objetivo {NIVEL_NOMINAL:.0%})")
        print(f"  Ancho relativo:     {ancho_antes:.2f}x -> {ancho_despues:.2f}x el p50")

        mejora = despues - antes
        if despues >= NIVEL_NOMINAL - 0.05:
            veredicto = "OK -- la banda calibrada alcanza la cobertura objetivo"
        elif mejora > 0.05:
            veredicto = ("MEJORA PARCIAL -- cubre más que antes pero sigue por debajo "
                         "del objetivo")
        else:
            veredicto = ("NO FUNCIONA -- la calibración no mejora la cobertura en "
                         "datos nuevos")
        print(f"  -> {veredicto}")

        resumen.append({
            "nivel": nivel, "n_obs": int(len(sub)),
            "picp_antes": round(antes, 4), "picp_despues": round(despues, 4),
            "ancho_rel_antes": round(ancho_antes, 3),
            "ancho_rel_despues": round(ancho_despues, 3),
            "q_global": round(q_global, 4),
            "veredicto": veredicto,
        })

    df_resumen = pd.DataFrame(resumen)
    df_resumen.to_sql("calibracion_bandas_verificacion", conn,
                      if_exists="replace", index=False)

    # -----------------------------------------------------------------
    # Persistir los factores para que el pipeline los aplique
    # -----------------------------------------------------------------
    filas_q = [{"nivel": n, "tipo_demanda": t, "q": q}
               for (n, t), q in q_grupo.items()]
    filas_q.append({"nivel": "_global", "tipo_demanda": "_global", "q": q_global})
    pd.DataFrame(filas_q).to_sql("calibracion_bandas_q", conn,
                                 if_exists="replace", index=False)

    # -----------------------------------------------------------------
    # Aplicar a las tablas de producción
    # -----------------------------------------------------------------
    print("\n" + "=" * 66)
    print("APLICACIÓN A LAS TABLAS DE PRODUCCIÓN")
    print("=" * 66)

    def q_para(nivel, tipo):
        return q_grupo.get((nivel, tipo), q_global)

    # forecast_sku
    try:
        fsku = pd.read_sql("SELECT * FROM forecast_sku", conn)
        fsku["sku"] = fsku["sku"].astype(str)
        fsku["_tipo"] = fsku["sku"].map(mapa_sku).fillna("desconocido")
        fsku["q_calibracion"] = [q_para("sku", t) for t in fsku["_tipo"]]
        fsku["p10_sin_calibrar"] = fsku["p10"]
        fsku["p90_sin_calibrar"] = fsku["p90"]
        fsku["p10"] = np.maximum(fsku["p10"] - fsku["q_calibracion"] * fsku["p50"], 0)
        fsku["p90"] = fsku["p90"] + fsku["q_calibracion"] * fsku["p50"]
        fsku = fsku.drop(columns=["_tipo"])
        fsku.to_sql("forecast_sku_calibrado", conn, if_exists="replace", index=False)

        ancho_ant = ((fsku["p90_sin_calibrar"] - fsku["p10_sin_calibrar"])
                     / fsku["p50"].replace(0, np.nan)).median()
        ancho_new = ((fsku["p90"] - fsku["p10"])
                     / fsku["p50"].replace(0, np.nan)).median()
        print(f"  forecast_sku_calibrado: {len(fsku)} filas, "
              f"ancho relativo {ancho_ant:.2f}x -> {ancho_new:.2f}x")
    except Exception as e:
        print(f"  [AVISO] No se pudo calibrar forecast_sku: {e}")

    # forecast_reconciliado
    try:
        frec = pd.read_sql("SELECT * FROM forecast_reconciliado", conn)
        frec["_tipo"] = frec["serie_id"].map(mapa_serie).fillna("desconocido")
        frec["q_calibracion"] = [q_para("serie", t) for t in frec["_tipo"]]
        frec["p10_sin_calibrar"] = frec["p10"]
        frec["p90_sin_calibrar"] = frec["p90"]
        frec["p10"] = np.maximum(frec["p10"] - frec["q_calibracion"] * frec["p50"], 0)
        frec["p90"] = frec["p90"] + frec["q_calibracion"] * frec["p50"]
        frec = frec.drop(columns=["_tipo"])
        frec.to_sql("forecast_reconciliado_calibrado", conn,
                    if_exists="replace", index=False)
        print(f"  forecast_reconciliado_calibrado: {len(frec)} filas")
    except Exception as e:
        print(f"  [AVISO] No se pudo calibrar forecast_reconciliado: {e}")

    conn.commit()

    # -----------------------------------------------------------------
    print("\n" + "=" * 66)
    print("QUÉ HACER CON ESTO")
    print("=" * 66)
    print("  1. El buffer DDMRP debe leer 'forecast_sku_calibrado'. El")
    print("     var_factor_dinamico = (p90-p10)/p50 se calcula sobre la banda")
    print("     corregida, así el TOR deja de estar subdimensionado.")
    print("  2. Los KPIs financieros deben leer 'forecast_reconciliado_calibrado'.")
    print("     El VaR va a SUBIR: el anterior estaba subestimado, no es que el")
    print("     sistema haya empeorado.")
    print("  3. Las tablas originales quedan intactas, y las calibradas conservan")
    print("     p10_sin_calibrar / p90_sin_calibrar para poder comparar.")
    print("\n  El punto central (p50) NO se toca. Esta corrección es solo sobre")
    print("  la incertidumbre, que es lo que el diagnóstico mostró que estaba mal.")

    conn.close()


if __name__ == "__main__":
    main()