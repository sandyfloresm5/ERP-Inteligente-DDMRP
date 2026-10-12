"""
simulador_escenarios.py
Cargill Ecuador - Aquanutrition
Módulo 4 (complemento) - Simulador de decisiones sobre el plan de producción
"""


import sys
import copy
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH
import sqlite3
from optimizar_produccion import cargar_datos, optimizar, HORAS_DIA_LINEA as HORAS_DIA_LINEA_DEFAULT


def _aplicar_overrides(d: dict, overrides: dict) -> dict:
    """Copia d y le aplica los cambios del escenario -- nunca modifica
    el diccionario base, así cada escenario parte limpio del mismo
    punto de partida real. Devuelve también horas_override por separado
    porque eso se pasa a optimizar(), no vive dentro de d."""
    d2 = copy.deepcopy(d)
    horas_override = {}

    pct_demanda = overrides.get("shock_demanda_pct")
    if pct_demanda and not d2["pedidos"].empty:
        d2["pedidos"]["cantidad_tm"] = d2["pedidos"]["cantidad_tm"] * (1 + pct_demanda / 100.0)

    excluir = overrides.get("excluir_pedidos")
    if excluir and not d2["pedidos"].empty:
        for sku, fecha in excluir:
            f = pd.Timestamp(fecha)
            d2["pedidos"] = d2["pedidos"][
                ~((d2["pedidos"]["sku"] == sku) & (d2["pedidos"]["fecha_entrega"].dt.normalize() == f))
            ]

    cap_pct = overrides.get("capacidad_pct")
    if cap_pct and not d2["capacidad"].empty:
        for _, row in d2["capacidad"].iterrows():
            recurso = f"{str(row['planta']).strip().upper()}|{str(row['linea']).strip().upper()}"
            if recurso in cap_pct:
                d2["capacidad"].loc[row.name, "tm_dia_p95"] *= (1 + cap_pct[recurso] / 100.0)

    # El límite que de verdad aprieta casi siempre son las HORAS
    # disponibles (HORAS_DIA_LINEA=20 fijo), no el tope de TM -- una
    # línea al 98-100% de horas no gana nada con más tope de TM. Por
    # eso capacidad_pct también escala las horas de esa línea; el tope
    # de TM (arriba) queda para cuando la restricción real es otra
    # (ej. capacidad de secado/enfriado medida en TM, no en horas).
    if cap_pct:
        for recurso, pct in cap_pct.items():
            horas_override[recurso] = HORAS_DIA_LINEA_DEFAULT * (1 + pct / 100.0)

    horas_pct = overrides.get("horas_pct")  # override explícito, si no se quiere via capacidad_pct
    if horas_pct:
        for recurso, pct in horas_pct.items():
            horas_override[recurso] = HORAS_DIA_LINEA_DEFAULT * (1 + pct / 100.0)

    return d2, horas_override


def correr_escenario(conn, nombre: str, overrides: dict = None, d_base: dict = None) -> dict:
    overrides = overrides or {}
    d = d_base if d_base is not None else cargar_datos(conn)
    d_esc, horas_override = _aplicar_overrides(d, overrides)

    restriccion = overrides.get("restriccion")
    plan, riesgo, estado, lineas, z = optimizar(d_esc, restriccion=restriccion,
                                                 horas_override=horas_override or None)

    tm_producidas = float(plan["tm"].sum()) if not plan.empty else 0.0
    tm_riesgo = float(riesgo["tm_no_servida"].sum()) if not riesgo.empty else 0.0
    n_sku_riesgo = int(riesgo["sku"].nunique()) if not riesgo.empty else 0
    tm_buffer = float(plan.loc[plan.get("motivo") == "BUFFER", "tm"].sum()) if "motivo" in plan.columns else None
    tm_pedido = float(plan.loc[plan.get("motivo") == "PEDIDO", "tm"].sum()) if "motivo" in plan.columns else None

    return {
        "escenario": nombre, "estado": estado, "z_usd": z,
        "tm_producidas": tm_producidas, "tm_pedido": tm_pedido, "tm_buffer": tm_buffer,
        "tm_riesgo": tm_riesgo, "n_sku_riesgo": n_sku_riesgo,
        "_plan": plan, "_riesgo": riesgo,
    }


