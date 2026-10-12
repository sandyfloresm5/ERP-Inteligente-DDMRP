import sqlite3
import pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

q = """
SELECT
    serie_id,
    sku,
    codigo_cliente,
    strftime('%Y-%m', fecha) AS mes,
    COUNT(*) AS n_transacciones
FROM ventas_demanda
WHERE fecha >= date('now', '-4 months')
GROUP BY serie_id, sku, codigo_cliente, mes
"""

df = pd.read_sql(q, conn)

promedio_serie = df.groupby('serie_id')['n_transacciones'].transform('mean')

df['ratio_vs_promedio'] = df['n_transacciones'] / promedio_serie

sospechosas = df[
    (df['ratio_vs_promedio'] < 0.4)
    & (df['n_transacciones'] > 0)
]

print(
    sospechosas
    .sort_values('ratio_vs_promedio')
    .head(20)
    .to_string(index=False)
)

conn.close()
