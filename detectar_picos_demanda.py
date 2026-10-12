"""
detectar_picos_demanda.py
Cargill Ecuador - Aquanutrition
Alerta: un cliente pide, en un solo día, más de lo que hay disponible.
"""
import sqlite3, pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

ped = pd.read_sql("""
    SELECT material AS sku, fecha_entrega, cliente_nombre, SUM(cantidad_tm) as tm_dia
    FROM pedidos_pendientes_pt
    WHERE fecha_snapshot = (SELECT MAX(fecha_snapshot) FROM pedidos_pendientes_pt)
    GROUP BY material, fecha_entrega, cliente_nombre
""", conn)

nfp = pd.read_sql("""
    SELECT sku, disponible_tm, transito_tm FROM ddmrp_flujo_neto
    WHERE fecha_calculo = (SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto) AND nodo='PT'
""", conn)
nfp["stock_fisico"] = nfp["disponible_tm"] + nfp["transito_tm"]

m = ped.merge(nfp[["sku", "stock_fisico"]], on="sku", how="left")
m["stock_fisico"] = m["stock_fisico"].fillna(0.0)
picos = m[m["tm_dia"] > m["stock_fisico"]].sort_values("tm_dia", ascending=False)

print(f"{len(picos)} caso(s): un cliente pide en un solo día más de lo que hay en stock")
print(picos[["sku", "fecha_entrega", "cliente_nombre", "tm_dia", "stock_fisico"]].to_string(index=False))

conn.close()