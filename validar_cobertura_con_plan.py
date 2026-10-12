"""
validar_cobertura_con_plan.py
Cargill Ecuador - Aquanutrition

Verifica, pedido por pedido, hasta dónde alcanza el PLAN DE PRODUCCIÓN
real (no solo el stock de hoy) -- qué pedidos llegan a tiempo, cuáles
llegan tarde (y en qué fecha), y cuáles no llegan a cubrirse en
absoluto dentro del horizonte planificado.
"""
import sys
import sqlite3
import pandas as pd
from config import DB_PATH

LEAD_TIME_DIAS = 3


def evaluar_cobertura_con_plan(conn, sku):
    nfp = pd.read_sql("""
        SELECT disponible_tm, transito_tm FROM ddmrp_flujo_neto
        WHERE sku=? AND fecha_calculo=(SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto)
    """, conn, params=(sku,))
    if nfp.empty:
        return None
    disponible_inicial = float(nfp.iloc[0]["disponible_tm"]) + float(nfp.iloc[0]["transito_tm"])

    pedidos = pd.read_sql("""
        SELECT fecha_entrega, cantidad_tm, cliente_nombre FROM pedidos_pendientes_pt
        WHERE material=? AND fecha_snapshot=(SELECT MAX(fecha_snapshot) FROM pedidos_pendientes_pt)
        AND cantidad_tm > 0 ORDER BY fecha_entrega
    """, conn, params=(sku,))
    if pedidos.empty:
        return pd.DataFrame()
    pedidos["fecha_entrega"] = pd.to_datetime(pedidos["fecha_entrega"])

    plan = pd.read_sql("SELECT fecha, tm FROM plan_produccion WHERE sku=?", conn, params=(sku,))
    plan["fecha"] = pd.to_datetime(plan["fecha"])
    plan["fecha_llegada"] = plan["fecha"] + pd.Timedelta(days=LEAD_TIME_DIAS)
    llegadas = plan.groupby("fecha_llegada")["tm"].sum().sort_index()
    llegadas_acumuladas = llegadas.cumsum()

    def produccion_llegada_hasta(fecha):
        disponibles = llegadas_acumuladas[llegadas_acumuladas.index <= fecha]
        return float(disponibles.iloc[-1]) if len(disponibles) else 0.0

    acumulado_pedidos = 0.0
    filas = []
    for _, p in pedidos.iterrows():
        saldo_a_esta_fecha = disponible_inicial + produccion_llegada_hasta(p["fecha_entrega"])
        cubierto = max(0.0, min(p["cantidad_tm"], saldo_a_esta_fecha - acumulado_pedidos))
        faltante = p["cantidad_tm"] - cubierto
        acumulado_pedidos += p["cantidad_tm"]

        fecha_real_cobertura = "NO CUBIERTO EN EL HORIZONTE"
        if faltante > 0.5:
            necesario_total = acumulado_pedidos
            for fecha_l, acumulado_prod in llegadas_acumuladas.items():
                if disponible_inicial + acumulado_prod >= necesario_total:
                    fecha_real_cobertura = fecha_l.strftime("%Y-%m-%d")
                    break

        filas.append({
            "fecha_entrega": p["fecha_entrega"].strftime("%Y-%m-%d"),
            "cliente": p["cliente_nombre"],
            "tm_pedido": round(p["cantidad_tm"], 1),
            "cubierto_con_plan": round(cubierto, 1),
            "faltante_con_plan": round(faltante, 1),
            "llega_a_tiempo": faltante <= 0.5,
            "fecha_real_cobertura": fecha_real_cobertura if faltante > 0.5 else "-",
        })

    return pd.DataFrame(filas)


if __name__ == "__main__":
    sku = sys.argv[1] if len(sys.argv) > 1 else "107710489"
    conn = sqlite3.connect(DB_PATH)
    r = evaluar_cobertura_con_plan(conn, sku)
    pd.set_option("display.width", 200)
    print(f"=== Cobertura CON PLAN para {sku} ===")
    if r is None:
        print("SKU sin fila en ddmrp_flujo_neto")
    elif r.empty:
        print("Sin pedidos pendientes para este SKU")
    else:
        print(r.to_string(index=False))
        print(f"\nTotal pedido: {r['tm_pedido'].sum():.1f} TM")
        print(f"Cubierto con plan: {r['cubierto_con_plan'].sum():.1f} TM")
        print(f"Faltante con plan: {r['faltante_con_plan'].sum():.1f} TM")
        print(f"Pedidos que llegan a tiempo: {int(r['llega_a_tiempo'].sum())} de {len(r)}")
    conn.close()