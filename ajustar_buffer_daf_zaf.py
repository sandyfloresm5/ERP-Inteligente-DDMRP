"""
ajustar_buffer_daf_zaf.py
Cargill Ecuador - Aquanutrition

Ajusta TOR/TOY/TOG por DAF (pico de demanda ya conocido), ZAF (zona
del buffer muy comprometida) y RAF (caída de demanda ya confirmada
este mes) -- el buffer no es fijo, respira antes del pico Y se achica
cuando la demanda real ya viene sostenidamente por debajo del
histórico. LTAF queda en 1.0 (lead time fijo, sin variación por ahora).
"""

import sqlite3

import numpy as np
import pandas as pd

from clasificar_mts_mto import _existe
from config import DB_PATH
from calcular_buffer_ddmrp_dinamico import calcular_zonas_vectorizado, CICLO_PEDIDO_PT_DIAS

ZAF_ZONA = "verde"            # zona a la que se aplica el ZAF: "verde" o "roja"
UMBRAL_AJUSTE_VISIBLE = 0.05  # se marca como ajustado si el TOG cambia más de 5 %


def _aplicar_zaf(z: pd.DataFrame, zaf: pd.Series) -> pd.DataFrame:
    num = lambda c: pd.to_numeric(z[c], errors="coerce")
    ok = z["estado"] == "ok"
    if ZAF_ZONA == "roja":
        z.loc[ok, "tor"] = (num("tor") * zaf).round(1)
        z.loc[ok, "toy"] = (num("tor") + num("yellow")).round(1)
    else:
        z.loc[ok, "green"] = (num("green") * zaf).round(1)
    z.loc[ok, "tog"] = (num("toy") + num("green")).round(1)
    return z


def main():
    conn = sqlite3.connect(DB_PATH)
    todo = pd.read_sql("SELECT * FROM ddmrp_buffer_pt", conn)
    todo["sku"] = todo["sku"].astype(str).str.strip()

    for base, col in (("adu_base", "adu"), ("dlt_base", "dlt_dias")):
        if base not in todo.columns:
            todo[base] = todo[col]
        todo[base] = todo[base].fillna(todo[col])

    sel = (todo["mes_offset"] == 0) & (todo["estado"] == "ok")
    m = todo.loc[sel].copy()

    if _existe(conn, "ddmrp_factores_manuales"):
        fac = pd.read_sql("SELECT sku, daf, zaf, ltaf FROM ddmrp_factores_manuales", conn)
        fac["sku"] = fac["sku"].astype(str).str.strip()
        m = m.drop(columns=["factor_daf", "factor_zaf", "factor_ltaf"], errors="ignore").merge(
            fac.rename(columns={"daf": "factor_daf", "zaf": "factor_zaf", "ltaf": "factor_ltaf"}),
            on="sku", how="left")
        m.index = todo.loc[sel].index
    for c in ("factor_daf", "factor_zaf", "factor_ltaf"):
        if c not in m.columns:
            m[c] = 1.0
        m[c] = m[c].fillna(1.0)

    ciclo = CICLO_PEDIDO_PT_DIAS

    # Zonas SIN ajuste (referencia para la app)
    base = m.copy()
    base["adu"], base["dlt_dias"] = base["adu_base"], base["dlt_base"]
    z_base = calcular_zonas_vectorizado(base, ciclo_pedido_dias=ciclo)

    # Zonas CON ajuste: DAF -> ADU, LTAF -> DLT, ZAF -> una zona
    m["adu"] = m["adu_base"] * m["factor_daf"]
    m["dlt_dias"] = m["dlt_base"] * m["factor_ltaf"]
    z = calcular_zonas_vectorizado(m, ciclo_pedido_dias=ciclo)
    z = _aplicar_zaf(z, m["factor_zaf"])

    for c in z.columns:
        if c == "estado":
            todo.loc[sel, c] = z[c].astype(str)
        else:
            if c not in todo.columns:
                todo[c] = np.nan
            todo[c] = pd.to_numeric(todo[c], errors="coerce")
            todo.loc[sel, c] = pd.to_numeric(z[c], errors="coerce").values
    todo.loc[sel, "adu"] = m["adu"]
    todo.loc[sel, "dlt_dias"] = m["dlt_dias"]
    for c in ("factor_daf", "factor_zaf", "factor_ltaf"):
        todo.loc[sel, c] = m[c]

    tog_base = pd.to_numeric(z_base["tog"], errors="coerce")
    tog_aj = pd.to_numeric(z["tog"], errors="coerce")
    cambio = (tog_aj / tog_base.replace(0, np.nan) - 1).fillna(0)

    def _motivo(r):
        partes = []
        if abs(r["factor_daf"] - 1) > 1e-9:
            partes.append(f"DAF {r['factor_daf']:.2f} (ADU)")
        if abs(r["factor_ltaf"] - 1) > 1e-9:
            partes.append(f"LTAF {r['factor_ltaf']:.2f} (lead time)")
        if abs(r["factor_zaf"] - 1) > 1e-9:
            partes.append(f"ZAF {r['factor_zaf']:.2f} (zona {ZAF_ZONA})")
        return "Ajuste planificado: " + ", ".join(partes) if partes else "Sin ajuste"

    detalle = pd.DataFrame({
        "sku": m["sku"],
        "factor_daf": m["factor_daf"], "factor_zaf": m["factor_zaf"],
        "factor_ltaf": m["factor_ltaf"],
        "factor_baja": 1.0,
        # Escala de la demanda-en-DLT (DAF × LTAF): la usa clasificar_mts_mto.py
        # para escalar p10/p50/p90 de forma consistente con el buffer ajustado.
        "factor_ajuste": m["factor_daf"] * m["factor_ltaf"],
        "ajuste_visible": cambio.abs() > UMBRAL_AJUSTE_VISIBLE,
        "motivo_ajuste": m.apply(_motivo, axis=1),
        "tor_base": z_base["tor"], "toy_base": z_base["toy"], "tog_base": z_base["tog"],
        "tor": z["tor"], "toy": z["toy"], "tog": z["tog"],
    })

    n_aj = int(((m[["factor_daf", "factor_zaf", "factor_ltaf"]] - 1).abs() > 1e-9).any(axis=1).sum())
    print(f" {n_aj} SKU con ajuste planificado (DAF/LTAF/ZAF) de {len(m)} con buffer.")
    if n_aj:
        print(detalle.loc[detalle["motivo_ajuste"] != "Sin ajuste",
                          ["sku", "motivo_ajuste", "tog_base", "tog"]].head(15).to_string(index=False))

    todo.to_sql("ddmrp_buffer_pt", conn, if_exists="replace", index=False)
    detalle.to_sql("ddmrp_ajuste_daf_zaf", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()
    print("[OK] ddmrp_buffer_pt actualizado, detalle en 'ddmrp_ajuste_daf_zaf'.")


if __name__ == "__main__":
    main()