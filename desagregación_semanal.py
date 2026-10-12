"""
desagregacion_semanal.py
Cargill Ecuador - Aquanutrition

Capa de reactividad semanal para Make-to-Stock -- NO es un modelo
estadístico semanal nuevo (con 25 meses de historia mensual, un modelo
semanal real perdería poder estadístico, no lo ganaría). Es la
distinción correcta: el FORECAST validado sigue siendo mensual (donde
el torneo tiene evidencia real); esta capa DESAGREGA ese P50 mensual en
semanas usando el patrón histórico real de cada serie, con bandas de
variabilidad derivadas de datos reales -- no un reparto proporcional
ingenuo (25%/25%/25%/25%).

Metodología:
  1. Para cada serie, calcular qué % de las ventas de un mes típico cae
     en cada semana (semana 1-4/5 del mes), usando el histórico diario
     real -- no un promedio genérico para todo el portafolio.
  2. Calcular la VARIABILIDAD de esos porcentajes entre meses (desviación
     estándar) -- esto es lo que da sustento estadístico a la banda,
     no solo el reparto del promedio.
  3. Desagregar el P50/P10/P90 mensual del torneo en 4-5 semanas,
     propagando la incertidumbre de la distribución intra-mes.
  4. Monitoreo: comparar venta acumulada real de la semana en curso
     contra la trayectoria esperada -- si se desvía más de Z desviaciones
     estándar históricas, dispara alerta temprana (reactividad real,
     no solo un forecast que se revisa una vez al mes).

CORRECCIÓN 2026-07: se agregó un guard contra P10/P50/P90 mensual NaN
(típico de series en fallback de baja confianza / cold start) antes de
desagregar. Antes se propagaba como NaN silencioso en p10_semana/
p90_semana sin quedar registrado; ahora esas combinaciones serie-mes se
excluyen explícitamente y se reportan en un CSV aparte.
"""

import sqlite3
import os
import sys

import numpy as np
import pandas as pd

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config import DB_PATH

MIN_MESES_PARA_PATRON = 6  # mínimo de meses históricos para confiar en el patrón semanal
Z_ALERTA = 1.5  # desviaciones estándar para disparar alerta de desviación


def calcular_patron_semanal(conn, serie_id: str) -> pd.DataFrame:
    """
    Devuelve, por semana-del-mes (1 a 5), el % promedio histórico de la
    venta mensual que cae ahí, y su desviación estándar entre meses.
    Semana-del-mes se define como ceil(día/7) -- aproximación estándar,
    no calendario ISO (evita depender de en qué día cae el 1ro del mes).
    """
    ventas = pd.read_sql(
        "SELECT fecha, ventas_tm FROM ventas_demanda WHERE serie_id = ?",
        conn, params=(serie_id,), parse_dates=["fecha"],
    )
    if ventas.empty:
        return pd.DataFrame()

    ventas["periodo"] = ventas["fecha"].dt.to_period("M")
    ventas["dia"] = ventas["fecha"].dt.day
    ventas["semana_del_mes"] = np.ceil(ventas["dia"] / 7).astype(int).clip(upper=5)

    total_por_mes = ventas.groupby("periodo")["ventas_tm"].sum().rename("total_mes")
    ventas = ventas.merge(total_por_mes, on="periodo")
    # Guard: si un mes tiene total_mes == 0 (mes con ventas en cero, posible
    # en series intermitentes/lumpy), la división da 0/0 = NaN. Antes esto
    # se colaba sin blindaje hasta pct_promedio (solo pct_desviacion tenía
    # fillna) y de ahí se propagaba silenciosamente a p50_semana/p10_semana/
    # p90_semana. Un mes en cero aporta 0% a cada semana, no NaN.
    ventas["pct_del_mes"] = np.where(
        ventas["total_mes"] > 0, ventas["ventas_tm"] / ventas["total_mes"], 0.0
    )

    por_semana_mes = ventas.groupby(["periodo", "semana_del_mes"])["pct_del_mes"].sum().reset_index()

    n_meses = ventas["periodo"].nunique()
    todas_combinaciones = pd.MultiIndex.from_product(
        [ventas["periodo"].unique(), range(1, 6)], names=["periodo", "semana_del_mes"]
    ).to_frame(index=False)
    por_semana_mes = todas_combinaciones.merge(
        por_semana_mes, on=["periodo", "semana_del_mes"], how="left"
    ).fillna(0)

    patron = por_semana_mes.groupby("semana_del_mes")["pct_del_mes"].agg(
        pct_promedio="mean", pct_desviacion="std"
    ).reset_index()
    # Defensa adicional: pct_promedio no debería traer NaN tras el fix de
    # división por cero arriba, pero se blinda igual -- un NaN residual
    # aquí es peor que un 0 explícito porque se propaga silenciosamente.
    patron["pct_promedio"] = patron["pct_promedio"].fillna(0)
    patron["pct_desviacion"] = patron["pct_desviacion"].fillna(0)
    patron["n_meses_historia"] = n_meses

    return patron


