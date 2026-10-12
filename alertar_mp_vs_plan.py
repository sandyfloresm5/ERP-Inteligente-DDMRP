"""
alertar_mp_vs_plan.py
Cargill Ecuador - Aquanutrition
Módulo 3-4 - Un solo sistema: el plan de producción de esta semana
consume MP día a día; esto proyecta si alguna MP se queda corta.
"""

import sys
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import numpy as np
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH
try:
    from config import CAPACIDAD_BODEGA_MP_TM
except ImportError:
    CAPACIDAD_BODEGA_MP_TM = None

HOY = pd.Timestamp(date.today())

# Código de centro (BOM) -> nombre de planta (plan_produccion). Confirmado
# con el planificador -- si se agregan más plantas, extender aquí.
CENTRO_A_PLANTA = {"1FZO": "GUAYAQUIL", "1FZE": "DURAN"}


def _tabla_existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def cargar_datos(conn) -> dict:
    d = {}
    faltantes = []

    for t in ["plan_produccion", "bom_semanal_pt", "ddmrp_buffer_mp",
              "posicion_inventario_mp"]:
        if not _tabla_existe(conn, t):
            faltantes.append(t)

    if faltantes:
        raise RuntimeError(f"Faltan tablas: {faltantes}. Correr optimizar_produccion.py, "
                           f"Ingesta_MP_Importacion_BOM.py, calcular_buffer_ddmrp_dinamico.py "
                           f"e Ingesta_Inventario.py en ese orden.")

    d["plan"] = pd.read_sql(
        "SELECT sku, fecha, planta, tm FROM plan_produccion "
        "WHERE fecha_calculo = (SELECT MAX(fecha_calculo) FROM plan_produccion)", conn)
    d["plan"]["sku"] = d["plan"]["sku"].astype(str).str.strip()

    d["bom"] = pd.read_sql(
        "SELECT sku_pt, centro, fecha_planeada, calibre, sku_mp, pct, sospechoso "
        "FROM bom_semanal_pt "
        "WHERE fecha_snapshot = (SELECT MAX(fecha_snapshot) FROM bom_semanal_pt)", conn)

    # El plan de producción no distingue calibre. Si un PT-centro-fecha
    # tiene varias líneas de calibre en el BOM, se usa la que sí suma
    # ~100% (más confiable); si hay más de una válida o ninguna, se toma
    # la primera y se marca sospechoso -- no se suman calibres entre sí
    # (eso fue el bug del 500%).
    if not d["bom"].empty:
        suma = d["bom"].groupby(["sku_pt", "centro", "fecha_planeada", "calibre"])["pct"].sum()
        d["bom"]["_desvio_100"] = d["bom"].apply(
            lambda r: abs(suma.loc[(r["sku_pt"], r["centro"], r["fecha_planeada"], r["calibre"])] - 100),
            axis=1)
        idx_mejor = (d["bom"].groupby(["sku_pt", "centro", "fecha_planeada"])["_desvio_100"]
                    .transform("min"))
        d["bom"] = d["bom"][d["bom"]["_desvio_100"] == idx_mejor].drop(columns="_desvio_100")
        d["bom"] = d["bom"].drop_duplicates(subset=["sku_pt", "centro", "fecha_planeada", "sku_mp"])

    d["buffer_mp"] = pd.read_sql(
        "SELECT sku, adu, tor, toy, tog, mp_descripcion, nivel_rotacion FROM ddmrp_buffer_mp "
        "WHERE estado = 'ok'", conn)
    d["buffer_mp"]["sku"] = d["buffer_mp"]["sku"].astype(str).str.strip()

    f_inv = conn.execute(
        "SELECT MAX(fecha_snapshot) FROM posicion_inventario_mp").fetchone()[0]
    d["inv_mp"] = pd.read_sql(
        "SELECT material, disponible_tm FROM posicion_inventario_mp "
        "WHERE fecha_snapshot = ?", conn, params=(f_inv,))
    d["inv_mp"]["material"] = d["inv_mp"]["material"].astype(str).str.strip()

    if _tabla_existe(conn, "oc_importacion_mp"):
        d["oc"] = pd.read_sql(
            "SELECT sku_mp, cantidad_tm, fecha_llegada, fuente_fecha, proveedor "
            "FROM oc_importacion_mp "
            "WHERE fecha_snapshot = (SELECT MAX(fecha_snapshot) FROM oc_importacion_mp)",
            conn)
    else:
        d["oc"] = pd.DataFrame(columns=["sku_mp", "cantidad_tm", "fecha_llegada"])
        print("  [AVISO] Sin 'oc_importacion_mp' -- se asume cero reposición en el "
              "horizonte (peor caso, no certeza). Correr Ingesta_MP_Importacion_BOM.py.")

    return d


