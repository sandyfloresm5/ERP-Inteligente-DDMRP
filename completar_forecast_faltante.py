"""
completar_forecast_faltante.py
Cargill Ecuador - Aquanutrition
Módulo 2 - Respaldo para SKU sin forecast de Comercial (nuevos, poca historia)
"""

import sys
from pathlib import Path
from datetime import date

import pandas as pd
import numpy as np
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH

MIN_DIAS_HISTORIA_PROPIA = 14
HOY = pd.Timestamp(date.today())
ANIO_ACTUAL = HOY.year


def cargar_ventas_skus_nuevos(conn) -> pd.DataFrame:
    """SKU con ventas de ESTE AÑO que no están en forecast_sku_calibrado."""
    ventas = pd.read_sql(
        "SELECT sku, sku_descripcion, forma, calibre, fecha, ventas_tm "
        "FROM ventas_raw WHERE ventas_tm > 0 AND anio = ?",
        conn, params=(ANIO_ACTUAL,))
    ventas["sku"] = ventas["sku"].astype(str).str.strip()

    if ventas.empty:
        return pd.DataFrame()

    forecast = pd.read_sql("SELECT DISTINCT sku FROM forecast_sku_calibrado", conn)
    forecast_set = set(forecast["sku"].astype(str).str.strip())

    faltantes = ventas[~ventas["sku"].isin(forecast_set)]
    return faltantes


def calcular_adu_nivel_a(faltantes: pd.DataFrame) -> pd.DataFrame:
    """Nivel A: CUALQUIER SKU con al menos una venta real usa su propio
    ADU -- sin umbral mínimo de días. Un dato real, aunque sea escaso,
    es siempre mejor que un dato prestado de otro producto. El umbral
    anterior de 14 días descartaba historia real a favor de inventar
    demanda con SKU "gemelos", lo cual podía inflar el ADU de un
    producto de bajo consumo real hasta niveles completamente ajenos
    a su comportamiento -- un error grave, no un matiz."""
    resumen = faltantes.groupby("sku", as_index=False).agg(
        sku_descripcion=("sku_descripcion", "first"),
        forma=("forma", "first"),
        calibre=("calibre", "first"),
        n_dias_con_venta=("fecha", "nunique"),
        tm_total=("ventas_tm", "sum"),
        primera_venta=("fecha", "min"),
    )
    resumen["dias_desde_primera_venta"] = (HOY - pd.to_datetime(resumen["primera_venta"])).dt.days.clip(lower=1)

    # Nivel A aplica a TODO SKU con al menos 1 día de venta real -- no
    # hay umbral de 14 días. Marca aparte los de muy poca historia
    # (<14 días) para que quien revise sepa que el dato es más volátil,
    # pero sigue siendo un dato REAL, no inventado.
    nivel_a = resumen[resumen["n_dias_con_venta"] >= 1].copy()
    nivel_a["adu"] = nivel_a["tm_total"] / nivel_a["dias_desde_primera_venta"]
    nivel_a["metodo_ganador"] = np.where(
        nivel_a["n_dias_con_venta"] >= 14,
        "Respaldo_NivelA_HistoriaPropia",
        "Respaldo_NivelA_HistoriaPropia_Escasa")
    nivel_a["nivel_evidencia"] = "A"

    return nivel_a[["sku", "sku_descripcion", "forma", "calibre", "adu",
                    "metodo_ganador", "nivel_evidencia", "n_dias_con_venta"]], resumen



def calcular_adu_nivel_b(resumen: pd.DataFrame, nivel_a: pd.DataFrame, conn) -> pd.DataFrame:
    """Nivel B: sin historia suficiente propia, pero hay gemelos
    (misma forma+calibre) ya calibrados -- se usa la mediana de su ADU."""
    pendientes = resumen[
        (resumen["n_dias_con_venta"] < MIN_DIAS_HISTORIA_PROPIA)
        & (~resumen["sku"].isin(nivel_a["sku"]))
    ].copy()

    if pendientes.empty:
        return pd.DataFrame()

    gemelos = pd.read_sql("""
        SELECT b.sku, b.adu, v.forma, v.calibre
        FROM ddmrp_buffer_pt b
        JOIN (SELECT DISTINCT sku, forma, calibre FROM ventas_raw) v
          ON v.sku = b.sku
        WHERE b.mes_offset = 0 AND b.estado = 'ok'
    """, conn)
    gemelos["forma"] = gemelos["forma"].astype(str).str.strip().str.upper()
    gemelos["calibre"] = pd.to_numeric(
        gemelos["calibre"].astype(str).str.replace(",", ".", regex=False), errors="coerce")

    pendientes["forma"] = pendientes["forma"].astype(str).str.strip().str.upper()
    pendientes["calibre"] = pd.to_numeric(
        pendientes["calibre"].astype(str).str.replace(",", ".", regex=False), errors="coerce")

    filas = []
    for _, r in pendientes.iterrows():
        match = gemelos[
            (gemelos["forma"] == r["forma"])
            & ((gemelos["calibre"] - r["calibre"]).abs() <= 0.05)
        ]
        if match.empty:
            continue
        filas.append({
            "sku": r["sku"], "sku_descripcion": r["sku_descripcion"],
            "forma": r["forma"], "calibre": r["calibre"],
            "adu": float(match["adu"].median()),
            "metodo_ganador": f"Respaldo_NivelB_Gemelos(n={len(match)})",
            "nivel_evidencia": "B",
            "n_dias_con_venta": int(r["n_dias_con_venta"]),
        })

    return pd.DataFrame(filas)


