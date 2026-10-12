"""
calcular_buffer_ddmrp_dinamico.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP (buffer dinámico, no estático)
"""

import os
import sys
import sqlite3
import warnings

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from statsmodels.tsa.stattools import adfuller

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config import (
    DB_PATH, LEAD_TIME_PRODUCCION_DIAS,
    LT_FACTOR_CORTE_CORTO, LT_FACTOR_CORTE_MEDIO,
    LT_FACTOR_VALOR_CORTO, LT_FACTOR_VALOR_MEDIO, LT_FACTOR_VALOR_LARGO,
)
try:
    from config import CICLO_PEDIDO_PT_DIAS
except ImportError:
    CICLO_PEDIDO_PT_DIAS = None
try:
    from config import CAPACIDAD_BODEGA_PT_TM
except ImportError:
    CAPACIDAD_BODEGA_PT_TM = None

from newsvendor_service_level import cargar_cu_co_por_sku, bootstrap_tor_newsvendor

# Aviso de cambio futuro de statsmodels (no afecta el resultado).
warnings.filterwarnings("ignore", message="adfuller currently returns")

VAR_FACTOR_MIN = 0.10
# DDMRP: variabilidad alta = 61-100 %. El VF no supera 100 %.
VAR_FACTOR_MAX = 1.00
SEMAFOROS_RESPALDADOS = {"VERDE", "AMARILLO"}
N_SIMULACIONES_DLT = 2000
MIN_DIAS_PARA_ESTACIONARIEDAD = 60
# ADU de MP sin RMB del mes: promedio de consumo de los últimos 6 meses
# (ventana móvil, decisión documentada), no de toda la historia.
VENTANA_ADU_MP_DIAS = 180


def _tabla_existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _capacidad_bodega_pt_tm(conn):
    """Capacidad de bodega PT de Configuración (config_capacidad_bodega);
    si nunca se llenó en la app, respaldo al valor fijo de config.py."""
    if _tabla_existe(conn, "config_capacidad_bodega"):
        fila = conn.execute(
            "SELECT capacidad_pt_tm FROM config_capacidad_bodega WHERE id = 1"
        ).fetchone()
        if fila and fila[0] is not None:
            return float(fila[0])
    return CAPACIDAD_BODEGA_PT_TM


def validar_estacionariedad(serie_diaria: pd.Series) -> dict:
    y = serie_diaria.values
    n = len(y)
    if n < MIN_DIAS_PARA_ESTACIONARIEDAD:
        return {"apto_bootstrap_completo": None, "n_dias": n}

    mitad = n // 2
    try:
        _, adf_pvalue, *_ = adfuller(y, autolag="AIC")
        adf_ok = adf_pvalue < 0.05
    except Exception:
        adf_ok = None

    try:
        _, levene_pvalue = scipy_stats.levene(y[:mitad], y[mitad:])
        levene_ok = levene_pvalue >= 0.05
    except Exception:
        levene_ok = None

    apto = bool(adf_ok) and bool(levene_ok) if (adf_ok is not None and levene_ok is not None) else None
    return {"apto_bootstrap_completo": apto, "n_dias": n}


