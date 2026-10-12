import sqlite3, pandas as pd, numpy as np
from config import DB_PATH

def demanda_diaria_sku(conn, sku):
    df = pd.read_sql("SELECT fecha, ventas_tm FROM ventas_demanda WHERE sku = ?",
                      conn, params=[sku], parse_dates=["fecha"])
    diaria = df.groupby("fecha")["ventas_tm"].sum()
    rango = pd.date_range(diaria.index.min(), diaria.index.max(), freq="D")
    return diaria.reindex(rango, fill_value=0.0)

conn = sqlite3.connect(DB_PATH)
no_estacionarios = ["107708591","107708576","107710387","107708566","107708575",
    "107679409","107713803","107708567","107708572","107680697","107708585",
    "107715263","107708607","107710493","107708574","107708845","107715265","107708582"]

for sku in no_estacionarios:
    serie = demanda_diaria_sku(conn, sku)
    n = len(serie)
    dias_ventana_reciente = n - (n // 2)
    print(f"{sku}: usar ultimos {dias_ventana_reciente} dias "
          f"({dias_ventana_reciente/30:.1f} meses aprox) para el bootstrap")
conn.close()
