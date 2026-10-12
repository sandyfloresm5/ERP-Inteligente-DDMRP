"""
clasificar_mts_mto.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP: posicionamiento estratégico del inventario
"""

import sys
from pathlib import Path
from datetime import date

import pandas as pd
import numpy as np
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH, LEAD_TIME_PRODUCCION_DIAS
from pedidos_manuales import cargar_pedidos_manuales

try:
    from config import CAPACIDAD_BODEGA_PT_TM
except ImportError:
    CAPACIDAD_BODEGA_PT_TM = None

# ---------------------------------------------------------------------
# Parámetros del newsvendor -- SUPUESTOS DE NEGOCIO, documentados para
# poder discutirlos y no reabrir sin evidencia nueva.
# ---------------------------------------------------------------------
MESES_VENTANA_CLIENTE_ANCLA = 12   # ventana de histórico para el ranking de clientes
TASA_COSTO_CAPITAL_ANUAL = 0.25    # 25%/año -- referencia de industria, no medida
UMBRAL_MTS_PLENO = 0.85            # cuantil desde el cual el buffer es completo
UMBRAL_MTS_MINIMO = 0.55           # cuantil mínimo para llevar buffer reducido
PISO_ZONA_VERDE_DIAS = 1.0         # racionamiento de bodega: nunca deja la Zona
                                   # Verde (TOG-TOY) en exactamente 0 -- le
                                   # reserva mínimo este equivalente en días de
                                   # ADU, para que el SKU siga generando órdenes
                                   # de tamaño operativo en vez de lotes
                                   # infinitesimales. Sin MOQ real por proveedor
                                   # cargado en el sistema; ajustar si aparece.

# Semáforos que indican dimensionamiento confiable. NO deciden si hay
# buffer -- solo si el TAMAÑO de ese buffer tiene error validado.
SEMAFOROS_CONFIABLES = {"VERDE", "AMARILLO"}

# Piso de días de cobertura que un SKU debe conservar tras cualquier
# racionamiento (por capacidad de producción o de bodega). Mismo valor que
# CICLO_REPOSICION_BAJA_ROTACION_DIAS en optimizar_produccion.py.
DIAS_MINIMOS_COBERTURA_TRAS_RACIONAR = 4

HOY = pd.Timestamp(date.today())


def _existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _ultimo_snapshot(conn, t):
    return conn.execute(f"SELECT MAX(fecha_snapshot) FROM {t}").fetchone()[0]


def inventario_promedio_ddmrp(tor, toy, tog):
    """Inventario físico promedio esperado de un buffer DDMRP:
    TOR + Zona Verde / 2 (Ptak & Smith). La Zona Amarilla es producto
    EN CAMINO (órdenes abiertas), no stock en bodega, por eso no se cuenta
    contra la capacidad física."""
    tor = pd.to_numeric(tor, errors="coerce").fillna(0)
    verde = (pd.to_numeric(tog, errors="coerce") - pd.to_numeric(toy, errors="coerce")).fillna(0).clip(lower=0)
    return tor + verde / 2.0


def _peso_clientes_ancla(conn) -> pd.Series:
    """
    Para cada SKU, cuánto pesan en su venta los clientes que son ancla
    del negocio (rankeados por volumen TOTAL, no por SKU). Índice: sku.
    """
    if not _existe(conn, "ventas_demanda"):
        print("  [AVISO] Sin 'ventas_demanda': factor_criticidad = 1 para todos "
              "(sin ajuste por cliente ancla).")
        return pd.Series(dtype=float)

    corte = (HOY - pd.DateOffset(months=MESES_VENTANA_CLIENTE_ANCLA)).strftime("%Y-%m-%d")
    v = pd.read_sql(
        "SELECT sku, codigo_cliente, ventas_tm FROM ventas_demanda "
        "WHERE fecha > ? AND ventas_tm > 0",
        conn, params=(corte,),
    )
    if v.empty:
        return pd.Series(dtype=float)

    v["sku"] = v["sku"].astype(str).str.strip()

    vol_cliente = v.groupby("codigo_cliente")["ventas_tm"].sum()
    participacion_global = vol_cliente / vol_cliente.sum()

    v["participacion_cliente_global"] = v["codigo_cliente"].map(participacion_global)

    # peso_clientes_ancla_del_sku = suma de la participación global de cada
    # cliente distinto que compra ese SKU.
    peso = (v.drop_duplicates(["sku", "codigo_cliente"])
              .groupby("sku")["participacion_cliente_global"].sum())
    return peso


