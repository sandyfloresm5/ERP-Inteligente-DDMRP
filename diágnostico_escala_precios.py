"""
diagnostico_escala_precios.py
Cargill Ecuador - Aquanutrition

Verifica si precio_ponderado_tm = $13,311.47/TM (encontrado en la serie
107708597__1800083314) es un caso aislado (dato mal cargado para ese
SKU específico) o un problema sistémico de escala/unidad que afecta a
todo precio_ponderado_sku_cliente -- y por tanto a rentabilidad_total,
clase_abc y margen_unitario_tm en TODO el portafolio, no solo a los
nuevos KPIs financieros.

No corrige nada -- solo diagnostica, para decidir el alcance del fix.
"""

import sqlite3
import os
import sys

import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config import DB_PATH


def main():
    conn = sqlite3.connect(DB_PATH)

    precios = pd.read_sql("SELECT * FROM precio_ponderado_sku_cliente", conn)

    print("=== Distribución de precio_ponderado_tm en todo el portafolio ===")
    print(precios["precio_ponderado_tm"].describe())

    print("\n=== Percentiles clave ===")
    for pct in [1, 5, 25, 50, 75, 95, 99]:
        val = np.percentile(precios["precio_ponderado_tm"].dropna(), pct)
        print(f"  P{pct}: ${val:,.2f}/TM")

    print("\n=== Top 15 precios más altos (para ver si el caso encontrado es outlier o típico) ===")
    print(precios.sort_values("precio_ponderado_tm", ascending=False).head(15)[
        ["sku", "codigo_cliente", "ventas_tm_sum", "precio_ponderado_tm"]
    ].to_string(index=False))

    print("\n=== Cuántas filas superan distintos umbrales de precio (referencia: alimento balanceado camarón) ===")
    for umbral in [1000, 2000, 5000, 10000]:
        n = (precios["precio_ponderado_tm"] > umbral).sum()
        pct = n / len(precios) * 100
        print(f"  > ${umbral:,}/TM: {n} de {len(precios)} filas ({pct:.1f}%)")

    # --- Cruce con rentabilidad_total para ver el impacto en ABC ---
    clasif = pd.read_sql(
        "SELECT sku, codigo_cliente, rentabilidad_total, clase_abc FROM sku_clasificacion", conn
    )
    cruce = clasif.merge(precios[["sku", "codigo_cliente", "precio_ponderado_tm"]],
                          on=["sku", "codigo_cliente"], how="left")

    print("\n=== Top 10 series por rentabilidad_total -- ¿coinciden con precios sospechosos? ===")
    print(cruce.sort_values("rentabilidad_total", ascending=False).head(10)[
        ["sku", "codigo_cliente", "clase_abc", "precio_ponderado_tm", "rentabilidad_total"]
    ].to_string(index=False))

    conn.close()


if __name__ == "__main__":
    main()