"""
ajustar_buffer_mp.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP: ajustes dinámicos y capacidad de bodega del nodo MP
"""

import sqlite3

import numpy as np
import pandas as pd

from config import DB_PATH
from calcular_buffer_ddmrp_dinamico import calcular_zonas_vectorizado
from clasificar_mts_mto import _existe, PISO_ZONA_VERDE_DIAS

ZAF_ZONA = "verde"        # "verde" o "roja": zona a la que se aplica el ZAF de MP
COL_PCT_BOM = "pct"       # % de la MP en la fórmula del PT (TM MP por 100 TM PT)

def _capacidad_bodega_mp_tm(conn):
    if _existe(conn, "config_capacidad_bodega"):
        fila = conn.execute(
            "SELECT capacidad_mp_tm FROM config_capacidad_bodega WHERE id = 1").fetchone()
        if fila and fila[0] is not None:
            return float(fila[0])
    return None

def _asegurar_tabla_factores_mp(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ddmrp_factores_manuales_mp (
            sku TEXT PRIMARY KEY,
            zaf REAL NOT NULL DEFAULT 1.0,
            ltaf REAL NOT NULL DEFAULT 1.0,
            actualizado_por TEXT,
            actualizado_en TEXT DEFAULT (datetime('now'))
        )""")
    conn.commit()

def _asegurar_tabla_lead_time_manual_mp(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ddmrp_lead_time_manual_mp (
            sku TEXT PRIMARY KEY,
            lead_time_dias REAL NOT NULL,
            actualizado_por TEXT,
            actualizado_en TEXT DEFAULT (datetime('now'))
        )""")
    conn.commit()

def delta_adu_por_daf_pt(conn) -> pd.DataFrame:
    """Demanda extra (TM/día) que el DAF de cada PT genera en cada MP,
    explotada por la fórmula vigente. Devuelve sku_mp, delta_adu, detalle."""
    vacio = pd.DataFrame(columns=["sku", "delta_adu_daf_pt", "pt_con_daf"])
    if not (_existe(conn, "ddmrp_factores_manuales") and _existe(conn, "bom_semanal_pt")):
        return vacio

    daf = pd.read_sql("SELECT sku, daf FROM ddmrp_factores_manuales", conn)
    daf["sku"] = daf["sku"].astype(str).str.strip()
    daf = daf[(daf["daf"].notna()) & ((daf["daf"] - 1.0).abs() > 1e-9)]
    if daf.empty:
        return vacio

    # ADU del PT SIN ajustar (adu_base): ajustar_buffer_daf_zaf.py ya dejó
    # en 'adu' el valor × DAF; usarlo aquí contaría el DAF dos veces.
    cols_pt = [c[1] for c in conn.execute("PRAGMA table_info(ddmrp_buffer_pt)").fetchall()]
    col_adu = "adu_base" if "adu_base" in cols_pt else "adu"
    adu_pt = pd.read_sql(
        f"SELECT sku, {col_adu} AS adu FROM ddmrp_buffer_pt WHERE mes_offset = 0 AND estado = 'ok'", conn)
    adu_pt["sku"] = adu_pt["sku"].astype(str).str.strip()

    bom = pd.read_sql(
        f"SELECT sku_pt, centro, fecha_planeada, calibre, sku_mp, {COL_PCT_BOM} AS pct "
        "FROM bom_semanal_pt "
        "WHERE fecha_snapshot = (SELECT MAX(fecha_snapshot) FROM bom_semanal_pt)", conn)
    bom["sku_pt"] = bom["sku_pt"].astype(str).str.strip()
    bom["sku_mp"] = bom["sku_mp"].astype(str).str.strip()
    # % promedio de la MP en el PT entre TODAS las corridas (centro-fecha-
    # calibre) del BOM vigente; si la MP no está en una corrida, cuenta 0 %.
    # Mismo criterio que calcular_nfp.py.
    bom["corrida"] = (bom["centro"].astype(str) + "|" + bom["fecha_planeada"].astype(str)
                      + "|" + bom["calibre"].astype(str))
    n_corridas = bom.groupby("sku_pt")["corrida"].nunique()
    bom = bom.groupby(["sku_pt", "sku_mp"], as_index=False)["pct"].sum()
    bom["pct"] = bom["pct"] / bom["sku_pt"].map(n_corridas)

    x = (daf.merge(adu_pt, on="sku").rename(columns={"sku": "sku_pt"})
            .merge(bom, on="sku_pt"))
    if x.empty:
        return vacio
    x["delta"] = (x["daf"] - 1.0) * x["adu"] * x["pct"] / 100.0
    x["txt"] = x.apply(lambda r: f"{r['sku_pt']} (DAF {r['daf']:.2f}, {r['pct']:.1f}%)", axis=1)
    out = (x.groupby("sku_mp")
             .agg(delta_adu_daf_pt=("delta", "sum"),
                  pt_con_daf=("txt", lambda s: "; ".join(s)))
             .reset_index().rename(columns={"sku_mp": "sku"}))
    return out

