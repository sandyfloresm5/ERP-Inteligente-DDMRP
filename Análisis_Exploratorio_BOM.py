"""
Validacion_BOM_completa.py
Análisis exploratorio de la tabla 'bom' antes de limpiar duplicados.
"""
import sqlite3
import pandas as pd
from config import DB_PATH

pd.set_option("display.max_rows", 50)
pd.set_option("display.width", 150)

conn = sqlite3.connect(DB_PATH)
df = pd.read_sql("SELECT * FROM bom", conn)

print("=" * 70)
print(f"TOTAL FILAS: {len(df)}")
print(f"sku_pt únicos: {df['sku_pt'].nunique()}")
print(f"sku_mp únicos: {df['sku_mp'].nunique()}")

print("\n" + "=" * 70)
print("1) CENTRO: ¿un sku_pt vive en uno o varios centros?")
por_sku = df[["sku_pt", "centro"]].drop_duplicates()
n_centros_por_sku = por_sku.groupby("sku_pt")["centro"].nunique()
print(f"sku_pt en 1 solo centro: {(n_centros_por_sku == 1).sum()}")
print(f"sku_pt en 2+ centros: {(n_centros_por_sku > 1).sum()}")
if (n_centros_por_sku > 1).sum() > 0:
    print("\nEjemplos de sku_pt en varios centros:")
    print(por_sku[por_sku["sku_pt"].isin(n_centros_por_sku[n_centros_por_sku > 1].index)]
          .sort_values("sku_pt").head(20))

print("\n" + "=" * 70)
print("2) FECHA_PLANEADA: rango y cuántas fechas distintas por sku_pt+sku_mp")
df["fecha_planeada"] = pd.to_datetime(df["fecha_planeada"], errors="coerce")
print(f"Rango de fechas: {df['fecha_planeada'].min()} a {df['fecha_planeada'].max()}")
print(f"Valores nulos en fecha_planeada: {df['fecha_planeada'].isna().sum()}")

n_fechas = df.groupby(["sku_pt", "sku_mp"])["fecha_planeada"].nunique()
print(f"\nCombinaciones sku_pt+sku_mp con 1 sola fecha: {(n_fechas == 1).sum()}")
print(f"Combinaciones sku_pt+sku_mp con 2+ fechas distintas: {(n_fechas > 1).sum()}")

print("\n" + "=" * 70)
print("3) DUPLICADOS EXACTOS: filas 100% idénticas (todas las columnas)")
dup_exactos = df[df.duplicated(keep=False)]
print(f"{len(dup_exactos)} filas son duplicados EXACTOS (mismo centro, misma fecha, todo igual)")

print("\n" + "=" * 70)
print("4) DUPLICADOS solo por sku_pt+sku_mp (ignorando centro/fecha)")
dup_simple = df[df.duplicated(subset=["sku_pt", "sku_mp"], keep=False)]
print(f"{len(dup_simple)} filas duplicadas por sku_pt+sku_mp (de {len(df)} totales)")

# Cruce: de esas, ¿cuántas siguen siendo duplicado si agrego centro Y fecha?
dup_con_centro_fecha = df[df.duplicated(subset=["sku_pt", "sku_mp", "centro", "fecha_planeada"], keep=False)]
print(f"{len(dup_con_centro_fecha)} filas SIGUEN duplicadas aun agregando centro + fecha_planeada")

print("\n" + "=" * 70)
print("5) Ejemplo concreto: primer sku_pt+sku_mp con duplicados, todas sus columnas")
if len(dup_simple) > 0:
    ejemplo_key = dup_simple.iloc[0][["sku_pt", "sku_mp"]]
    ejemplo = df[(df["sku_pt"] == ejemplo_key["sku_pt"]) & (df["sku_mp"] == ejemplo_key["sku_mp"])]
    print(ejemplo.to_string())

conn.close()