def desagregar_forecast_mensual(patron: pd.DataFrame, p10_mes: float, p50_mes: float,
                                 p90_mes: float) -> pd.DataFrame:
    """
    Aplica el patrón semanal al forecast mensual del torneo, propagando
    la incertidumbre: el ancho de banda semanal combina la incertidumbre
    del forecast mensual (P90-P10) CON la variabilidad histórica de
    cuándo dentro del mes cae la venta (pct_desviacion).

    Asume p10_mes/p50_mes/p90_mes ya validados como no-NaN por el
    llamador (ver guard en main()).
    """
    filas = []
    ancho_banda_mensual = (p90_mes - p10_mes) / 2

    for _, fila in patron.iterrows():
        p50_semana = p50_mes * fila["pct_promedio"]
        incertidumbre_semana = np.sqrt(
            (ancho_banda_mensual * fila["pct_promedio"]) ** 2 +
            (p50_mes * fila["pct_desviacion"]) ** 2
        )
        filas.append({
            "semana_del_mes": int(fila["semana_del_mes"]),
            "p10_semana": max(p50_semana - incertidumbre_semana, 0),
            "p50_semana": max(p50_semana, 0),
            "p90_semana": p50_semana + incertidumbre_semana,
            "pct_esperado": fila["pct_promedio"],
            "desviacion_pct": fila["pct_desviacion"],
        })

    return pd.DataFrame(filas)


def evaluar_desviacion_actual(venta_acumulada_semana: float, p50_esperado_semana: float,
                               desviacion_pct: float, p50_mes: float) -> dict:
    """
    Monitoreo: dado lo vendido hasta ahora en la semana en curso, calcula
    el z-score de desviación respecto a lo esperado, usando la
    variabilidad histórica real (no un umbral arbitrario).
    """
    desviacion_absoluta = desviacion_pct * p50_mes
    if desviacion_absoluta == 0:
        z_score = 0.0
    else:
        z_score = (venta_acumulada_semana - p50_esperado_semana) / desviacion_absoluta

    if abs(z_score) >= Z_ALERTA:
        direccion = "por ENCIMA" if z_score > 0 else "por DEBAJO"
        alerta = f"⚠️ Venta {direccion} de lo esperado (z={z_score:.2f})"
    else:
        alerta = "Dentro del rango esperado"

    return {"z_score": z_score, "alerta": alerta}


def main():
    conn = sqlite3.connect(DB_PATH)

    series = pd.read_sql(
        "SELECT DISTINCT serie_id, sku, codigo_cliente FROM forecast_final", conn,
    )
    print(f"Calculando patrón semanal para {len(series)} series con forecast...")

    resultados = []
    n_sin_patron_confiable = 0
    n_sin_forecast_valido = 0
    series_sin_forecast_valido = []

    for _, row in series.iterrows():
        patron = calcular_patron_semanal(conn, row["serie_id"])
        if patron.empty or patron["n_meses_historia"].iloc[0] < MIN_MESES_PARA_PATRON:
            n_sin_patron_confiable += 1
            continue

        forecast = pd.read_sql(
            "SELECT mes_offset, p10, p50, p90 FROM forecast_final WHERE serie_id = ?",
            conn, params=(row["serie_id"],),
        )
        for _, mes in forecast.iterrows():
            # Guard: si el forecast mensual (P10/P50/P90) viene con NaN --
            # típico de series en fallback de baja confianza / cold start --
            # no desagregar. Antes esto se propagaba como NaN silencioso
            # en p10_semana/p90_semana sin quedar registrado en ningún lado.
            if pd.isna(mes["p10"]) or pd.isna(mes["p50"]) or pd.isna(mes["p90"]):
                n_sin_forecast_valido += 1
                series_sin_forecast_valido.append((row["serie_id"], int(mes["mes_offset"])))
                continue

            semanal = desagregar_forecast_mensual(patron, mes["p10"], mes["p50"], mes["p90"])
            semanal["serie_id"] = row["serie_id"]
            semanal["sku"] = row["sku"]
            semanal["codigo_cliente"] = row["codigo_cliente"]
            semanal["mes_offset"] = int(mes["mes_offset"])
            resultados.append(semanal)

    df_final = pd.concat(resultados, ignore_index=True) if resultados else pd.DataFrame()
    ruta = os.path.join(ROOT_DIR, "forecast_semanal_desagregado.csv")
    df_final.to_csv(ruta, index=False)

    print(f"\n[OK] {len(df_final)} filas (serie x mes x semana) -> {ruta}")
    print(f"⚠️  {n_sin_patron_confiable} series sin suficiente historia "
          f"(< {MIN_MESES_PARA_PATRON} meses) para calcular patrón semanal confiable "
          f"-- quedan fuera de esta capa, mismo criterio de honestidad que baja_confianza.")

    if n_sin_forecast_valido > 0:
        ruta_faltantes = os.path.join(ROOT_DIR, "desagregacion_semanal_forecast_invalido.csv")
        pd.DataFrame(series_sin_forecast_valido, columns=["serie_id", "mes_offset"]).to_csv(
            ruta_faltantes, index=False
        )
        print(f"⚠️  {n_sin_forecast_valido} combinación(es) serie-mes con P10/P50/P90 "
              f"mensual inválido (NaN) en forecast_final -- excluidas de la desagregación "
              f"semanal en vez de propagar NaN silenciosamente. Detalle -> {ruta_faltantes}")

    conn.close()


if __name__ == "__main__":
    main()