def racionar_bodega_mp(df: pd.DataFrame, capacidad: float) -> pd.DataFrame:
    """La bodega es FÍSICA: se compara contra el inventario promedio
    esperado del buffer, TOR + Zona Verde / 2 (Ptak & Smith). La Zona
    Amarilla es material EN CAMINO (órdenes de compra abiertas), no stock.
    Si no cabe, recorta solo la Zona Verde, proporcional a lo recortable de
    cada MP. Recortar X TM de verde baja el promedio X/2 -> verde a
    recortar = 2 × exceso."""
    ok = df["estado"] == "ok"
    df["tog_antes_racionamiento"] = df["tog"]
    df["tog_racionado"] = False
    tor = pd.to_numeric(df["tor"], errors="coerce")
    toy = pd.to_numeric(df["toy"], errors="coerce")
    tog = pd.to_numeric(df["tog"], errors="coerce")
    verde = (tog - toy).clip(lower=0)
    total = (tor + verde / 2.0)[ok].sum()
    if not capacidad:
        print("  [AVISO] Sin capacidad de bodega MP en Configuración -- no se raciona.")
        return df
    if total <= capacidad:
        print(f"  Bodega MP: inventario promedio esperado (TOR + Verde/2) {total:,.0f} TM "
              f"de {capacidad:,.0f} TM -- cabe. (TOG total {tog[ok].sum():,.0f} TM, "
              f"la Zona Amarilla es material en camino.)")
        return df

    exceso = total - capacidad
    verde_a_recortar = 2.0 * exceso
    piso = np.minimum(verde, df["adu"].clip(lower=0) * PISO_ZONA_VERDE_DIAS)
    recortable = (verde - piso).where(ok, 0.0).fillna(0.0)
    total_recortable = recortable.sum()
    print(f"  ⚠️  Bodega MP: inventario promedio esperado {total:,.0f} TM excede "
          f"{capacidad:,.0f} TM por {exceso:,.0f} TM -- recortando {verde_a_recortar:,.0f} TM "
          f"de Zona Verde (proporcional, piso {PISO_ZONA_VERDE_DIAS:.0f} día de ADU; "
          f"Roja y Amarilla intactas).")
    if total_recortable <= 0:
        print("  ⚠️  No hay Zona Verde recortable.")
        return df

    frac = min(1.0, verde_a_recortar / total_recortable)
    corte = recortable * frac
    df["tog"] = (tog - corte).round(1)
    df["green"] = (pd.to_numeric(df["tog"], errors="coerce") - toy).round(1)
    df.loc[corte > 0.01, "tog_racionado"] = True
    falta_promedio = (verde_a_recortar - corte.sum()) / 2.0
    prom_final = (tor + (pd.to_numeric(df["tog"], errors="coerce") - toy).clip(lower=0) / 2.0)[ok].sum()
    print(f"  {int(df['tog_racionado'].sum())} MP recortadas ({frac:.0%} de su Zona Verde "
          f"recortable). Inventario promedio final: {prom_final:,.0f} TM.")
    if falta_promedio > 0.5:
        print(f"  ⚠️  Aun con la Zona Verde en su piso faltan {falta_promedio:,.0f} TM: la Zona "
              f"Roja (lo que exige el lead time y la variabilidad del proveedor) ya no cabe en "
              f"la bodega. Es capacidad física, no de política: más bodega, lead time más "
              f"corto con el proveedor, o compras más frecuentes.")
    return df

