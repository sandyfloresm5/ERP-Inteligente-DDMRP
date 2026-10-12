"""
pedidos_manuales.py
Cargill Ecuador - Aquanutrition

Pedidos que el planificador ingresa DIRECTO en la app (Configuración ->
Pedidos manuales) -- para un pedido nuevo que ya se conoce pero que SAP /
el sistema de origen todavía no ha registrado. Se SUMAN a los pedidos de
pedidos_pendientes_pt en todo cálculo de demanda (NFP, plan de producción,
MTS/MTO, pedidos por cliente) -- nunca los reemplazan.
"""

import pandas as pd


def _existe(conn, t) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def cargar_pedidos_manuales(conn) -> pd.DataFrame:
    """Mismo esquema de columnas que pedidos_pendientes_pt (material,
    fecha_entrega, cantidad_tm, cliente, cliente_nombre, documento) para
    poder concatenar directo con pd.concat en cualquier script."""
    cols = ["material", "fecha_entrega", "cantidad_tm", "cliente", "cliente_nombre", "documento"]
    if not _existe(conn, "pedidos_manuales_pt"):
        return pd.DataFrame(columns=cols)

    df = pd.read_sql(
        "SELECT sku, fecha_entrega, cantidad_tm, cliente_nombre "
        "FROM pedidos_manuales_pt WHERE activo = 1", conn)
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["material"] = df["sku"].astype(str).str.strip().str.replace(".0", "", regex=False)
    df["cliente"] = df["cliente_nombre"]
    df["documento"] = "MANUAL"
    return df[cols]