"""
validar_plan_vs_buffer.py
Cargill Ecuador - Aquanutrition
"""
import sys
from pathlib import Path
from datetime import date
import pandas as pd
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))
from config import DB_PATH

HOY = pd.Timestamp(date.today())


def main():
    conn = sqlite3.connect(DB_PATH)
    f = conn.execute("SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto").fetchone()[0]
    buffer = pd.read_sql("""
        SELECT sku, descripcion, disponible_tm, transito_tm, demanda_calificada_tm,
               tor, toy, tog, zona_nfp
        FROM ddmrp_flujo_neto WHERE fecha_calculo=? AND nodo='PT'
    """, conn, params=(f,))

    buffer["sku"] = buffer["sku"].astype(str).str.strip()
    
    buffer["stock_fisico"] = buffer["disponible_tm"].fillna(0) + buffer["transito_tm"].fillna(0)

    plan = pd.read_sql("""
        SELECT sku, SUM(tm) as tm_plan FROM plan_produccion GROUP BY sku
    """, conn)

    plan["sku"] = plan["sku"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

    m = buffer.merge(plan, on="sku", how="left")
    m["tm_plan"] = m["tm_plan"].fillna(0.0)
    m["stock_proyectado"] = m["stock_fisico"] + m["tm_plan"]
    m["tiene_pedido"] = m["demanda_calificada_tm"] > 0
    m["excede_tog"] = m["stock_proyectado"] > m["tog"]
    m["no_cubre_pedido"] = m["tiene_pedido"] & (
        (m["stock_fisico"] + m["tm_plan"]) < m["demanda_calificada_tm"])

    def clasificar(r):
        if r["no_cubre_pedido"]:
            return "4-RIESGO: NO CUBRE PEDIDO"
        if r["excede_tog"] and not r["tiene_pedido"]:
            return "3-RIESGO: EXCEDE TOG SIN PEDIDO"
        if r["excede_tog"] and r["tiene_pedido"]:
            return "3b-EXCEDE TOG (con pedido, revisar si es necesario)"
        if r["tiene_pedido"]:
            return "1-CORRECTO CON PEDIDO"
        return "2-CORRECTO SIN PEDIDO"

    m["escenario"] = m.apply(clasificar, axis=1)

    print(f"Validación Plan vs Buffer — {HOY.date()}")
    print(m["escenario"].value_counts().sort_index().to_string())
    print()

    problemas = m[m["escenario"].str.startswith(("3", "4"))].sort_values("escenario")
    if problemas.empty:
        print("Sin inconsistencias -- todo el plan es coherente con el buffer.")
    else:
        cols = ["sku", "descripcion", "escenario", "stock_fisico", "demanda_calificada_tm",
                "tm_plan", "stock_proyectado", "tog"]
        print(problemas[cols].round(1).to_string(index=False))

    m.to_sql("validacion_plan_vs_buffer", conn, if_exists="replace", index=False)
    conn.commit()
    print(f"\n[OK] Tabla: validacion_plan_vs_buffer ({len(m)} SKU evaluados)")
    conn.close()


if __name__ == "__main__":
    main()