def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"=== Forecast de respaldo para SKU nuevos ({ANIO_ACTUAL}) ===")

    faltantes = cargar_ventas_skus_nuevos(conn)
    if faltantes.empty:
        print("  Ningún SKU nuevo detectado -- todos los SKU con ventas de este "
              "año ya tienen forecast de Comercial.")
        conn.close()
        return

    nivel_a, resumen = calcular_adu_nivel_a(faltantes)
    nivel_b = calcular_adu_nivel_b(resumen, nivel_a, conn)

    resultado = pd.concat([nivel_a, nivel_b], ignore_index=True) if not nivel_b.empty else nivel_a

    cubiertos = set(resultado["sku"]) if not resultado.empty else set()
    sin_evidencia = resumen[~resumen["sku"].isin(cubiertos)]

    print(f"\n {resumen['sku'].nunique()} SKU nuevos detectados (con ventas {ANIO_ACTUAL}, "
          f"sin forecast de Comercial):")
    n_escasa = int((nivel_a["metodo_ganador"] == "Respaldo_NivelA_HistoriaPropia_Escasa").sum())
    n_solida = int((nivel_a["metodo_ganador"] == "Respaldo_NivelA_HistoriaPropia").sum())
    print(f" Nivel A con historia sólida (≥14 días): {n_solida}")
    print(f" Nivel A con historia escasa (<14 días, pero REAL): {n_escasa}")
    print(f" Nivel B (gemelos, solo SKU sin NINGUNA venta): {len(nivel_b)}")
    print(f" Sin evidencia suficiente (NO se inventa ADU): {len(sin_evidencia)}")


    if len(sin_evidencia):
        print(f"\n  [AVISO] {len(sin_evidencia)} SKU quedan SIN buffer -- ni historia "
              f"propia ni gemelos comparables. Requieren forecast manual de Comercial "
              f"o revisión: {sin_evidencia['sku'].tolist()}")

    if resultado.empty:
        print("\n  Sin ningún SKU con evidencia suficiente para generar ADU de respaldo.")
        conn.close()
        return

    # Formato compatible con forecast_sku_calibrado -- mismo esquema que
    # usa calcular_buffer_ddmrp_dinamico.py, para que se integre sin
    # tocar ese script.
    salida = pd.DataFrame({
        "sku": resultado["sku"],
        "mes_offset": 0,
        "p10": np.nan,
        "p50": resultado["adu"] * 30.0,  # mismo supuesto de 30 días que usa el resto del pipeline
        "p90": np.nan,
        "metodo_ganador": resultado["metodo_ganador"],
        "rango_calibrado": np.nan,
        "wape_holdout": np.nan,
        "fva_holdout": np.nan,
        "semaforo": "SIN_VALIDAR",
    })

    conn.execute("DROP TABLE IF EXISTS forecast_respaldo_sku_nuevo")
    salida.to_sql("forecast_respaldo_sku_nuevo", conn, if_exists="replace", index=False)

    # Se agrega (append) a forecast_sku_calibrado -- no se sobreescribe:
    # Comercial sigue siendo la fuente principal, esto solo llena el hueco.
    salida.to_sql("forecast_sku_calibrado", conn, if_exists="append", index=False)
    conn.commit()

    print(f"\n[OK] {len(salida)} SKU agregados a 'forecast_sku_calibrado' con ADU de "
          f"respaldo. Tabla de detalle: 'forecast_respaldo_sku_nuevo'.")
    conn.close()


if __name__ == "__main__":
    main()