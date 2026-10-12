import sqlite3
conn = sqlite3.connect(r"C:\Users\s545890\OneDrive - Cargill Inc\Simulador Inteligente\db\simulador.db")

# Borrar las filas de respaldo que vinieron de "gemelos" -- van a
# recalcularse correctamente con el fix aplicado
conn.execute("DELETE FROM forecast_sku_calibrado WHERE sku IN (SELECT sku FROM forecast_respaldo_sku_nuevo WHERE metodo_ganador LIKE '%Gemelos%')")
n = conn.execute("SELECT COUNT(*) FROM forecast_respaldo_sku_nuevo WHERE metodo_ganador LIKE '%Gemelos%'").fetchone()[0]
print(f"{n} SKU tenían ADU prestado de gemelos -- purgados de forecast_sku_calibrado")
conn.execute("DROP TABLE IF EXISTS forecast_respaldo_sku_nuevo")
conn.commit()
conn.close()