def explotar_consumo(plan: pd.DataFrame, bom: pd.DataFrame) -> tuple:
    """Devuelve (consumo_diario_mp, pt_sin_bom)."""
    pt_con_bom = set(bom["sku_pt"])
    pt_en_plan = set(plan["sku"])
    pt_sin_bom = sorted(pt_en_plan - pt_con_bom)

    if pt_sin_bom:
        tm_sin_bom = plan[plan["sku"].isin(pt_sin_bom)]["tm"].sum()
        print(f"  [AVISO] {len(pt_sin_bom)} PT en el plan SIN fórmula en el BOM de "
              f"esta semana ({tm_sin_bom:,.1f} TM) -- su consumo de MP NO se proyecta, "
              f"no se asume cero.")

    bom = bom.copy()
    bom["planta"] = bom["centro"].map(CENTRO_A_PLANTA)
    sin_mapeo = sorted(set(bom.loc[bom["planta"].isna(), "centro"].unique()))
    if sin_mapeo:
        print(f"  [AVISO] Centros del BOM sin mapeo a planta: {sin_mapeo} -- esas "
              f"filas cruzan solo por PT+fecha, sin filtrar planta.")

    # Cruce en 2 pasos: 1) PT+planta+fecha exacta (preciso). 2) si no hay,
    # PT+fecha sin filtrar planta (aproximado, se marca).
    filas = []
    for _, p in plan.iterrows():
        sub = bom[(bom["sku_pt"] == p["sku"]) & (bom["planta"] == p["planta"])
                 & (bom["fecha_planeada"] == p["fecha"])]
        aproximado = False
        if sub.empty:
            sub = bom[(bom["sku_pt"] == p["sku"]) & (bom["fecha_planeada"] == p["fecha"])]
            aproximado = True
        if sub.empty:
            sub = bom[bom["sku_pt"] == p["sku"]]
            aproximado = True
        if sub.empty:
            continue
        for _, b in sub.iterrows():
            filas.append({
                "sku_mp": b["sku_mp"], "fecha": p["fecha"],
                "consumo_tm": p["tm"] * (b["pct"] / 100.0),
                "sospechoso": bool(b["sospechoso"]),
                "formula_aproximada": aproximado,
            })

    consumo = pd.DataFrame(filas)
    if consumo.empty:
        return consumo, pt_sin_bom

    n_aprox = int(consumo["formula_aproximada"].sum())
    if n_aprox:
        print(f"  [AVISO] {n_aprox} filas de consumo usan la fórmula de OTRA fecha del "
              f"mismo PT-centro (sin fórmula exacta para ese día) -- aproximado, no exacto.")

    consumo_diario = (consumo.groupby(["sku_mp", "fecha"], as_index=False)
                      .agg(consumo_tm=("consumo_tm", "sum"),
                           algun_sospechoso=("sospechoso", "any")))
    return consumo_diario, pt_sin_bom


