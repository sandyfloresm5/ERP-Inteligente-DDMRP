"""
alertar_urgencias_plan.py
Cargill Ecuador - Aquanutrition
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import date

from config import DB_PATH

HOY = pd.Timestamp(date.today())


def cargar_plan_y_pedidos(conn) -> tuple:
    plan = pd.read_sql(
        "SELECT * FROM plan_produccion WHERE fecha_calculo = "
        "(SELECT MAX(fecha_calculo) FROM plan_produccion)", conn
    )
    plan["fecha"] = pd.to_datetime(plan["fecha"], format="mixed")

    ped = pd.read_sql(
        "SELECT material AS sku, fecha_entrega, cantidad_tm FROM pedidos_pendientes_pt "
        "WHERE fecha_snapshot = (SELECT MAX(fecha_snapshot) FROM pedidos_pendientes_pt)",
        conn,
    )
    ped["sku"] = ped["sku"].astype(str).str.strip()
    ped["fecha_entrega"] = pd.to_datetime(ped["fecha_entrega"], errors="coerce")
    ped = ped.dropna(subset=["fecha_entrega"])

    return plan, ped


def marcar_urgencias(plan: pd.DataFrame, ped: pd.DataFrame) -> pd.DataFrame:
    if plan.empty or ped.empty:
        plan["dias_atraso_pedido"] = 0
        plan["urgente_producir_hoy"] = False
        plan["adelantado_correctamente"] = None
        return plan

    ped = ped.copy()
    ped["dias_vencido"] = (HOY - ped["fecha_entrega"]).dt.days
    vencidos = ped[ped["dias_vencido"] > 0]
    peor_atraso_por_sku = vencidos.groupby("sku")["dias_vencido"].max()

    plan["dias_atraso_pedido"] = plan["sku"].map(peor_atraso_por_sku).fillna(0).astype(int)
    plan["urgente_producir_hoy"] = (
        (plan["motivo"] == "PEDIDO") & (plan["dias_atraso_pedido"] > 0)
    )

    if plan.empty:
        return plan

    primer_dia = plan["fecha"].min()
    plan["adelantado_correctamente"] = np.where(
        plan["urgente_producir_hoy"],
        plan["fecha"] == primer_dia,
        None,
    )

    return plan


def main():
    conn = sqlite3.connect(DB_PATH)
    plan, ped = cargar_plan_y_pedidos(conn)

    if plan.empty:
        print("[AVISO] No hay 'plan_produccion' -- correr optimizar_produccion.py antes.")
        conn.close()
        return

    plan = marcar_urgencias(plan, ped)
    plan.to_sql("plan_produccion", conn, if_exists="replace", index=False)
    conn.commit()

    n_urgentes = int(plan.loc[plan["urgente_producir_hoy"], "sku"].nunique())
    if n_urgentes == 0:
        print("[OK] Ningún SKU tiene pedido vencido en el plan actual.")
        conn.close()
        return

    print(f"⚠️  {n_urgentes} SKU con pedido YA VENCIDO en el plan:\n")

    no_adelantados = plan[
        (plan["urgente_producir_hoy"]) & (plan["adelantado_correctamente"] == False)
    ]
    if not no_adelantados.empty:
        print(f"  {no_adelantados['sku'].nunique()} SKU NO se lograron adelantar al "
              f"primer día -- la capacidad de línea no alcanzó, revisar manualmente "
              f"(otra línea, turno extra, o aceptar el atraso):\n")
        print(no_adelantados[["sku", "fecha", "tm", "linea", "dias_atraso_pedido"]]
              .sort_values("dias_atraso_pedido", ascending=False)
              .to_string(index=False))
    else:
        print("  Todos los pedidos vencidos sí quedaron programados en el primer día.")

    conn.close()
    print("\n[OK] 'plan_produccion' actualizado con columnas de urgencia.")


if __name__ == "__main__":
    main()