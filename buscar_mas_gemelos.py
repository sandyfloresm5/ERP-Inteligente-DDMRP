import sqlite3, pandas as pd
conn = sqlite3.connect(r"C:\Users\s545890\OneDrive - Cargill Inc\Simulador Inteligente\db\simulador.db")

fecha = conn.execute("SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto").fetchone()[0]
df = pd.read_sql("SELECT sku, adu, tog, cantidad_a_ordenar_tm FROM ddmrp_flujo_neto WHERE fecha_calculo = ? AND nodo='PT'", conn, params=(fecha,))

# Sospechosos: ADU alto pero cantidad_a_ordenar desproporcionada
# respecto a lo que tendría sentido para su venta real -- comparamos
# contra el ADU real calculado directo de ventas_raw.
ventas = pd.read_sql("SELECT sku, SUM(ventas_tm) as tm_2026 FROM ventas_raw WHERE anio=2026 AND ventas_tm>0 GROUP BY sku", conn)
ventas["adu_real_aprox"] = ventas["tm_2026"] / 246

m = df.merge(ventas, on="sku", how="left")
m["ratio"] = m["adu"] / m["adu_real_aprox"].replace(0, 0.001)
sospechosos = m[m["ratio"] > 10].sort_values("ratio", ascending=False)

print(f"SKU con ADU del sistema >10x el ADU real de ventas: {len(sospechosos)}")
print(sospechosos[["sku","adu","adu_real_aprox","tog","cantidad_a_ordenar_tm"]].to_string(index=False))
conn.close()