def proyectar_inventario_mp(consumo_diario, buffer_mp, inv_mp, oc) -> pd.DataFrame:
    dias = sorted(consumo_diario["fecha"].unique()) if not consumo_diario.empty else []
    dias = [f for f in dias if pd.Timestamp(f) >= HOY.normalize()]
    if not dias:
        return pd.DataFrame()

    inv_inicial = inv_mp.set_index("material")["disponible_tm"].to_dict()
    buffer_idx = buffer_mp.set_index("sku")

    llegadas = pd.DataFrame()
    if not oc.empty:
        llegadas = (oc.groupby(["sku_mp", "fecha_llegada"], as_index=False)
                   ["cantidad_tm"].sum())

    mps_relevantes = sorted(set(consumo_diario["sku_mp"]) & set(buffer_idx.index))
    mps_sin_buffer = sorted(set(consumo_diario["sku_mp"]) - set(buffer_idx.index))
    if mps_sin_buffer:
        print(f"  [AVISO] {len(mps_sin_buffer)} MP con consumo planificado pero SIN "
              f"buffer calculable: {mps_sin_buffer[:10]}"
              f"{' …' if len(mps_sin_buffer) > 10 else ''}")

    filas = []
    for mp in mps_relevantes:
        stock = inv_inicial.get(mp, 0.0)
        tor = float(buffer_idx.loc[mp, "tor"])
        tog = float(buffer_idx.loc[mp, "tog"])
        desc = buffer_idx.loc[mp, "mp_descripcion"]
        rotacion = buffer_idx.loc[mp, "nivel_rotacion"] if "nivel_rotacion" in buffer_idx.columns else "media"
        tiene_llegadas = not llegadas.empty and mp in set(llegadas["sku_mp"])

        for f in dias:
            cons = consumo_diario[(consumo_diario["sku_mp"] == mp)
                                  & (consumo_diario["fecha"] == f)]["consumo_tm"]
            cons = float(cons.iloc[0]) if len(cons) else 0.0

            llega = 0.0
            if not llegadas.empty:
                l = llegadas[(llegadas["sku_mp"] == mp) & (llegadas["fecha_llegada"] == f)]
                llega = float(l["cantidad_tm"].iloc[0]) if len(l) else 0.0

            stock = stock - cons + llega
            filas.append({
                "sku_mp": mp, "descripcion": desc, "fecha": f,
                "nivel_rotacion": rotacion,
                "consumo_tm": round(cons, 2), "llegada_tm": round(llega, 2),
                "stock_proyectado_tm": round(stock, 2),
                "tor": round(tor, 1), "tog": round(tog, 1),
                "en_riesgo": stock < tor,
                # Sobrestock: no existía este chequeo -- para baja rotación
                # es el riesgo que de verdad importa (capital inmovilizado),
                # no el quiebre.
                "en_sobrestock": stock > tog,
                "sin_dato_reposicion": not tiene_llegadas,
            })

    return pd.DataFrame(filas)


