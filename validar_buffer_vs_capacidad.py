"""
validar_buffer_vs_capacidad.py
Cargill Ecuador - Aquanutrition
"""

import sys
from pathlib import Path
from datetime import date

import pandas as pd
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH, LEAD_TIME_PRODUCCION_DIAS

HORAS_DIA_LINEA_REAL = {
    "DURAN|L1": 22.0, "DURAN|L2": 20.0, "DURAN|L3": 20.0,
    "GUAYAQUIL|L1": 20.0, "GUAYAQUIL|L3": 20.0, "GUAYAQUIL|L4": 15.0,
    "GUAYAQUIL|L5": 22.0, "GUAYAQUIL|L6": 22.0,
}
HORAS_DIA_LINEA_DEFECTO = 20.0

HOY = pd.Timestamp(date.today())


def _tabla_existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _columnas_tabla(conn, t) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
    except Exception:
        return set()


def cargar_capacidad_por_sku(conn) -> pd.DataFrame:
    """Capacidad máxima diaria por SKU, usando su mejor línea disponible.
    Ya no depende de ratio_planificacion (eliminada) -- calcula el calibre
    real del SKU desde ventas_raw y lo cruza contra config_lineas_produccion
    (llenada en la app), mismo criterio que optimizar_produccion.py."""
    TOLERANCIA_CALIBRE_MM = 0.05

    ventas = pd.read_sql(
        "SELECT DISTINCT sku, forma, calibre FROM ventas_raw "
        "WHERE calibre IS NOT NULL AND calibre != '' AND calibre != 'nan'", conn)
    if ventas.empty:
        return pd.DataFrame(columns=["sku", "mejor_recurso", "capacidad_max_tm_dia"])

    ventas["sku"] = ventas["sku"].astype(str).str.strip()
    ventas["forma"] = ventas["forma"].astype(str).str.strip().str.upper()
    ventas["calibre"] = pd.to_numeric(
        ventas["calibre"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    ventas = ventas.dropna(subset=["calibre"])

    # Calibre representativo (moda) por SKU -- mismo criterio que el MILP.
    moda = (ventas.groupby(["sku", "calibre"]).size().reset_index(name="n")
            .sort_values(["sku", "n"], ascending=[True, False])
            .drop_duplicates("sku", keep="first"))
    forma_sku = ventas.drop_duplicates("sku", keep="last")[["sku", "forma"]]
    sku_calibre = moda.merge(forma_sku, on="sku")[["sku", "forma", "calibre"]]

    if not _tabla_existe(conn, "config_lineas_produccion"):
        return pd.DataFrame(columns=["sku", "mejor_recurso", "capacidad_max_tm_dia"])

    cfg = pd.read_sql(
        "SELECT planta, linea, tipo_producto, tamano_mm, horas_dia, rate_tm_hora "
        "FROM config_lineas_produccion WHERE activo = 1", conn)
    if cfg.empty:
        return pd.DataFrame(columns=["sku", "mejor_recurso", "capacidad_max_tm_dia"])

    cfg["planta"] = cfg["planta"].astype(str).str.strip().str.upper().str.replace("Á", "A")
    cfg["linea"] = cfg["linea"].astype(str).str.strip().str.upper()
    cfg["recurso"] = cfg["planta"] + "|" + cfg["linea"].apply(lambda l: l if l.startswith("L") else "L" + l)
    cfg["tamano_mm"] = pd.to_numeric(cfg["tamano_mm"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    cfg["horas_dia"] = pd.to_numeric(cfg["horas_dia"], errors="coerce").fillna(HORAS_DIA_LINEA_DEFECTO)
    cfg["rate_tm_hora"] = pd.to_numeric(cfg["rate_tm_hora"], errors="coerce")
    cfg = cfg.dropna(subset=["tamano_mm", "rate_tm_hora"])
    cfg["cap_tm_dia"] = cfg["rate_tm_hora"] * cfg["horas_dia"]

    filas = []
    for _, r in sku_calibre.iterrows():
        match = cfg[(cfg["tamano_mm"] - r["calibre"]).abs() <= TOLERANCIA_CALIBRE_MM]
        if match.empty:
            continue
        mejor = match.sort_values("cap_tm_dia", ascending=False).iloc[0]
        filas.append({"sku": r["sku"], "mejor_recurso": mejor["recurso"],
                      "capacidad_max_tm_dia": mejor["cap_tm_dia"]})

    return pd.DataFrame(filas)

def cargar_buffer_pendiente(conn) -> pd.DataFrame:
    f = conn.execute("SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto").fetchone()[0]
    cols_disp = _columnas_tabla(conn, "ddmrp_flujo_neto")
    col_transito = "transito_tm" if "transito_tm" in cols_disp else None
    campos = ("sku, descripcion, adu, disponible_tm" + (", transito_tm" if col_transito else "")
              + ", tor, toy, tog, cantidad_a_ordenar_tm, requiere_orden, zona_nfp")
    df = pd.read_sql(
        f"SELECT {campos} FROM ddmrp_flujo_neto WHERE fecha_calculo = ? AND nodo = 'PT'",
        conn, params=(f,))
    df["sku"] = df["sku"].astype(str).str.strip()
    if col_transito:
        # PT: tránsito en SAP = producto ya en piso pendiente de
        # reclasificación -- es stock físico real, se suma como uno solo.
        df["disponible_tm"] = df["disponible_tm"].fillna(0.0) + df["transito_tm"].fillna(0.0)
    else:
        print("  [AVISO] 'ddmrp_flujo_neto' no tiene 'transito_tm' todavía -- "
              "correr calcular_nfp.py para regenerarla. Por ahora se usa solo "
              "disponible_tm sin sumar tránsito.")
        df["disponible_tm"] = df["disponible_tm"].fillna(0.0)
    return df


def cargar_plan_por_sku(conn) -> pd.DataFrame:
    if not _tabla_existe(conn, "plan_produccion"):
        return pd.DataFrame()
    df = pd.read_sql(
        "SELECT sku, fecha, tm, motivo FROM plan_produccion "
        "WHERE fecha_calculo = (SELECT MAX(fecha_calculo) FROM plan_produccion)", conn)
    df["sku"] = df["sku"].astype(str).str.strip()
    if "motivo" in df.columns:
        df = df[df["motivo"] != "YA PRODUCIDO"]
    df["fecha"] = pd.to_datetime(df["fecha"], format="mixed")
    return df


def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"Buffer vs. plan real — ¿arranca a tiempo? — {HOY.date()}")

    buffer = cargar_buffer_pendiente(conn)
    if buffer.empty:
        print("  Sin posición de flujo neto calculada.")
        conn.close()
        return

    cap = cargar_capacidad_por_sku(conn)
    plan = cargar_plan_por_sku(conn)

    m = buffer.merge(cap, on="sku", how="left")

    if not plan.empty:
        primer_dia = plan.groupby("sku")["fecha"].min().rename("primer_dia_plan")
        tm_semana = plan.groupby("sku")["tm"].sum().rename("tm_en_plan_semana")
        m = m.merge(primer_dia, on="sku", how="left").merge(tm_semana, on="sku", how="left")
    else:
        m["primer_dia_plan"] = pd.NaT
        m["tm_en_plan_semana"] = 0.0

    m["sin_capacidad_calculable"] = m["capacidad_max_tm_dia"].isna()
    validos = m[~m["sin_capacidad_calculable"] & (m["adu"] > 0)].copy()

    validos["dias_cobertura_fisica"] = validos["disponible_tm"] / validos["adu"]
    validos["dias_necesarios_producir"] = (
        validos["cantidad_a_ordenar_tm"] / validos["capacidad_max_tm_dia"])
    validos["fecha_limite_inicio"] = (
        HOY + pd.to_timedelta(validos["dias_cobertura_fisica"], unit="D")
        - pd.to_timedelta(validos["dias_necesarios_producir"], unit="D"))

    en_riesgo = validos[validos["requiere_orden"] == 1].copy()
    en_riesgo["arranca_tarde"] = (
        en_riesgo["primer_dia_plan"].isna()
        | (en_riesgo["primer_dia_plan"] > en_riesgo["fecha_limite_inicio"]))

    tarde = en_riesgo[en_riesgo["arranca_tarde"]].sort_values("fecha_limite_inicio")

    print("\n" + "=" * 72)
    print("RIESGO DE STOCKOUT — el plan NO arranca a tiempo según el buffer")
    print("=" * 72)
    if tarde.empty:
        print("  Ninguno. Todo SKU que el buffer pide reponer ya tiene producción "
              "programada antes de la fecha límite que exige su cobertura física.")
    else:
        print(f"  {len(tarde)} SKU donde el plan llega tarde o no está programado:\n")
        cols = ["sku", "descripcion", "disponible_tm", "adu", "dias_cobertura_fisica",
                "fecha_limite_inicio", "primer_dia_plan", "dias_necesarios_producir"]
        vista = tarde[cols].copy()
        vista["descripcion"] = vista["descripcion"].astype(str).str.slice(0, 26)
        vista["fecha_limite_inicio"] = vista["fecha_limite_inicio"].dt.strftime("%Y-%m-%d")
        vista["primer_dia_plan"] = vista["primer_dia_plan"].dt.strftime("%Y-%m-%d")
        for c in ["disponible_tm", "adu", "dias_cobertura_fisica", "dias_necesarios_producir"]:
            vista[c] = vista[c].round(1)
        print(vista.to_string(index=False))
        print(f"\n  'fecha_limite_inicio': el día más tardío en que la línea puede "
              f"empezar y aun así llegar antes de agotar el stock. 'primer_dia_plan': "
              f"cuándo el plan de esta semana de verdad lo tiene programado (vacío = "
              f"no está en el plan).")

    vista_guardar = tarde[cols].copy() if not tarde.empty else tarde[cols]
    vista_guardar["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    if not vista_guardar.empty:
        vista_guardar["fecha_limite_inicio"] = pd.to_datetime(
            vista_guardar["fecha_limite_inicio"]).dt.strftime("%Y-%m-%d")
        vista_guardar["primer_dia_plan"] = pd.to_datetime(
            vista_guardar["primer_dia_plan"]).dt.strftime("%Y-%m-%d")
    conn.execute("DROP TABLE IF EXISTS alerta_stockout_plan")
    vista_guardar.to_sql("alerta_stockout_plan", conn, if_exists="replace", index=False)

    print("\n" + "=" * 72)
    print("RIESGO DE SOBREPRODUCCIÓN — plan produce sin que el buffer lo pida")
    print("=" * 72)
    sin_necesidad = validos[(validos["requiere_orden"] == 0)
                            & (validos["tm_en_plan_semana"] > 0)
                            & (validos["zona_nfp"].isin(["VERDE", "SOBRE_TOG"]))]
    if sin_necesidad.empty:
        print("  Ninguno. Toda la producción del plan responde a algo que el "
              "buffer o un pedido justifican.")
    else:
        print(f"  {len(sin_necesidad)} SKU con producción programada esta semana "
              f"pero el buffer los tiene en zona verde/sobre TOG (sin urgencia):\n")
        print(sin_necesidad[["sku", "descripcion", "zona_nfp", "tog", "disponible_tm",
                             "tm_en_plan_semana"]].round(1).to_string(index=False))
        print(f"\n  Puede ser legítimo (reposición de buffer con capacidad sobrante, "
              f"que ya está permitido por diseño) -- revisar si desplaza a un SKU "
              f"que sí tenía urgencia esa misma línea/día.")

    cols_sobre = ["sku", "descripcion", "zona_nfp", "tog", "disponible_tm", "tm_en_plan_semana"]
    vista_sobre = sin_necesidad[cols_sobre].round(1).copy() if not sin_necesidad.empty else sin_necesidad[cols_sobre]
    vista_sobre["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    conn.execute("DROP TABLE IF EXISTS alerta_sobreproduccion_plan")
    vista_sobre.to_sql("alerta_sobreproduccion_plan", conn, if_exists="replace", index=False)

    conn.commit()
    print(f"\n[OK] Tablas: alerta_stockout_plan ({len(vista_guardar)} filas), "
          f"alerta_sobreproduccion_plan ({len(vista_sobre)} filas)")
    conn.close()


if __name__ == "__main__":
    main()