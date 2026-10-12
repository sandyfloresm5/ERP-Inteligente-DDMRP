"""
ver_distribucion_lead_time_mp.py
Diagnóstico rápido: distribución real de lead_time_total_dias en la
tabla lead_time_mp -- para definir cortes de LT_Factor por terciles
(sin benchmark externo, ver conversación Módulo 3).
"""

import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.append(str(ROOT_DIR))

import pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
lt = pd.read_sql("SELECT sku_mp, mp_descripcion, lead_time_total_dias FROM lead_time_mp", conn)
conn.close()

print(f"Total MP en lead_time_mp: {len(lt)}")
print(f"Con lead_time_total_dias no nulo: {lt['lead_time_total_dias'].notna().sum()}")
print()
print(lt["lead_time_total_dias"].describe())
print()

q1, q2 = lt["lead_time_total_dias"].quantile([1/3, 2/3])
print(f"Tercil 1 (corto <= x): {q1:.1f} días")
print(f"Tercil 2 (medio <= x): {q2:.1f} días")
print(f"(largo > {q2:.1f} días)")
print()

print("Distribución de valores únicos (para ver si hay pocos valores repetidos "
      "o es realmente continuo):")
print(lt["lead_time_total_dias"].value_counts().sort_index())