def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"Alertas MP vs Plan de Producción — {HOY.date()}")

    try:
        d = cargar_datos(conn)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        conn.close()
        return

    print(f"\n  Plan: {d['plan']['sku'].nunique()} PT · {d['plan']['tm'].sum():,.1f} TM")
    print(f"  BOM: {d['bom']['sku_pt'].nunique()} PT con fórmula esta semana")
    print(f"  OC en camino: {len(d['oc'])} filas, "
          f"{d['oc']['cantidad_tm'].sum() if not d['oc'].empty else 0:,.1f} TM")

    consumo_diario, pt_sin_bom = explotar_consumo(d["plan"], d["bom"])
    if consumo_diario.empty:
        print("\n  Sin consumo de MP explotable (ningún PT del plan tiene BOM esta semana).")
        conn.close()
        return

    proy = proyectar_inventario_mp(consumo_diario, d["buffer_mp"], d["inv_mp"], d["oc"])
    if proy.empty:
        print("\n  Sin proyección posible (MP del plan sin buffer calculable).")
        conn.close()
        return

    proy["fecha_calculo"] = HOY.strftime("%Y-%m-%d")

    if CAPACIDAD_BODEGA_MP_TM:
        stock_x_dia = proy.groupby("fecha")["stock_proyectado_tm"].sum()
        pico = stock_x_dia.max()
        print("\n" + "=" * 62)
        print("CAPACIDAD DE BODEGA MP (todas las MP con consumo esta semana)")
        print("=" * 62)
        print(f"  Pico proyectado: {pico:,.0f} TM · Capacidad: {CAPACIDAD_BODEGA_MP_TM:,.0f} TM")
        if pico > CAPACIDAD_BODEGA_MP_TM:
            dias_exceso = stock_x_dia[stock_x_dia > CAPACIDAD_BODEGA_MP_TM]
            print(f"  ⚠️  EXCEDE en {pico - CAPACIDAD_BODEGA_MP_TM:,.0f} TM el día pico "
                  f"({dias_exceso.idxmax()}). {len(dias_exceso)} día(s) sobre capacidad.")
        else:
            print(f"  Cabe -- holgura {CAPACIDAD_BODEGA_MP_TM - pico:,.0f} TM.")

    conn.execute("DROP TABLE IF EXISTS mp_proyeccion_vs_plan")
    proy.to_sql("mp_proyeccion_vs_plan", conn, if_exists="replace", index=False)
    conn.commit()

    riesgo = proy[proy["en_riesgo"]]
    print("\n" + "=" * 62)
    print("MP EN RIESGO POR EL PLAN DE ESTA SEMANA")
    print("=" * 62)
    if riesgo.empty:
        print("  Ninguna. El plan actual no deja a ninguna MP bajo su TOR.")
    else:
        primer_dia_riesgo = riesgo.groupby("sku_mp")["fecha"].min().reset_index()
        primer_dia_riesgo.columns = ["sku_mp", "primer_dia_riesgo"]
        resumen = riesgo.merge(primer_dia_riesgo, on="sku_mp")
        resumen = resumen[resumen["fecha"] == resumen["primer_dia_riesgo"]]
        orden_rotacion = {"alta": 0, "media": 1, "baja": 2, "sin_rotacion": 3}
        resumen["_orden"] = resumen["nivel_rotacion"].map(orden_rotacion).fillna(1)
        resumen = resumen.sort_values(["_orden", "primer_dia_riesgo"]).drop(columns="_orden")

        print(f"  {riesgo['sku_mp'].nunique()} MP entran en riesgo en algún día del plan "
              f"(ordenado por rotación -- alta primero, es la más urgente de evitar):\n")
        cols = ["sku_mp", "descripcion", "nivel_rotacion", "primer_dia_riesgo",
                "stock_proyectado_tm", "tor", "sin_dato_reposicion"]
        print(resumen[cols].to_string(index=False))

        sin_dato = resumen[resumen["sin_dato_reposicion"]]
        if len(sin_dato):
            print(f"\n  ⚠️  {len(sin_dato)} de esas MP no tienen ninguna OC de "
                  f"importación en camino registrada -- puede ser MP local (sin "
                  f"visibilidad de OC) o que de verdad no hay nada pedido. "
                  f"Confirmar con Compras antes de tomarlo como certeza.")

        # Qué corridas del plan están consumiendo la MP más crítica
        peor = resumen.iloc[0]["sku_mp"]
        corridas = consumo_diario[consumo_diario["sku_mp"] == peor].merge(
            d["plan"].groupby(["sku", "fecha"], as_index=False)["tm"].sum(),
            left_on="fecha", right_on="fecha", how="left")
        print(f"\n  MP más urgente: {peor} -- consumo diario planificado:")
        print(consumo_diario[consumo_diario["sku_mp"] == peor]
              [["fecha", "consumo_tm"]].round(1).to_string(index=False))

    sobrestock = proy[proy["en_sobrestock"]]
    print("\n" + "=" * 62)
    print("MP EN RIESGO DE SOBRESTOCK (capital inmovilizado)")
    print("=" * 62)
    if sobrestock.empty:
        print("  Ninguna. El plan actual no deja a ninguna MP sobre su TOG.")
    else:
        peor_dia_sobre = sobrestock.groupby("sku_mp")["stock_proyectado_tm"].max().reset_index()
        peor_dia_sobre.columns = ["sku_mp", "pico_stock_tm"]
        rsum = sobrestock.merge(peor_dia_sobre, on="sku_mp")
        rsum = rsum[rsum["stock_proyectado_tm"] == rsum["pico_stock_tm"]].drop_duplicates("sku_mp")
        orden_rotacion2 = {"baja": 0, "sin_rotacion": 1, "media": 2, "alta": 3}
        rsum["_orden"] = rsum["nivel_rotacion"].map(orden_rotacion2).fillna(2)
        rsum = rsum.sort_values("_orden").drop(columns="_orden")

        print(f"  {sobrestock['sku_mp'].nunique()} MP proyectan quedar sobre su TOG en algún "
              f"día (ordenado por rotación -- baja primero, ahí es donde el sobrestock pesa "
              f"más en capital inmovilizado):\n")
        print(rsum[["sku_mp", "descripcion", "nivel_rotacion", "fecha", "pico_stock_tm", "tog"]]
              .rename(columns={"fecha": "dia_pico"}).to_string(index=False))

    if pt_sin_bom:
        print(f"\n  {len(pt_sin_bom)} PT del plan sin fórmula esta semana (consumo NO "
              f"proyectado para ellos): {pt_sin_bom[:15]}"
              f"{' …' if len(pt_sin_bom) > 15 else ''}")

    print(f"\n[OK] Tabla: mp_proyeccion_vs_plan")
    conn.close()


if __name__ == "__main__":
    main()