"""
plan_compras_mp.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP: plan de compras de MP (proyección diaria)
"""

import sys
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH

HOY = pd.Timestamp(date.today())
HORIZONTE_EXTRA_DIAS = 28        # horizonte = lead time de la MP + 28 días
HORIZONTE_MINIMO_DIAS = 60
DIAS_REAL_A_GUARDAR = 60         # días de consumo real que se guardan en la serie
VENTANA_AVISO_COMPRA_DIAS = 14   # "comprar a más tardar" si la compra cae en esta ventana


# ------------------------------------------------------------ utilidades
def _existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _cols(conn, t):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
    except Exception:
        return set()


def _sku(s):
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _f(d):
    return d.strftime("%Y-%m-%d") if pd.notna(d) else None


# ------------------------------------------------------------ carga
def cargar_buffer(conn) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM ddmrp_buffer_mp WHERE estado = 'ok'", conn)
    df["sku"] = _sku(df["sku"])
    for c in ("adu", "tor", "toy", "tog", "dlt_dias"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "mp_descripcion" not in df.columns:
        df["mp_descripcion"] = None
    df = df.dropna(subset=["tor", "toy", "tog", "dlt_dias"])
    return df[["sku", "mp_descripcion", "adu", "tor", "toy", "tog", "dlt_dias"]]


def cargar_en_planta(conn) -> pd.Series:
    """Disponible + tránsito SAP (ya está en planta). Último snapshot."""
    if not _existe(conn, "posicion_inventario_mp"):
        return pd.Series(dtype=float)
    f = conn.execute("SELECT MAX(fecha_snapshot) FROM posicion_inventario_mp").fetchone()[0]
    p = pd.read_sql("SELECT material, disponible_tm, transito_tm FROM posicion_inventario_mp "
                    "WHERE fecha_snapshot = ?", conn, params=(f,))
    p["material"] = _sku(p["material"])
    p["en_planta"] = (pd.to_numeric(p["disponible_tm"], errors="coerce").fillna(0)
                      + pd.to_numeric(p["transito_tm"], errors="coerce").fillna(0))
    print(f"  Inventario MP: snapshot {f}")
    if f and pd.Timestamp(f) < HOY - pd.Timedelta(days=1):
        print(f"  [AVISO] El inventario de MP es del {f}: la proyección arranca de un stock "
              f"desactualizado. Correr la ingesta de inventario antes.")
    return p.groupby("material")["en_planta"].sum()


def cargar_oc(conn) -> pd.DataFrame:
    if not _existe(conn, "oc_importacion_mp"):
        print("  [AVISO] Sin oc_importacion_mp: no se proyectan llegadas.")
        return pd.DataFrame(columns=["sku_mp", "cantidad_tm", "fecha_llegada", "nave", "booking"])
    cols = _cols(conn, "oc_importacion_mp")
    extra = ", ".join(c for c in ("nave", "booking", "proveedor", "fuente_fecha") if c in cols)
    oc = pd.read_sql(f"SELECT sku_mp, cantidad_tm, fecha_llegada{', ' + extra if extra else ''} "
                     "FROM oc_importacion_mp", conn)
    oc["sku_mp"] = _sku(oc["sku_mp"])
    oc["fecha_llegada"] = pd.to_datetime(oc["fecha_llegada"], errors="coerce").dt.normalize()
    oc["cantidad_tm"] = pd.to_numeric(oc["cantidad_tm"], errors="coerce").fillna(0)
    oc = oc[(oc["fecha_llegada"] >= HOY) & (oc["cantidad_tm"] > 0)]
    for c in ("nave", "booking", "proveedor", "fuente_fecha"):
        if c not in oc.columns:
            oc[c] = None
    return oc


def _bom_promedio(conn) -> pd.DataFrame:
    """% de cada MP en cada PT: promedio de todas las corridas del BOM vigente
    (0 % en las corridas donde la MP no aparece). Igual que calcular_nfp.py."""
    if not _existe(conn, "bom_semanal_pt"):
        return pd.DataFrame(columns=["sku_pt", "sku_mp", "pct"])
    bom = pd.read_sql(
        "SELECT sku_pt, centro, fecha_planeada, calibre, sku_mp, pct FROM bom_semanal_pt "
        "WHERE fecha_snapshot = (SELECT MAX(fecha_snapshot) FROM bom_semanal_pt)", conn)
    bom["sku_pt"] = _sku(bom["sku_pt"])
    bom["sku_mp"] = _sku(bom["sku_mp"])
    bom["corrida"] = (bom["centro"].astype(str) + "|" + bom["fecha_planeada"].astype(str)
                      + "|" + bom["calibre"].astype(str))
    n = bom.groupby("sku_pt")["corrida"].nunique()
    s = bom.groupby(["sku_pt", "sku_mp"], as_index=False)["pct"].sum()
    s["pct"] = s["pct"] / s["sku_pt"].map(n)
    return s


def serie_demanda(conn, buf: pd.DataFrame, fin: pd.Timestamp) -> pd.DataFrame:
    """Serie diaria única: REAL (pasado) / PLAN / RMB (futuro)."""
    skus = set(buf["sku"])

    # --- REAL ---
    real = pd.DataFrame(columns=["sku", "fecha", "demanda_tm", "fuente"])
    if _existe(conn, "consumo_mp"):
        desde = (HOY - pd.Timedelta(days=DIAS_REAL_A_GUARDAR)).strftime("%Y-%m-%d")
        c = pd.read_sql("SELECT sku_mp, fecha_contabilizacion, consumo_tm FROM consumo_mp "
                        "WHERE fecha_contabilizacion >= ?", conn, params=(desde,))
        c["sku"] = _sku(c["sku_mp"])
        c["fecha"] = pd.to_datetime(c["fecha_contabilizacion"], errors="coerce").dt.normalize()
        c = c[c["sku"].isin(skus) & (c["fecha"] < HOY)]
        real = (c.groupby(["sku", "fecha"], as_index=False)["consumo_tm"].sum()
                 .rename(columns={"consumo_tm": "demanda_tm"}))
        real["fuente"] = "REAL"

    # --- PLAN ---
    plan_fin = None
    plan_mp = pd.DataFrame(columns=["sku", "fecha", "demanda_tm"])
    if _existe(conn, "plan_produccion"):
        plan = pd.read_sql("SELECT sku, fecha, tm FROM plan_produccion", conn)
        plan["sku"] = _sku(plan["sku"])
        plan["fecha"] = pd.to_datetime(plan["fecha"], errors="coerce").dt.normalize()
        plan = plan[plan["fecha"] >= HOY]
        if not plan.empty:
            plan_fin = plan["fecha"].max()
            bom = _bom_promedio(conn)
            x = plan.merge(bom, left_on="sku", right_on="sku_pt", how="left")
            sin_bom = sorted(x.loc[x["sku_mp"].isna(), "sku"].unique())
            if sin_bom:
                print(f"  [AVISO] {len(sin_bom)} PT del plan sin fórmula en el BOM vigente: "
                      f"su consumo de MP no entra ({sin_bom[:8]}).")
            x = x.dropna(subset=["sku_mp"])
            x["demanda_tm"] = x["tm"] * x["pct"] / 100.0
            plan_mp = (x.groupby(["sku_mp", "fecha"], as_index=False)["demanda_tm"].sum()
                        .rename(columns={"sku_mp": "sku"}))
            plan_mp = plan_mp[plan_mp["sku"].isin(skus)]
    if plan_fin is None:
        print("  [AVISO] Sin plan de producción desde hoy: toda la demanda futura sale del RMB.")

    # --- Grilla futura: PLAN dentro del plan, RMB después ---
    dias = pd.date_range(HOY, fin, freq="D")
    grid = pd.MultiIndex.from_product([sorted(skus), dias], names=["sku", "fecha"]).to_frame(index=False)
    grid = grid.merge(plan_mp, on=["sku", "fecha"], how="left")
    en_plan = grid["fecha"] <= plan_fin if plan_fin is not None else pd.Series(False, index=grid.index)
    grid["fuente"] = np.where(en_plan, "PLAN", "RMB")
    grid.loc[en_plan, "demanda_tm"] = grid.loc[en_plan, "demanda_tm"].fillna(0.0)

    rmb = {}
    if _existe(conn, "rmb_mp"):
        r = pd.read_sql("SELECT sku_mp, anio, mes_num, rmb_tm FROM rmb_mp", conn)
        r["sku_mp"] = _sku(r["sku_mp"])
        r = r.groupby(["sku_mp", "anio", "mes_num"], as_index=False)["rmb_tm"].sum()
        rmb = {(a, int(b), int(c)): float(d) for a, b, c, d in r.itertuples(index=False)}
    adu = buf.set_index("sku")["adu"].to_dict()

    fuera = ~en_plan
    def _rmb_dia(row):
        v = rmb.get((row.sku, row.fecha.year, row.fecha.month))
        if v is None:
            return float(adu.get(row.sku) or 0.0), "ADU"
        return v / row.fecha.days_in_month, "RMB"
    vals = [_rmb_dia(r) for r in grid.loc[fuera, ["sku", "fecha"]].itertuples(index=False)]
    if vals:
        grid.loc[fuera, "demanda_tm"] = [v[0] for v in vals]
        grid.loc[fuera, "fuente"] = [v[1] for v in vals]

    serie = pd.concat([real[["sku", "fecha", "demanda_tm", "fuente"]],
                       grid[["sku", "fecha", "demanda_tm", "fuente"]]], ignore_index=True)
    serie["demanda_tm"] = pd.to_numeric(serie["demanda_tm"], errors="coerce").fillna(0.0)
    return serie, plan_fin


def cargar_nfp_hoy(conn) -> dict:
    """NFP de hoy del flujo neto (misma cifra que Inventario Estratégico)."""
    if not _existe(conn, "ddmrp_flujo_neto"):
        return {}
    df = pd.read_sql("SELECT sku, nfp_tm FROM ddmrp_flujo_neto WHERE nodo = 'MP' "
                     "AND fecha_calculo = ?", conn, params=(HOY.strftime("%Y-%m-%d"),))
    if df.empty:
        print("  [AVISO] ddmrp_flujo_neto no es de hoy: correr calcular_nfp.py antes. "
              "Se usa el NFP calculado aquí.")
        return {}
    df["sku"] = _sku(df["sku"])
    return df.set_index("sku")["nfp_tm"].astype(float).to_dict()


def cargar_vida_util(conn) -> dict:
    conn.execute("""CREATE TABLE IF NOT EXISTS config_vida_util_mp (
        sku TEXT PRIMARY KEY, vida_util_dias REAL,
        actualizado_por TEXT, actualizado_en TEXT DEFAULT (datetime('now')))""")
    conn.commit()
    v = pd.read_sql("SELECT sku, vida_util_dias FROM config_vida_util_mp "
                    "WHERE vida_util_dias IS NOT NULL AND vida_util_dias > 0", conn)
    v["sku"] = _sku(v["sku"])
    return v.set_index("sku")["vida_util_dias"].astype(float).to_dict()


def _capacidad_mp(conn):
    if _existe(conn, "config_capacidad_bodega"):
        f = conn.execute("SELECT capacidad_mp_tm FROM config_capacidad_bodega WHERE id = 1").fetchone()
        if f and f[0] is not None:
            return float(f[0])
    return None


# ------------------------------------------------------------ simulación
def simular(sku, stock0, oc_sku, dem, dias, tor, toy, tog, dlt, nfp0=None, con_compras=True):
    """Proyección diaria. Devuelve (filas, compras_sugeridas)."""
    llegadas_oc = oc_sku.groupby("fecha_llegada")["cantidad_tm"].sum().to_dict()
    llegadas_sug = {}
    compras = []
    filas = []
    stock = float(stock0)
    dlt_d = int(round(dlt))
    for i, d in enumerate(dias):
        a_oc = llegadas_oc.get(d, 0.0)
        a_sug = llegadas_sug.get(d, 0.0)
        stock_ini = stock + a_oc + a_sug
        pend = (sum(v for k, v in llegadas_oc.items() if k > d)
                + sum(v for k, v in llegadas_sug.items() if k > d))
        dm = float(dem.get(d, 0.0))
        nfp = stock_ini + pend - dm
        if i == 0 and nfp0 is not None:
            nfp = float(nfp0)
        compra = 0.0
        if con_compras and tog > 0 and nfp <= toy:
            compra = max(tog - max(nfp, 0.0), 0.0)
            if compra > 0.01:
                llega = d + pd.Timedelta(days=dlt_d)
                llegadas_sug[llega] = llegadas_sug.get(llega, 0.0) + compra
                compras.append({"sku": sku, "fecha_lanzar": d, "cantidad_tm": round(compra, 1),
                                "fecha_llegada": llega})
        stock = stock_ini - dm
        zona = ("ROJO" if nfp <= tor else "AMARILLO" if nfp <= toy
                else "VERDE" if nfp <= tog else "SOBRE_TOG")
        filas.append({"sku": sku, "fecha": d, "demanda_tm": dm, "llegada_oc_tm": a_oc,
                      "llegada_sugerida_tm": a_sug, "stock_proyectado_tm": stock,
                      "nfp_proyectado_tm": nfp, "compra_sugerida_tm": compra, "zona": zona})
    return filas, compras


def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"=== Plan de compras MP — {HOY:%Y-%m-%d} ===")
    if not _existe(conn, "ddmrp_buffer_mp"):
        print("[ERROR] Falta ddmrp_buffer_mp.")
        return

    buf = cargar_buffer(conn)
    if buf.empty:
        print("[ERROR] Ninguna MP con buffer calculable.")
        return
    en_planta = cargar_en_planta(conn)
    oc = cargar_oc(conn)
    nfp_hoy = cargar_nfp_hoy(conn)
    vida = cargar_vida_util(conn)
    cap = _capacidad_mp(conn)

    horizonte = max(HORIZONTE_MINIMO_DIAS, int(buf["dlt_dias"].max()) + HORIZONTE_EXTRA_DIAS)
    fin = HOY + pd.Timedelta(days=horizonte - 1)
    serie, plan_fin = serie_demanda(conn, buf, fin)
    print(f"  Horizonte: {horizonte} días (hasta {fin:%Y-%m-%d}) · plan de producción hasta "
          f"{plan_fin:%Y-%m-%d}" if plan_fin is not None else f"  Horizonte: {horizonte} días")

    dias = list(pd.date_range(HOY, fin, freq="D"))
    fut = serie[serie["fecha"] >= HOY]
    dem_por_sku = {s: g.set_index("fecha")["demanda_tm"].to_dict() for s, g in fut.groupby("sku")}

    proy, sugeridas, resumen = [], [], []
    for r in buf.itertuples(index=False):
        s = r.sku
        stock0 = float(en_planta.get(s, 0.0))
        oc_s = oc[oc["sku_mp"] == s]
        dem = dem_por_sku.get(s, {})
        dias_s = [d for d in dias if d <= HOY + pd.Timedelta(days=int(r.dlt_dias) + HORIZONTE_EXTRA_DIAS)]
        if len(dias_s) < HORIZONTE_MINIMO_DIAS:
            dias_s = dias[:HORIZONTE_MINIMO_DIAS]

        filas, compras = simular(s, stock0, oc_s, dem, dias_s, r.tor, r.toy, r.tog,
                                 r.dlt_dias, nfp_hoy.get(s))
        filas_sin, _ = simular(s, stock0, oc_s, dem, dias_s, r.tor, r.toy, r.tog,
                               r.dlt_dias, nfp_hoy.get(s), con_compras=False)
        proy.extend(filas)
        sugeridas.extend(compras)

        nfp0 = filas[0]["nfp_proyectado_tm"]
        en_camino = float(oc_s["cantidad_tm"].sum())
        adu = float(r.adu or 0.0)
        cobertura = (stock0 + en_camino) / adu if adu > 0 else np.nan
        prox = compras[0] if compras else None
        quiebre_sin = next((f["fecha"] for f in filas_sin if f["stock_proyectado_tm"] < 0), None)
        quiebre_con = next((f["fecha"] for f in filas if f["stock_proyectado_tm"] < 0), None)

        vu = vida.get(s)
        if vu and pd.notna(cobertura) and cobertura > vu:
            senal = "RIESGO_VENCIMIENTO"
            msg = (f"Cobertura {cobertura:,.0f} días supera la vida útil ({vu:,.0f} días): "
                   f"no comprar.")
            fecha_ref = None
        elif nfp0 <= r.toy:
            senal = "COMPRAR_HOY"
            q = prox["cantidad_tm"] if prox else max(r.tog - max(nfp0, 0), 0)
            msg = (f"Emitir OC por {q:,.0f} TM hoy · llegada estimada "
                   f"{(HOY + pd.Timedelta(days=int(r.dlt_dias))):%d/%m/%Y}.")
            fecha_ref = HOY
        elif nfp0 > r.tog:
            senal = "NO_COMPRAR_HASTA"
            fecha_ref = prox["fecha_lanzar"] if prox else None
            msg = (f"No emitir nuevas OC hasta el {fecha_ref:%d/%m/%Y}." if fecha_ref is not None
                   else f"No emitir nuevas OC en los próximos {len(dias_s)} días.")
        elif prox is not None and prox["fecha_lanzar"] <= HOY + pd.Timedelta(days=VENTANA_AVISO_COMPRA_DIAS):
            senal = "COMPRAR_ANTES_DE"
            fecha_ref = prox["fecha_lanzar"]
            msg = f"Lanzar OC por {prox['cantidad_tm']:,.0f} TM a más tardar el {fecha_ref:%d/%m/%Y}."
        else:
            senal = "CUBIERTO"
            fecha_ref = prox["fecha_lanzar"] if prox else None
            msg = ("Sin compra necesaria" + (f"; próxima compra estimada el {fecha_ref:%d/%m/%Y}."
                                              if fecha_ref is not None else " en el horizonte."))
        if quiebre_con is not None:
            msg += (f" ⚠️ Riesgo de quiebre el {quiebre_con:%d/%m/%Y} aun comprando hoy: "
                    f"el lead time ({int(r.dlt_dias)} d) no alcanza.")

        resumen.append({
            "sku": s, "descripcion": r.mp_descripcion, "senal": senal, "mensaje": msg,
            "fecha_referencia": _f(fecha_ref),
            "cantidad_sugerida_tm": round(prox["cantidad_tm"], 1) if prox else 0.0,
            "fecha_llegada_sugerida": _f(prox["fecha_llegada"]) if prox else None,
            "en_planta_tm": round(stock0, 1), "en_camino_tm": round(en_camino, 1),
            "nfp_hoy_tm": round(nfp0, 1), "tor": r.tor, "toy": r.toy, "tog": r.tog,
            "adu": round(adu, 2), "lead_time_dias": int(r.dlt_dias),
            "cobertura_dias": round(cobertura, 1) if pd.notna(cobertura) else None,
            "fecha_quiebre_sin_comprar": _f(quiebre_sin),
            "fecha_quiebre_aun_comprando": _f(quiebre_con),
            "vida_util_dias": vu, "fecha_calculo": HOY.strftime("%Y-%m-%d"),
        })

    res = pd.DataFrame(resumen)
    orden = {"COMPRAR_HOY": 0, "COMPRAR_ANTES_DE": 1, "RIESGO_VENCIMIENTO": 2,
             "NO_COMPRAR_HASTA": 3, "CUBIERTO": 4}
    res["_o"] = res["senal"].map(orden)
    res = res.sort_values(["_o", "fecha_referencia", "sku"]).drop(columns="_o")

    df_proy = pd.DataFrame(proy)
    df_proy["fecha"] = df_proy["fecha"].dt.strftime("%Y-%m-%d")
    for c in ("demanda_tm", "llegada_oc_tm", "llegada_sugerida_tm", "stock_proyectado_tm",
              "nfp_proyectado_tm", "compra_sugerida_tm"):
        df_proy[c] = df_proy[c].round(1)
    df_sug = pd.DataFrame(sugeridas, columns=["sku", "fecha_lanzar", "cantidad_tm", "fecha_llegada"])
    for c in ("fecha_lanzar", "fecha_llegada"):
        df_sug[c] = pd.to_datetime(df_sug[c]).dt.strftime("%Y-%m-%d")
    serie_out = serie.copy()
    serie_out["fecha"] = serie_out["fecha"].dt.strftime("%Y-%m-%d")
    serie_out["demanda_tm"] = serie_out["demanda_tm"].round(2)

    # --- Avisos ---
    avisos = []
    if cap:
        tot = (pd.DataFrame(proy).assign(st=lambda x: x["stock_proyectado_tm"].clip(lower=0))
               .groupby("fecha")["st"].sum())
        sobre = tot[tot > cap]
        if len(sobre):
            d0 = sobre.index[0]
            avisos.append({"tipo": "BODEGA_LLENA", "sku": None, "fecha": _f(d0),
                           "mensaje": f"El stock proyectado de MP ({sobre.iloc[0]:,.0f} TM) supera la "
                                      f"capacidad de bodega ({cap:,.0f} TM) desde el {d0:%d/%m/%Y}."})
    if _existe(conn, "oc_importacion_mp_vencidas"):
        v = pd.read_sql("SELECT * FROM oc_importacion_mp_vencidas", conn)
        if not v.empty and "fuente_fecha" in v.columns:
            for x in v[v["fuente_fecha"] == "ETA_ESTIMADO"].itertuples(index=False):
                avisos.append({"tipo": "OC_ETA_VENCIDA", "sku": str(x.sku_mp), "fecha": x.fecha_llegada,
                               "mensaje": f"OC de {x.cantidad_tm:,.0f} TM con ETA estimada vencida "
                                          f"({x.fecha_llegada}): no se cuenta en la proyección. "
                                          f"Confirmar si llegó o actualizar la ETA."})
    if _existe(conn, "oc_importacion_mp_sin_fecha"):
        v = pd.read_sql("SELECT * FROM oc_importacion_mp_sin_fecha", conn)
        for x in v.itertuples(index=False):
            avisos.append({"tipo": "OC_SIN_FECHA", "sku": str(x.sku_mp), "fecha": None,
                           "mensaje": f"OC de {x.cantidad_tm:,.0f} TM sin ETA: no se puede proyectar. "
                                      f"Pedir fecha a Compras."})
    df_av = pd.DataFrame(avisos, columns=["tipo", "sku", "fecha", "mensaje"])
    df_av["fecha_calculo"] = HOY.strftime("%Y-%m-%d")

    serie_out.to_sql("demanda_mp_diaria", conn, if_exists="replace", index=False)
    df_proy.to_sql("proyeccion_mp_diaria", conn, if_exists="replace", index=False)
    df_sug.to_sql("compras_sugeridas_mp", conn, if_exists="replace", index=False)
    res.to_sql("plan_compras_mp", conn, if_exists="replace", index=False)
    df_av.to_sql("plan_compras_avisos", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()

    print("\n" + "=" * 62)
    print("PLAN DE COMPRAS MP")
    print("=" * 62)
    print(res["senal"].value_counts().to_string())
    vista = res[res["senal"].isin(["COMPRAR_HOY", "COMPRAR_ANTES_DE"])]
    if len(vista):
        print("\n  Comprar:")
        print(vista[["sku", "descripcion", "senal", "cantidad_sugerida_tm", "fecha_referencia",
                     "fecha_llegada_sugerida"]].to_string(index=False))
    if len(df_av):
        print(f"\n  Avisos: {df_av['tipo'].value_counts().to_dict()}")
    print("\n[OK] Tablas: plan_compras_mp, compras_sugeridas_mp, proyeccion_mp_diaria, "
          "demanda_mp_diaria, plan_compras_avisos")


if __name__ == "__main__":
    main()