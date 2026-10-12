"""
API - Sistema Predictivo-Prescriptivo de Abastecimiento
Cargill Ecuador - Aquanutrition

Sirve forecast_consolidado + sku_clasificacion desde SQLite, en JSON.

Correr con:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Desde la raíz del proyecto, NO desde dentro de api/.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

import sqlite3
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import DB_PATH, API_HOST, API_PORT

app = FastAPI(title="Cargill Aquanutrition - Sistema Predictivo-Prescriptivo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mapeo de tipo_demanda técnico -> etiqueta operativa (sin jerga estadística)
DIFICULTAD_MAP = {
    "smooth": "facil",
    "erratic": "variable",
    "intermittent": "variable",
    "lumpy": "dificil",
    "insuficiente_data": "dificil",
}
DIFICULTAD_LABEL = {
    "facil": "Fácil de predecir",
    "variable": "Variable",
    "dificil": "Difícil de predecir",
}


def get_connection():
    if not Path(DB_PATH).exists():
        raise HTTPException(status_code=500, detail=f"Base de datos no encontrada en {DB_PATH}")
    return sqlite3.connect(DB_PATH)


def _clasificacion_con_dificultad(conn) -> pd.DataFrame:
    """Carga sku_clasificacion y agrega columna 'dificultad' (fácil/variable/difícil),
    ocultando el detalle técnico de qué motor de forecast se usó."""
    clasif = pd.read_sql("SELECT * FROM sku_clasificacion", conn)
    clasif["dificultad"] = clasif["tipo_demanda"].map(DIFICULTAD_MAP).fillna("dificil")
    return clasif


def _precio_costo_por_serie(conn) -> pd.DataFrame:
    """
    Precio ponderado en cascada: SKU+Cliente primero (más preciso, refleja
    condiciones comerciales de ese cliente específico); si no existe (poca
    historia esa combinación), cae de vuelta a precio por SKU solo (más
    estable, calculado con más datos).

    Costo: promedio de costos_tm por serie desde ventas_demanda (columna
    real confirmada -- NO es 'costo_tm', es 'costos_tm', con 's').

    Devuelve DataFrame con columnas: serie_id, precio_tm, costo_tm,
    precio_fuente ('sku_cliente' o 'sku' -- para transparencia/debug).
    """
    precio_sc = pd.read_sql(
        "SELECT sku, codigo_cliente, precio_ponderado_tm FROM precio_ponderado_sku_cliente",
        conn,
    )
    precio_sc["serie_id"] = precio_sc["sku"] + "__" + precio_sc["codigo_cliente"]
    precio_sc = precio_sc[["serie_id", "precio_ponderado_tm"]].rename(
        columns={"precio_ponderado_tm": "precio_tm_sc"}
    )

    precio_sku = pd.read_sql(
        "SELECT sku, precio_ponderado_tm FROM precio_ponderado_sku",
        conn,
    ).rename(columns={"precio_ponderado_tm": "precio_tm_sku"})

    # serie_id -> sku, para poder cruzar con el respaldo por SKU solo
    series_sku = pd.read_sql("SELECT DISTINCT serie_id, sku FROM ventas_demanda", conn)

    precios = series_sku.merge(precio_sc, on="serie_id", how="left")
    precios = precios.merge(precio_sku, on="sku", how="left")

    precios["precio_tm"] = precios["precio_tm_sc"].fillna(precios["precio_tm_sku"])
    precios["precio_fuente"] = precios["precio_tm_sc"].notna().map(
        {True: "sku_cliente", False: "sku"}
    )

    costo = pd.read_sql(
        "SELECT serie_id, AVG(costos_tm) as costo_tm FROM ventas_demanda GROUP BY serie_id",
        conn,
    )

    resultado = precios[["serie_id", "precio_tm", "precio_fuente"]].merge(
        costo, on="serie_id", how="left"
    )
    return resultado


@app.get("/api/health")
def health():
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
        return {"status": "ok", "db_path": str(DB_PATH)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/forecast")
def get_forecast():
    """
    Devuelve forecast_consolidado + clasificación, una fila por (serie, mes_offset).
    NOTA: 'metodo' se conserva en el JSON por si se necesita para debug, pero
    el frontend no debe mostrarlo al usuario final -- usa 'dificultad' en su lugar.
    """
    conn = get_connection()
    try:
        query = """
            SELECT
                f.serie_id, f.sku, f.codigo_cliente, f.metodo, f.mes_offset,
                f.fecha_proyectada, f.p10, f.p50, f.p90, f.mae_validacion,
                f.bias_validacion,
                c.tipo_demanda, c.clase_abc, c.rentabilidad_total
            FROM forecast_consolidado f
            LEFT JOIN sku_clasificacion c ON f.serie_id = c.serie_id
        """
        df = pd.read_sql(query, conn)

        # Descripción de SKU y Holding/Cliente -- vienen de ventas_demanda,
        # no del serie_id (que se mantiene simple: sku + "__" + codigo_cliente
        # para no romper el cruce entre tablas). Se toma un valor por serie
        # (first) ya que la descripción/holding no debería variar mes a mes
        # para la misma combinación sku+cliente.
        descripciones = pd.read_sql(
            """
            SELECT serie_id,
                   sku_descripcion,
                   holding,
                   cliente
            FROM ventas_demanda
            GROUP BY serie_id
            """,
            conn,
        )

        # Demanda histórica promedio por serie -- denominador del WMAPE.
        # NOTA: esto es una APROXIMACIÓN de WMAPE. El cálculo "de libro" pondera
        # cada mes de validación individualmente (suma errores / suma demanda
        # real mes a mes); aquí solo tenemos el promedio de error (mae_validacion)
        # y el promedio de demanda histórica, así que se aproxima como su cociente.
        # Es razonable para priorizar/comparar series, pero no es exactamente
        # el mismo número que un WMAPE calculado mes a mes.
        demanda_hist = pd.read_sql(
            "SELECT serie_id, AVG(ventas_tm) as demanda_promedio FROM ventas_demanda GROUP BY serie_id",
            conn,
        )

        precios = _precio_costo_por_serie(conn)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error consultando forecast: {e}")
    finally:
        conn.close()

    df["dificultad"] = df["tipo_demanda"].map(DIFICULTAD_MAP).fillna("dificil")
    df = df.merge(descripciones, on="serie_id", how="left")
    df = df.merge(demanda_hist, on="serie_id", how="left")
    df = df.merge(precios, on="serie_id", how="left")

    # Confianza (categoría alta/media/baja) y WMAPE/accuracy (número, para
    # la barra de color verde/amarillo/rojo que se pidió en el detalle).
    #
    # NOTA sobre división por cero: a diferencia de un MAPE calculado
    # mes a mes (que falla si algún mes tiene demanda real = 0, como
    # ocurrió en el notebook de referencia con np.abs((y - yhat) / y)),
    # aquí usamos demanda_promedio (promedio histórico) como denominador,
    # no la demanda de un mes individual. Solo se excluye la serie
    # completa si su promedio histórico es 0 (demanda nula real, sin
    # información útil para calcular error relativo).
    def calc_metricas(row):
        mae = row.get("mae_validacion")
        demanda_prom = row.get("demanda_promedio")
        if pd.isnull(mae) or pd.isnull(demanda_prom) or demanda_prom == 0:
            return pd.Series({"confianza": None, "wmape_pct": None, "accuracy_pct": None})

        wmape = abs(mae) / demanda_prom  # aproximación, ver nota arriba
        accuracy = max(0.0, 1 - wmape)

        if wmape <= 0.15:
            confianza = "alta"
        elif wmape <= 0.35:
            confianza = "media"
        else:
            confianza = "baja"

        return pd.Series({
            "confianza": confianza,
            "wmape_pct": round(wmape * 100, 1),
            "accuracy_pct": round(accuracy * 100, 1),
        })

    metricas = df.apply(calc_metricas, axis=1)
    df["confianza"] = metricas["confianza"]
    df["wmape_pct"] = metricas["wmape_pct"]
    df["accuracy_pct"] = metricas["accuracy_pct"]

    # Etiqueta legible de sesgo -- umbral relativo a la demanda promedio
    # de cada serie (no un número fijo en TM, ya que 5 TM es mucho en una
    # serie chica e insignificante en una grande).
    def calc_tendencia(row):
        bias = row.get("bias_validacion")
        demanda_prom = row.get("demanda_promedio")
        if pd.isnull(bias) or pd.isnull(demanda_prom) or demanda_prom == 0:
            return None
        bias_relativo = bias / demanda_prom
        if bias_relativo > 0.10:
            return "subestima"   # riesgo de quiebre
        elif bias_relativo < -0.10:
            return "sobreestima"  # riesgo de sobre-stock
        else:
            return "neutral"

    df["tendencia_sesgo"] = df.apply(calc_tendencia, axis=1)

    # IMPORTANTE: df.where(pd.notnull(df), None) no siempre convierte NaN -> None
    # en columnas numéricas (queda como NaN, y json.dumps no acepta NaN).
    # Forzamos a tipo 'object' primero para que None se guarde de verdad.
    df = df.astype(object).where(pd.notnull(df), None)
    return df.to_dict(orient="records")


@app.get("/api/forecast/{serie_id}")
def get_forecast_serie(serie_id: str):
    conn = get_connection()
    try:
        query = """
            SELECT
                f.serie_id, f.sku, f.codigo_cliente, f.metodo, f.mes_offset,
                f.fecha_proyectada, f.p10, f.p50, f.p90, f.mae_validacion,
                f.bias_validacion,
                c.tipo_demanda, c.clase_abc, c.rentabilidad_total
            FROM forecast_consolidado f
            LEFT JOIN sku_clasificacion c ON f.serie_id = c.serie_id
            WHERE f.serie_id = ?
            ORDER BY f.mes_offset
        """
        df = pd.read_sql(query, conn, params=(serie_id,))
    finally:
        conn.close()

    if df.empty:
        raise HTTPException(status_code=404, detail=f"Serie '{serie_id}' no encontrada")

    df = df.astype(object).where(pd.notnull(df), None)
    return df.to_dict(orient="records")


@app.get("/api/historia/{serie_id}")
def get_historia_serie(serie_id: str):
    conn = get_connection()
    try:
        query = """
            SELECT fecha, ventas_tm FROM ventas_demanda
            WHERE serie_id = ?
            ORDER BY fecha
        """
        df = pd.read_sql(query, conn, params=(serie_id,), parse_dates=["fecha"])
    finally:
        conn.close()

    if df.empty:
        return []

    df["periodo"] = df["fecha"].dt.to_period("M").astype(str)
    mensual = df.groupby("periodo", as_index=False)["ventas_tm"].sum()
    return mensual.to_dict(orient="records")


@app.get("/api/matriz")
def get_matriz():
    """
    Matriz estratégica: Clase ABC x Dificultad de predicción.
    Devuelve conteo de series por celda -- esto reemplaza la vista por
    método de forecast (Prophet/SBA/TSB), que es detalle interno, no
    información útil para decisiones de negocio.
    """
    conn = get_connection()
    try:
        clasif = _clasificacion_con_dificultad(conn)
    finally:
        conn.close()

    celdas = []
    for clase in ["A", "B", "C"]:
        for dif in ["facil", "variable", "dificil"]:
            subset = clasif[(clasif["clase_abc"] == clase) & (clasif["dificultad"] == dif)]
            celdas.append({
                "clase_abc": clase,
                "dificultad": dif,
                "dificultad_label": DIFICULTAD_LABEL[dif],
                "num_series": int(len(subset)),
                "rentabilidad_total": float(subset["rentabilidad_total"].fillna(0).sum()) if len(subset) else 0.0,
            })

    return {"celdas": celdas}


@app.get("/api/resumen_ejecutivo")
def get_resumen_ejecutivo(
    clase_abc: str | None = None,
    dificultad: str | None = None,
    metodo: str | None = None,
    mes: str | None = None,
):
    """
    KPIs para la Pestaña 1 (Prioridad de Atención / Triage Gerencial).
    Todos los filtros son opcionales; si se pasan, los KPIs se recalculan
    solo sobre el subconjunto filtrado (no son estáticos).

    - accuracy_global: WMAPE ponderado por rentabilidad.
    - riesgo_financiero_usd: volumen en riesgo (TM, en series de baja
      confianza) convertido a dólares, separado en:
        * venta_en_riesgo_usd: TM de series con sesgo "subestima"
          (riesgo de quiebre) x precio_tm -- venta que se podría perder.
        * capital_inmovilizado_usd: TM de series con sesgo "sobreestima"
          (riesgo de sobre-stock) x costo_tm -- capital atrapado en
          inventario de más.
      Series con sesgo "neutral" o sin bias_validacion calculable no
      se asignan a ninguno de los dos (se reportan aparte, no se ocultan
      ni se fuerzan a una categoría).
    - semaforo: agregación de accuracy_global en 3 bandas.
    """
    conn = get_connection()
    try:
        query = """
            SELECT
                f.serie_id, f.mes_offset, f.p10, f.p90, f.mae_validacion,
                f.bias_validacion, f.metodo, f.fecha_proyectada,
                c.clase_abc, c.rentabilidad_total, c.tipo_demanda
            FROM forecast_consolidado f
            LEFT JOIN sku_clasificacion c ON f.serie_id = c.serie_id
        """
        df = pd.read_sql(query, conn)
        demanda_hist = pd.read_sql(
            "SELECT serie_id, AVG(ventas_tm) as demanda_promedio FROM ventas_demanda GROUP BY serie_id",
            conn,
        )
        precios = _precio_costo_por_serie(conn)
    finally:
        conn.close()

    df["dificultad"] = df["tipo_demanda"].map(DIFICULTAD_MAP).fillna("dificil")
    df = df.merge(demanda_hist, on="serie_id", how="left")
    df = df.merge(precios, on="serie_id", how="left")
    df["rentabilidad_total"] = df["rentabilidad_total"].fillna(0)

    # --- Aplicar filtros (todos opcionales, se combinan con AND) ---
    if mes:
        df = df[df["fecha_proyectada"] == mes]
    if clase_abc:
        df = df[df["clase_abc"] == clase_abc]
    if dificultad:
        df = df[df["dificultad"] == dificultad]
    if metodo:
        df = df[df["metodo"] == metodo]

    # Se usa una fila por serie (mes_offset=1, o el primer mes disponible
    # tras el filtro de mes) para no contar el mismo mae_validacion varias
    # veces -- es el mismo valor en las filas de una misma serie.
    if mes:
        base = df.copy()  # ya está filtrado a un solo mes
    else:
        base = df[df["mes_offset"] == 1].copy()

    base_valida = base[base["mae_validacion"].notna() & (base["demanda_promedio"] > 0)]

    # --- Accuracy global ponderado por rentabilidad ---
    if len(base_valida) > 0 and base_valida["rentabilidad_total"].sum() > 0:
        base_valida = base_valida.copy()
        base_valida["wmape_serie"] = (base_valida["mae_validacion"].abs() / base_valida["demanda_promedio"]).clip(upper=1.0)
        peso = base_valida["rentabilidad_total"]
        wmape_global = (base_valida["wmape_serie"] * peso).sum() / peso.sum()
        accuracy_global_pct = round((1 - wmape_global) * 100, 1)
    else:
        accuracy_global_pct = None

    # --- Riesgo financiero monetizado ---
    riesgo = base.copy()
    riesgo["demanda_promedio_safe"] = riesgo["demanda_promedio"].fillna(0)
    riesgo["wmape_serie"] = riesgo["mae_validacion"].abs() / riesgo["demanda_promedio_safe"].replace(0, pd.NA)
    riesgo_bajo_confianza = riesgo[riesgo["wmape_serie"] > 0.35].copy()
    riesgo_bajo_confianza["ancho_banda_tm"] = (riesgo_bajo_confianza["p90"] - riesgo_bajo_confianza["p10"]).clip(lower=0)

    # Clasificar por tendencia de sesgo, umbral relativo (igual criterio que en /api/forecast)
    def tendencia(row):
        bias = row.get("bias_validacion")
        demanda_prom = row.get("demanda_promedio")
        if pd.isnull(bias) or pd.isnull(demanda_prom) or demanda_prom == 0:
            return "sin_dato"
        bias_rel = bias / demanda_prom
        if bias_rel > 0.10:
            return "subestima"
        elif bias_rel < -0.10:
            return "sobreestima"
        return "neutral"

    riesgo_bajo_confianza["tendencia"] = riesgo_bajo_confianza.apply(tendencia, axis=1)

    subestima = riesgo_bajo_confianza[riesgo_bajo_confianza["tendencia"] == "subestima"]
    sobreestima = riesgo_bajo_confianza[riesgo_bajo_confianza["tendencia"] == "sobreestima"]

    venta_en_riesgo_usd = float((subestima["ancho_banda_tm"] * subestima["precio_tm"].fillna(0)).sum())
    capital_inmovilizado_usd = float((sobreestima["ancho_banda_tm"] * sobreestima["costo_tm"].fillna(0)).sum())
    riesgo_financiero_usd = venta_en_riesgo_usd + capital_inmovilizado_usd

    volumen_en_riesgo_tm = float(riesgo_bajo_confianza["ancho_banda_tm"].sum())
    volumen_sin_precio_asignable_tm = float(
        riesgo_bajo_confianza[riesgo_bajo_confianza["tendencia"].isin(["neutral", "sin_dato"])]["ancho_banda_tm"].sum()
    )

    # --- Semáforo ---
    if accuracy_global_pct is None:
        semaforo = "gris"
    elif accuracy_global_pct >= 80:
        semaforo = "verde"
    elif accuracy_global_pct >= 60:
        semaforo = "amarillo"
    else:
        semaforo = "rojo"

    return {
        "accuracy_global_pct": accuracy_global_pct,
        "accuracy_global_es_proxy": True,
        "volumen_en_riesgo_tm": round(volumen_en_riesgo_tm, 1),
        "volumen_sin_precio_asignable_tm": round(volumen_sin_precio_asignable_tm, 1),
        "riesgo_financiero_usd": round(riesgo_financiero_usd, 0),
        "venta_en_riesgo_usd": round(venta_en_riesgo_usd, 0),
        "capital_inmovilizado_usd": round(capital_inmovilizado_usd, 0),
        "semaforo": semaforo,
        "series_evaluadas": int(len(base)),
    }


@app.get("/api/focos_rojos")
def get_focos_rojos(limite: int = 5, clases: str = "A", metodo: str | None = None, dificultad: str | None = None):
    """
    Top N combinaciones Cliente/SKU que requieren revisión manual inmediata,
    ordenadas por IMPACTO EN DÓLARES (mae_validacion x precio_tm), no solo
    por WMAPE -- un error del 60% en una serie de 2 TM/mes importa menos
    que un error del 10% en una de 500 TM/mes.

    clases: lista separada por comas, ej. "A" o "A,B" (default solo "A",
    donde el impacto económico de un error suele ser mayor).
    """
    conn = get_connection()
    try:
        clases_lista = [c.strip() for c in clases.split(",") if c.strip()]
        placeholders = ",".join("?" for _ in clases_lista)
        query = f"""
            SELECT
                f.serie_id, f.sku, f.codigo_cliente, f.metodo, f.mae_validacion, f.p50,
                c.clase_abc, c.rentabilidad_total, c.tipo_demanda
            FROM forecast_consolidado f
            LEFT JOIN sku_clasificacion c ON f.serie_id = c.serie_id
            WHERE f.mes_offset = 1 AND c.clase_abc IN ({placeholders})
        """
        df = pd.read_sql(query, conn, params=clases_lista)
        demanda_hist = pd.read_sql(
            "SELECT serie_id, AVG(ventas_tm) as demanda_promedio FROM ventas_demanda GROUP BY serie_id",
            conn,
        )
        descripciones = pd.read_sql(
            "SELECT serie_id, sku_descripcion, holding FROM ventas_demanda GROUP BY serie_id",
            conn,
        )
        precios = _precio_costo_por_serie(conn)
    finally:
        conn.close()

    if df.empty:
        return {"focos_rojos": []}

    df["dificultad"] = df["tipo_demanda"].map(DIFICULTAD_MAP).fillna("dificil")
    if metodo:
        df = df[df["metodo"] == metodo]
    if dificultad:
        df = df[df["dificultad"] == dificultad]

    df = df.merge(demanda_hist, on="serie_id", how="left")
    df = df.merge(descripciones, on="serie_id", how="left")
    df = df.merge(precios, on="serie_id", how="left")
    df = df[df["mae_validacion"].notna() & (df["demanda_promedio"] > 0)]

    if df.empty:
        return {"focos_rojos": []}

    df["wmape_pct"] = (df["mae_validacion"].abs() / df["demanda_promedio"] * 100).round(1)
    # Impacto en USD: error absoluto en TM x precio_tm. Si no hay precio
    # disponible ni por SKU+Cliente ni por SKU (caso raro), queda en 0,
    # NO se excluye la fila (mejor mostrarla con impacto 0 y visible que
    # ocultarla silenciosamente).
    df["impacto_usd"] = (df["mae_validacion"].abs() * df["precio_tm"].fillna(0)).round(0)
    df = df.sort_values("impacto_usd", ascending=False).head(limite)

    df = df.astype(object).where(pd.notnull(df), None)
    return {"focos_rojos": df.to_dict(orient="records")}



def get_kpis(mes: str | None = None):
    """
    KPIs agregados. 'rentabilidad_mensual_estimada' es un PROMEDIO histórico
    (rentabilidad_total / meses de historia por serie), no un valor exacto
    de un mes calendario -- no existe una tabla de margen mensual todavía.
    Si se agrega esa tabla, este cálculo debe reemplazarse por el real.
    """
    conn = get_connection()
    try:
        clasif = _clasificacion_con_dificultad(conn)
        ventas = pd.read_sql("SELECT serie_id, fecha FROM ventas_demanda", conn, parse_dates=["fecha"])
    finally:
        conn.close()

    total_series = clasif["serie_id"].nunique()
    clase_a = (clasif["clase_abc"] == "A").sum()
    en_riesgo = (clasif["dificultad"] == "dificil").sum()

    # meses de historia por serie, para promediar la rentabilidad acumulada
    if not ventas.empty:
        meses_hist = ventas.groupby("serie_id")["fecha"].agg(lambda x: x.dt.to_period("M").nunique())
        clasif = clasif.merge(meses_hist.rename("meses_historia"), on="serie_id", how="left")
        clasif["meses_historia"] = clasif["meses_historia"].fillna(1).clip(lower=1)
        # rentabilidad_total puede tener NaN (series sin margen calculado) -- se
        # tratan como 0 para que no invaliden la suma total.
        clasif["rentabilidad_total"] = clasif["rentabilidad_total"].fillna(0)
        clasif["rentabilidad_mensual"] = clasif["rentabilidad_total"] / clasif["meses_historia"]
        rentabilidad_mensual_estimada = clasif["rentabilidad_mensual"].sum()
        if pd.isna(rentabilidad_mensual_estimada):
            rentabilidad_mensual_estimada = 0.0
    else:
        rentabilidad_mensual_estimada = None

    return {
        "mes_referencia": mes,
        "total_series": int(total_series),
        "rentabilidad_mensual_estimada": float(rentabilidad_mensual_estimada) if rentabilidad_mensual_estimada is not None else None,
        "rentabilidad_es_estimado": True,
        "series_clase_a": int(clase_a),
        "series_dificiles": int(en_riesgo),
    }


frontend_dir = ROOT_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=True)