def comparar_escenarios(conn, escenarios: list) -> pd.DataFrame:
    """escenarios: lista de (nombre, overrides_dict). Siempre incluye
    'Base (sin cambios)' primero para que todo se compare contra él."""
    print("=" * 70)
    print("COMPARADOR DE ESCENARIOS -- S&OP")
    print("=" * 70)

    d_base = cargar_datos(conn)
    resultados = [correr_escenario(conn, "Base (plan actual)", {}, d_base=d_base)]

    for nombre, overrides in escenarios:
        print(f"\n  Resolviendo: {nombre}...")
        resultados.append(correr_escenario(conn, nombre, overrides, d_base=d_base))

    tabla = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in resultados])
    z_base = tabla.loc[tabla["escenario"] == "Base (plan actual)", "z_usd"].iloc[0]
    tabla["delta_z_vs_base"] = tabla["z_usd"] - z_base

    print("\n" + "-" * 70)
    print("RESUMEN COMPARATIVO")
    print("-" * 70)
    cols = ["escenario", "estado", "z_usd", "delta_z_vs_base", "tm_producidas",
            "tm_pedido", "tm_buffer", "tm_riesgo", "n_sku_riesgo"]
    print(tabla[cols].round(0).to_string(index=False))
    print("\n  z_usd = valor de la función objetivo (margen de producción MENOS "
          "penalizaciones por stock bajo TOR, demanda no servida y exceso sobre TOG -- "
          "no es el margen bruto de venta). delta_z_vs_base es cuánto mejor/peor es "
          "el escenario en $ frente al plan actual.")
    print("  tm_riesgo = demanda que ese escenario deja sin poder servir.")

    return tabla, resultados


def _preguntar(texto: str, tipo=float, permitir_vacio=True):
    val = input(texto).strip()
    if val == "" and permitir_vacio:
        return None
    try:
        return tipo(val)
    except ValueError:
        print("  Valor inválido, se ignora.")
        return None


def _armar_escenario_interactivo() -> tuple:
    print("\n--- Nuevo escenario (Enter para saltar un campo) ---")
    nombre = input("Nombre del escenario: ").strip() or "Escenario sin nombre"
    overrides = {}

    pct_dem = _preguntar("  Shock de demanda, % (ej. 15 o -10): ")
    if pct_dem is not None:
        overrides["shock_demanda_pct"] = pct_dem

    if input("  ¿Cancelar algún pedido? (s/n): ").strip().lower() == "s":
        excluir = []
        while True:
            sku = input("    SKU a cancelar (Enter para terminar): ").strip()
            if not sku:
                break
            fecha = input("    Fecha de entrega (YYYY-MM-DD): ").strip()
            excluir.append((sku, fecha))
        if excluir:
            overrides["excluir_pedidos"] = excluir

    if input("  ¿Cambiar capacidad de alguna línea? (s/n): ").strip().lower() == "s":
        cap_pct = {}
        while True:
            recurso = input("    Recurso (ej. GUAYAQUIL|L1, Enter para terminar): ").strip().upper()
            if not recurso:
                break
            pct = _preguntar(f"    % de cambio en horas/capacidad para {recurso} (ej. 20 o -15): ")
            if pct is not None:
                cap_pct[recurso] = pct
        if cap_pct:
            overrides["capacidad_pct"] = cap_pct

    return nombre, overrides


def modo_interactivo(conn):
    print("=" * 70)
    print("SIMULADOR DE ESCENARIOS -- modo interactivo")
    print("Cada escenario que ingreses corre el MILP completo (toma un rato).")
    print("=" * 70)

    d_base = cargar_datos(conn)
    resultados = [correr_escenario(conn, "Base (plan actual)", {}, d_base=d_base)]
    print("\n[Base calculada]")

    while True:
        nombre, overrides = _armar_escenario_interactivo()
        print(f"\n  Resolviendo '{nombre}'...")
        r = correr_escenario(conn, nombre, overrides, d_base=d_base)
        resultados.append(r)
        print(f"  Listo -- Z: {r['z_usd']:,.0f} · TM producidas: {r['tm_producidas']:,.0f} "
              f"· TM en riesgo: {r['tm_riesgo']:,.0f}")

        if input("\n¿Agregar otro escenario? (s/n): ").strip().lower() != "s":
            break

    tabla = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in resultados])
    z_base = tabla.loc[tabla["escenario"] == "Base (plan actual)", "z_usd"].iloc[0]
    tabla["delta_z_vs_base"] = tabla["z_usd"] - z_base

    print("\n" + "-" * 70)
    print("RESUMEN COMPARATIVO")
    print("-" * 70)
    cols = ["escenario", "estado", "z_usd", "delta_z_vs_base", "tm_producidas",
            "tm_pedido", "tm_buffer", "tm_riesgo", "n_sku_riesgo"]
    print(tabla[cols].round(0).to_string(index=False))
    return tabla, resultados


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    modo_interactivo(conn)
    conn.close()