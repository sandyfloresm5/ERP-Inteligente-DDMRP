import sqlite3, pandas as pd
conn = sqlite3.connect(r"C:\Users\s545890\OneDrive - Cargill Inc\Simulador Inteligente\db\simulador.db")

print("=== Todo lo que el plan puso en DURAN L1 y GUAYAQUIL L4, los 14 días ===")
df = pd.read_sql("""
    SELECT fecha, planta, linea, sku, tm, horas FROM plan_produccion
    WHERE linea IN ('L1','L4') AND planta IN ('DURAN','GUAYAQUIL')
    ORDER BY fecha, planta, linea
""", conn)
print(df.to_string(index=False))

print()
resumen = df.groupby(['planta','linea'])['horas'].sum()
print("Horas totales usadas en el horizonte (de 22*14=308 en DURAN L1, 15*14=210 en GUAYAQUIL L4):")
print(resumen)

conn.close()