def _columnas_bootstrap_dlt(conn) -> list:
    """Nombres reales de las columnas p10/p50/p90 del bootstrap de
    demanda-en-DLT guardadas en ddmrp_buffer_pt (detectadas por PRAGMA)."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(ddmrp_buffer_pt)").fetchall()]
    candidatas = {
        "p10": ["p10", "p10_dlt", "bootstrap_p10"],
        "p50": ["p50", "p50_dlt", "bootstrap_p50"],
        "p90": ["p90", "p90_dlt", "bootstrap_p90"],
    }
    encontradas = []
    for _, opciones in candidatas.items():
        real = next((c for c in opciones if c in cols), None)
        if real:
            encontradas.append(real)
    return encontradas


def clasificar(conn) -> tuple:
    cols_bootstrap = _columnas_bootstrap_dlt(conn)
    extra_select = (", " + ", ".join(cols_bootstrap)) if cols_bootstrap else ""
    buf = pd.read_sql(
        f"SELECT sku, adu, tor, toy, tog, semaforo, wape_holdout{extra_select} "
        "FROM ddmrp_buffer_pt WHERE mes_offset = 0 AND estado = 'ok'",
        conn,
    )
    buf["sku"] = buf["sku"].astype(str).str.strip()
    for c in ("adu", "tor", "toy", "tog"):
        buf[c] = pd.to_numeric(buf[c], errors="coerce")

    mapa_canonico = {"p10": ["p10", "p10_dlt", "bootstrap_p10"],
                     "p50": ["p50", "p50_dlt", "bootstrap_p50"],
                     "p90": ["p90", "p90_dlt", "bootstrap_p90"]}
    for canonico, opciones in mapa_canonico.items():
        real = next((c for c in opciones if c in buf.columns), None)
        if real and real != canonico:
            buf[canonico] = buf[real]
        elif not real:
            buf[canonico] = np.nan
    if not cols_bootstrap:
        print("  [AVISO] Sin columnas p10/p50/p90 del bootstrap de demanda-en-DLT en "
              "ddmrp_buffer_pt -- el racionamiento por capacidad usará contribución "
              "marginal neutra (F=0.5) para los SKU sin ese dato.")

    # factor_ajuste de ajustar_buffer_daf_zaf.py (DAF × LTAF): escala la
    # demanda-en-DLT. Se aplica a p10/p50/p90 para que la contribución
    # marginal se evalúe contra una distribución consistente con el buffer
    # ya ajustado. 1.0 para cualquier SKU sin ajuste.
    if _existe(conn, "ddmrp_ajuste_daf_zaf"):
        factores = pd.read_sql("SELECT sku, factor_ajuste FROM ddmrp_ajuste_daf_zaf", conn)
        factores["sku"] = factores["sku"].astype(str).str.strip()
        factor_ajuste = buf["sku"].map(factores.set_index("sku")["factor_ajuste"]).fillna(1.0)
    else:
        factor_ajuste = pd.Series(1.0, index=buf.index)
    buf["_factor_ajuste_daf_zaf"] = factor_ajuste
    buf["p10_ajustado"] = buf["p10"] * factor_ajuste
    buf["p50_ajustado"] = buf["p50"] * factor_ajuste
    buf["p90_ajustado"] = buf["p90"] * factor_ajuste

    # --- Criterio de evidencia física: stock actual y pedidos pendientes ---
    if _existe(conn, "posicion_inventario_pt"):
        f = _ultimo_snapshot(conn, "posicion_inventario_pt")
        inv = pd.read_sql(
            "SELECT material, disponible_tm, transito_tm FROM posicion_inventario_pt "
            "WHERE fecha_snapshot = ?",
            conn, params=(f,),
        )
        inv["material"] = inv["material"].astype(str).str.strip()
        stock = inv.set_index("material")[["disponible_tm", "transito_tm"]].sum(axis=1)
    else:
        stock = pd.Series(dtype=float)

    if _existe(conn, "pedidos_pendientes_pt"):
        f = _ultimo_snapshot(conn, "pedidos_pendientes_pt")
        ped = pd.read_sql(
            "SELECT material, cantidad_tm, fecha_entrega FROM pedidos_pendientes_pt "
            "WHERE fecha_snapshot = ?",
            conn, params=(f,),
        )
        ped["material"] = ped["material"].astype(str).str.strip()
    else:
        ped = pd.DataFrame(columns=["material", "cantidad_tm", "fecha_entrega"])

    manuales = cargar_pedidos_manuales(conn)
    if not manuales.empty:
        ped = pd.concat([ped, manuales[["material", "cantidad_tm", "fecha_entrega"]]], ignore_index=True)

    pedidos_tm = ped.groupby("material")["cantidad_tm"].sum() if not ped.empty else pd.Series(dtype=float)

    buf["stock_tm"] = buf["sku"].map(stock).fillna(0.0)
    buf["pedidos_tm"] = buf["sku"].map(pedidos_tm).fillna(0.0)
    buf["c2_stock_o_pedidos"] = (buf["stock_tm"] > 0) | (buf["pedidos_tm"] > 0)

    # SKU con inventario físico pero sin buffer calculado -- solo para
    # visibilidad en posicionamiento_pt (no participan en la lógica MTS/MTO).
    inventario_skus = set(stock.index.astype(str))
    buffer_skus = set(buf["sku"].astype(str))
    skus_faltantes = inventario_skus - buffer_skus

    if skus_faltantes:
        extras = pd.DataFrame({"sku": list(skus_faltantes)})
        extras["adu"] = 0.0
        extras["tor"] = 0.0
        extras["toy"] = 0.0
        extras["tog"] = 0.0
        extras["semaforo"] = "SIN_BUFFER"
        extras["wape_holdout"] = np.nan
        extras["p10"] = np.nan
        extras["p50"] = np.nan
        extras["p90"] = np.nan
        extras["_factor_ajuste_daf_zaf"] = 1.0
        extras["p10_ajustado"] = np.nan
        extras["p50_ajustado"] = np.nan
        extras["p90_ajustado"] = np.nan
        extras["stock_tm"] = extras["sku"].map(stock).fillna(0.0)
        extras["pedidos_tm"] = extras["sku"].map(pedidos_tm).fillna(0.0)
        extras["c2_stock_o_pedidos"] = True

        buf = pd.concat([buf, extras], ignore_index=True, sort=False)
        print(f"[INFO] {len(extras)} SKU con inventario físico "
              f"sin buffer fueron agregados para monitoreo.")

    # --- Margen unitario (Cu) y costo unitario (base de Co) ---
    if _existe(conn, "precio_ponderado_sku") and _existe(conn, "ventas_demanda"):
        precio = pd.read_sql(
            "SELECT sku, precio_ponderado_tm FROM precio_ponderado_sku", conn)
        precio["sku"] = precio["sku"].astype(str).str.strip()

        costo = pd.read_sql(
            "SELECT sku, AVG(costos_tm) AS costo_tm FROM ventas_demanda "
            "WHERE costos_tm IS NOT NULL GROUP BY sku", conn)
        costo["sku"] = costo["sku"].astype(str).str.strip()

        margen = precio.merge(costo, on="sku", how="left")
        margen["margen_usd_tm"] = margen["precio_ponderado_tm"] - margen["costo_tm"]
        margen_map = margen.set_index("sku")["margen_usd_tm"]
        costo_map = margen.set_index("sku")["costo_tm"]
    else:
        print("  [AVISO] Sin 'precio_ponderado_sku'/'ventas_demanda': Cu y Co se "
              "aproximan solo con evidencia de stock/pedidos.")
        margen_map = pd.Series(dtype=float)
        costo_map = pd.Series(dtype=float)

    buf["margen_usd_tm"] = buf["sku"].map(margen_map)
    margen_mediano = buf["margen_usd_tm"].median()
    if pd.isna(margen_mediano):
        margen_mediano = 0.0
    buf["margen_es_estimado"] = buf["margen_usd_tm"].isna()
    buf["margen_usd_tm"] = buf["margen_usd_tm"].fillna(margen_mediano)

    buf["costo_usd_tm"] = buf["sku"].map(costo_map)
    costo_mediano = buf["costo_usd_tm"].median()
    if pd.isna(costo_mediano):
        costo_mediano = 0.0
    buf["costo_es_estimado"] = buf["costo_usd_tm"].isna()
    buf["costo_usd_tm"] = buf["costo_usd_tm"].fillna(costo_mediano)

    # --- Factor de criticidad por cliente ancla ---
    peso_ancla = _peso_clientes_ancla(conn)
    buf["peso_clientes_ancla"] = buf["sku"].map(peso_ancla).fillna(0.0)
    buf["factor_criticidad"] = 1.0 + buf["peso_clientes_ancla"]

    # --- Newsvendor: Cu, Co, cuantil ---
    buf["cu"] = buf["margen_usd_tm"] * buf["factor_criticidad"]
    dias_para_vender_1tm = (1.0 / buf["adu"].replace(0, np.nan)).clip(upper=365).fillna(365)
    buf["co"] = buf["costo_usd_tm"] * TASA_COSTO_CAPITAL_ANUAL * (dias_para_vender_1tm / 365.0)
    buf["co"] = buf["co"].clip(lower=0.01)
    buf["cuantil_objetivo"] = buf["cu"] / (buf["cu"] + buf["co"])

    UMBRAL_ADU_MINIMO_MTS = 10  # TM/día -- por debajo de esto, nunca MTS_PLENO

    def politica(row):
        if row["adu"] < 5:
            return "MTO"
        if not row["c2_stock_o_pedidos"] and row["cuantil_objetivo"] < UMBRAL_MTS_MINIMO:
            return "MTO"
        if row["adu"] < UMBRAL_ADU_MINIMO_MTS:
            return "MTS MINIMO" if row["cuantil_objetivo"] >= UMBRAL_MTS_MINIMO else "MTO"
        if row["cuantil_objetivo"] >= UMBRAL_MTS_PLENO:
            return "MTS PLENO"
        if row["cuantil_objetivo"] >= UMBRAL_MTS_MINIMO:
            return "MTS MINIMO"
        return "MTO"

    buf["politica"] = buf.apply(politica, axis=1)
    buf["es_mts"] = buf["politica"].isin(["MTS PLENO", "MTS MINIMO"])

    # --- Dimensionamiento del buffer según política ---
    # MTS_PLENO: buffer tal como lo calculó el cálculo base DDMRP.
    # MTS_MINIMO: Zona Roja y Verde recortadas en proporción al cuantil,
    #   Zona Amarilla (ADU x DLT) intacta.
    # MTO: TOG=0, se gestiona con alertas_mto().
    buf["tog_original"] = buf["tog"]

    rango = UMBRAL_MTS_PLENO - UMBRAL_MTS_MINIMO
    frac_minimo = ((buf["cuantil_objetivo"] - UMBRAL_MTS_MINIMO) / rango).clip(0.15, 1.0)

    zona_amarilla_orig = buf["toy"] - buf["tor"]
    zona_verde_orig = buf["tog"] - buf["toy"]
    tor_orig = buf["tor"]

    tor_minimo = tor_orig * frac_minimo
    toy_minimo = tor_minimo + zona_amarilla_orig
    tog_minimo = toy_minimo + zona_verde_orig * frac_minimo

    es_pleno = buf["politica"] == "MTS PLENO"
    es_minimo = buf["politica"] == "MTS MINIMO"

    buf["tor"] = np.select([es_pleno, es_minimo], [tor_orig, tor_minimo], default=0.0)
    buf["toy"] = np.select([es_pleno, es_minimo], [buf["toy"], toy_minimo], default=0.0)
    buf["tog"] = np.select([es_pleno, es_minimo], [buf["tog"], tog_minimo], default=0.0)

    buf = racionar_capacidad_produccion(buf, conn, ped)

    mask_mto = buf["politica"] == "MTO"
    buf.loc[mask_mto, ["tor", "toy", "tog"]] = 0.0
    buf["es_mts"] = buf["politica"].isin(["MTS PLENO", "MTS MINIMO"])

    # --- Subclasificación por rotación dentro de MTS ---
    mts_adu = buf.loc[buf["es_mts"], "adu"]
    if len(mts_adu) >= 6:
        p33, p66 = np.percentile(mts_adu, [33.33, 66.67])
    else:
        p33 = p66 = None

    def nivel_rotacion(row):
        if not row["es_mts"]:
            return ""
        if p33 is None:
            return "media"
        if row["adu"] <= p33:
            return "baja"
        if row["adu"] <= p66:
            return "media"
        return "alta"

    buf["nivel_rotacion"] = buf.apply(nivel_rotacion, axis=1)

    buf["dimensionamiento_confiable"] = buf["semaforo"].isin(SEMAFOROS_CONFIABLES)

    def motivo(r):
        if r["politica"] == "MTS PLENO":
            return ""
        if r["politica"] == "MTS MINIMO":
            return (f"cuantil {r['cuantil_objetivo']:.2f} entre "
                    f"{UMBRAL_MTS_MINIMO}-{UMBRAL_MTS_PLENO} -> Zona Roja y Verde "
                    f"recortadas a {frac_minimo[r.name]*100:.0f}% (Zona Amarilla "
                    f"intacta: ADU x DLT)")
        if r["adu"] < 1:
            return "ADU=0, sin rotación registrada -- buffer no aplica"
        faltan = []
        if not r["c2_stock_o_pedidos"]:
            faltan.append("sin stock ni pedidos")
        if r["cuantil_objetivo"] < UMBRAL_MTS_MINIMO:
            faltan.append(f"cuantil {r['cuantil_objetivo']:.2f} < {UMBRAL_MTS_MINIMO}")
        else:
            faltan.append(f"cuantil {r['cuantil_objetivo']:.2f} sin evidencia física que lo sostenga")
        if r["margen_es_estimado"]:
            faltan.append("margen estimado (mediana del catálogo, no medido)")
        if r["costo_es_estimado"]:
            faltan.append("costo estimado (mediana del catálogo, no medido)")
        return " · ".join(faltan)

    buf["motivo_mto"] = buf.apply(motivo, axis=1)

    def motivo_capacidad(row):
        if row["degradado_por_capacidad_produccion"]:
            return (f"cuantil {row['cuantil_objetivo']:.2f} -- degradado a MTO porque el ADU "
                    f"total de MTS excedía la capacidad de producción real")
        return row["motivo_mto"]

    buf["motivo_mto"] = buf.apply(motivo_capacidad, axis=1)

    # --- Ajustes manuales del planificador (Inventario Estratégico) ---
    # Antes del racionamiento de bodega: el override no se recorta, pero
    # cuenta en el total contra la capacidad física.
    buf = aplicar_ajustes_manuales(buf, conn)

    buf = racionar_capacidad_bodega(buf, conn, ped)

    buf["inventario_promedio_tm"] = inventario_promedio_ddmrp(buf["tor"], buf["toy"], buf["tog"]).round(1)
    buf["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    return buf, ped


def cargar_ajustes_manuales_vigentes(conn) -> pd.DataFrame:
    """Último ajuste manual de buffer (TOR/TOY/TOG) por SKU, hecho desde
    Inventario Estratégico (tabla ddmrp_overrides_auditoria)."""
    if not _existe(conn, "ddmrp_overrides_auditoria"):
        return pd.DataFrame(columns=["sku", "tor", "toy", "tog", "actualizado_en"])
    df = pd.read_sql(
        "SELECT sku, tor_nuevo AS tor, toy_nuevo AS toy, tog_nuevo AS tog, actualizado_en "
        "FROM ddmrp_overrides_auditoria ORDER BY actualizado_en",
        conn,
    )
    if df.empty:
        return df
    df["sku"] = df["sku"].astype(str).str.strip()
    return df.drop_duplicates("sku", keep="last")


def aplicar_ajustes_manuales(buf: pd.DataFrame, conn) -> pd.DataFrame:
    """Reaplica el último ajuste manual vigente de cada SKU por encima del
    TOR/TOY/TOG calculado. TOG 0 -> MTO; TOG > 0 -> MTS."""
    buf["tor_recomendado_sistema"] = buf["tor"]
    buf["toy_recomendado_sistema"] = buf["toy"]
    buf["tog_recomendado_sistema"] = buf["tog"]

    ajustes = cargar_ajustes_manuales_vigentes(conn)
    buf["ajuste_manual"] = False
    if ajustes.empty:
        return buf

    aplicados = 0
    for _, fila in ajustes.iterrows():
        m = buf["sku"] == fila["sku"]
        if not m.any():
            continue
        buf.loc[m, "tor"] = fila["tor"]
        buf.loc[m, "toy"] = fila["toy"]
        buf.loc[m, "tog"] = fila["tog"]
        buf.loc[m, "ajuste_manual"] = True
        buf.loc[m, "tog_racionado"] = False
        if fila["tog"] > 0:
            buf.loc[m, "es_mts"] = True
            buf.loc[m & (buf["politica"] == "MTO"), "politica"] = "MTS MINIMO"
            buf.loc[m, "motivo_mto"] = "Ajuste manual del planificador desde Inventario Estratégico"
        else:
            buf.loc[m, "es_mts"] = False
            buf.loc[m, "politica"] = "MTO"
            buf.loc[m, "motivo_mto"] = "Ajuste manual del planificador: TOG en 0 (sin buffer)"
        aplicados += 1

    if aplicados:
        print(f" {aplicados} SKU con ajuste manual vigente (Inventario Estratégico) -- "
              f"se reaplicó su TOR/TOY/TOG por encima del cálculo del newsvendor.")
    return buf


def _capacidad_produccion_tm_dia(conn) -> float:
    """Capacidad de producción real, TM/día: mejor combinación (mayor TM/día)
    por línea física, sumada entre líneas."""
    if not _existe(conn, "config_lineas_produccion"):
        return None
    lineas = pd.read_sql(
        "SELECT planta, linea, horas_dia, rate_tm_hora FROM config_lineas_produccion "
        "WHERE activo = 1",
        conn,
    )
    lineas = lineas.dropna(subset=["rate_tm_hora"])
    if lineas.empty:
        return None
    lineas["recurso"] = (lineas["planta"].astype(str).str.upper().str.strip() + "|" +
                         lineas["linea"].astype(str).str.upper().str.strip())
    lineas["cap_dia"] = lineas["horas_dia"] * lineas["rate_tm_hora"]
    mejor_por_recurso = lineas.groupby("recurso")["cap_dia"].max()
    return float(mejor_por_recurso.sum())


def _f_empirica(q, p10, p50, p90) -> float:
    """F(q) = P(demanda-en-DLT <= q) por interpolación lineal entre los
    percentiles del bootstrap empírico. Sin dato -> 0.5 (neutral)."""
    if pd.isna(p10) or pd.isna(p50) or pd.isna(p90):
        return 0.5
    if p10 >= p90:
        return 1.0 if q >= p50 else 0.0
    if q <= p10:
        pendiente = 0.10 / max(p50 - p10, 1e-9)
        return max(0.0, 0.10 - pendiente * (p10 - q))
    if q <= p50:
        pendiente = 0.40 / max(p50 - p10, 1e-9)
        return 0.10 + pendiente * (q - p10)
    if q <= p90:
        pendiente = 0.40 / max(p90 - p50, 1e-9)
        return 0.50 + pendiente * (q - p50)
    pendiente = 0.10 / max(p90 - p50, 1e-9)
    return min(1.0, 0.90 + pendiente * (q - p90))


def _contribucion_marginal(buf: pd.DataFrame, q_col: str) -> pd.Series:
    """MC_i(Q_i) = Cu_i x (1 - F_i(Q_i)) - Co_i x F_i(Q_i) (Chopra & Meindl,
    cap. 13.4), con F_i de la distribución empírica ajustada por DAF × LTAF."""
    f = buf.apply(
        lambda r: _f_empirica(r[q_col], r["p10_ajustado"], r["p50_ajustado"], r["p90_ajustado"]),
        axis=1)
    return buf["cu"] * (1 - f) - buf["co"] * f


def _capacidad_bodega_pt_tm(conn):
    """Capacidad de bodega PT de Configuración; respaldo: config.py."""
    if _existe(conn, "config_capacidad_bodega"):
        fila = conn.execute(
            "SELECT capacidad_pt_tm FROM config_capacidad_bodega WHERE id = 1"
        ).fetchone()
        if fila and fila[0] is not None:
            return float(fila[0])
    return CAPACIDAD_BODEGA_PT_TM


HORIZONTE_MAX_PISO_REACCION_DIAS = 14  # tope del piso de reacción usado al racionar


def _cargar_adu_hibrido(conn) -> pd.Series:
    """ADU real del mes en curso (adu_mes_actual_hibrido), indexado por sku."""
    if not _existe(conn, "adu_mes_actual_hibrido"):
        return pd.Series(dtype=float)
    df = pd.read_sql("SELECT sku, adu_hibrido FROM adu_mes_actual_hibrido", conn)
    df["sku"] = df["sku"].astype(str).str.strip()
    return df.set_index("sku")["adu_hibrido"]


def _piso_reaccion_por_sku(ped: pd.DataFrame) -> pd.DataFrame:
    """Por SKU: días hasta el próximo aviso real de demanda y demanda ya
    calificada dentro de ese hueco. Se usa SOLO para proteger SKU al
    racionar capacidad, no para dimensionar el buffer."""
    if ped.empty:
        return pd.DataFrame(columns=["sku", "dias_piso_pedidos", "demanda_calificada_piso"])

    p = ped.copy()
    p["sku"] = p["material"].astype(str).str.strip()
    p["fecha_entrega"] = pd.to_datetime(p["fecha_entrega"], errors="coerce")
    p = p.dropna(subset=["fecha_entrega"])

    filas = []
    for sku, grupo in p.groupby("sku"):
        fechas = grupo.sort_values("fecha_entrega")
        gap_inicial = (fechas["fecha_entrega"].iloc[0] - HOY).days
        gaps = [gap_inicial] + list(fechas["fecha_entrega"].diff().dt.days.dropna())
        gap_max = max(gaps) if gaps else DIAS_MINIMOS_COBERTURA_TRAS_RACIONAR
        dias_piso = max(DIAS_MINIMOS_COBERTURA_TRAS_RACIONAR,
                        min(gap_max, HORIZONTE_MAX_PISO_REACCION_DIAS))
        limite = HOY + pd.Timedelta(days=dias_piso)
        demanda_calificada = fechas.loc[fechas["fecha_entrega"] <= limite, "cantidad_tm"].sum()
        filas.append((sku, dias_piso, float(demanda_calificada)))

    return pd.DataFrame(filas, columns=["sku", "dias_piso_pedidos", "demanda_calificada_piso"])


def _agregar_piso_reaccion(buf: pd.DataFrame, conn, ped: pd.DataFrame) -> pd.DataFrame:
    adu_hib = _cargar_adu_hibrido(conn)
    buf["_adu_referencia"] = buf["sku"].map(adu_hib)
    buf["_adu_referencia"] = buf["_adu_referencia"].fillna(buf["adu"])

    piso = _piso_reaccion_por_sku(ped)
    buf = buf.drop(columns=["dias_piso_pedidos", "demanda_calificada_piso"], errors="ignore")
    buf = buf.merge(piso, on="sku", how="left")
    buf["dias_piso_pedidos"] = buf["dias_piso_pedidos"].fillna(DIAS_MINIMOS_COBERTURA_TRAS_RACIONAR)
    buf["demanda_calificada_piso"] = buf["demanda_calificada_piso"].fillna(0.0)

    piso_por_ritmo = buf["dias_piso_pedidos"] * buf["_adu_referencia"]
    buf["_tor_minimo_reaccion"] = np.maximum(piso_por_ritmo, buf["demanda_calificada_piso"])
    return buf


def _score_hibrido_racionamiento(buf: pd.DataFrame, conn, ped: pd.DataFrame) -> pd.DataFrame:
    """score = _mc_marginal × exposicion_tm × factor_ajuste × factor_tendencia
    (si la base es negativa se divide por el factor en vez de multiplicar,
    para que un factor > 1 siempre proteja)."""
    if "_mc_marginal" not in buf.columns:
        buf["_mc_marginal"] = _contribucion_marginal(buf, "tog")
    if "_tor_minimo_reaccion" not in buf.columns:
        buf = _agregar_piso_reaccion(buf, conn, ped)
    if "_factor_ajuste_daf_zaf" not in buf.columns:
        buf["_factor_ajuste_daf_zaf"] = 1.0

    if "pedidos_tm" not in buf.columns:
        buf["pedidos_tm"] = 0.0
    exposicion_tm = np.maximum(buf["pedidos_tm"], buf["_tor_minimo_reaccion"])
    buf["_exposicion_tm"] = exposicion_tm

    adu_hib = _cargar_adu_hibrido(conn)
    adu_hibrido_sku = buf["sku"].map(adu_hib)
    factor_tendencia = (
        (adu_hibrido_sku / buf["adu"].replace(0, np.nan)).fillna(1.0).clip(lower=0.3, upper=3.0)
    )
    buf["_factor_tendencia"] = factor_tendencia

    riesgo_valor = buf["_mc_marginal"] * exposicion_tm
    factor_total = (buf["_factor_ajuste_daf_zaf"] * factor_tendencia).replace(0, np.nan).fillna(1.0)
    buf["_score_hibrido"] = np.where(
        riesgo_valor >= 0, riesgo_valor * factor_total, riesgo_valor / factor_total)
    return buf


def racionar_capacidad_produccion(buf: pd.DataFrame, conn, ped: pd.DataFrame) -> pd.DataFrame:
    """Si el ADU total de los MTS supera la capacidad de producción real
    (TM/día), degrada a MTO por score híbrido (más bajo primero)."""
    capacidad = _capacidad_produccion_tm_dia(conn)
    buf["degradado_por_capacidad_produccion"] = False
    if not capacidad:
        print("  [AVISO] Sin 'config_lineas_produccion' o sin rate_tm_hora cargado: "
              "no se pudo verificar la capacidad de producción real.")
        return buf

    es_mts_actual = buf["politica"].isin(["MTS PLENO", "MTS MINIMO"])
    adu_total = buf.loc[es_mts_actual, "adu"].sum()

    if adu_total <= capacidad:
        print(f"  Capacidad de producción: ADU total en MTS {adu_total:,.1f} TM/día de "
              f"{capacidad:,.1f} TM/día reales -- cabe, sin degradar ningún SKU.")
        return buf

    exceso = adu_total - capacidad
    print(f"  ⚠️  Capacidad de producción: ADU total en MTS {adu_total:,.1f} TM/día excede "
          f"los {capacidad:,.1f} TM/día reales por {exceso:,.1f} TM/día -- degradando a MTO "
          f"por score híbrido (más bajo primero) hasta que quepa.")

    buf = _score_hibrido_racionamiento(buf, conn, ped)

    buf["_dias_cobertura_actual"] = np.where(
        buf["_adu_referencia"] > 0, buf["tog"] / buf["_adu_referencia"], np.inf)
    buf["_protegido_por_rotacion"] = es_mts_actual & (buf["tog"] < buf["_tor_minimo_reaccion"])

    candidatos = buf.loc[es_mts_actual & ~buf["_protegido_por_rotacion"]]
    orden = candidatos.sort_values("_score_hibrido", ascending=True).index

    restante = exceso
    n_protegidos = int(buf["_protegido_por_rotacion"].sum())
    if n_protegidos:
        print(f"  {n_protegidos} SKU por debajo de su propio piso de reacción -- "
              f"protegidos de la degradación aunque su contribución marginal sea baja.")

    for idx in orden:
        if restante <= 0:
            break
        adu_i = buf.loc[idx, "adu"]
        buf.loc[idx, "politica"] = "MTO"
        buf.loc[idx, "es_mts"] = False
        buf.loc[idx, "degradado_por_capacidad_produccion"] = True
        restante -= adu_i

    if restante > 0.01 and n_protegidos:
        print(f"  ⚠️  Aun degradando todos los SKU no protegidos, faltan {restante:,.1f} "
              f"TM/día -- se degradan también SKU protegidos, por score más bajo.")
        orden_protegidos = (buf.loc[es_mts_actual & buf["_protegido_por_rotacion"]]
                            .sort_values("_score_hibrido", ascending=True).index)
        for idx in orden_protegidos:
            if restante <= 0:
                break
            adu_i = buf.loc[idx, "adu"]
            buf.loc[idx, "politica"] = "MTO"
            buf.loc[idx, "es_mts"] = False
            buf.loc[idx, "degradado_por_capacidad_produccion"] = True
            restante -= adu_i

    n_degradados = int(buf["degradado_por_capacidad_produccion"].sum())
    adu_final = buf.loc[buf["politica"].isin(["MTS PLENO", "MTS MINIMO"]), "adu"].sum()
    print(f"  {n_degradados} SKU degradados a MTO. ADU final en MTS: {adu_final:,.1f} TM/día.")
    if restante > 0.01:
        print(f"  ⚠️  Aun degradando todo lo posible faltan {restante:,.1f} TM/día -- "
              f"capacidad física de producción insuficiente: hay que escalarlo.")

    return buf


def racionar_capacidad_bodega(buf: pd.DataFrame, conn, ped: pd.DataFrame) -> pd.DataFrame:
    """
    La capacidad de bodega es FÍSICA, así que se compara contra el
    inventario físico que el buffer genera, no contra el TOG. En DDMRP el
    inventario promedio esperado es TOR + Zona Verde / 2 (Ptak & Smith):
    la Zona Amarilla es producto en camino (órdenes abiertas), no stock.

    Si la suma de inventario promedio supera la bodega, se recorta SOLO la
    Zona Verde (nunca Roja ni Amarilla), con piso PISO_ZONA_VERDE_DIAS de
    ADU. Recortar X TM de verde baja el inventario promedio en X/2, por eso
    el verde a recortar es 2 × exceso. A quién recortar primero: reparto
    proporcional por score híbrido (ver _recortar_proporcional).
    """
    capacidad = _capacidad_bodega_pt_tm(conn)
    buf["tog_antes_racionamiento"] = buf["tog"]
    buf["tog_racionado"] = False
    if not capacidad:
        return buf

    elegibles = buf["politica"].isin(["MTS PLENO", "MTS MINIMO"])
    inv_prom = inventario_promedio_ddmrp(buf["tor"], buf["toy"], buf["tog"])
    total_prom = inv_prom[elegibles].sum()

    if total_prom <= capacidad:
        print(f"  Capacidad de bodega PT: inventario promedio esperado (TOR + Verde/2) "
              f"{total_prom:,.0f} TM de {capacidad:,.0f} TM -- cabe, sin racionamiento.")
        return buf

    exceso = total_prom - capacidad
    verde_a_recortar = 2.0 * exceso
    print(f"  ⚠️  Capacidad de bodega PT: inventario promedio esperado {total_prom:,.0f} TM "
          f"excede los {capacidad:,.0f} TM por {exceso:,.0f} TM -- se recortan "
          f"{verde_a_recortar:,.0f} TM de Zona Verde (reparto proporcional por score).")

    buf = _score_hibrido_racionamiento(buf, conn, ped)

    protegido = pd.Series(False, index=buf.index)
    protegido.loc[elegibles] = buf.loc[elegibles, "toy"] < buf.loc[elegibles, "_tor_minimo_reaccion"]
    protegido = protegido & (buf["tog"] < buf["_tor_minimo_reaccion"] * 2)

    con_override = buf.get("ajuste_manual", pd.Series(False, index=buf.index)).fillna(False).astype(bool)

    candidatos = buf.loc[elegibles & ~protegido & ~con_override]

    n_protegidos = int(protegido.sum())
    if n_protegidos:
        print(f"  {n_protegidos} SKU protegidos del recorte de bodega (quedarían por debajo "
              f"de su propio piso de reacción).")
    n_override = int((elegibles & con_override).sum())
    if n_override:
        print(f"  {n_override} SKU con ajuste manual vigente -- excluidos del recorte, "
              f"pero cuentan en el total.")

    def _recortar_proporcional(indices, restante):
        """Reparte `restante` TM de Zona Verde entre todos los `indices`,
        con peso por magnitud del score híbrido (score más bajo cede más),
        acotado a la Zona Verde recortable de cada SKU (water-filling)."""
        if restante <= 0 or not len(indices):
            return restante
        orden_local = list(
            buf.loc[indices].sort_values("_score_hibrido", ascending=True).index)
        n = len(orden_local)
        scores = buf.loc[orden_local, "_score_hibrido"]
        score_max = scores.max()
        score_min = scores.min()
        rango = score_max - score_min
        margen = rango * 0.05 if rango > 0 else 1.0
        pesos = {idx: (score_max - scores.loc[idx] + margen) for idx in orden_local}
        activos = list(orden_local)

        for _ in range(n + 1):
            if restante <= 0.5 or not activos:
                break
            peso_total = sum(pesos[idx] for idx in activos)
            if peso_total <= 0:
                break
            aplicado = 0.0
            siguen_activos = []
            for idx in activos:
                tog_i = buf.loc[idx, "tog"]
                toy_i = buf.loc[idx, "toy"]
                zona_verde_i = max(tog_i - toy_i, 0.0)
                piso_verde_i = min(zona_verde_i, buf.loc[idx, "adu"] * PISO_ZONA_VERDE_DIAS)
                recortable = max(zona_verde_i - piso_verde_i, 0.0)
                cuota = restante * pesos[idx] / peso_total
                corte = min(cuota, recortable)
                if corte > 0.01:
                    buf.loc[idx, "tog"] = tog_i - corte
                    buf.loc[idx, "tog_racionado"] = True
                    aplicado += corte
                if corte < recortable - 0.01:
                    siguen_activos.append(idx)
            if aplicado <= 0.01:
                break
            restante -= aplicado
            activos = siguen_activos

        return restante

    restante = _recortar_proporcional(candidatos.index, verde_a_recortar)

    if restante > 0.5 and n_protegidos:
        print(f"  ⚠️  Aun recortando a todos los SKU no protegidos faltan {restante:,.0f} TM de "
              f"Zona Verde -- se recorta también a los protegidos, como último recurso.")
        indices_protegidos = buf.loc[elegibles & protegido & ~con_override].index
        restante = _recortar_proporcional(indices_protegidos, restante)

    n_racionados = int(buf["tog_racionado"].sum())
    prom_final = inventario_promedio_ddmrp(buf["tor"], buf["toy"], buf["tog"])[elegibles].sum()
    print(f"  {n_racionados} SKU recortados. Inventario promedio esperado final: "
          f"{prom_final:,.0f} TM.")
    if restante > 0.5:
        print(f"  ⚠️  Aun con la Zona Verde en su piso mínimo ({PISO_ZONA_VERDE_DIAS:.0f} día de "
              f"ADU), la bodega no alcanza por {restante / 2:,.0f} TM de inventario promedio: "
              f"es capacidad física insuficiente (más bodega o menos mix/ADU).")

    return buf


def alertas_mto(buf: pd.DataFrame, ped: pd.DataFrame) -> pd.DataFrame:
    """Pedidos de SKU sin buffer (MTO) que hay que lanzar a producción con
    la anticipación del lead time."""
    mto = set(buf.loc[buf["politica"] == "MTO", "sku"])
    if ped.empty or not mto:
        return pd.DataFrame()

    p = ped[ped["material"].isin(mto)].copy()
    if p.empty:
        return pd.DataFrame()

    p["fecha_entrega"] = pd.to_datetime(p["fecha_entrega"], errors="coerce")
    p = p.dropna(subset=["fecha_entrega"])

    a = (p.groupby("material")
         .agg(cantidad_tm=("cantidad_tm", "sum"),
              n_pedidos=("cantidad_tm", "count"),
              primera_entrega=("fecha_entrega", "min"))
         .reset_index()
         .rename(columns={"material": "sku"}))

    a["fecha_limite_lanzamiento"] = (a["primera_entrega"]
                                     - pd.Timedelta(days=LEAD_TIME_PRODUCCION_DIAS))
    a["dias_para_lanzar"] = (a["fecha_limite_lanzamiento"] - HOY).dt.days

    stock = buf.set_index("sku")["stock_tm"]
    a["stock_tm"] = a["sku"].map(stock).fillna(0.0)
    a["a_producir_tm"] = (a["cantidad_tm"] - a["stock_tm"]).clip(lower=0)

    a["urgencia"] = np.select(
        [a["a_producir_tm"] <= 0,
         a["dias_para_lanzar"] < 0,
         a["dias_para_lanzar"] <= 1],
        ["CUBIERTO", "TARDE", "HOY"], default="A_TIEMPO")

    a["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    return a.sort_values("dias_para_lanzar")


def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"Posicionamiento MTS_PLENO / MTS_MINIMO / MTO (newsvendor) — {HOY:%Y-%m-%d}")

    if not _existe(conn, "ddmrp_buffer_pt"):
        print("[ERROR] Falta 'ddmrp_buffer_pt'. Correr calcular_buffer_ddmrp_dinamico.py.")
        conn.close()
        return

    buf, ped = clasificar(conn)

    buf.to_sql("posicionamiento_pt", conn, if_exists="replace", index=False)
    alertas = alertas_mto(buf, ped)
    if not alertas.empty:
        alertas.to_sql("alertas_mto", conn, if_exists="replace", index=False)
    conn.commit()

    pleno = buf[buf["politica"] == "MTS PLENO"]
    minimo = buf[buf["politica"] == "MTS MINIMO"]
    mto = buf[buf["politica"] == "MTO"]

    print("\n" + "=" * 60)
    print("POSICIONAMIENTO (newsvendor: cuantil = Cu / (Cu+Co))")
    print("=" * 60)
    print(f"  MTS pleno:  {len(pleno)} SKU · TOG {pleno['tog'].sum():,.0f} TM "
          f"(cuantil >= {UMBRAL_MTS_PLENO})")
    print(f"  MTS mínimo: {len(minimo)} SKU · TOG {minimo['tog'].sum():,.0f} TM "
          f"({UMBRAL_MTS_MINIMO} <= cuantil < {UMBRAL_MTS_PLENO}, buffer recortado)")
    print(f"  MTO puro:   {len(mto)} SKU (cuantil < {UMBRAL_MTS_MINIMO})")

    if "ajuste_manual" in buf.columns:
        n_ajustados = int(buf["ajuste_manual"].sum())
        if n_ajustados:
            print(f"\n  ⚙ {n_ajustados} SKU con ajuste manual vigente (Inventario Estratégico).")

    mts_adu_main = buf.loc[buf["es_mts"], "adu"]
    if len(mts_adu_main) >= 6:
        rot = buf.loc[buf["es_mts"], "nivel_rotacion"].value_counts()
        print(f"\n  Rotación dentro de MTS -- alta: {rot.get('alta',0)} · "
              f"media: {rot.get('media',0)} · baja: {rot.get('baja',0)}")

    tog_antes = buf["tog_original"].sum()
    tog_ahora = pleno["tog"].sum() + minimo["tog"].sum()
    prom_ahora = buf.loc[buf["es_mts"], "inventario_promedio_tm"].sum()
    print(f"\n  TOG antes de clasificar: {tog_antes:,.0f} TM")
    print(f"  TOG con newsvendor:      {tog_ahora:,.0f} TM "
          f"({tog_ahora/max(tog_antes,1e-9) - 1:+.1%})")
    print(f"  Inventario promedio esperado (TOR + Verde/2): {prom_ahora:,.0f} TM")

    cap = _capacidad_bodega_pt_tm(conn)
    if cap:
        print(f"  Capacidad de bodega:     {cap:,.0f} TM")
        diff = prom_ahora - cap
        cabe = diff <= 0.5
        print(f"  {'CABE' if cabe else 'EXCEDE'} con el inventario promedio "
              f"({abs(diff):,.0f} TM de {'holgura' if cabe else 'exceso'})")

    print(f"\n--- Supuestos usados (documentar / validar con Finanzas) ---")
    print(f"  Tasa de costo de capital anual: {TASA_COSTO_CAPITAL_ANUAL:.0%} "
          f"(referencia de industria, NO medida en el negocio)")
    print(f"  Umbral MTS pleno / mínimo: {UMBRAL_MTS_PLENO} / {UMBRAL_MTS_MINIMO}")
    sin_margen = buf["margen_es_estimado"].sum()
    if sin_margen:
        print(f"  {sin_margen} SKU sin margen registrado -> se usó la mediana "
              f"del catálogo (${buf['margen_usd_tm'].median():,.0f}/TM) como estimado.")
    sin_costo = buf["costo_es_estimado"].sum()
    if sin_costo:
        print(f"  {sin_costo} SKU sin costo registrado -> se usó la mediana "
              f"del catálogo (${buf['costo_usd_tm'].median():,.0f}/TM) como estimado.")

    if len(minimo):
        print(f"\n--- SKU en MTS_MINIMO ---")
        cols = ["sku", "adu", "cuantil_objetivo", "peso_clientes_ancla", "tog", "motivo_mto"]
        print(minimo[cols].round(2).sort_values("cuantil_objetivo", ascending=False)
              .head(20).to_string(index=False))

    if len(mto):
        print(f"\n--- SKU en MTO puro ---")
        cols = ["sku", "adu", "cuantil_objetivo", "tog_original", "stock_tm",
                "pedidos_tm", "motivo_mto"]
        print(mto[cols].round(2).sort_values("tog_original", ascending=False)
              .head(20).to_string(index=False))

    print("\n" + "=" * 60)
    print("ALERTAS MTO — pedidos que requieren lanzar producción")
    print("=" * 60)
    if alertas.empty:
        print("  Ninguna. Ningún SKU en MTO puro tiene pedidos pendientes.")
    else:
        for u in ["TARDE", "HOY", "A_TIEMPO", "CUBIERTO"]:
            sub = alertas[alertas["urgencia"] == u]
            if not len(sub):
                continue
            etiqueta = {"TARDE": "YA VENCIÓ EL LANZAMIENTO",
                        "HOY": "LANZAR HOY",
                        "A_TIEMPO": "A tiempo",
                        "CUBIERTO": "Cubierto con stock — no requiere producir"}[u]
            print(f"\n  {etiqueta} ({len(sub)}):")
            vista = sub[["sku", "cantidad_tm", "stock_tm", "a_producir_tm",
                         "primera_entrega", "fecha_limite_lanzamiento",
                         "dias_para_lanzar"]].copy()
            for c in ["cantidad_tm", "stock_tm", "a_producir_tm"]:
                vista[c] = vista[c].round(1)
            for c in ["primera_entrega", "fecha_limite_lanzamiento"]:
                vista[c] = pd.to_datetime(vista[c]).dt.strftime("%Y-%m-%d")
            print(vista.to_string(index=False))

    print("\n[OK] Tablas: posicionamiento_pt" +
          (", alertas_mto" if not alertas.empty else ""))
    conn.close()


if __name__ == "__main__":
    main()