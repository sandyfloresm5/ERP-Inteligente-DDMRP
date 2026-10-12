"""
calcular_nfp.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP: ecuación de flujo neto y recomendación de reposición
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
    from config import UMBRAL_PICO_FRACCION_TOR
except ImportError:
    UMBRAL_PICO_FRACCION_TOR = 0.50
try:
    from config import HORIZONTE_PICOS_PT_DIAS
except ImportError:
    HORIZONTE_PICOS_PT_DIAS = LEAD_TIME_PRODUCCION_DIAS
try:
    from config import DEMANDA_CALIFICADA_MES_COMPLETO
except ImportError:
    DEMANDA_CALIFICADA_MES_COMPLETO = True
try:
    from config import ONHAND_CRITICO, ONHAND_ALERTA
except ImportError:
    ONHAND_CRITICO, ONHAND_ALERTA = 0.50, 1.00

FV_MAX_ESTANDAR_DDI = 1.00

HOY = date.today().isoformat()


def _tabla_existe(conn, nombre: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {nombre} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _limpiar_sku(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _snapshot_mas_reciente(conn, tabla: str) -> str:
    return conn.execute(f"SELECT MAX(fecha_snapshot) FROM {tabla}").fetchone()[0]


def cargar_descripciones_respaldo(conn) -> dict:
    """sku -> descripción más reciente vista en ventas_raw."""
    df = pd.read_sql("""
        SELECT sku, sku_descripcion, fecha FROM ventas_raw
        WHERE sku_descripcion IS NOT NULL AND sku_descripcion != ''
        ORDER BY fecha DESC
    """, conn)
    df["sku"] = _limpiar_sku(df["sku"])
    return df.drop_duplicates("sku", keep="first").set_index("sku")["sku_descripcion"].to_dict()


def cargar_descripciones_respaldo_mp(conn) -> dict:
    """sku_mp -> descripción desde lead_time_mp y, si falta, oc_importacion_mp."""
    desc = {}
    try:
        lt = pd.read_sql(
            "SELECT sku_mp, mp_descripcion FROM lead_time_mp "
            "WHERE mp_descripcion IS NOT NULL AND mp_descripcion != ''", conn)
        lt["sku_mp"] = _limpiar_sku(lt["sku_mp"])
        desc.update(lt.drop_duplicates("sku_mp", keep="first")
                      .set_index("sku_mp")["mp_descripcion"].to_dict())
    except Exception:
        pass
    try:
        oc = pd.read_sql(
            "SELECT sku_mp, descripcion FROM oc_importacion_mp "
            "WHERE descripcion IS NOT NULL AND descripcion != ''", conn)
        oc["sku_mp"] = _limpiar_sku(oc["sku_mp"])
        faltantes = oc[~oc["sku_mp"].isin(desc.keys())]
        desc.update(faltantes.drop_duplicates("sku_mp", keep="first")
                              .set_index("sku_mp")["descripcion"].to_dict())
    except Exception:
        pass
    return desc


def cargar_posicion(conn, tabla: str) -> pd.DataFrame:
    if not _tabla_existe(conn, tabla):
        return pd.DataFrame()
    fecha = _snapshot_mas_reciente(conn, tabla)
    df = pd.read_sql(f"SELECT * FROM {tabla} WHERE fecha_snapshot = ?",
                     conn, params=(fecha,))
    df["material"] = _limpiar_sku(df["material"])
    print(f"  {tabla}: snapshot del {fecha}, {len(df)} materiales")
    return df


def cargar_buffer(conn, tabla: str) -> pd.DataFrame:
    """Buffer del mes vigente. PT: posicionamiento_pt (política MTS/MTO ya
    aplicada). MP: ddmrp_buffer_mp."""
    if not _tabla_existe(conn, tabla):
        return pd.DataFrame()
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
    condiciones = []
    if "estado" in cols:
        condiciones.append("estado = 'ok'")
    if "mes_offset" in cols:
        condiciones.append("mes_offset = 0")
    filtro = " WHERE " + " AND ".join(condiciones) if condiciones else ""
    df = pd.read_sql(f"SELECT * FROM {tabla}{filtro}", conn)
    df["sku"] = _limpiar_sku(df["sku"])
    return df


# --------------------------------------------------------------------- PT
def calcular_demanda_calificada(pedidos, buffer_df, horizonte_dias) -> pd.DataFrame:
    hoy = pd.Timestamp(HOY)
    limite = hoy + pd.Timedelta(days=horizonte_dias)

    p = pedidos.copy()
    p["material"] = _limpiar_sku(p["material"])
    p["fecha_entrega"] = pd.to_datetime(p["fecha_entrega"], errors="coerce")
    p = p.dropna(subset=["fecha_entrega"])

    umbrales = buffer_df.set_index("sku")["tor"] * UMBRAL_PICO_FRACCION_TOR
    p["umbral_pico"] = p["material"].map(umbrales)

    es_post = p["fecha_entrega"] < hoy
    es_hoy = p["fecha_entrega"] == hoy
    en_horiz = (p["fecha_entrega"] > hoy) & (p["fecha_entrega"] <= limite)
    lejano = p["fecha_entrega"] > limite

    if DEMANDA_CALIFICADA_MES_COMPLETO:
        es_pico = en_horiz | lejano
        print("  Modo MES COMPLETO: todo pedido pendiente califica (la fecha de "
              "entrega es un máximo, no una fecha exacta).")
    else:
        es_pico = (en_horiz & p["umbral_pico"].notna()
                   & (p["cantidad_tm"] > p["umbral_pico"]))
        print(f"  Modo DDI: picos > {UMBRAL_PICO_FRACCION_TOR:.0%} del TOR dentro "
              f"de {horizonte_dias} días.")

    p["componente"] = np.select([es_post, es_hoy, es_pico],
                                ["postergado", "hoy", "pico"],
                                default="futuro_absorbido")

    resumen = (p[p["componente"] != "futuro_absorbido"]
               .pivot_table(index="material", columns="componente",
                            values="cantidad_tm", aggfunc="sum", fill_value=0.0)
               .reset_index())
    if resumen.empty:
        resumen = pd.DataFrame(columns=["material"])

    for c in ["postergado", "hoy", "pico"]:
        if c not in resumen.columns:
            resumen[c] = 0.0

    resumen = resumen.rename(columns={"postergado": "postergados_tm",
                                      "hoy": "demanda_hoy_tm", "pico": "picos_tm"})
    resumen["demanda_calificada_tm"] = (resumen["postergados_tm"] + resumen["demanda_hoy_tm"]
                                        + resumen["picos_tm"])

    print(f"  Postergados: {float(resumen['postergados_tm'].sum()):,.1f} TM")
    print(f"  Demanda de hoy: {float(resumen['demanda_hoy_tm'].sum()):,.1f} TM")
    print(f"  Pedidos futuros calificados: {float(resumen['picos_tm'].sum()):,.1f} TM")

    n_abs = int((p["componente"] == "futuro_absorbido").sum())
    if n_abs:
        tm = float(p.loc[p["componente"] == "futuro_absorbido", "cantidad_tm"].sum())
        print(f"  {n_abs} pedidos ({tm:,.1f} TM) NO califican: el buffer los absorbe.")

    return resumen[["material", "postergados_tm", "demanda_hoy_tm", "picos_tm",
                    "demanda_calificada_tm"]]


# --------------------------------------------------------------------- MP
def _bom_promedio(conn) -> pd.DataFrame:
    """% de cada MP en cada PT, promedio de las corridas (centro-fecha-
    calibre) del BOM vigente. Una MP que no está en una corrida cuenta
    0 % en esa corrida (no se promedia solo donde aparece)."""
    if not _tabla_existe(conn, "bom_semanal_pt"):
        return pd.DataFrame(columns=["sku_pt", "sku_mp", "pct"])
    bom = pd.read_sql(
        "SELECT sku_pt, centro, fecha_planeada, calibre, sku_mp, pct FROM bom_semanal_pt "
        "WHERE fecha_snapshot = (SELECT MAX(fecha_snapshot) FROM bom_semanal_pt)", conn)
    bom["sku_pt"] = _limpiar_sku(bom["sku_pt"])
    bom["sku_mp"] = _limpiar_sku(bom["sku_mp"])
    bom["corrida"] = (bom["centro"].astype(str) + "|" + bom["fecha_planeada"].astype(str)
                      + "|" + bom["calibre"].astype(str))
    n_corridas = bom.groupby("sku_pt")["corrida"].nunique()
    suma = bom.groupby(["sku_pt", "sku_mp"], as_index=False)["pct"].sum()
    suma["pct"] = suma["pct"] / suma["sku_pt"].map(n_corridas)
    return suma


def calcular_demanda_calificada_mp(conn, buffer_df) -> pd.DataFrame:
    """Demanda calificada de MP desde el plan de producción vigente."""
    vacio = pd.DataFrame(columns=["material", "postergados_tm", "demanda_hoy_tm",
                                  "picos_tm", "demanda_calificada_tm"])
    if not _tabla_existe(conn, "plan_produccion"):
        print("  [AVISO] Sin 'plan_produccion' -- demanda calificada de MP = 0.")
        return vacio

    plan = pd.read_sql("SELECT sku, fecha, tm FROM plan_produccion", conn)
    plan["sku"] = _limpiar_sku(plan["sku"])
    plan["fecha"] = pd.to_datetime(plan["fecha"], errors="coerce")
    hoy = pd.Timestamp(HOY)
    plan = plan[plan["fecha"] >= hoy]
    if plan.empty:
        print("  [AVISO] El plan de producción vigente no tiene días desde hoy -- "
              "correr optimizar_producción.py. Demanda calificada de MP = 0.")
        return vacio

    bom = _bom_promedio(conn)
    x = plan.merge(bom, left_on="sku", right_on="sku_pt", how="left")
    sin_bom = sorted(x.loc[x["sku_mp"].isna(), "sku"].unique())
    if sin_bom:
        tm_sin = plan[plan["sku"].isin(sin_bom)]["tm"].sum()
        print(f"  [AVISO] {len(sin_bom)} PT del plan ({tm_sin:,.0f} TM) sin fórmula en el "
              f"BOM de la semana -- su consumo de MP no entra: {sin_bom[:10]}")
    x = x.dropna(subset=["sku_mp"])
    x["consumo_tm"] = x["tm"] * x["pct"] / 100.0

    diario = x.groupby(["sku_mp", "fecha"], as_index=False)["consumo_tm"].sum()
    umbral = buffer_df.set_index("sku")["tor"] * UMBRAL_PICO_FRACCION_TOR
    diario["umbral_pico"] = diario["sku_mp"].map(umbral)

    es_hoy = diario["fecha"] == hoy
    es_pico = (~es_hoy) & diario["umbral_pico"].notna() & (diario["consumo_tm"] > diario["umbral_pico"])
    diario["demanda_hoy_tm"] = np.where(es_hoy, diario["consumo_tm"], 0.0)
    diario["picos_tm"] = np.where(es_pico, diario["consumo_tm"], 0.0)

    res = (diario.groupby("sku_mp", as_index=False)[["demanda_hoy_tm", "picos_tm"]].sum()
                 .rename(columns={"sku_mp": "material"}))
    res["postergados_tm"] = 0.0
    res["demanda_calificada_tm"] = res["demanda_hoy_tm"] + res["picos_tm"]

    print(f"  Plan de producción: {plan['fecha'].min():%Y-%m-%d} a {plan['fecha'].max():%Y-%m-%d}")
    print(f"  Consumo de MP de hoy (plan × fórmula): {res['demanda_hoy_tm'].sum():,.1f} TM")
    print(f"  Picos de consumo (> {UMBRAL_PICO_FRACCION_TOR:.0%} del TOR en un día): "
          f"{res['picos_tm'].sum():,.1f} TM")
    return res[["material", "postergados_tm", "demanda_hoy_tm", "picos_tm",
                "demanda_calificada_tm"]]


def cargar_oc_abiertas_mp(conn) -> pd.Series:
    """TM en camino por MP según el Excel de OC (solo ETA >= hoy)."""
    if not _tabla_existe(conn, "oc_importacion_mp"):
        print("  [AVISO] Sin 'oc_importacion_mp' -- correr Ingesta_MP_Importacion_BOM.py.")
        return pd.Series(dtype=float)
    oc = pd.read_sql("SELECT sku_mp, cantidad_tm, fecha_llegada FROM oc_importacion_mp", conn)
    oc["sku_mp"] = _limpiar_sku(oc["sku_mp"])
    oc = oc[pd.to_datetime(oc["fecha_llegada"], errors="coerce") >= pd.Timestamp(HOY)]
    s = oc.groupby("sku_mp")["cantidad_tm"].sum()
    print(f"  OC de importación en camino: {len(oc)} OC · {s.sum():,.1f} TM · {len(s)} MP")
    return s


# ------------------------------------------------------------------ común
def zona_de(nfp, tor, toy, tog):
    if pd.isna(nfp):
        return "SIN_POSICION"
    if nfp <= tor:
        return "ROJO"
    if nfp <= toy:
        return "AMARILLO"
    if nfp <= tog:
        return "VERDE"
    return "SOBRE_TOG"


def calcular_nodo(conn, nodo, tabla_buffer, tabla_pos, pedidos=None,
                  horizonte_dias=HORIZONTE_PICOS_PT_DIAS) -> pd.DataFrame:
    print(f"\n=== Nodo {nodo} (buffer: {tabla_buffer}) ===")

    buffer_df = cargar_buffer(conn, tabla_buffer)
    if buffer_df.empty:
        print(f"  Sin buffer calculable en '{tabla_buffer}'.")
        return pd.DataFrame()

    pos = cargar_posicion(conn, tabla_pos)
    if pos.empty:
        print(f"  Sin posición de inventario en '{tabla_pos}'.")
        return pd.DataFrame()

    df = buffer_df.merge(
        pos[["material", "disponible_tm", "transito_tm", "descripcion"]],
        left_on="sku", right_on="material", how="left")

    if df["descripcion"].isna().any():
        desc_respaldo = (cargar_descripciones_respaldo(conn) if nodo == "PT"
                         else cargar_descripciones_respaldo_mp(conn))
        df["descripcion"] = df["descripcion"].fillna(df["sku"].map(desc_respaldo))

    sin_pos = int(df["disponible_tm"].isna().sum())
    if sin_pos:
        print(f"  [INFO] {sin_pos} ítems con buffer sin fila en inventario -- "
              f"stock físico = 0 confirmado.")

    df["tiene_posicion"] = True
    df["disponible_tm"] = df["disponible_tm"].fillna(0.0)
    df["transito_tm"] = df["transito_tm"].fillna(0.0)

    # --- En mano / en orden ---
    if nodo == "MP":
        # Tránsito SAP de MP = ya está en planta -> en mano.
        # En orden = solo OC de importación (barcos) que aún no llegan.
        df["transito_sap_tm"] = df["transito_tm"]
        df["en_mano_tm"] = df["disponible_tm"] + df["transito_sap_tm"]
        oc = cargar_oc_abiertas_mp(conn)
        df["oc_en_camino_tm"] = df["sku"].map(oc).fillna(0.0)
        df["transito_tm"] = df["oc_en_camino_tm"]
    else:
        df["en_mano_tm"] = df["disponible_tm"]

    # --- Demanda calificada ---
    if nodo == "PT" and pedidos is not None and not pedidos.empty:
        dc = calcular_demanda_calificada(pedidos, buffer_df, horizonte_dias)
    elif nodo == "MP":
        dc = calcular_demanda_calificada_mp(conn, buffer_df)
    else:
        dc = pd.DataFrame(columns=["material", "postergados_tm", "demanda_hoy_tm",
                                   "picos_tm", "demanda_calificada_tm"])
        print("  Sin pedidos: demanda calificada = 0.")

    if not dc.empty:
        dc["material"] = _limpiar_sku(dc["material"])
    df = df.merge(dc, left_on="sku", right_on="material", how="left", suffixes=("", "_dc"))
    df = df.drop(columns=[c for c in df.columns if c.endswith("_dc")], errors="ignore")

    if not dc.empty:
        total_dc_antes = dc["demanda_calificada_tm"].sum()
        total_dc_despues = df["demanda_calificada_tm"].fillna(0).sum()
        print(f"  TOTAL DEMANDA CALIFICADA: {total_dc_despues:,.1f} TM")
        diferencia = total_dc_antes - total_dc_despues
        if diferencia > 0.5:
            sin_buffer = sorted(set(dc["material"]) - set(df["sku"]))
            print(f"  [AVISO] {diferencia:,.1f} TM pertenecen a ítems SIN buffer "
                  f"calculable -- no entran al NFP: {sin_buffer[:15]}")
    df["demanda_calificada_completa"] = not dc.empty

    for c in ["postergados_tm", "demanda_hoy_tm", "picos_tm", "demanda_calificada_tm"]:
        df[c] = df[c].fillna(0.0)

    # --- Ecuación de flujo neto ---
    nfp_real = df["en_mano_tm"] + df["transito_tm"] - df["demanda_calificada_tm"]
    if nodo == "MP":
        # DDMRP: el NFP puede ser negativo; orden = TOG - NFP completo.
        df["nfp_tm"] = nfp_real
    else:
        # PT: se mantiene el piso en 0 (el optimizador usa NFP/TOG como
        # urgencia y, en modo MES COMPLETO, el NFP negativo duplicaría los
        # pedidos que el optimizador ya atiende por separado).
        df["nfp_tm"] = np.maximum(nfp_real, 0)

    df["zona_nfp"] = df.apply(lambda r: zona_de(r["nfp_tm"], r["tor"], r["toy"], r["tog"]), axis=1)
    df["prioridad_pct"] = np.where(df["tog"] > 0, df["nfp_tm"] / df["tog"], np.nan)
    df["requiere_orden"] = (df["nfp_tm"] <= df["toy"]) & (df["tog"] > 0)
    # Reposición: piso en 0. Un NFP negativo indica faltante para el plan,
    # pero en la operación el stock no baja de 0: se repone hasta TOG.
    df["nfp_reposicion_tm"] = np.maximum(df["nfp_tm"], 0)
    df["faltante_plan_tm"] = np.maximum(-df["nfp_tm"], 0)
    df["cantidad_a_ordenar_tm"] = np.where(
        df["requiere_orden"], np.maximum(df["tog"] - df["nfp_reposicion_tm"], 0), 0.0)

    df["onhand_pct_tor"] = np.where(df["tor"] > 0, df["en_mano_tm"] / df["tor"], np.nan)
    df["alerta_onhand"] = np.select(
        [df["onhand_pct_tor"] < ONHAND_CRITICO, df["onhand_pct_tor"] < ONHAND_ALERTA],
        ["CRITICO", "ALERTA"], default="OK")

    # Coberturas en días de ADU. OJO: el TOG incluye la Zona Amarilla
    # (material en camino), no es cobertura física.
    df["cobertura_tog_dias"] = np.where(df["adu"] > 0, df["tog"] / df["adu"], np.nan)
    verde = (df["tog"] - df["toy"]).clip(lower=0)
    df["inventario_promedio_objetivo_tm"] = df["tor"] + verde / 2.0
    df["cobertura_fisica_objetivo_dias"] = np.where(
        df["adu"] > 0, df["inventario_promedio_objetivo_tm"] / df["adu"], np.nan)
    df["cobertura_disponible_dias"] = np.where(
        df["adu"] > 0, df["en_mano_tm"] / df["adu"], np.nan)

    df["nodo"] = nodo
    df["fecha_calculo"] = HOY

    df["stock_cero_real"] = df["en_mano_tm"] <= 0
    df["tiene_pedido_pendiente"] = df["demanda_calificada_tm"] > 0
    df["urgencia_stockout"] = np.select(
        [df["stock_cero_real"] & df["tiene_pedido_pendiente"],
         df["stock_cero_real"] & ~df["tiene_pedido_pendiente"]],
        ["QUIEBRE_CON_PEDIDO", "CERO_SIN_PEDIDO"], default="")

    cols = ["fecha_calculo", "nodo", "sku", "descripcion",
            "adu", "tor", "toy", "tog", "var_factor_dinamico",
            "cobertura_tog_dias", "cobertura_fisica_objetivo_dias",
            "inventario_promedio_objetivo_tm", "cobertura_disponible_dias",
            "tiene_posicion", "disponible_tm", "transito_sap_tm", "en_mano_tm",
            "transito_tm", "oc_en_camino_tm",
            "postergados_tm", "demanda_hoy_tm", "picos_tm",
            "demanda_calificada_tm", "demanda_calificada_completa",
            "nfp_tm", "faltante_plan_tm", "zona_nfp", "prioridad_pct",
            "requiere_orden", "cantidad_a_ordenar_tm",
            "onhand_pct_tor", "alerta_onhand",
            "stock_cero_real", "tiene_pedido_pendiente", "urgencia_stockout"]
    for extra in ["semaforo", "politica", "nivel_rotacion", "es_mts"]:
        if extra in df.columns:
            cols.append(extra)
    cols = [c for c in cols if c in df.columns]

    return df[cols].sort_values("prioridad_pct", na_position="last")


def guardar_snapshot(conn, tabla: str, df: pd.DataFrame):
    if _tabla_existe(conn, tabla):
        cols_tabla = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
        faltantes = set(df.columns) - cols_tabla
        if "fecha_calculo" not in cols_tabla or faltantes:
            if faltantes:
                print(f"  [MIGRACIÓN] '{tabla}' no tiene {sorted(faltantes)} -- "
                      f"se recrea. Se pierde el histórico anterior.")
            conn.execute(f"DROP TABLE {tabla}")
        else:
            conn.execute(f"DELETE FROM {tabla} WHERE fecha_calculo = ?", (HOY,))
    df.to_sql(tabla, conn, if_exists="append", index=False)


def main():
    conn = sqlite3.connect(DB_PATH)

    print(f"Cálculo de flujo neto — {HOY}")
    print("Demanda calificada PT: MES COMPLETO (todo pedido pendiente califica)"
          if DEMANDA_CALIFICADA_MES_COMPLETO else
          f"Horizonte de picos PT: {HORIZONTE_PICOS_PT_DIAS} días · "
          f"umbral {UMBRAL_PICO_FRACCION_TOR:.0%} del TOR")

    pedidos = pd.DataFrame()
    if _tabla_existe(conn, "pedidos_pendientes_pt"):
        fecha = _snapshot_mas_reciente(conn, "pedidos_pendientes_pt")
        pedidos = pd.read_sql(
            "SELECT * FROM pedidos_pendientes_pt WHERE fecha_snapshot = ?",
            conn, params=(fecha,))
        pedidos["material"] = _limpiar_sku(pedidos["material"])

    # Pedidos manuales: UNA sola vez (antes se sumaban dos veces).
    manuales = cargar_pedidos_manuales(conn)
    if not manuales.empty:
        pedidos = pd.concat([pedidos, manuales], ignore_index=True)

    pt = calcular_nodo(conn, "PT", "posicionamiento_pt", "posicion_inventario_pt",
                       pedidos=pedidos)
    mp = calcular_nodo(conn, "MP", "ddmrp_buffer_mp", "posicion_inventario_mp")

    todo = pd.concat([d for d in [pt, mp] if not d.empty], ignore_index=True)
    if todo.empty:
        print("\nSin resultados. Correr Ingesta_Inventario.py, "
              "calcular_buffer_ddmrp_dinamico.py y clasificar_mts_mto.py.")
        conn.close()
        return

    guardar_snapshot(conn, "ddmrp_flujo_neto", todo)
    conn.commit()
    todo.to_csv(ROOT_DIR / "ddmrp_flujo_neto.csv", index=False)

    print("\n" + "=" * 62)
    print("POSICIÓN DE FLUJO NETO")
    print("=" * 62)

    for nodo in todo["nodo"].unique():
        sub = todo[todo["nodo"] == nodo]
        g = sub[sub["tiene_posicion"]]

        print(f"\n--- {nodo}: {len(g)} gestionables de {len(sub)} ---")
        print(g["zona_nfp"].value_counts().to_string())

        ordenar = g[g["requiere_orden"]]
        print(f"\n  Requieren reposición: {len(ordenar)} ítems, "
              f"{ordenar['cantidad_a_ordenar_tm'].sum():,.1f} TM")

        if nodo == "PT" and "politica" in g.columns:
            mto_en_cola = ordenar[ordenar["politica"] == "MTO"]
            if len(mto_en_cola):
                print(f"    {len(mto_en_cola)} MTO en la cola de reposición -- "
                      f"esto no debería pasar (MTO tiene tog=0).")
            else:
                print(f"  OK: ningún MTO en la cola de reposición.")

        crit = g[g["alerta_onhand"] == "CRITICO"]
        if len(crit):
            print(f"    {len(crit)} ítems con físico bajo el {ONHAND_CRITICO:.0%} "
                  f"del TOR -- riesgo de quiebre.")

        eng = g[g["zona_nfp"].isin(["VERDE", "SOBRE_TOG"]) & (g["alerta_onhand"] == "CRITICO")]
        if len(eng):
            print(f"    {len(eng)} ítems con NFP en verde pero físico crítico "
                  f"(hay suministro en camino, puede no llegar a tiempo):")
            for _, r in eng.head(5).iterrows():
                print(f"        {r['sku']} — en mano {r['en_mano_tm']:.1f}, "
                      f"en camino {r['transito_tm']:.1f}, TOR {r['tor']:.1f} TM")

        if nodo == "MP":
            falt = g[g["faltante_plan_tm"] > 0]
            if len(falt):
                print(f"\n    {len(falt)} MP con NFP negativo: el stock no alcanza para el "
                      f"plan ({falt['faltante_plan_tm'].sum():,.1f} TM de faltante). Se ordena "
                      f"hasta TOG desde 0; el faltante es riesgo para el plan de producción:")
                print(falt[["sku", "descripcion", "en_mano_tm", "demanda_calificada_tm",
                            "faltante_plan_tm", "cantidad_a_ordenar_tm"]]
                      .round(1).head(10).to_string(index=False))
            cob = g["cobertura_fisica_objetivo_dias"].dropna()
            if len(cob):
                print(f"\n  Cobertura física objetivo (TOR + Verde/2): mediana "
                      f"{cob.median():.0f} días · p90 {cob.quantile(.9):.0f} días")

    print("\n" + "=" * 62)
    print("DIAGNÓSTICO DEL DIMENSIONAMIENTO")
    print("=" * 62)

    g = todo[todo["tiene_posicion"]]
    cob = g["cobertura_tog_dias"].dropna()
    if len(cob):
        print(f"  Cobertura del TOG (incluye Zona Amarilla en camino): mediana "
              f"{cob.median():.1f} días (p10 {cob.quantile(.1):.1f} · p90 {cob.quantile(.9):.1f})")

    if "var_factor_dinamico" in g.columns:
        fv = g["var_factor_dinamico"].dropna()
        n_alto = int((fv > FV_MAX_ESTANDAR_DDI).sum())
        if n_alto:
            print(f"\n    {n_alto} de {len(fv)} ítems con Factor de Variabilidad "
                  f"> {FV_MAX_ESTANDAR_DDI:.1f}.")

    print("\n" + "=" * 62)
    print("COLA DE TRABAJO — 15 más urgentes por prioridad (NFP/TOG)")
    print("=" * 62)
    cols_show = [c for c in ["nodo", "sku", "descripcion", "nfp_tm", "tor", "tog",
                             "prioridad_pct", "zona_nfp", "cantidad_a_ordenar_tm",
                             "alerta_onhand", "politica", "nivel_rotacion"]
                 if c in todo.columns]
    top = todo[todo["tiene_posicion"]].nsmallest(15, "prioridad_pct")[cols_show].copy()
    top["prioridad_pct"] = (top["prioridad_pct"] * 100).round(1)
    for c in ["nfp_tm", "tor", "tog", "cantidad_a_ordenar_tm"]:
        if c in top.columns:
            top[c] = top[c].round(1)
    if "descripcion" in top.columns:
        top["descripcion"] = top["descripcion"].astype(str).str.slice(0, 28)
    print(top.to_string(index=False))

    quiebres = todo[todo["urgencia_stockout"] == "QUIEBRE_CON_PEDIDO"]
    if len(quiebres):
        print(f"\n   {len(quiebres)} ítems en CERO físico CON demanda esperando -- "
              f"quiebre real, máxima urgencia:")
        print(quiebres[["nodo", "sku", "descripcion", "demanda_calificada_tm"]]
              .to_string(index=False))

    n_dias = pd.read_sql("SELECT COUNT(DISTINCT fecha_calculo) n FROM ddmrp_flujo_neto",
                         conn).iloc[0]["n"]
    print(f"\n[OK] Tabla 'ddmrp_flujo_neto' — {int(n_dias)} día(s) de histórico")
    conn.close()


if __name__ == "__main__":
    main()