def bootstrap_var_factor_dlt(serie_diaria: pd.Series, dlt_dias: float, apto_bootstrap_completo: bool,
                             n_sim: int = N_SIMULACIONES_DLT, random_state: int = 42) -> dict:
    if pd.isna(dlt_dias) or dlt_dias <= 0:
        return {"var_factor": np.nan, "ventana_usada": None, "estado_bootstrap": "sin_dlt"}

    dlt_dias = max(1, round(dlt_dias))
    y_completa = serie_diaria.values
    n = len(y_completa)

    if apto_bootstrap_completo is True:
        y, ventana = y_completa, "completa"
    elif apto_bootstrap_completo is False:
        y, ventana = y_completa[n // 2:], "reciente_post_quiebre"
    else:
        y, ventana = y_completa, "sin_validar_estacionariedad"

    # Si la ventana reciente (post-quiebre) es corta para un lead time largo
    # pero la historia completa sí alcanza, se usa la completa antes que
    # dejar el ítem sin buffer (queda marcado en ventana_usada).
    if len(y) < dlt_dias * 3 and ventana == "reciente_post_quiebre" and n >= dlt_dias * 3:
        y, ventana = y_completa, "completa_por_lead_time_largo"
    if len(y) < dlt_dias * 3:
        return {"var_factor": np.nan, "ventana_usada": ventana, "estado_bootstrap": "datos_insuficientes"}

    rng = np.random.default_rng(random_state)
    inicios = len(y) - dlt_dias

    starts = rng.integers(0, inicios + 1, size=n_sim)
    window_offsets = np.arange(dlt_dias)
    sumas = y[starts[:, None] + window_offsets].sum(axis=1)

    media, std = sumas.mean(), sumas.std(ddof=1)
    vf = std / media if media > 0 else np.nan
    return {
        "var_factor": round(vf, 4) if not np.isnan(vf) else np.nan,
        "ventana_usada": ventana,
        "estado_bootstrap": "ok"
    }


def _acotar(v):
    if pd.isna(v):
        return np.nan
    return float(min(max(v, VAR_FACTOR_MIN), VAR_FACTOR_MAX))


def calcular_lt_factor(dias: float):
    """LTF por categoría de LEAD TIME (DDMRP): corto -> factor alto,
    largo -> factor bajo. Mismo criterio para PT y MP."""
    if pd.isna(dias):
        return None
    if dias <= LT_FACTOR_CORTE_CORTO:
        return LT_FACTOR_VALOR_CORTO
    if dias <= LT_FACTOR_CORTE_MEDIO:
        return LT_FACTOR_VALOR_MEDIO
    return LT_FACTOR_VALOR_LARGO


# Compatibilidad con scripts que importaban el nombre anterior.
calcular_lt_factor_mp = calcular_lt_factor


def calcular_zonas_vectorizado(df: pd.DataFrame, ciclo_pedido_dias: float = None) -> pd.DataFrame:
    """Zonas DDMRP. Verde = máx(ADU×DLT×LTF, MOQ, ADU×ciclo de pedido)."""
    res = pd.DataFrame(index=df.index)

    valid_mask = (
        df["adu"].notna() & (df["adu"] >= 0) &
        df["dlt_dias"].notna() & (df["dlt_dias"] > 0) &
        df["lt_factor"].notna() &
        df["var_factor_dinamico"].notna()
    )

    res["estado"] = np.where(valid_mask, "ok", "datos_insuficientes")

    adu = pd.to_numeric(df["adu"], errors="coerce")
    dlt = pd.to_numeric(df["dlt_dias"], errors="coerce")
    lt_factor = pd.to_numeric(df["lt_factor"], errors="coerce")
    var_factor = pd.to_numeric(df["var_factor_dinamico"], errors="coerce")

    red_base = adu * dlt * lt_factor
    red_safety = red_base * var_factor
    tor = red_base + red_safety
    yellow = adu * dlt

    green = adu * dlt * lt_factor
    if ciclo_pedido_dias is not None:
        green = np.maximum(green, adu * ciclo_pedido_dias)
    if "moq_tm" in df.columns:
        green = np.maximum(green, pd.to_numeric(df["moq_tm"], errors="coerce").fillna(0))

    toy = tor + yellow
    tog = toy + green

    res["red_base"] = np.where(valid_mask, red_base.round(1), None)
    res["red_safety"] = np.where(valid_mask, red_safety.round(1), None)
    res["tor"] = np.where(valid_mask, tor.round(1), None)
    res["yellow"] = np.where(valid_mask, yellow.round(1), None)
    res["green"] = np.where(valid_mask, pd.Series(green, index=df.index).round(1), None)
    res["toy"] = np.where(valid_mask, toy.round(1), None)
    res["tog"] = np.where(valid_mask, pd.Series(tog, index=df.index).round(1), None)

    return res


def cargar_todas_demandas_pt(conn) -> dict:
    df = pd.read_sql("SELECT sku, fecha, ventas_tm FROM ventas_demanda", conn, parse_dates=["fecha"])
    if df.empty:
        return {}

    series_dict = {}
    df["sku"] = df["sku"].astype(str)

    for sku, grp in df.groupby("sku"):
        diaria = grp.groupby("fecha")["ventas_tm"].sum()
        rango = pd.date_range(diaria.index.min(), diaria.index.max(), freq="D")
        series_dict[sku] = diaria.reindex(rango, fill_value=0.0)

    return series_dict


def cargar_forecast_sku(conn) -> pd.DataFrame:
    for tabla in ("forecast_sku_calibrado", "forecast_sku"):
        try:
            df = pd.read_sql(
                f"SELECT sku, mes_offset, p10, p50, p90, metodo_ganador, "
                f"rango_calibrado, wape_holdout, fva_holdout, semaforo "
                f"FROM {tabla} ORDER BY sku, mes_offset", conn)
            df["sku"] = df["sku"].astype(str)
            return df
        except Exception:
            continue
    print("[ERROR] No existe forecast_sku_calibrado ni forecast_sku.")
    return pd.DataFrame()


def calcular_buffer_pt(forecast_sku: pd.DataFrame, conn) -> pd.DataFrame:
    if forecast_sku.empty:
        return pd.DataFrame()

    df = forecast_sku.copy()
    # ADU del forecast mensual: p50 / días REALES de ese mes (no 30 fijo).
    mes_base = pd.Timestamp.today().to_period("M")
    df["dias_mes"] = df["mes_offset"].apply(lambda k: (mes_base + int(k)).days_in_month)
    df["adu"] = df["p50"] / df["dias_mes"]

    adu_hibrido = pd.read_sql("SELECT sku, adu_hibrido FROM adu_mes_actual_hibrido", conn)
    adu_hibrido["sku"] = adu_hibrido["sku"].astype(str)
    df = df.merge(adu_hibrido, on="sku", how="left")
    mask = (df["mes_offset"] == 0) & df["adu_hibrido"].notna()
    df.loc[mask, "adu"] = df.loc[mask, "adu_hibrido"]
    df = df.drop(columns=["adu_hibrido"])

    print(f"\n=== Nodo PT: {df['sku'].nunique()} SKU x "
          f"{df['mes_offset'].nunique()} meses = {len(df)} buffers ===")
    print(f"DLT fijo: {LEAD_TIME_PRODUCCION_DIAS} días. var_factor: bootstrap real.")

    demandas_pt = cargar_todas_demandas_pt(conn)

    estacionariedad = {}
    resultados_bootstrap = {}

    for sku in df["sku"].unique():
        serie = demandas_pt.get(sku, pd.Series(dtype=float))
        if serie.empty:
            estacionariedad[sku] = None
            resultados_bootstrap[sku] = {
                "apto_bootstrap_completo": None, "n_dias": 0,
                "var_factor": np.nan, "ventana_usada": None,
                "estado_bootstrap": "sin_datos"
            }
            continue

        est = validar_estacionariedad(serie)
        estacionariedad[sku] = est["apto_bootstrap_completo"]
        boot = bootstrap_var_factor_dlt(serie, LEAD_TIME_PRODUCCION_DIAS, est["apto_bootstrap_completo"])
        resultados_bootstrap[sku] = {**est, **boot}

    boot_df = pd.DataFrame.from_dict(resultados_bootstrap, orient="index").reset_index()
    boot_df = boot_df.rename(columns={"index": "sku"})
    df = df.merge(boot_df[["sku", "var_factor", "ventana_usada"]], on="sku", how="left")

    df["var_factor_dinamico"] = df["var_factor"].apply(_acotar)
    df["var_factor_metodo"] = "bootstrap_dlt"
    df["dlt_produccion_pt_dias"] = LEAD_TIME_PRODUCCION_DIAS
    df["dlt_dias"] = LEAD_TIME_PRODUCCION_DIAS

    # LTF por categoría de lead time (DDMRP). Todos los PT comparten el
    # lead time de producción, así que comparten el mismo LTF.
    df["lt_factor"] = calcular_lt_factor(LEAD_TIME_PRODUCCION_DIAS)
    df["ciclo_pedido_dias"] = CICLO_PEDIDO_PT_DIAS

    # Newsvendor (referencia, inactivo en TOR)
    cu_co = cargar_cu_co_por_sku(conn)
    if not cu_co.empty:
        cu_co["sku"] = cu_co["sku"].astype(str)
        df = df.merge(cu_co[["sku", "cu", "co", "cuantil_objetivo", "co_estimado"]], on="sku", how="left")
    else:
        for col in ["cu", "co", "cuantil_objetivo", "co_estimado"]:
            df[col] = np.nan

    # Cruce con clasificación ADI/CV² -- lumpy requiere atención especial.
    clas_sku = pd.read_sql(
        "SELECT sku, tipo_demanda, adi, cv2 FROM clasificacion_nivel_sku", conn
    )
    clas_sku["sku"] = clas_sku["sku"].astype(str)
    df = df.merge(clas_sku[["sku", "tipo_demanda", "adi", "cv2"]], on="sku", how="left")

    df["requiere_atencion_especial"] = df["tipo_demanda"] == "lumpy"
    df["motivo_atencion"] = np.where(
        df["requiere_atencion_especial"],
        "Demanda irregular (ADI alto + CV² alto): el buffer automático puede "
        "no ser suficiente. Revisar antes de confiar en el TOG calculado.",
        ""
    )

    tor_resultados = {}
    for sku in df["sku"].unique():
        serie_diaria = demandas_pt.get(sku, pd.Series(dtype=float))
        apto = estacionariedad.get(sku)
        fila = df.loc[df["sku"] == sku, "cuantil_objetivo"]
        cuantil_sku = fila.iloc[0] if not fila.empty else np.nan

        if not serie_diaria.empty:
            tor_resultados[sku] = bootstrap_tor_newsvendor(
                serie_diaria, LEAD_TIME_PRODUCCION_DIAS, cuantil_sku, apto
            )
        else:
            tor_resultados[sku] = {"tor_newsvendor": np.nan, "estado": "sin_datos"}

    tor_df = pd.DataFrame.from_dict(tor_resultados, orient="index").reset_index()
    tor_df = tor_df.rename(columns={"index": "sku"})
    df = df.merge(tor_df[["sku", "tor_newsvendor"]], on="sku", how="left")

    zonas = calcular_zonas_vectorizado(df, ciclo_pedido_dias=CICLO_PEDIDO_PT_DIAS)
    resultado = pd.concat([df, zonas], axis=1)

    resultado["nodo"] = "PT"
    resultado["buffer_respaldado"] = resultado["semaforo"].isin(SEMAFOROS_RESPALDADOS)

    # Los pedidos en firme NO agrandan el buffer: en DDMRP entran como
    # demanda calificada en la Ecuación de Flujo Neto (calcular_nfp.py),
    # que es la que dispara la reposición. Sumarlos también al TOR los
    # contaría dos veces.

    print(f"var_factor (bootstrap): mediana {df['var_factor_dinamico'].median():.2f} "
          f"(acotado {VAR_FACTOR_MIN:.2f}-{VAR_FACTOR_MAX:.2f})")
    n_reciente = int((df["ventana_usada"] == "reciente_post_quiebre").sum() / max(1, df["mes_offset"].nunique()))
    print(f" {n_reciente} SKU usaron ventana reciente (no estacionarios).")
    print(f"lt_factor PT: {calcular_lt_factor(LEAD_TIME_PRODUCCION_DIAS)} "
          f"(lead time de producción {LEAD_TIME_PRODUCCION_DIAS} días)")
    print(f"[INFO] newsvendor calculado como referencia (tor_newsvendor), "
          f"NO activo en TOR -- falta Co real (Planning no puede proveerlo).")

    n_lumpy = int(df.loc[df["mes_offset"] == 0, "requiere_atencion_especial"].sum())
    if n_lumpy:
        print(f" {n_lumpy} SKU clasificados 'lumpy' (irregular): buffer "
              f"automático puede no bastar, requieren revisión manual.")

    return resultado


def cargar_todos_consumos_mp(conn) -> dict:
    df = pd.read_sql(
        "SELECT sku_mp, fecha_contabilizacion AS fecha, consumo_tm FROM consumo_mp",
        conn, parse_dates=["fecha"]
    )
    if df.empty:
        return {}

    series_dict = {}
    df["sku_mp"] = df["sku_mp"].astype(str)

    # La serie llega hasta HOY, incluyendo los días sin consumo como 0.
    # Esto permite utilizar todo el consumo real disponible del mes actual.
    hoy = pd.Timestamp.today().normalize()

    for sku_mp, grp in df.groupby("sku_mp"):
        diaria = grp.groupby("fecha")["consumo_tm"].sum()
        diaria = diaria[diaria.index <= hoy]

        if diaria.empty:
            continue

        rango = pd.date_range(diaria.index.min(), hoy, freq="D")
        series_dict[sku_mp] = diaria.reindex(rango, fill_value=0.0)

    return series_dict


def calcular_buffer_mp(conn) -> pd.DataFrame:
    print(f"\n=== Nodo MP: ADU y var_factor desde consumo diario real ===")

    try:
        rmb_mp = pd.read_sql("SELECT * FROM rmb_mp", conn)
        rmb_mp["sku_mp"] = rmb_mp["sku_mp"].astype(str)
    except Exception:
        rmb_mp = pd.DataFrame(columns=["sku_mp", "anio", "mes_num", "rmb_tm"])
        print("[AVISO] Sin tabla 'rmb_mp' -- correr Ingesta_RMB_MP.py. ADU de MP "
              "usa solo promedio de consumo de los últimos 6 meses.")

    try:
        consumos_dict = cargar_todos_consumos_mp(conn)
        if not consumos_dict:
            print("[AVISO] Tabla 'consumo_mp' está vacía.")
            return pd.DataFrame()
    except Exception:
        print("[AVISO] Tabla 'consumo_mp' no existe.")
        return pd.DataFrame()

    try:
        lead_time_mp = pd.read_sql(
            "SELECT sku_mp, mp_descripcion, lead_time_total_dias FROM lead_time_mp", conn)
        lead_time_mp["sku_mp"] = lead_time_mp["sku_mp"].astype(str)
    except Exception:
        print("[AVISO] Tabla 'lead_time_mp' no existe -- sin buffer de MP.")
        return pd.DataFrame()

    hoy = pd.Timestamp.today().normalize()
    dias_mes = pd.Period(hoy, freq="M").days_in_month
    # El consumo disponible llega hasta HOY.
    # Por tanto, el RMB proyecta únicamente desde mañana hasta fin de mes.
    dias_transcurridos_mes = hoy.day
    dias_restantes = dias_mes - dias_transcurridos_mes

    filas = []
    for sku_mp, serie in consumos_dict.items():
        if serie.empty:
            continue

        lt_row = lead_time_mp[lead_time_mp["sku_mp"] == sku_mp]
        dlt = lt_row["lead_time_total_dias"].iloc[0] if not lt_row.empty else np.nan
        est = validar_estacionariedad(serie)
        boot = bootstrap_var_factor_dlt(serie, dlt, est["apto_bootstrap_completo"])

        # ADU híbrido (mismo criterio que PT): consumo real del mes en curso
        # + proyección del resto del mes con el RMB mensual, repartido en
        # TODOS los días del mes.
        consumo_real_mtd = serie[serie.index.to_period("M") == hoy.to_period("M")].sum()
        rmb_row = rmb_mp[(rmb_mp["sku_mp"] == sku_mp) & (rmb_mp["anio"] == hoy.year)
                         & (rmb_mp["mes_num"] == hoy.month)]
        if not rmb_row.empty:
            rmb_mensual = float(rmb_row["rmb_tm"].iloc[0])
            estimado_dias_restantes = rmb_mensual / dias_mes * dias_restantes
            adu = (consumo_real_mtd + estimado_dias_restantes) / dias_mes
            fuente_adu = "hibrido_rmb"
        else:
            # Sin RMB: promedio móvil de consumo de los últimos 6 meses.
            desde = hoy - pd.Timedelta(days=VENTANA_ADU_MP_DIAS)
            adu = float(serie[serie.index >= desde].mean()) if (serie.index >= desde).any() else 0.0
            fuente_adu = "consumo_6m"

        filas.append({
            "sku": sku_mp,
            "mp_descripcion": lt_row["mp_descripcion"].iloc[0] if not lt_row.empty else None,
            "adu": adu, "fuente_adu": fuente_adu,
            "dlt_dias": dlt, "lt_factor": calcular_lt_factor(dlt),
            "var_factor_dinamico": _acotar(boot["var_factor"]),
            "ventana_usada": boot["ventana_usada"],
            "n_dias_historia": len(serie),
        })

    df = pd.DataFrame(filas)
    if df.empty:
        return df

    # --- MP con stock pero SIN consumo registrado: también se listan ---
    # (sin buffer, pero visibles en la app con su motivo).
    try:
        cols_inv = {r[1] for r in conn.execute("PRAGMA table_info(posicion_inventario_mp)").fetchall()}
        if "material" in cols_inv:
            inv = pd.read_sql(
                "SELECT DISTINCT material FROM posicion_inventario_mp WHERE fecha_snapshot = "
                "(SELECT MAX(fecha_snapshot) FROM posicion_inventario_mp)", conn)
            inv_skus = set(inv["material"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True))
            faltan = sorted(inv_skus - set(df["sku"]))
            if faltan:
                extra = pd.DataFrame({"sku": faltan, "adu": 0.0, "fuente_adu": "sin_consumo",
                                      "n_dias_historia": 0})
                extra["dlt_dias"] = extra["sku"].map(
                    lead_time_mp.drop_duplicates("sku_mp").set_index("sku_mp")["lead_time_total_dias"])
                extra["lt_factor"] = extra["dlt_dias"].apply(calcular_lt_factor)
                extra["var_factor_dinamico"] = np.nan
                extra["ventana_usada"] = None
                df = pd.concat([df, extra], ignore_index=True, sort=False)
                print(f"  {len(faltan)} MP con inventario pero sin consumo registrado -- "
                      f"se listan sin buffer.")
    except Exception as e:
        print(f"  [AVISO] No se pudo cruzar con posicion_inventario_mp: {e}")

    # --- Descripción: lead_time_mp -> stock_mp -> rmb_mp -> OC ---
    df["sku"] = df["sku"].astype(str)
    if "mp_descripcion" not in df.columns:
        df["mp_descripcion"] = None
    for tabla, col_sku, col_desc in (("lead_time_mp", "sku_mp", "mp_descripcion"),
                                     ("stock_mp", "sku_mp", "mp_descripcion"),
                                     ("rmb_mp", "sku_mp", "mp_descripcion"),
                                     ("oc_importacion_mp", "sku_mp", "descripcion")):
        if not df["mp_descripcion"].isna().any():
            break
        try:
            d = pd.read_sql(f"SELECT {col_sku} AS sku, {col_desc} AS d FROM {tabla} "
                            f"WHERE {col_desc} IS NOT NULL AND TRIM({col_desc}) != ''", conn)
            d["sku"] = d["sku"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            mapa = d.drop_duplicates("sku").set_index("sku")["d"]
            df["mp_descripcion"] = df["mp_descripcion"].fillna(df["sku"].map(mapa))
        except Exception:
            continue

    n_sin_lt = int(df["dlt_dias"].isna().sum())
    if n_sin_lt:
        print(f"[WARN] {n_sin_lt} MP sin lead time -- sin buffer calculable.")
    print("Sin MOQ (confirmado: no existe mínimo real de proveedor)")

    zonas = calcular_zonas_vectorizado(df)
    resultado = pd.concat([df, zonas], axis=1)

    resultado["nodo"] = "MP"
    resultado["var_factor_metodo"] = "bootstrap_dlt"

    # Motivo por el que una MP no tiene buffer (lo muestra la app).
    resultado["motivo_sin_buffer"] = np.select(
        [resultado["estado"] == "ok",
         resultado["fuente_adu"] == "sin_consumo",
         resultado["dlt_dias"].isna(),
         resultado["dlt_dias"] <= 0],
        ["",
         "Sin consumo registrado",
         "Proveedor local (sin lead time en el Excel)",
         "Lead time 0 en el Excel -- cargar el lead time real"],
        default="Historia de consumo insuficiente para su lead time")

    n_reciente = int((df["ventana_usada"] == "reciente_post_quiebre").sum())
    print(f"var_factor (bootstrap): mediana {df['var_factor_dinamico'].median():.2f}")
    print(f" {n_reciente} MP usaron ventana reciente (no estacionarios).")

    # Rotación por terciles de ADU -- solo para priorizar alertas en la app.
    adu_positivos = resultado.loc[resultado["adu"] > 0, "adu"]
    if len(adu_positivos) >= 6:
        p33, p66 = np.percentile(adu_positivos, [33.33, 66.67])

        def _nivel(adu):
            if adu <= 0:
                return "sin_rotacion"
            if adu <= p33:
                return "baja"
            if adu <= p66:
                return "media"
            return "alta"
        resultado["nivel_rotacion"] = resultado["adu"].apply(_nivel)
    else:
        resultado["nivel_rotacion"] = "media"
    print(f" Rotación MP: {resultado['nivel_rotacion'].value_counts().to_dict()}")

    return resultado


def main():
    with sqlite3.connect(DB_PATH) as conn:
        print("=== Cargando forecast a nivel SKU ===")
        forecast_sku = cargar_forecast_sku(conn)
        buffer_pt = calcular_buffer_pt(forecast_sku, conn)
        buffer_mp = calcular_buffer_mp(conn)

        if not buffer_pt.empty:
            buffer_pt.to_sql("ddmrp_buffer_pt", conn, if_exists="replace", index=False)
        if not buffer_mp.empty:
            buffer_mp.to_sql("ddmrp_buffer_mp", conn, if_exists="replace", index=False)

        if not buffer_pt.empty:
            ok = buffer_pt[buffer_pt["estado"] == "ok"]
            m0 = ok[ok["mes_offset"] == 0]
            if not m0.empty:
                tog = pd.to_numeric(m0["tog"]).sum()
                tor = pd.to_numeric(m0["tor"]).sum()
                verde = pd.to_numeric(m0["green"]).sum()
                prom = tor + verde / 2
                adu_total = m0["adu"].sum()
                print(f"\n[OK] PT (mes 0): TOG {tog:,.0f} TM · TOR {tor:,.0f} TM · "
                      f"inventario promedio esperado (TOR + Verde/2) {prom:,.0f} TM"
                      + (f" · {tog/adu_total:.1f} días cobertura" if adu_total else ""))
                cap = _capacidad_bodega_pt_tm(conn)
                if cap:
                    print(f" Capacidad bodega PT: {cap:,.0f} TM ({prom/cap:.0%} con el inventario promedio)")

        if not buffer_mp.empty:
            ok_mp = buffer_mp[buffer_mp["estado"] == "ok"]
            if not ok_mp.empty:
                print(f"[OK] MP: TOG {pd.to_numeric(ok_mp['tog']).sum():,.0f} TM · "
                      f"TOR {pd.to_numeric(ok_mp['tor']).sum():,.0f} TM")


if __name__ == "__main__":
    main()