def _asegurar_tablas_overrides_mp(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ddmrp_overrides_mp (
            sku TEXT PRIMARY KEY,
            tor REAL NOT NULL, toy REAL NOT NULL, tog REAL NOT NULL,
            actualizado_por TEXT,
            actualizado_en TEXT DEFAULT (datetime('now'))
        )""")
    conn.commit()

def aplicar_overrides_mp(df: pd.DataFrame, conn, capacidad) -> pd.DataFrame:
    """Reaplica el ajuste manual vigente del planificador (TOR/TOY/TOG) por
    encima del cálculo DDMRP. Guarda la recomendación del sistema en
    *_recomendado_sistema para poder revertir. Un ajuste manual también
    puede darle buffer a una MP que el sistema no pudo calcular."""
    for c in ("tor", "toy", "tog", "green"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["tor_recomendado_sistema"] = df["tor"]
    df["toy_recomendado_sistema"] = df["toy"]
    df["tog_recomendado_sistema"] = df["tog"]
    df["estado_sistema"] = df["estado"]
    df["ajuste_manual"] = False

    _asegurar_tablas_overrides_mp(conn)
    ov = pd.read_sql("SELECT sku, tor, toy, tog FROM ddmrp_overrides_mp", conn)
    if ov.empty:
        return df
    ov["sku"] = ov["sku"].astype(str).str.strip()
    n = 0
    for _, f in ov.iterrows():
        m = df["sku"] == f["sku"]
        if not m.any():
            continue
        df.loc[m, ["tor", "toy", "tog"]] = [f["tor"], f["toy"], f["tog"]]
        df.loc[m, "green"] = f["tog"] - f["toy"]
        df.loc[m, "estado"] = "ok"
        df.loc[m, "ajuste_manual"] = True
        df.loc[m, "tog_racionado"] = False
        n += 1
    print(f"  {n} MP con ajuste manual vigente (TOR/TOY/TOG del planificador).")
    if capacidad:
        ok = df["estado"] == "ok"
        prom = (df["tor"] + (df["tog"] - df["toy"]).clip(lower=0) / 2)[ok].sum()
        if prom > capacidad:
            print(f"  ⚠️  Con los ajustes manuales el inventario promedio MP es {prom:,.0f} TM "
                  f"y supera la bodega ({capacidad:,.0f} TM). Los ajustes manuales no se recortan.")
    return df

def main():
    conn = sqlite3.connect(DB_PATH)
    if not _existe(conn, "ddmrp_buffer_mp"):
        print("[ERROR] Falta ddmrp_buffer_mp. Correr calcular_buffer_ddmrp_dinamico.py.")
        return
    _asegurar_tabla_factores_mp(conn)
    _asegurar_tabla_lead_time_manual_mp(conn)

    df = pd.read_sql("SELECT * FROM ddmrp_buffer_mp", conn)
    df["sku"] = df["sku"].astype(str).str.strip()

    # --- Ancla: ADU y DLT sin ajustes (se capturan una vez por cálculo base) ---
    for base, col in (("adu_base", "adu"), ("dlt_base", "dlt_dias")):
        if base not in df.columns:
            df[base] = df[col]
        df[base] = df[base].fillna(df[col])

    # --- Lead time manual (corrección del dato real del proveedor) ---
    # Se guarda dlt_base_sistema ANTES de aplicar el override, para que el
    # frontend pueda mostrar "Sistema: X días" y revertir a ese valor. El
    # override reemplaza dlt_base aquí mismo, ANTES de LTAF y del cálculo de
    # zonas: el lead time entra directo en calcular_zonas_vectorizado(), así
    # que debe quedar corregido antes de llegar a esa fórmula.
    df["dlt_base_sistema"] = df["dlt_base"]
    lt_manual = pd.read_sql(
        "SELECT sku, lead_time_dias AS dlt_manual FROM ddmrp_lead_time_manual_mp", conn)
    lt_manual["sku"] = lt_manual["sku"].astype(str).str.strip()
    df = df.merge(lt_manual, on="sku", how="left")
    df["lead_time_manual_activo"] = df["dlt_manual"].notna()
    df["dlt_base"] = df["dlt_manual"].combine_first(df["dlt_base"])
    df = df.drop(columns=["dlt_manual"])
    n_lt_manual = int(df["lead_time_manual_activo"].sum())
    if n_lt_manual:
        print(f"  {n_lt_manual} MP con lead time manual (corrección del dato real del proveedor).")

    # --- 1) DAF de los PT explotado por la fórmula ---
    delta = delta_adu_por_daf_pt(conn)
    df = df.drop(columns=["delta_adu_daf_pt", "pt_con_daf"], errors="ignore").merge(delta, on="sku", how="left")
    df["delta_adu_daf_pt"] = df["delta_adu_daf_pt"].fillna(0.0)
    df["adu"] = (df["adu_base"] + df["delta_adu_daf_pt"]).clip(lower=0)

    # --- 2) y 3) ZAF / LTAF propios de la MP ---
    fac = pd.read_sql("SELECT sku, zaf, ltaf FROM ddmrp_factores_manuales_mp", conn)
    fac["sku"] = fac["sku"].astype(str).str.strip()
    df = df.drop(columns=["factor_zaf", "factor_ltaf"], errors="ignore").merge(
        fac.rename(columns={"zaf": "factor_zaf", "ltaf": "factor_ltaf"}), on="sku", how="left")
    df["factor_zaf"] = df["factor_zaf"].fillna(1.0)
    df["factor_ltaf"] = df["factor_ltaf"].fillna(1.0)
    df["dlt_dias"] = df["dlt_base"] * df["factor_ltaf"]

    # Zonas con la MISMA fórmula DDMRP del cálculo base
    zonas = calcular_zonas_vectorizado(df)
    for c in zonas.columns:
        df[c] = zonas[c]
    ok = df["estado"] == "ok"
    num = lambda c: pd.to_numeric(df[c], errors="coerce")
    if ZAF_ZONA == "roja":
        df.loc[ok, "tor"] = (num("tor") * df["factor_zaf"]).round(1)
        df.loc[ok, "toy"] = (num("tor") + num("yellow")).round(1)
        df.loc[ok, "tog"] = (num("toy") + num("green")).round(1)
    else:
        df.loc[ok, "green"] = (num("green") * df["factor_zaf"]).round(1)
        df.loc[ok, "tog"] = (num("toy") + num("green")).round(1)

    n_daf = int((df["delta_adu_daf_pt"].abs() > 1e-6).sum())
    n_zaf = int((df["factor_zaf"] != 1.0).sum())
    n_ltaf = int((df["factor_ltaf"] != 1.0).sum())
    print(f"=== Ajustes MP: {n_daf} MP con DAF heredado de PT · {n_zaf} con ZAF ({ZAF_ZONA}) · "
          f"{n_ltaf} con LTAF · {n_lt_manual} con lead time manual ===")
    if n_daf:
        print(df.loc[df["delta_adu_daf_pt"].abs() > 1e-6,
                     ["sku", "adu_base", "delta_adu_daf_pt", "adu", "pt_con_daf"]]
              .round(2).sort_values("delta_adu_daf_pt", key=abs, ascending=False)
              .head(10).to_string(index=False))

    # --- 4) Capacidad de bodega MP ---
    cap_mp = _capacidad_bodega_mp_tm(conn)
    df = racionar_bodega_mp(df, cap_mp)

    # --- 5) Ajustes manuales del planificador (no se recortan) ---
    df = aplicar_overrides_mp(df, conn, cap_mp)

    df.to_sql("ddmrp_buffer_mp", conn, if_exists="replace", index=False)
    df[["sku", "adu_base", "delta_adu_daf_pt", "pt_con_daf", "adu", "dlt_base",
        "dlt_base_sistema", "lead_time_manual_activo",
        "factor_ltaf", "dlt_dias", "factor_zaf", "tor", "toy", "tog",
        "tog_antes_racionamiento", "tog_racionado", "ajuste_manual",
        "tor_recomendado_sistema", "toy_recomendado_sistema", "tog_recomendado_sistema"]].to_sql(
        "ddmrp_ajuste_mp", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()
    print("[OK] ddmrp_buffer_mp actualizado · detalle en ddmrp_ajuste_mp.")

if __name__ == "__main__":
    main()