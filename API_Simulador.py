"""
api_simulador.py
Cargill Ecuador - Aquanutrition
"""

import sqlite3
import sys
import json
import subprocess
import os
import tempfile
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, API_HOST, API_PORT
from etiquetas_negocio import (
    traducir_serie, mensaje_seguimiento_oni, mensaje_seguimiento_precio,
)

app = FastAPI(
    title="Simulador Inteligente de Abastecimiento - API",
    description="Cargill Ecuador - Aquanutrition. API de solo lectura sobre "
                "resultados ya calculados por el pipeline.",
    version="1.2.5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _conn():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _df_to_records(df: pd.DataFrame) -> list:
    """
    NaN -> None para que el JSON sea válido.
    Forzar dtype object antes del reemplazo para evitar errores Out of range float.
    """
    if df.empty:
        return []
    return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")


def _tabla_existe(conn, nombre: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {nombre} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _tabla_bandas(conn, base: str) -> tuple:
    calibrada = f"{base}_calibrado"
    if _tabla_existe(conn, calibrada):
        return calibrada, "calibrada"
    return base, "sin_calibrar"


# ------------------------------------------------------------------
# /api/v1/pipeline
# ------------------------------------------------------------------
@app.get("/api/v1/pipeline/estado")
def estado_pipeline():
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY fecha_fin DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"estado": "sin_corridas",
                    "mensaje": "El pipeline aún no se ha ejecutado."}

        resultado = dict(row)
        resultado["fecha_corte_datos"] = conn.execute(
            "SELECT MAX(fecha) FROM ventas_demanda"
        ).fetchone()[0]
        resultado["bandas_calibradas"] = _tabla_existe(conn, "forecast_sku_calibrado")
        return resultado
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/forecast -- SKU-cliente
# ------------------------------------------------------------------
@app.get("/api/v1/forecast/resumen")
def resumen_forecast():
    conn = _conn()
    try:
        total = pd.read_sql(
            "SELECT COUNT(DISTINCT serie_id) as n FROM forecast_final", conn
        ).iloc[0]["n"]
        cold_start = pd.read_sql(
            "SELECT COUNT(DISTINCT serie_id) as n FROM forecast_final "
            "WHERE baja_confianza = 1", conn
        ).iloc[0]["n"]

        wape_global, fuente_wape = None, "no_disponible"
        if _tabla_existe(conn, "diagnostico_final"):
            try:
                w = pd.read_sql(
                    "SELECT wape_holdout, p50_promedio FROM diagnostico_final "
                    "WHERE wape_holdout IS NOT NULL AND p50_promedio > 0", conn
                )
                if len(w) and w["p50_promedio"].sum() > 0:
                    wape_global = float((w["wape_holdout"] * w["p50_promedio"]).sum()
                                          / w["p50_promedio"].sum())
                    fuente_wape = "holdout"
            except Exception:
                pass

        toneladas = pd.read_sql(
            "SELECT SUM(p50) as t FROM forecast_final", conn
        ).iloc[0]["t"]

        return {
            "total_series": int(total),
            "series_cold_start": int(cold_start),
            "series_con_forecast_validado": int(total - cold_start),
            "wape_global_ponderado": round(wape_global, 4) if wape_global else None,
            "fuente_wape": fuente_wape,
            "toneladas_totales_6m": round(float(toneladas), 1) if toneladas else None,
            "advertencia": "El forecast SKU-cliente es para visibilidad comercial. "
                           "Para planificar producción usar /api/v1/forecast-sku.",
        }
    finally:
        conn.close()


@app.get("/api/v1/forecast/{serie_id}")
def forecast_serie(serie_id: str):
    conn = _conn()
    try:
        df = pd.read_sql(
            "SELECT * FROM forecast_final WHERE serie_id = ? ORDER BY mes_offset",
            conn, params=(serie_id,),
        )
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Serie '{serie_id}' no encontrada")
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/forecast/{serie_id}/historia")
def historia_serie(serie_id: str):
    conn = _conn()
    try:
        df = pd.read_sql(
            "SELECT fecha, ventas_tm FROM ventas_demanda WHERE serie_id = ? ORDER BY fecha",
            conn, params=(serie_id,),
        )
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/forecast/{serie_id}/reconciliado")
def forecast_reconciliado_serie(serie_id: str):
    conn = _conn()
    try:
        tabla, fuente = _tabla_bandas(conn, "forecast_reconciliado")
        if not _tabla_existe(conn, tabla):
            raise HTTPException(status_code=503,
                                  detail="Tabla forecast_reconciliado no disponible.")
        df = pd.read_sql(
            f"SELECT * FROM {tabla} WHERE serie_id = ? ORDER BY mes_offset",
            conn, params=(serie_id,),
        )
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Serie '{serie_id}' no encontrada")
        return {"fuente_banda": fuente, "datos": _df_to_records(df)}
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/forecast-sku -- SALIDA OPERATIVA
# ------------------------------------------------------------------
@app.get("/api/v1/forecast-sku/resumen")
def resumen_forecast_sku():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "forecast_sku_validacion"):
            raise HTTPException(status_code=503,
                                  detail="Tabla forecast_sku_validacion no disponible. "
                                         "Correr forecast_sku.py.")

        val = pd.read_sql("SELECT * FROM forecast_sku_validacion", conn)
        con_error = val.dropna(subset=["wape_holdout"])
        _, fuente = _tabla_bandas(conn, "forecast_sku")

        return {
            "total_sku": int(len(val)),
            "sku_con_error_validado": int(len(con_error)),
            "wape_holdout_mediano": (round(float(con_error["wape_holdout"].median()), 4)
                                     if len(con_error) else None),
            "fva_holdout_medio": (round(float(con_error["fva_holdout"].mean()), 4)
                                  if len(con_error) else None),
            "sku_peor_que_naive": (int((con_error["fva_holdout"] < 0).sum())
                                   if len(con_error) else 0),
            "semaforo": {k: int(v) for k, v in val["semaforo"].value_counts().items()},
            "fuente_banda": fuente,
        }
    finally:
        conn.close()


@app.get("/api/v1/forecast-sku")
def listar_forecast_sku(
    semaforo: str = Query(None, description="VERDE, AMARILLO, ROJO o SIN_VALIDAR"),
):
    conn = _conn()
    try:
        tabla, fuente = _tabla_bandas(conn, "forecast_sku")
        if not _tabla_existe(conn, tabla):
            raise HTTPException(status_code=503, detail="Tabla forecast_sku no disponible.")

        query = f"SELECT * FROM {tabla} WHERE 1=1"
        params = []
        if semaforo:
            query += " AND semaforo = ?"
            params.append(semaforo)
        query += " ORDER BY sku, mes_offset"

        return {"fuente_banda": fuente,
                "datos": _df_to_records(pd.read_sql(query, conn, params=params))}
    finally:
        conn.close()


@app.get("/api/v1/forecast-sku/validacion")
def validacion_forecast_sku():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "forecast_sku_validacion"):
            raise HTTPException(status_code=503,
                                  detail="Tabla forecast_sku_validacion no disponible.")
        return _df_to_records(pd.read_sql(
            "SELECT * FROM forecast_sku_validacion ORDER BY wape_holdout", conn))
    finally:
        conn.close()


@app.get("/api/v1/forecast-sku/{sku}")
def forecast_sku_individual(sku: str):
    conn = _conn()
    try:
        tabla, fuente = _tabla_bandas(conn, "forecast_sku")
        if not _tabla_existe(conn, tabla):
            raise HTTPException(status_code=503, detail="Tabla forecast_sku no disponible.")

        df = pd.read_sql(f"SELECT * FROM {tabla} WHERE sku = ? ORDER BY mes_offset",
                         conn, params=(sku,))
        if df.empty:
            raise HTTPException(status_code=404, detail=f"SKU '{sku}' no encontrado")

        validacion = None
        if _tabla_existe(conn, "forecast_sku_validacion"):
            v = pd.read_sql("SELECT * FROM forecast_sku_validacion WHERE sku = ?",
                            conn, params=(sku,))
            if len(v):
                validacion = _df_to_records(v)[0]

        clasificacion = None
        if _tabla_existe(conn, "clasificacion_nivel_sku"):
            c = pd.read_sql("SELECT * FROM clasificacion_nivel_sku WHERE sku = ?",
                            conn, params=(sku,))
            if len(c):
                clasificacion = _df_to_records(c)[0]

        return {"forecast": _df_to_records(df), "validacion": validacion,
                "clasificacion": clasificacion, "fuente_banda": fuente}
    finally:
        conn.close()


@app.get("/api/v1/forecast-sku/{sku}/historia")
def historia_sku(sku: str):
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT strftime('%Y-%m-01', fecha) AS fecha, SUM(ventas_tm) AS ventas_tm
            FROM ventas_demanda
            WHERE substr(serie_id, 1, instr(serie_id, '__') - 1) = ?
            GROUP BY 1 ORDER BY 1
        """, conn, params=(sku,))
        return _df_to_records(df)
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/ddmrp -- POSICIONAMIENTO MTS / MTO
# ------------------------------------------------------------------
@app.get("/api/v1/ddmrp/posicionamiento")
def posicionamiento(politica: str = Query(None, description="MTS o MTO")):
    """Clasificación MTS/MTO por SKU."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "posicionamiento_pt"):
            return []

        q = "SELECT * FROM posicionamiento_pt WHERE 1=1"
        p = []
        if politica:
            q += " AND UPPER(politica) = ?"
            p.append(politica.upper())
        q += " ORDER BY tog DESC, adu DESC"

        df = pd.read_sql(q, conn, params=p)
        return _df_to_records(df)
    finally:
        conn.close()


def _inventario_promedio_ddmrp(tor, toy, tog) -> float:
    """Inventario físico promedio esperado de buffers DDMRP: Σ(TOR + Verde/2),
    Verde = TOG - TOY (Ptak & Smith). Acepta escalares o Series."""
    tor = pd.to_numeric(pd.Series(tor), errors="coerce").fillna(0)
    toy = pd.to_numeric(pd.Series(toy), errors="coerce").fillna(0)
    tog = pd.to_numeric(pd.Series(tog), errors="coerce").fillna(0)
    return float((tor.values + np.clip(tog.values - toy.values, 0, None) / 2.0).sum())


@app.get("/api/v1/ddmrp/posicionamiento/resumen")
def resumen_posicionamiento():
    """Resumen del posicionamiento MTS vs MTO y capacidad de bodega."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "posicionamiento_pt"):
            return {
                "fecha_calculo": None,
                "mts": {"n_sku": 0, "adu_tm_dia": 0.0, "tog_tm": 0.0, "tor_tm": 0.0, "n_dimensionamiento_confiable": 0, "pct_tog_confiable": None},
                "mto": {"n_sku": 0, "adu_tm_dia": 0.0, "tog_evitado_tm": 0.0},
                "capacidad_bodega_tm": None,
                "cabe_en_bodega": None,
                "exceso_tm": 0,
            }

        df = pd.read_sql("SELECT * FROM posicionamiento_pt", conn)
        if df.empty:
            return {
                "fecha_calculo": None,
                "mts": {"n_sku": 0, "adu_tm_dia": 0.0, "tog_tm": 0.0, "tor_tm": 0.0, "n_dimensionamiento_confiable": 0, "pct_tog_confiable": None},
                "mto": {"n_sku": 0, "adu_tm_dia": 0.0, "tog_evitado_tm": 0.0},
                "capacidad_bodega_tm": None,
                "cabe_en_bodega": None,
                "exceso_tm": 0,
            }

        es_mts_mask = df["es_mts"].fillna(0).astype(int) == 1
        mts = df[es_mts_mask]
        mto = df[~es_mts_mask]

        tog_mts = float(mts["tog"].fillna(0).sum()) if not mts.empty else 0.0
        # Bodega física vs inventario PROMEDIO esperado (DDMRP: TOR + Verde/2).
        # La Zona Amarilla es producto en camino, no ocupa bodega.
        inv_prom_mts = (_inventario_promedio_ddmrp(mts["tor"], mts["toy"], mts["tog"])
                        if not mts.empty else 0.0)

        conf_mask = mts["dimensionamiento_confiable"].fillna(0).astype(int) == 1 if "dimensionamiento_confiable" in mts.columns else pd.Series(True, index=mts.index)
        conf = mts[conf_mask] if not mts.empty else mts

        cap = None
        if _tabla_existe(conn, "config_capacidad_bodega"):
            fila = conn.execute(
                "SELECT capacidad_pt_tm FROM config_capacidad_bodega WHERE id = 1"
            ).fetchone()
            if fila and fila["capacidad_pt_tm"] is not None:
                cap = fila["capacidad_pt_tm"]
        if cap is None:
            try:
                from config import CAPACIDAD_BODEGA_PT_TM as cap
            except ImportError:
                cap = None

        fecha_calc = df["fecha_calculo"].max() if "fecha_calculo" in df.columns and len(df) else None

        return {
            "fecha_calculo": str(fecha_calc) if fecha_calc else None,
            "mts": {
                "n_sku": int(len(mts)),
                "adu_tm_dia": round(float(mts["adu"].fillna(0).sum()), 1) if not mts.empty else 0.0,
                "tog_tm": round(tog_mts, 1),
                "tor_tm": round(float(mts["tor"].fillna(0).sum()), 1) if not mts.empty else 0.0,
                "n_dimensionamiento_confiable": int(len(conf)),
                "pct_tog_confiable": (round(float(conf["tog"].fillna(0).sum()) / tog_mts * 100, 1)
                                      if tog_mts > 0 else None),
            },
            "mto": {
                "n_sku": int(len(mto)),
                "adu_tm_dia": round(float(mto["adu"].fillna(0).sum()), 1) if not mto.empty else 0.0,
                "tog_evitado_tm": round(float(mto["tog_original"].fillna(0).sum()), 1) if ("tog_original" in mto.columns and not mto.empty) else 0.0,
            },
            "capacidad_bodega_tm": cap,
            "inventario_promedio_tm": round(inv_prom_mts, 1),
            "criterio_bodega": "inventario_promedio_tor_mas_verde_medio",
            "cabe_en_bodega": (bool(inv_prom_mts <= cap) if cap else None),
            "exceso_tm": (round(inv_prom_mts - cap, 1) if cap and inv_prom_mts > cap else 0),
        }
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/alertas-mto")
def alertas_mto():
    """Pedidos de SKU MTO que requieren producción."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "alertas_mto"):
            return []
        df = pd.read_sql("SELECT * FROM alertas_mto ORDER BY dias_para_lanzar", conn)
        return _df_to_records(df)
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/ddmrp -- BUFFERS & NFP
# ------------------------------------------------------------------
@app.get("/api/v1/ddmrp/resumen")
def resumen_ddmrp():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_buffer_pt"):
            raise HTTPException(status_code=503, detail="Tabla ddmrp_buffer_pt no disponible.")

        pt = pd.read_sql("SELECT * FROM ddmrp_buffer_pt WHERE estado = 'ok' AND mes_offset = 0", conn)
        tog_total = float(pt["tog"].sum()) if len(pt) else 0.0

        respaldado = 0.0
        if "buffer_respaldado" in pt.columns and len(pt):
            respaldado = float(pt.loc[pt["buffer_respaldado"].astype(bool), "tog"].sum())

        por_semaforo = []
        if "semaforo" in pt.columns and len(pt):
            g = pt.groupby("semaforo").agg(n_sku=("sku", "count"), tog=("tog", "sum"))
            por_semaforo = [{"semaforo": k, "n_sku": int(v["n_sku"]),
                            "tog_tm": round(float(v["tog"]), 1)}
                           for k, v in g.iterrows()]

        total_pt = pd.read_sql(
            "SELECT COUNT(*) n, SUM(estado='datos_insuficientes') sin_datos "
            "FROM ddmrp_buffer_pt", conn).iloc[0]

        mp_info = None
        if _tabla_existe(conn, "ddmrp_buffer_mp"):
            mp = pd.read_sql("SELECT * FROM ddmrp_buffer_mp", conn)
            ok_mp = mp[mp["estado"] == "ok"] if "estado" in mp.columns else mp
            mp_info = {
                "total_mp": int(len(mp)),
                "mp_calculables": int(len(ok_mp)),
                "tog_total_tm": round(float(ok_mp["tog"].sum()), 1) if len(ok_mp) else 0.0,
                "tor_total_tm": round(float(ok_mp["tor"].sum()), 1) if len(ok_mp) else 0.0,
            }

        return {
            "nodo_pt": {
                "n_sku_con_buffer": int(pt["sku"].nunique()) if len(pt) else 0,
                "filas_totales": int(total_pt["n"]),
                "filas_sin_datos": int(total_pt["sin_datos"] or 0),
                "tog_total_tm": round(tog_total, 1),
                "tor_total_tm": round(float(pt["tor"].sum()), 1) if len(pt) else 0.0,
                "toy_total_tm": round(float(pt["toy"].sum()), 1) if len(pt) else 0.0,
                "pct_tog_respaldado": (round(respaldado / tog_total * 100, 1)
                                       if tog_total > 0 else None),
                "por_semaforo": por_semaforo,
            },
            "nodo_mp": mp_info,
            "bandas_calibradas": _tabla_existe(conn, "forecast_sku_calibrado"),
            "nota": "El TOG sin respaldo proviene de SKU cuyo forecast no tiene error validado.",
        }
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/buffer-pt")
def buffer_pt(
    mes_offset: int = Query(None, ge=0),
    sku: str = Query(None),
    solo_calculables: bool = Query(False),
):
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_buffer_pt"):
            raise HTTPException(status_code=503, detail="Tabla ddmrp_buffer_pt no disponible.")

        query = "SELECT * FROM ddmrp_buffer_pt WHERE 1=1"
        params = []
        if mes_offset is not None:
            query += " AND mes_offset = ?"
            params.append(mes_offset)
        if sku:
            query += " AND sku = ?"
            params.append(sku)
        if solo_calculables:
            query += " AND estado = 'ok'"
        query += " ORDER BY tog DESC, sku, mes_offset"

        return _df_to_records(pd.read_sql(query, conn, params=params))
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/buffer-mp")
def buffer_mp(solo_calculables: bool = Query(False)):
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_buffer_mp"):
            raise HTTPException(status_code=503, detail="Tabla ddmrp_buffer_mp no disponible.")
        query = "SELECT * FROM ddmrp_buffer_mp"
        if solo_calculables:
            query += " WHERE estado = 'ok'"
        query += " ORDER BY tog DESC"
        return _df_to_records(pd.read_sql(query, conn))
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/flujo-neto")
def flujo_neto(
    nodo: str = Query(None, description="PT o MP"),
    solo_accionables: bool = Query(True),
):
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_flujo_neto"):
            raise HTTPException(status_code=503, detail="Tabla ddmrp_flujo_neto no disponible.")

        fecha = conn.execute("SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto").fetchone()[0]

        query = "SELECT * FROM ddmrp_flujo_neto WHERE fecha_calculo = ?"
        params = [fecha]
        if nodo:
            query += " AND nodo = ?"
            params.append(nodo)
        if solo_accionables:
            query += " AND tiene_posicion = 1"
        query += " ORDER BY prioridad_pct ASC"

        df = pd.read_sql(query, conn, params=params)
        return {"fecha_calculo": fecha, "datos": _df_to_records(df)}
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/flujo-neto/resumen")
def resumen_flujo_neto():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_flujo_neto"):
            raise HTTPException(status_code=503, detail="Tabla ddmrp_flujo_neto no disponible.")

        fecha = conn.execute("SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto").fetchone()[0]
        df = pd.read_sql("SELECT * FROM ddmrp_flujo_neto WHERE fecha_calculo = ?", conn, params=(fecha,))

        out = {"fecha_calculo": fecha, "nodos": {}}

        for nodo in df["nodo"].unique():
            sub = df[df["nodo"] == nodo]
            g = sub[sub["tiene_posicion"].astype(bool)]
            ordenar = g[g["requiere_orden"].astype(bool)]
            enganio = g[g["zona_nfp"].isin(["VERDE", "SOBRE_TOG"]) & (g["alerta_onhand"] == "CRITICO")]

            out["nodos"][nodo] = {
                "items_total": int(len(sub)),
                "items_gestionables": int(len(g)),
                "items_sin_posicion": int(len(sub) - len(g)),
                "zonas": {k: int(v) for k, v in g["zona_nfp"].value_counts().items()},
                "requieren_orden": int(len(ordenar)),
                "tm_a_ordenar": round(float(ordenar["cantidad_a_ordenar_tm"].sum()), 1),
                "onhand_critico": int((g["alerta_onhand"] == "CRITICO").sum()),
                "nfp_verde_fisico_critico": int(len(enganio)),
                "demanda_calificada_completa": bool(g["demanda_calificada_completa"].all()) if len(g) else None,
                "tog_total_tm": round(float(g["tog"].sum()), 1),
                "disponible_total_tm": round(float(g["disponible_tm"].sum()), 1),
            }

        return out
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/trayectoria/{sku}")
def trayectoria_flujo_neto(sku: str):
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_flujo_neto"):
            raise HTTPException(status_code=503, detail="Tabla ddmrp_flujo_neto no disponible.")
        df = pd.read_sql(
            "SELECT fecha_calculo, nfp_tm, disponible_tm, transito_tm, "
            "demanda_calificada_tm, tor, toy, tog "
            "FROM ddmrp_flujo_neto WHERE sku = ? ORDER BY fecha_calculo",
            conn, params=(sku,))
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/pedidos/{sku}")
def pedidos_del_sku(sku: str):
    conn = _conn()
    try:
        clave = str(sku).replace(".0", "").strip()
        partes = []

        if _tabla_existe(conn, "pedidos_pendientes_pt"):
            fecha = conn.execute("SELECT MAX(fecha_snapshot) FROM pedidos_pendientes_pt").fetchone()[0]
            df = pd.read_sql(
                "SELECT material, fecha_entrega, cantidad_tm, cliente, cliente_nombre, documento "
                "FROM pedidos_pendientes_pt WHERE fecha_snapshot = ?",
                conn, params=(fecha,))
            if not df.empty:
                df = df[df["material"].astype(str).str.replace(".0", "", regex=False).str.strip() == clave]
                if not df.empty:
                    partes.append(df)

        if _tabla_existe(conn, "pedidos_manuales_pt"):
            dm = pd.read_sql(
                "SELECT sku AS material, fecha_entrega, cantidad_tm, cliente_nombre "
                "FROM pedidos_manuales_pt WHERE activo = 1", conn)
            if not dm.empty:
                dm["material"] = dm["material"].astype(str).str.replace(".0", "", regex=False).str.strip()
                dm = dm[dm["material"] == clave]
                if not dm.empty:
                    dm["cliente"] = dm["cliente_nombre"]
                    dm["documento"] = "MANUAL"
                    partes.append(dm)

        if not partes:
            return []
        todo = pd.concat(partes, ignore_index=True).sort_values("fecha_entrega")
        return _df_to_records(todo.drop(columns=["material"]))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/margen
# ------------------------------------------------------------------
@app.get("/api/v1/margen/sku-cliente")
def margen_sku_cliente():
    """
    Margen unitario por SKU y cliente, ponderado por volumen.
    """
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT sku,
                   codigo_cliente,
                   SUM(ventas_tm * (precio_tm - costo_tm)) AS margen_total,
                   SUM(ventas_tm) AS tm
            FROM ventas_raw
            WHERE ventas_tm > 0
              AND precio_tm IS NOT NULL
              AND costo_tm IS NOT NULL
              AND fecha >= date('now', '-12 months')
            GROUP BY sku, codigo_cliente
            HAVING SUM(ventas_tm) > 0
        """, conn)

        if df.empty:
            return {"nivel": "sku_cliente", "datos": [], "n": 0}

        df["margen_usd_tm"] = df["margen_total"] / df["tm"]
        df["sku"] = df["sku"].astype(str).str.strip()
        df["codigo_cliente"] = df["codigo_cliente"].astype(str).str.strip()

        return {
            "nivel": "sku_cliente",
            "n": int(len(df)),
            "datos": _df_to_records(
                df[["sku", "codigo_cliente", "margen_usd_tm", "tm"]].round(2)),
        }
    finally:
        conn.close()


@app.get("/api/v1/margen/sku")
def margen_sku():
    """
    Margen unitario por SKU, para cuando no hay dato del cliente puntual.
    """
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT sku,
                   SUM(ventas_tm * (precio_tm - costo_tm)) AS margen_total,
                   SUM(ventas_tm) AS tm
            FROM ventas_raw
            WHERE ventas_tm > 0 AND precio_tm IS NOT NULL AND costo_tm IS NOT NULL
              AND fecha >= date('now', '-12 months')
            GROUP BY sku HAVING SUM(ventas_tm) > 0
        """, conn)
        if df.empty:
            return []
        df["margen_usd_tm"] = df["margen_total"] / df["tm"]
        df["sku"] = df["sku"].astype(str).str.strip()
        return _df_to_records(df[["sku", "margen_usd_tm", "tm"]].round(2))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/produccion
# ------------------------------------------------------------------
@app.get("/api/v1/produccion/plan")
def plan_produccion(fecha: str = Query(None, description="Filtrar por día")):
    """
    Plan de producción del MILP: qué producir, qué día y en qué línea.
    """
    conn = _conn()
    try:
        if not _tabla_existe(conn, "plan_produccion"):
            raise HTTPException(status_code=503,
                                  detail="Tabla plan_produccion no disponible. "
                                         "Correr optimizar_produccion.py.")
        q = "SELECT * FROM plan_produccion WHERE 1=1"
        p = []
        if fecha:
            q += " AND fecha = ?"
            p.append(fecha)
        q += " ORDER BY fecha, linea, tm DESC"
        return _df_to_records(pd.read_sql(q, conn, params=p))
    finally:
        conn.close()


@app.get("/api/v1/produccion/cambios")
def produccion_cambios():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM plan_cambios", conn)
        return _df_to_records(df)
    except Exception:
        return []
    finally:
        conn.close()


@app.get("/api/v1/produccion/alertas-stockout")
def alertas_stockout_plan():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM alerta_stockout_plan", conn)
        return _df_to_records(df)
    except Exception:
        return []
    finally:
        conn.close()


@app.get("/api/v1/produccion/alertas-sobreproduccion")
def alertas_sobreproduccion_plan():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM alerta_sobreproduccion_plan", conn)
        return _df_to_records(df)
    except Exception:
        return []
    finally:
        conn.close()


@app.get("/api/v1/produccion/riesgo-detalle")
def riesgo_detalle():
    """Detalle de plan_riesgo con su causa (CAPACIDAD_O_LEAD_TIME,
    SIN_LINEA_CONFIGURADA, SIN_POSICIONAMIENTO, FUERA_DEL_MODELO) --
    usado por el Simulador y el aviso de Plan de Producción para
    distinguir SKU que el modelo ni siquiera puede planificar."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "plan_riesgo"):
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(plan_riesgo)").fetchall()}
        col_causa = "causa" if "causa" in cols else "'CAPACIDAD_O_LEAD_TIME' AS causa"
        df = pd.read_sql(
            f"SELECT sku, fecha, tm_no_servida, politica, {col_causa} FROM plan_riesgo", conn)
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/mp/proyeccion-vs-plan")
def mp_proyeccion_vs_plan():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM mp_proyeccion_vs_plan", conn)
        return _df_to_records(df)
    except Exception:
        return []
    finally:
        conn.close()


@app.get("/api/v1/mp/por-pt/{sku_pt}")
def mp_por_pt(sku_pt: str):
    """Para un PT: su fórmula (BOM más reciente) cruzada con el estado
    real del buffer de cada MP -- riesgo de quiebre, sobrestock, y
    cuánto reponer de cada una."""
    conn = sqlite3.connect(DB_PATH)
    try:
        bom = pd.read_sql(
            "SELECT sku_mp, pct FROM bom_semanal_pt "
            "WHERE sku_pt = ? AND fecha_snapshot = "
            "(SELECT MAX(fecha_snapshot) FROM bom_semanal_pt)",
            conn, params=(sku_pt,))
        if bom.empty:
            return {"sku_pt": sku_pt, "mp": [], "mensaje": "Sin fórmula esta semana."}

        buffer_mp = pd.read_sql(
            "SELECT sku, mp_descripcion, tor, tog, adu, nivel_rotacion "
            "FROM ddmrp_buffer_mp WHERE estado='ok'", conn)

        f = conn.execute("SELECT MAX(fecha_snapshot) FROM posicion_inventario_mp").fetchone()[0]
        inv = pd.read_sql(
            "SELECT material, disponible_tm FROM posicion_inventario_mp "
            "WHERE fecha_snapshot = ?", conn, params=(f,))

        m = bom.merge(buffer_mp, left_on="sku_mp", right_on="sku", how="left")
        m = m.merge(inv, left_on="sku_mp", right_on="material", how="left")
        m["disponible_tm"] = m["disponible_tm"].fillna(0)
        m["cantidad_a_reponer_tm"] = (m["tog"] - m["disponible_tm"]).clip(lower=0).fillna(0)
        m["estado"] = m.apply(
            lambda r: "riesgo" if pd.notna(r["tor"]) and r["disponible_tm"] < r["tor"]
            else ("sobrestock" if pd.notna(r["tog"]) and r["disponible_tm"] > r["tog"]
                  else "ok"), axis=1)

        cols = ["sku_mp", "mp_descripcion", "pct", "nivel_rotacion", "disponible_tm",
                "tor", "tog", "cantidad_a_reponer_tm", "estado"]
        return {"sku_pt": sku_pt, "mp": _df_to_records(m[cols].round(1))}
    finally:
        conn.close()


@app.get("/api/v1/produccion/resumen")
def resumen_produccion():
    """
    Resumen del plan: carga por línea, desglose PEDIDO/BUFFER y demanda no servida.
    """
    conn = _conn()
    try:
        if not _tabla_existe(conn, "plan_produccion"):
            raise HTTPException(status_code=503,
                                  detail="Tabla plan_produccion no disponible.")

        plan = pd.read_sql("SELECT * FROM plan_produccion", conn)
        if plan.empty:
            return {"vacio": True}

        try:
            from config import LEAD_TIME_PRODUCCION_DIAS as LT
        except ImportError:
            LT = 3

        HORAS_DIA_LINEA_REAL = {
            "DURAN|L1": 22.0, "DURAN|L2": 20.0, "DURAN|L3": 20.0,
            "GUAYAQUIL|L1": 20.0, "GUAYAQUIL|L3": 20.0, "GUAYAQUIL|L4": 15.0,
            "GUAYAQUIL|L5": 22.0, "GUAYAQUIL|L6": 22.0,
        }
        col = "recurso" if "recurso" in plan.columns else "linea"
        car = (plan.groupby([col, "fecha"])
                    .agg(tm=("tm", "sum"), h=("horas", "sum")).reset_index())
        lineas = (car.groupby(col)
                     .agg(tm_total=("tm", "sum"), h_dia_prom=("h", "mean"),
                          h_dia_max=("h", "max")).reset_index().rename(columns={col: "recurso"}))
        lineas["horas_disponibles"] = lineas["recurso"].map(HORAS_DIA_LINEA_REAL).fillna(20.0)
        lineas["utilizacion"] = (lineas["h_dia_prom"] / lineas["horas_disponibles"]).round(2)

        por_dia = (plan.groupby("fecha")
                       .agg(tm=("tm", "sum"), n_corridas=("sku", "count")).reset_index())

        riesgo = {"total_tm": 0, "ya_no_llegan_tm": 0, "falta_capacidad_tm": 0,
                  "detalle": []}
        if _tabla_existe(conn, "plan_riesgo"):
            r = pd.read_sql("SELECT * FROM plan_riesgo", conn)
            if not r.empty:
                r["fecha"] = pd.to_datetime(r["fecha"])
                limite = pd.Timestamp.today().normalize() + pd.Timedelta(days=LT)
                tarde = r[r["fecha"] < limite]
                evit = r[r["fecha"] >= limite]
                agg = (r.groupby("sku")["tm_no_servida"].sum()
                        .sort_values(ascending=False).reset_index())
                agg["tipo"] = agg["sku"].map(
                    lambda s: "YA_NO_LLEGA" if s in set(tarde["sku"]) else "FALTA_CAPACIDAD")
                riesgo = {
                    "total_tm": round(float(r["tm_no_servida"].sum()), 1),
                    "ya_no_llegan_tm": round(float(tarde["tm_no_servida"].sum()), 1),
                    "falta_capacidad_tm": round(float(evit["tm_no_servida"].sum()), 1),
                    "detalle": _df_to_records(agg.round(1)),
                }

        return {
            "vacio": False,
            "fecha_calculo": plan["fecha_calculo"].max()
                            if "fecha_calculo" in plan.columns else None,
            "tm_total": round(float(plan["tm"].sum()), 1),
            "n_sku": int(plan["sku"].nunique()),
            "n_dias": int(plan["fecha"].nunique()),
            "tm_pedido": round(float(plan.loc[plan.get("motivo") == "PEDIDO", "tm"].sum()), 1)
                      if "motivo" in plan.columns else None,
            "tm_buffer": round(float(plan.loc[plan.get("motivo") == "BUFFER", "tm"].sum()), 1)
                      if "motivo" in plan.columns else None,
            "lineas": _df_to_records(lineas.round(2)),
            "por_dia": _df_to_records(por_dia.round(1)),
            "riesgo": riesgo,
            "horas_dia_linea": HORAS_DIA_LINEA_REAL,
            "lead_time_produccion_dias": LT,
        }
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/hoy — panel de arranque
# ------------------------------------------------------------------
@app.get("/api/v1/hoy")
def panel_hoy():
    """
    Lo que el planificador necesita al abrir la app, sin buscar nada.
    """
    conn = _conn()
    try:
        out = {"fecha": None, "kpis": {}, "quiebre_inminente": [],
               "reponer": [], "mto_pendiente": [], "produccion_hoy": []}

        if not _tabla_existe(conn, "ddmrp_flujo_neto"):
            raise HTTPException(status_code=503,
                                  detail="Sin datos de flujo neto. Correr calcular_nfp.py.")

        f = conn.execute("SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto").fetchone()[0]
        out["fecha"] = f

        fn = pd.read_sql(
            "SELECT * FROM ddmrp_flujo_neto WHERE fecha_calculo = ? AND nodo = 'PT' "
            "AND tiene_posicion = 1", conn, params=(f,))

        try:
            mg = pd.read_sql("""
                SELECT sku, SUM(ventas_tm*(precio_tm-costo_tm))/SUM(ventas_tm) m
                FROM ventas_raw WHERE ventas_tm > 0 AND precio_tm IS NOT NULL
                  AND costo_tm IS NOT NULL AND fecha >= date('now','-12 months')
                GROUP BY sku HAVING SUM(ventas_tm) > 0""", conn)
            margen = dict(zip(mg["sku"].astype(str).str.strip(), mg["m"]))
        except Exception:
            margen = {}

        fn["sku"] = fn["sku"].astype(str).str.strip()
        fn["margen_usd_tm"] = fn["sku"].map(margen)

        q = fn[fn["alerta_onhand"] == "CRITICO"].copy()
        q["dias_cobertura"] = np.where(q["adu"] > 0, q["disponible_tm"] / q["adu"], None)
        q = q.sort_values("dias_cobertura")
        out["quiebre_inminente"] = _df_to_records(q[[
            "sku", "descripcion", "disponible_tm", "tor", "adu",
            "dias_cobertura", "margen_usd_tm"]].head(20).round(2))

        rep = fn[fn["requiere_orden"].astype(bool)].copy()
        rep["margen_en_riesgo"] = (rep["cantidad_a_ordenar_tm"]
                                   * rep["margen_usd_tm"].fillna(0))
        rep["dias_cobertura"] = np.where(
            rep["adu"] > 0, rep["disponible_tm"] / rep["adu"], 99)

        rep["_urg"] = rep["dias_cobertura"].clip(lower=0.5)
        rep["score"] = rep["margen_en_riesgo"] / rep["_urg"]
        rep["tiene_pedido"] = rep["demanda_calificada_tm"] > 0
        rep.loc[rep["tiene_pedido"], "score"] *= 3

        rep = rep.sort_values("score", ascending=False)
        out["reponer"] = _df_to_records(rep[[
            "sku", "descripcion", "nfp_tm", "tor", "tog", "prioridad_pct",
            "cantidad_a_ordenar_tm", "zona_nfp", "margen_en_riesgo",
            "dias_cobertura", "tiene_pedido"]].head(25).round(2))

        if _tabla_existe(conn, "alertas_mto"):
            a = pd.read_sql(
                "SELECT * FROM alertas_mto WHERE urgencia IN ('TARDE','HOY') "
                "ORDER BY dias_para_lanzar", conn)
            out["mto_pendiente"] = _df_to_records(a.round(2))

        if _tabla_existe(conn, "plan_produccion"):
            hoy = pd.Timestamp.today().strftime("%Y-%m-%d")
            p = pd.read_sql(
                "SELECT * FROM plan_produccion WHERE fecha = ? ORDER BY linea, tm DESC",
                conn, params=(hoy,))
            out["produccion_hoy"] = _df_to_records(p.round(2))

        out["kpis"] = {
            "sku_gestionados": int(len(fn)),
            "en_rojo": int((fn["zona_nfp"] == "ROJO").sum()),
            "quiebre_inminente": int(len(q)),
            "requieren_reposicion": int(len(rep)),
            "tm_a_reponer": round(float(rep["cantidad_a_ordenar_tm"].sum()), 1),
            "margen_en_riesgo_usd": round(float(rep["margen_en_riesgo"].sum()), 0),
        }
        return out
    finally:
        conn.close()


@app.get("/api/v1/forecast/lote/agregado")
def forecast_lote_agregado(
    series: str = Query(..., description="serie_id separados por coma"),
):
    ids = [s.strip() for s in series.split(",") if s.strip()]
    if not ids:
        return {"historia": [], "forecast": [], "financiero": [], "n_series": 0}

    LOTE = 800
    conn = _conn()
    try:
        hist_partes, fc_partes, fin_partes = [], [], []

        for k in range(0, len(ids), LOTE):
            chunk = ids[k:k + LOTE]
            ph = ",".join("?" for _ in chunk)

            hist_partes.append(pd.read_sql(f"""
                SELECT strftime('%Y-%m-01', fecha) AS fecha,
                       SUM(ventas_tm) AS ventas_tm
                FROM ventas_demanda
                WHERE serie_id IN ({ph})
                GROUP BY 1
            """, conn, params=chunk))

            tabla, _fuente = _tabla_bandas(conn, "forecast_reconciliado")
            if not _tabla_existe(conn, tabla):
                tabla = "forecast_final"

            fc_partes.append(pd.read_sql(f"""
                SELECT mes_offset,
                       SUM(p10) AS p10, SUM(p50) AS p50, SUM(p90) AS p90
                FROM {tabla}
                WHERE serie_id IN ({ph})
                GROUP BY mes_offset
            """, conn, params=chunk))

            fin_partes.append(pd.read_sql(f"""
                SELECT mes_offset,
                       SUM(revenue_p10) AS revenue_p10,
                       SUM(revenue_p50) AS revenue_p50,
                       SUM(revenue_p90) AS revenue_p90,
                       SUM(margin_p10)  AS margin_p10,
                       SUM(margin_p50)  AS margin_p50,
                       SUM(margin_p90)  AS margin_p90
                FROM forecast_financiero_detalle
                WHERE serie_id IN ({ph})
                GROUP BY mes_offset
            """, conn, params=chunk))

        def _fusionar(partes, clave, cols):
            df = pd.concat([p for p in partes if not p.empty], ignore_index=True) if any(not p.empty for p in partes) else pd.DataFrame()
            if df.empty:
                return df
            return df.groupby(clave, as_index=False)[cols].sum().sort_values(clave)

        historia = _fusionar(hist_partes, "fecha", ["ventas_tm"])
        forecast = _fusionar(fc_partes, "mes_offset", ["p10", "p50", "p90"])
        financiero = _fusionar(fin_partes, "mes_offset",
                              ["revenue_p10", "revenue_p50", "revenue_p90",
                               "margin_p10", "margin_p50", "margin_p90"])

        metodo = None
        if len(ids) == 1:
            try:
                r = conn.execute(
                    "SELECT metodo_ganador FROM forecast_final WHERE serie_id = ? "
                    "LIMIT 1", (ids[0],)).fetchone()
                metodo = r[0] if r else None
            except Exception:
                pass

        return {
            "n_series": len(ids),
            "metodo_ganador": metodo,
            "historia": _df_to_records(historia) if not historia.empty else [],
            "forecast": _df_to_records(forecast) if not forecast.empty else [],
            "financiero": _df_to_records(financiero) if not financiero.empty else [],
        }
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/semaforo
# ------------------------------------------------------------------
@app.get("/api/v1/semaforo/resumen")
def resumen_semaforo():
    conn = _conn()
    try:
        general = pd.read_sql(
            "SELECT semaforo_general, COUNT(*) as n FROM diagnostico_final "
            "GROUP BY semaforo_general", conn)
        capa1 = pd.read_sql(
            "SELECT semaforo_confiabilidad_estadistica, COUNT(*) as n "
            "FROM diagnostico_final GROUP BY semaforo_confiabilidad_estadistica", conn)
        capa2 = pd.read_sql(
            "SELECT semaforo_alerta_negocio, COUNT(*) as n FROM diagnostico_final "
            "GROUP BY semaforo_alerta_negocio", conn)
        return {
            "semaforo_general": _df_to_records(general),
            "capa1_confiabilidad_estadistica": _df_to_records(capa1),
            "capa2_alerta_negocio": _df_to_records(capa2),
        }
    finally:
        conn.close()


@app.get("/api/v1/semaforo/criticas")
def series_criticas(
    clase_abc: str = Query(None, description="Filtrar por clase A, B o C"),
    solo_rojo: bool = Query(True, description="Solo series en semáforo general rojo"),
):
    conn = _conn()
    try:
        query = "SELECT * FROM diagnostico_final WHERE 1=1"
        params = []
        if solo_rojo:
            query += " AND semaforo_general = 'ROJO'"
        if clase_abc:
            query += " AND clase_abc = ?"
            params.append(clase_abc)
        query += " ORDER BY clase_abc, wape_holdout DESC"

        df = pd.read_sql(query, conn, params=params)
        return [traducir_serie(r) for r in _df_to_records(df)]
    finally:
        conn.close()


@app.get("/api/v1/semaforo/{serie_id}")
def semaforo_serie(serie_id: str):
    conn = _conn()
    try:
        df = pd.read_sql("SELECT * FROM diagnostico_final WHERE serie_id = ?",
                         conn, params=(serie_id,))
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Serie '{serie_id}' no encontrada")
        return traducir_serie(_df_to_records(df)[0])
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/kpis
# ------------------------------------------------------------------
@app.get("/api/v1/kpis/inventario_total_pt")
def inventario_total_pt():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "posicion_inventario_pt"):
            return {"total_tm": None}
        f = conn.execute("SELECT MAX(fecha_snapshot) FROM posicion_inventario_pt").fetchone()[0]
        df = pd.read_sql("SELECT total_tm FROM posicion_inventario_pt WHERE fecha_snapshot=?",
                         conn, params=(f,))
        return {"total_tm": round(float(df["total_tm"].sum()), 1), "n_materiales": int(len(df)), "fecha_snapshot": f}
    finally:
        conn.close()


@app.get("/api/v1/kpis/inventario_total_mp")
def inventario_total_mp():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "posicion_inventario_mp"):
            return {"total_tm": None, "detalle": []}
        f = conn.execute("SELECT MAX(fecha_snapshot) FROM posicion_inventario_mp").fetchone()[0]
        df = pd.read_sql(
            "SELECT material, disponible_tm, bloqueado_tm, inspeccion_tm, transito_tm, total_tm "
            "FROM posicion_inventario_mp WHERE fecha_snapshot=?",
            conn, params=(f,))
        return {
            "total_tm": round(float(df["total_tm"].sum()), 1),
            "n_materiales": int(len(df)),
            "fecha_snapshot": f,
            "detalle": _df_to_records(df.round(2)),
        }
    finally:
        conn.close()


@app.get("/api/v1/kpis/financieros")
def kpis_financieros():
    conn = _conn()
    try:
        return _df_to_records(pd.read_sql("SELECT * FROM kpis_financieros_resumen", conn))
    finally:
        conn.close()


@app.get("/api/v1/kpis/top-riesgo")
def top_riesgo(n: int = Query(10, ge=1, le=100)):
    conn = _conn()
    try:
        df = pd.read_sql("SELECT * FROM forecast_financiero_por_serie "
                         "ORDER BY var_margen_usd DESC LIMIT ?", conn, params=(n,))
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/kpis/financieros/por_serie")
def kpis_financieros_por_serie():
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT fp.serie_id, fp.sku, fp.codigo_cliente, fp.clase_abc,
                   fp.metodo_ganador, fp.baja_confianza,
                   fp.revenue_p10_total, fp.revenue_p50_total, fp.revenue_p90_total,
                   fp.margin_p10_total, fp.margin_p50_total, fp.margin_p90_total,
                   fp.var_margen_usd, fp.var_revenue_usd,
                   mae.mae_validacion
            FROM forecast_financiero_por_serie fp
            LEFT JOIN (
                SELECT serie_id, MAX(mae_validacion) as mae_validacion
                FROM forecast_final GROUP BY serie_id
            ) mae ON mae.serie_id = fp.serie_id
        """, conn)
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/kpis/financieros/detalle_completo")
def kpis_financieros_detalle_completo():
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT serie_id, mes_offset,
                   p10 AS volumen_p10, p50 AS volumen_p50, p90 AS volumen_p90,
                   revenue_p10, revenue_p50, revenue_p90,
                   margin_p10, margin_p50, margin_p90
            FROM forecast_financiero_detalle
        """, conn)
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/kpis/financieros/serie/{serie_id}")
def kpis_financieros_serie(serie_id: str):
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT serie_id, mes_offset,
                   p10 AS volumen_p10, p50 AS volumen_p50, p90 AS volumen_p90,
                   revenue_p10, revenue_p50, revenue_p90,
                   margin_p10, margin_p50, margin_p90
            FROM forecast_financiero_detalle
            WHERE serie_id = ? ORDER BY mes_offset
        """, conn, params=(serie_id,))
        return _df_to_records(df)
    finally:
        conn.close()


@app.get("/api/v1/kpis/ventas_mes_actual")
def ventas_mes_actual():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "adu_mes_actual_hibrido"):
            return {"total_tm": None, "dias_transcurridos": None, "detalle": [],
                    "mensaje": "Tabla no disponible. Correr calcular_adu_mes_actual.py."}
        df = pd.read_sql("SELECT sku, venta_real_mtd, dias_transcurridos "
                         "FROM adu_mes_actual_hibrido", conn)
        return {
            "total_tm": round(float(df["venta_real_mtd"].sum()), 1),
            "dias_transcurridos": int(df["dias_transcurridos"].max()) if len(df) else None,
            "detalle": _df_to_records(df[["sku", "venta_real_mtd"]].round(2)),
        }
    finally:
        conn.close()


@app.get("/api/v1/kpis/consumo_mes_actual_mp")
def consumo_mes_actual_mp():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "consumo_mp"):
            return {"total_tm": None, "dias_transcurridos": None, "detalle": [],
                    "mensaje": "Tabla consumo_mp no disponible. Correr Ingesta_Consumo_MP.py."}
        df = pd.read_sql("""
            SELECT sku_mp AS sku, SUM(consumo_tm) AS consumo_mtd
            FROM consumo_mp
            WHERE strftime('%Y-%m', fecha_contabilizacion) = strftime('%Y-%m', 'now')
            GROUP BY sku_mp
        """, conn)
        return {
            "total_tm": round(float(df["consumo_mtd"].sum()), 1) if len(df) else 0.0,
            "dias_transcurridos": int(pd.Timestamp.today().day),
            "detalle": _df_to_records(df.round(2)),
        }
    finally:
        conn.close()


@app.get("/api/v1/kpis/margen_historico")
def margen_historico():
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT v.serie_id, strftime('%Y-%m', v.fecha) AS periodo,
                   SUM(v.ventas_tm) AS volumen_tm,
                   SUM(v.ventas_tm * c.margen_unitario_tm) AS margen_real
            FROM ventas_demanda v
            LEFT JOIN sku_clasificacion c ON c.serie_id = v.serie_id
            GROUP BY v.serie_id, periodo ORDER BY periodo
        """, conn)
        return _df_to_records(df)
    except sqlite3.OperationalError as e:
        return {"mensaje": f"No se pudo calcular margen histórico: {e}"}
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/contexto
# ------------------------------------------------------------------
@app.get("/api/v1/contexto/enso")
def contexto_enso(historico: bool = Query(False)):
    conn = _conn()
    try:
        query = "SELECT * FROM enso_forecast_context"
        if not historico:
            query += " WHERE es_vigente = 1"
        query += " ORDER BY fecha_actualizacion DESC"

        registros = _df_to_records(pd.read_sql(query, conn))

        if not historico and registros:
            vigente = registros[0]
            vigente["mensaje_seguimiento"] = mensaje_seguimiento_oni(
                vigente.get("alert_status"),
                vigente.get("porcentaje_persistencia"),
                vigente.get("frase_original"),
            )
            vigente["es_input_del_modelo"] = False
            return vigente

        return registros
    except sqlite3.OperationalError:
        return {"mensaje": "Tabla enso_forecast_context no existe -- correr Ingesta_ENSO_Forecast.py"}
    finally:
        conn.close()


@app.get("/api/v1/contexto/precio-historico")
def precio_historico():
    conn = _conn()
    try:
        df = pd.read_sql(
            "SELECT fecha, precio_promedio_libra FROM cna_exportaciones ORDER BY fecha",
            conn)
        if df.empty:
            return {"mensaje": "Sin datos de precio disponibles."}

        cambio_pct = None
        if len(df) >= 2:
            cambio_pct = float(df["precio_promedio_libra"].iloc[-1]
                              / df["precio_promedio_libra"].iloc[-2] - 1)

        return {"historico": _df_to_records(df),
                "mensaje_seguimiento": mensaje_seguimiento_precio(cambio_pct)}
    except sqlite3.OperationalError:
        return {"mensaje": "Tabla cna_exportaciones no existe -- correr Ingesta_External.py"}
    finally:
        conn.close()


@app.get("/api/v1/contexto/precio-proyectado")
def precio_proyectado():
    conn = _conn()
    try:
        df = pd.read_sql("SELECT * FROM precio_proyectado ORDER BY mes_offset", conn)
        return {
            "fuente": "estimacion_interna",
            "nota": "Proyección propia (Holt amortiguado), no es dato oficial de NOAA/CNA.",
            "es_input_del_modelo": False,
            "datos": _df_to_records(df),
        }
    except sqlite3.OperationalError:
        return {"mensaje": "Tabla precio_proyectado no existe -- correr proyeccion_precio_camaron.py"}
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/clasificacion
# ------------------------------------------------------------------
@app.get("/api/v1/clasificacion/resumen")
def resumen_clasificacion():
    conn = _conn()
    try:
        tipo = pd.read_sql(
            "SELECT tipo_demanda, COUNT(*) as n FROM sku_clasificacion "
            "GROUP BY tipo_demanda", conn)
        abc = pd.read_sql(
            "SELECT clase_abc, COUNT(*) as n FROM sku_clasificacion GROUP BY clase_abc",
            conn)

        resultado = {"tipo_demanda": _df_to_records(tipo),
                     "clase_abc": _df_to_records(abc)}

        if _tabla_existe(conn, "clasificacion_nivel_sku"):
            resultado["tipo_demanda_nivel_sku"] = _df_to_records(pd.read_sql(
                "SELECT tipo_demanda, COUNT(*) as n FROM clasificacion_nivel_sku "
                "GROUP BY tipo_demanda", conn))
            resultado["sku_descontinuados"] = int(pd.read_sql(
                "SELECT COUNT(*) n FROM clasificacion_nivel_sku "
                "WHERE probable_descontinuado = 1", conn).iloc[0]["n"])

        return resultado
    finally:
        conn.close()


@app.get("/api/v1/clasificacion/nivel-sku")
def clasificacion_nivel_sku(solo_descontinuados: bool = Query(False)):
    conn = _conn()
    try:
        if not _tabla_existe(conn, "clasificacion_nivel_sku"):
            raise HTTPException(status_code=503,
                                  detail="Tabla clasificacion_nivel_sku no disponible.")
        query = "SELECT * FROM clasificacion_nivel_sku"
        if solo_descontinuados:
            query += " WHERE probable_descontinuado = 1"
        query += " ORDER BY demanda_mensual_promedio DESC"
        return _df_to_records(pd.read_sql(query, conn))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/catalogo
# ------------------------------------------------------------------
@app.get("/api/v1/catalogo/skus")
def catalogo_skus():
    conn = _conn()
    try:
        df = pd.read_sql("""
            WITH ventas AS (
                SELECT sku, sku_descripcion
                FROM (
                    SELECT sku,
                           sku_descripcion,
                           fecha,
                           ROW_NUMBER() OVER (
                               PARTITION BY sku
                               ORDER BY fecha DESC
                           ) rn
                    FROM ventas_raw
                )
                WHERE rn = 1
            ),

            inventario AS (
                SELECT
                    material AS sku,
                    descripcion AS sku_descripcion
                FROM posicion_inventario_pt
                WHERE descripcion IS NOT NULL
                  AND TRIM(descripcion) <> ''
            )

            SELECT * FROM ventas

            UNION

            SELECT i.*
            FROM inventario i
            WHERE NOT EXISTS (
                SELECT 1
                FROM ventas v
                WHERE TRIM(v.sku) = TRIM(i.sku)
            )
        """, conn)

        return _df_to_records(df)

    finally:
        conn.close()


@app.get("/api/v1/catalogo/clientes")
def catalogo_clientes():
    conn = _conn()
    try:
        df = pd.read_sql("""
            SELECT codigo_cliente, cliente FROM (
                SELECT codigo_cliente, cliente, fecha,
                       ROW_NUMBER() OVER (PARTITION BY codigo_cliente ORDER BY fecha DESC) as rn
                FROM ventas_raw
            ) WHERE rn = 1
        """, conn)
        return _df_to_records(df)
    except sqlite3.OperationalError as e:
        return {"mensaje": f"No se pudo leer catálogo de clientes: {e}"}
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/produccion/simular -- re-optimización real
# ------------------------------------------------------------------
class MoverCorridaRequest(BaseModel):
    sku: str
    fecha_origen: str
    linea_origen: str
    fecha_destino: str
    linea_destino: str = None
    motivo_usuario: str = ""


RUTA_RESTRICCIONES = Path(tempfile.gettempdir()) / "restricciones_simulacion.json"


@app.post("/api/v1/produccion/simular")
def simular_movimiento_corrida(req: MoverCorridaRequest):
    conn = _conn()
    try:
        fila = pd.read_sql(
            "SELECT * FROM plan_produccion WHERE sku=? AND fecha=? AND linea=? "
            "ORDER BY fecha_calculo DESC LIMIT 1",
            conn, params=(req.sku, req.fecha_origen, req.linea_origen),
        )
    finally:
        conn.close()

    if fila.empty:
        raise HTTPException(status_code=404,
            detail=f"No se encontró la corrida {req.sku} en {req.fecha_origen}/{req.linea_origen}.")

    if fila.iloc[0].get("motivo") != "BUFFER":
        raise HTTPException(status_code=400,
            detail="Solo se pueden mover corridas con motivo BUFFER. Esta es PEDIDO -- "
                   "está comprometida con un cliente y no se reprograma desde aquí.")

    restriccion = {
        "sku": req.sku, "fecha_origen": req.fecha_origen, "linea_origen": req.linea_origen,
        "fecha_destino": req.fecha_destino,
        "linea_destino": req.linea_destino or req.linea_origen,
        "motivo_usuario": req.motivo_usuario,
    }
    with open(RUTA_RESTRICCIONES, "w", encoding="utf-8") as f:
        json.dump(restriccion, f, ensure_ascii=False, indent=2)

    ruta_script = Path(__file__).resolve().parent / "optimizar_produccion.py"
    env_hijo = os.environ.copy()
    env_hijo["PYTHONIOENCODING"] = "utf-8"
    resultado = subprocess.run(
        [sys.executable, str(ruta_script), "--restricciones", str(RUTA_RESTRICCIONES)],
        capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace",
        env=env_hijo,
    )
    RUTA_RESTRICCIONES.unlink(missing_ok=True)

    if resultado.returncode != 0:
        raise HTTPException(status_code=500,
            detail=f"La re-optimización falló: {resultado.stderr[-800:]}")

    conn = _conn()
    try:
        if not _tabla_existe(conn, "plan_produccion"):
            raise HTTPException(status_code=500,
                detail="La re-optimización corrió pero no generó plan_produccion.")
        plan_nuevo = pd.read_sql("SELECT * FROM plan_produccion ORDER BY fecha, linea, tm DESC", conn)
        riesgo_nuevo = (pd.read_sql("SELECT * FROM plan_riesgo", conn)
                        if _tabla_existe(conn, "plan_riesgo") else pd.DataFrame())
    finally:
        conn.close()

    return {
        "estado": "reoptimizado", "restriccion_aplicada": restriccion,
        "plan": _df_to_records(plan_nuevo), "riesgo": _df_to_records(riesgo_nuevo),
    }


# ------------------------------------------------------------------
# /api/v1/produccion/escenario -- Simulador: corre el MILP con
# overrides (shock de demanda, horas extra por linea, prioridad de
# cliente, mover demanda entre fechas) SIN tocar plan_produccion.
# Se ejecuta en un subproceso nuevo -- igual que /produccion/simular
# arriba -- para que HOY/LUNES_ACTUAL (fijados al cargar el modulo de
# optimizar_produccion.py) sean siempre la fecha real, aunque la API
# lleve dias corriendo sin reiniciarse.
# ------------------------------------------------------------------
class EscenarioRequest(BaseModel):
    # El front (armarOverridesCombinados() en dashboard.js) manda
    # exactamente { nombre, overrides }, con overrides ya armado como
    # {horas_override_extra, shock_demanda_pct, prioridad_cliente,
    # mover_demanda} -- se deja como dict libre y se pasa tal cual a
    # aplicar_overrides_escenario(), que ya sabe leer cada forma.
    nombre: str = "Escenario"
    overrides: dict = {}


RUTA_ESCENARIO_TMP = Path(tempfile.gettempdir()) / "_escenario_simulador_tmp.json"


def _limpiar_nan(obj):
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if isinstance(obj, dict):
        return {k: _limpiar_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_limpiar_nan(v) for v in obj]
    return obj


@app.post("/api/v1/produccion/escenario")
def correr_escenario_simulador(req: EscenarioRequest):
    """Corre un escenario del Simulador (no escribe en la base -- es un
    'que pasa si'). Devuelve margen_bruto_usd, tm_producidas, tm_riesgo,
    estado y el detalle del plan propuesto para ese escenario."""
    payload = {"nombre": req.nombre, "overrides": req.overrides or {}}

    ruta_tmp = RUTA_ESCENARIO_TMP.with_name(
        f"_escenario_simulador_{os.getpid()}_{id(req)}.json")
    with open(ruta_tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    ruta_script = Path(__file__).resolve().parent / "optimizar_produccion.py"
    env_hijo = os.environ.copy()
    env_hijo["PYTHONIOENCODING"] = "utf-8"
    try:
        resultado = subprocess.run(
            [sys.executable, str(ruta_script), "--escenario-json", str(ruta_tmp)],
            capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace",
            env=env_hijo,
        )
    finally:
        ruta_tmp.unlink(missing_ok=True)

    if resultado.returncode != 0:
        raise HTTPException(status_code=500,
            detail=f"El escenario no se pudo calcular: {resultado.stderr[-800:]}")

    marcador = "###RESULTADO_ESCENARIO_JSON###"
    salida = resultado.stdout
    if marcador not in salida:
        raise HTTPException(status_code=500,
            detail="El escenario corrió pero no devolvió resultado (revisar logs del optimizador).")

    linea_json = salida.split(marcador, 1)[1].strip().splitlines()[0]
    try:
        data = json.loads(linea_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500,
            detail="No se pudo leer el resultado del escenario (JSON inválido).")

    return _limpiar_nan(data)


# ------------------------------------------------------------------
# /api/v1/config/lineas-produccion -- config dinamica de lineas de
# produccion (horas/dia, forma AP/AX por planta+linea). Antes esto
# estaba fijo en HORAS_DIA_LINEA_REAL / FORMA_POR_RECURSO dentro de
# optimizar_produccion.py; ahora el planificador lo edita desde
# Configuracion en la app y el optimizador lee de esta misma tabla
# (ver cargar_config_lineas_dinamica() en optimizar_produccion.py).
# ------------------------------------------------------------------
class LineaProduccionConfig(BaseModel):
    bu: str
    tipo_producto: str              # "Pellet" | "Extruido"
    forma: str                      # "AP" | "AX"
    planta: str
    linea: str
    tamano_mm: Optional[float] = None
    horas_dia: float
    rate_tm_hora: Optional[float] = None
    activo: bool = True


class GuardarLineasRequest(BaseModel):
    lineas: List[LineaProduccionConfig]


@app.get("/api/v1/config/lineas-produccion")
def obtener_config_lineas():
    """Lineas de produccion tal como estan guardadas hoy. Si la tabla
    no existe todavia (falta correr migrar_config_lineas_produccion.py
    una vez), lo dice explicito en vez de fallar, para que el frontend
    pueda mostrar el aviso correcto en vez de un error generico."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "config_lineas_produccion"):
            return {
                "conectado": True,
                "lineas": [],
                "aviso": "Tabla config_lineas_produccion no existe todavia -- "
                         "correr migrar_config_lineas_produccion.py.",
            }
        df = pd.read_sql(
            "SELECT * FROM config_lineas_produccion WHERE activo = 1 "
            "ORDER BY planta, linea", conn)
        return {"conectado": True, "lineas": _df_to_records(df)}
    finally:
        conn.close()


@app.post("/api/v1/config/lineas-produccion")
def guardar_config_lineas(body: GuardarLineasRequest):
    """Reemplaza la lista completa de lineas: Configuracion -> Lineas de
    produccion en la app es la fuente de verdad (agregar, editar o
    borrar una fila ahi), no parches puntuales por fila.

    Usa una conexion de escritura real (no _conn(), que abre la base
    en modo solo-lectura) porque este endpoint necesita CREATE TABLE /
    DELETE / INSERT -- mismo patron que ya usan optimizar_produccion.py
    y migrar_config_lineas_produccion.py."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_lineas_produccion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bu TEXT NOT NULL,
                tipo_producto TEXT NOT NULL,
                forma TEXT NOT NULL,
                planta TEXT NOT NULL,
                linea TEXT NOT NULL,
                tamano_mm REAL,
                horas_dia REAL NOT NULL,
                rate_tm_hora REAL,
                activo INTEGER NOT NULL DEFAULT 1,
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("DELETE FROM config_lineas_produccion")
        conn.executemany(
            "INSERT INTO config_lineas_produccion "
            "(bu, tipo_producto, forma, planta, linea, tamano_mm, horas_dia, "
            "rate_tm_hora, activo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(l.bu, l.tipo_producto, l.forma, l.planta, l.linea,
              l.tamano_mm, l.horas_dia, l.rate_tm_hora, 1 if l.activo else 0)
             for l in body.lineas],
        )
        conn.commit()
        return {"ok": True, "guardadas": len(body.lineas)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/config/paradas-programadas -- mantenimiento/parada real de
# una línea, con fecha de inicio y fin. A diferencia de "horas/día" en
# Líneas de producción (que aplica siempre), esto SOLO rige durante esa
# ventana -- optimizar_produccion.py lo lee en cargar_paradas_programadas()
# y limita las horas disponibles nada más esos días.
# ------------------------------------------------------------------
class ParadaProgramada(BaseModel):
    planta: str
    linea: str
    fecha_inicio: str  # "YYYY-MM-DD"
    fecha_fin: str  # "YYYY-MM-DD"
    horas_dia: float  # HORAS DE PARADA por día (24 = parada total)
    tipo: str = "Programada"  # "Programada" | "No planeada"
    motivo: str = ""          # texto libre, opcional


class GuardarParadasRequest(BaseModel):
    paradas: List[ParadaProgramada]


def _asegurar_columna(conn, tabla: str, columna: str, tipo_sql: str, default_sql: str = None):
    """Agrega una columna a una tabla existente si todavía no la tiene --
    ALTER TABLE no admite "IF NOT EXISTS" en SQLite, así que se intenta y
    se ignora el error si la columna ya está (evita romper instalaciones
    que crearon la tabla antes de que existiera esta columna)."""
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
        if columna not in cols:
            extra = f" DEFAULT {default_sql}" if default_sql is not None else ""
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo_sql}{extra}")
    except Exception:
        pass


@app.get("/api/v1/config/paradas-programadas")
def obtener_paradas_programadas():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "paradas_programadas"):
            return {"paradas": []}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(paradas_programadas)").fetchall()}
        col_tipo = "tipo" if "tipo" in cols else "'Programada' AS tipo"
        col_motivo = "motivo" if "motivo" in cols else "'' AS motivo"
        df = pd.read_sql(
            f"SELECT planta, linea, fecha_inicio, fecha_fin, horas_dia, {col_tipo}, {col_motivo} "
            "FROM paradas_programadas ORDER BY fecha_inicio", conn)
        return {"paradas": _df_to_records(df)}
    finally:
        conn.close()


@app.post("/api/v1/config/paradas-programadas")
def guardar_paradas_programadas(body: GuardarParadasRequest):
    """Reemplaza la lista completa -- misma lógica que Líneas de producción:
    Configuración en la app es la fuente de verdad, no parches por fila."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paradas_programadas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                planta TEXT NOT NULL,
                linea TEXT NOT NULL,
                fecha_inicio TEXT NOT NULL,
                fecha_fin TEXT NOT NULL,
                horas_dia REAL NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'Programada',
                motivo TEXT DEFAULT '',
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        _asegurar_columna(conn, "paradas_programadas", "tipo", "TEXT", "'Programada'")
        _asegurar_columna(conn, "paradas_programadas", "motivo", "TEXT", "''")
        conn.execute("DELETE FROM paradas_programadas")
        conn.executemany(
            "INSERT INTO paradas_programadas "
            "(planta, linea, fecha_inicio, fecha_fin, horas_dia, tipo, motivo) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(p.planta, p.linea, p.fecha_inicio, p.fecha_fin, p.horas_dia,
              p.tipo or "Programada", p.motivo or "")
             for p in body.paradas],
        )
        conn.commit()
        return {"ok": True, "guardadas": len(body.paradas)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/config/paradas-programadas/impacto -- para una parada "No
# planeada", qué SKU y TM del plan de producción vigente caen dentro de
# esa ventana planta+línea+fechas. Es de solo lectura (no escribe nada);
# el planificador decide fila por fila si reasigna (botón "Reasignar" en
# el frontend, que reusa /api/v1/produccion/simular ya existente).
# ------------------------------------------------------------------
class ImpactoParadaRequest(BaseModel):
    planta: str
    linea: str
    fecha_inicio: str
    fecha_fin: str


@app.post("/api/v1/config/paradas-programadas/impacto")
def impacto_parada(body: ImpactoParadaRequest):
    conn = _conn()
    try:
        if not _tabla_existe(conn, "plan_produccion"):
            return {"filas": [], "tm_total": 0.0}
        df = pd.read_sql(
            "SELECT sku, fecha, planta, linea, tm, horas, motivo, recurso "
            "FROM plan_produccion WHERE planta = ? AND linea = ? "
            "AND fecha BETWEEN ? AND ? AND motivo != 'YA PRODUCIDO' "
            "ORDER BY fecha, sku",
            conn, params=(body.planta, body.linea, body.fecha_inicio, body.fecha_fin),
        )
        return {
            "filas": _df_to_records(df.round(2)),
            "tm_total": round(float(df["tm"].sum()), 1) if len(df) else 0.0,
        }
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/config/capacidad-bodega -- capacidad máxima de bodega PT y MP,
# usada por Inventario Estratégico para el KPI "% Ocupación" de cada buffer.
# Una sola fila (id=1) que se sobrescribe cada vez que se guarda desde
# Configuración -- misma lógica que Líneas de producción y Paradas: la
# app es la fuente de verdad, no se edita el script.
# ------------------------------------------------------------------
class GuardarCapacidadBodegaRequest(BaseModel):
    capacidad_pt_tm: Optional[float] = None
    capacidad_mp_tm: Optional[float] = None


@app.get("/api/v1/config/capacidad-bodega")
def obtener_capacidad_bodega():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "config_capacidad_bodega"):
            return {"capacidad_pt_tm": None, "capacidad_mp_tm": None}
        fila = conn.execute(
            "SELECT capacidad_pt_tm, capacidad_mp_tm FROM config_capacidad_bodega WHERE id = 1"
        ).fetchone()
        if not fila:
            return {"capacidad_pt_tm": None, "capacidad_mp_tm": None}
        return {"capacidad_pt_tm": fila["capacidad_pt_tm"], "capacidad_mp_tm": fila["capacidad_mp_tm"]}
    finally:
        conn.close()


@app.post("/api/v1/config/capacidad-bodega")
def guardar_capacidad_bodega(body: GuardarCapacidadBodegaRequest):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_capacidad_bodega (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                capacidad_pt_tm REAL,
                capacidad_mp_tm REAL,
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO config_capacidad_bodega (id, capacidad_pt_tm, capacidad_mp_tm, actualizado_en)
            VALUES (1, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                capacidad_pt_tm = excluded.capacidad_pt_tm,
                capacidad_mp_tm = excluded.capacidad_mp_tm,
                actualizado_en = excluded.actualizado_en
        """, (body.capacidad_pt_tm, body.capacidad_mp_tm))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/config/pedidos-manuales -- pedidos que el planificador
# ingresa directo en la app (Configuración -> Pedidos manuales), para
# demanda ya conocida que SAP todavía no registró. Se suman -- nunca
# reemplazan -- a pedidos_pendientes_pt en calcular_nfp.py,
# optimizar_produccion.py, clasificar_mts_mto.py y en pedidos_del_sku.
# ------------------------------------------------------------------
class PedidoManual(BaseModel):
    sku: str
    cliente_nombre: str
    cantidad_tm: float
    fecha_entrega: str  # "YYYY-MM-DD"


class GuardarPedidosManualesRequest(BaseModel):
    pedidos: List[PedidoManual]


@app.get("/api/v1/config/pedidos-manuales")
def obtener_pedidos_manuales():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "pedidos_manuales_pt"):
            return {"pedidos": []}
        df = pd.read_sql(
            "SELECT sku, cliente_nombre, cantidad_tm, fecha_entrega "
            "FROM pedidos_manuales_pt WHERE activo = 1 ORDER BY fecha_entrega", conn)
        return {"pedidos": _df_to_records(df)}
    finally:
        conn.close()


@app.post("/api/v1/config/pedidos-manuales")
def guardar_pedidos_manuales(body: GuardarPedidosManualesRequest):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pedidos_manuales_pt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                cliente_nombre TEXT NOT NULL,
                cantidad_tm REAL NOT NULL,
                fecha_entrega TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1,
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("DELETE FROM pedidos_manuales_pt")
        conn.executemany(
            "INSERT INTO pedidos_manuales_pt "
            "(sku, cliente_nombre, cantidad_tm, fecha_entrega) VALUES (?, ?, ?, ?)",
            [(p.sku, p.cliente_nombre, p.cantidad_tm, p.fecha_entrega) for p in body.pedidos],
        )
        conn.commit()
        return {"ok": True, "guardados": len(body.pedidos)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/config/exclusiones-planta -- SKU que NO deben producirse en
# cierta planta/línea por tema de negocio (Configuración -> Exclusiones
# SKU-Planta/Línea). optimizar_produccion.py las lee en
# cargar_exclusiones_sku_planta() y nunca genera esa combinación como
# variable de decisión del MILP (no hace falta restricción adicional,
# con no crear la variable basta).
# ------------------------------------------------------------------
class SkuExclusionPlanta(BaseModel):
    sku: str
    planta_excluida: str
    linea_excluida: Optional[str] = None
    motivo: str = ""


class GuardarExclusionesRequest(BaseModel):
    exclusiones: List[SkuExclusionPlanta]


@app.get("/api/v1/config/exclusiones-planta")
def obtener_exclusiones_planta():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "sku_exclusion_planta"):
            return {"exclusiones": []}
        df = pd.read_sql(
            "SELECT sku, planta_excluida, linea_excluida, motivo FROM sku_exclusion_planta "
            "WHERE activo = 1 ORDER BY sku", conn)
        return {"exclusiones": _df_to_records(df)}
    finally:
        conn.close()


@app.post("/api/v1/config/exclusiones-planta")
def guardar_exclusiones_planta(body: GuardarExclusionesRequest):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sku_exclusion_planta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                planta_excluida TEXT NOT NULL,
                linea_excluida TEXT,
                motivo TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("DELETE FROM sku_exclusion_planta")
        conn.executemany(
            "INSERT INTO sku_exclusion_planta "
            "(sku, planta_excluida, linea_excluida, motivo) VALUES (?, ?, ?, ?)",
            [(e.sku, e.planta_excluida, e.linea_excluida or None, e.motivo or "")
             for e in body.exclusiones],
        )
        conn.commit()
        return {"ok": True, "guardadas": len(body.exclusiones)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# /api/v1/ddmrp/overrides -- edición directa del buffer DDMRP
# (TOR/TOY/TOG) en posicionamiento_pt. No es un campo "manual" aparte:
# es el mismo valor que ya lee optimizar_produccion.py (cargar_datos()
# -> pos = SELECT * FROM posicionamiento_pt), así que la próxima corrida
# ya usa el nuevo buffer sin ningún cambio adicional en el optimizador
# (objetivo_reposicion[sku] = TOG - NFP se recalcula solo).
# /evaluar es de solo lectura (no escribe nada): calcula días de
# cobertura, compara contra la rotación del SKU y la capacidad de
# bodega, y devuelve un semáforo + mensaje ANTES de guardar. /guardar
# vuelve a calcular lo mismo (para dejarlo en la auditoría) y recién
# ahí hace el UPDATE -- el semáforo asesora, nunca bloquea el guardado.
# ------------------------------------------------------------------
# Tolerancia en puntos de cuantil (0-1) antes de avisar. Por debajo de esto
# la diferencia entre "lo que propone el planificador" y "lo que dice la
# economía del SKU" se considera ruido, no una decisión mala.
TOLERANCIA_CUANTIL = 0.10


def _f_empirica(q, p10, p50, p90) -> float:
    """
    Copia exacta de _f_empirica() en clasificar_mts_mto.py: interpolación
    lineal a trozos entre p10 (0.10), p50 (0.50) y p90 (0.90) de la
    distribución EMPÍRICA de demanda-en-DLT (bootstrap, sin asumir
    Normal). Se duplica aquí (en vez de importar) porque api_simulador.py
    es de solo lectura y no debe depender de la lógica interna del
    pipeline de clasificación -- pero la fórmula debe ser idéntica o el
    semáforo del modal contradeciría al pipeline.
    """
    if p10 is None or p50 is None or p90 is None:
        return None
    if pd.isna(p10) or pd.isna(p50) or pd.isna(p90):
        return None
    if p10 >= p90:
        return 1.0 if q >= p50 else 0.0
    if q <= p10:
        pendiente = 0.10 / max(p50 - p10, 1e-9)
        return max(0.0, 0.10 - pendiente * (p10 - q))
    if q <= p50:
        pendiente = 0.40 / max(p50 - p10, 1e-9)
        return 0.10 + pendiente * (q - p10)
    if q <= p90:
        pendiente = 0.40 / max(p90 - p50, 1e-9)
        return 0.50 + pendiente * (q - p50)
    pendiente = 0.10 / max(p90 - p50, 1e-9)
    return min(1.0, 0.90 + pendiente * (q - p90))


def _evaluar_consistencia_newsvendor(conn, sku: str, tor: float, toy: float, tog: float) -> list:
    """
    Criterio único y coherente para juzgar si un ajuste manual de buffer
    es bueno o malo, basado en el MISMO newsvendor empírico que ya
    gobierna TOR/TOY/TOG en clasificar_mts_mto.py (Chopra & Meindl 13.4,
    adaptado con la distribución bootstrap propia de la empresa -- no
    Normal, no supuestos nuevos).

    Idea: cada valor propuesto (TOR, TOG) implica cubrir cierto percentil
    de la demanda-en-DLT real del SKU (F_empirica(valor)). La economía del
    propio SKU (su margen vs. su costo de sobre-stock) ya define cuál es
    el percentil ÓPTIMO a cubrir: cuantil_objetivo = Cu / (Cu + Co),
    calculado y guardado por clasificar_mts_mto.py en posicionamiento_pt.

    Si el percentil que cubre el valor propuesto se aleja del percentil
    óptimo, hay una decisión económicamente subóptima -- y a diferencia
    de "rota mucho / rota poco", esto es cuantificable: dice exactamente
    cuántos puntos de probabilidad de quiebre (o de sobre-cobertura) está
    aceptando el planificador con su ajuste, en el propio SKU que está
    editando, no en una categoría genérica de rotación.

    Reemplaza las reglas de "nivel_rotacion alta/baja" con umbrales fijos
    en días, que no tenían relación matemática entre sí ni con el resto
    del sistema (eran heurísticas separadas, una por cada síntoma).
    """
    fila = conn.execute(
        "SELECT cu, co, cuantil_objetivo, "
        "p10_ajustado, p50_ajustado, p90_ajustado "
        "FROM posicionamiento_pt WHERE sku = ?",
        (sku,),
    ).fetchone()
    if not fila:
        return []

    cu, co = fila["cu"], fila["co"]
    cuantil_objetivo = fila["cuantil_objetivo"]
    p10a, p50a, p90a = fila["p10_ajustado"], fila["p50_ajustado"], fila["p90_ajustado"]

    if cuantil_objetivo is None or pd.isna(cuantil_objetivo):
        return []  # sin cu/co calculado para este SKU, no hay con qué comparar

    f_tog = _f_empirica(tog, p10a, p50a, p90a)
    if f_tog is None:
        return []  # sin distribución bootstrap para este SKU (extras sin buffer, etc.)

    avisos = []
    diferencia = f_tog - cuantil_objetivo

    if diferencia < -TOLERANCIA_CUANTIL:
        # El TOG propuesto cubre MENOS demanda-probabilidad de la que el
        # propio margen/costo del SKU justifica cubrir.
        avisos.append(("amarillo",
            f"Este TOG cubre el percentil {f_tog*100:.0f} de la demanda real del SKU "
            f"(bootstrap), pero su margen frente al costo de sobre-stock justifica cubrir "
            f"hasta el percentil {cuantil_objetivo*100:.0f}. Con este ajuste, la probabilidad "
            f"estimada de quiebre en un ciclo de reposición es de ~{(1-f_tog)*100:.0f}%, "
            f"más alta que el ~{(1-cuantil_objetivo)*100:.0f}% que el propio SKU justifica "
            f"económicamente."))
    elif diferencia > TOLERANCIA_CUANTIL:
        # El TOG propuesto cubre MÁS demanda-probabilidad de la necesaria:
        # capital inmovilizado, cuantificado, no por regla de rotación.
        avisos.append(("amarillo",
            f"Este TOG cubre el percentil {f_tog*100:.0f} de la demanda real del SKU "
            f"(bootstrap), por encima del percentil {cuantil_objetivo*100:.0f} que su propio "
            f"margen frente al costo de sobre-stock justifica. Son ~{diferencia*100:.0f} puntos "
            f"de cobertura de más: capital inmovilizado en escenarios de demanda poco "
            f"probables para este SKU en particular."))

    return avisos


def _adu_hibrido_sku(conn, sku: str) -> float:
    """
    ADU real para asesorar un override de buffer: NO el 'adu' estático que
    trae posicionamiento_pt (calculado una vez, en la última corrida de
    clasificar_mts_mto.py), sino el ADU híbrido del mes en curso --
    ventas reales ya transcurridas + la proyección (p50) prorrateada para
    los días que faltan del mes, sobre el total de días del mes. Así el
    semáforo reacciona a cómo va vendiendo el SKU AHORA, no a una foto
    vieja. Tabla la genera calcular_adu_mes_actual.py
    (adu_mes_actual_hibrido.adu_hibrido). Si no existe o el SKU no tiene
    fila ahí (sin ventas ni forecast este mes), se cae al adu estático de
    posicionamiento_pt para no dejar el semáforo sin dato.
    """
    if _tabla_existe(conn, "adu_mes_actual_hibrido"):
        f = conn.execute(
            "SELECT adu_hibrido FROM adu_mes_actual_hibrido WHERE sku = ?", (sku,)
        ).fetchone()
        if f and f["adu_hibrido"] is not None:
            return float(f["adu_hibrido"])
    f = conn.execute("SELECT adu FROM posicionamiento_pt WHERE sku = ?", (sku,)).fetchone()
    return float(f["adu"]) if f and f["adu"] is not None else 0.0


def _evaluar_override_buffer(conn, sku: str, tor: float, toy: float, tog: float) -> dict:
    clave = str(sku).replace(".0", "").strip()
    cols_pos = {r[1] for r in conn.execute("PRAGMA table_info(posicionamiento_pt)").fetchall()}
    col_tor_rec = "tor_recomendado_sistema" if "tor_recomendado_sistema" in cols_pos else "tor AS tor_recomendado_sistema"
    col_toy_rec = "toy_recomendado_sistema" if "toy_recomendado_sistema" in cols_pos else "toy AS toy_recomendado_sistema"
    col_tog_rec = "tog_recomendado_sistema" if "tog_recomendado_sistema" in cols_pos else "tog AS tog_recomendado_sistema"
    fila = conn.execute(
        f"SELECT tor, toy, tog, nivel_rotacion, {col_tor_rec}, {col_toy_rec}, {col_tog_rec} "
        "FROM posicionamiento_pt WHERE sku = ?",
        (clave,),
    ).fetchone()

    if not fila:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' no encontrado en posicionamiento_pt.")

    adu = _adu_hibrido_sku(conn, clave)
    nivel_rotacion = (fila["nivel_rotacion"] or "").lower()
    tog_actual = fila["tog"] or 0.0

    dias_tor = round(tor / adu, 1) if adu > 0 else None
    dias_toy = round(toy / adu, 1) if adu > 0 else None
    dias_tog = round(tog / adu, 1) if adu > 0 else None

    cap = None
    if _tabla_existe(conn, "config_capacidad_bodega"):
        f = conn.execute("SELECT capacidad_pt_tm FROM config_capacidad_bodega WHERE id = 1").fetchone()
        if f and f["capacidad_pt_tm"] is not None:
            cap = f["capacidad_pt_tm"]

    tog_total_actual = conn.execute(
        "SELECT SUM(tog) FROM posicionamiento_pt WHERE es_mts = 1"
    ).fetchone()[0] or 0.0
    tog_total_nuevo = tog_total_actual - tog_actual + tog

    # La bodega se compara contra el inventario PROMEDIO esperado
    # (TOR + Verde/2, DDMRP), no contra el TOG: la Zona Amarilla es
    # producto en camino. Misma regla que clasificar_mts_mto.py.
    tot = conn.execute(
        "SELECT SUM(COALESCE(tor,0) + MAX(COALESCE(tog,0) - COALESCE(toy,0), 0) / 2.0) "
        "FROM posicionamiento_pt WHERE es_mts = 1"
    ).fetchone()[0] or 0.0
    prom_sku_actual = _inventario_promedio_ddmrp(fila["tor"] or 0.0, fila["toy"] or 0.0, tog_actual)
    prom_sku_nuevo = _inventario_promedio_ddmrp(tor, toy, tog)
    inv_prom_total_actual = float(tot)
    inv_prom_total_nuevo = inv_prom_total_actual - prom_sku_actual + prom_sku_nuevo

    # Antes esto era un if/elif: si excedía bodega, nunca se evaluaba
    # capital inmovilizado o riesgo de quiebre por rotación -- eran
    # problemas distintos (uno de espacio físico, otro financiero/de
    # servicio) y un SKU puede tener los dos a la vez. Ahora se evalúan
    # todos los criterios de forma independiente y se juntan los avisos;
    # el semáforo final es el peor de los que aplican (rojo > amarillo >
    # verde), pero el mensaje no oculta ninguno.
    avisos = []

    # El total de TOG del catálogo puede ya venir por encima del tope de
    # bodega SIN que este SKU en particular tenga nada que ver -- por
    # ejemplo, si otro ajuste manual se guardó después de la última
    # corrida de clasificar_mts_mto.py (que sí recorta todo para que
    # quepa) y nadie volvió a correr ese recorte. Si eso pasa, avisarle
    # al planificador "supera capacidad" sobre un valor que él ni tocó es
    # engañoso: parece que SU edición rompe algo, cuando el problema ya
    # existía antes de que abriera el modal. Se distinguen los dos casos:
    # exceso HEREDADO (ya existía, este SKU no lo causa) vs. exceso que
    # esta edición AGRAVA (el SKU sube y eso es lo que cruza el tope).
    excede_con_cambio = cap is not None and inv_prom_total_nuevo > cap
    # "Este SKU es el causante" solo si además de excederse, subir su TOG
    # es lo que empeora el total (no basta con tog > tog_actual: si el
    # total YA excedía antes de tocar este SKU, subirlo un poco más no lo
    # convierte en el culpable, sigue siendo el mismo problema heredado).
    lo_causa_este_ajuste = (excede_con_cambio and inv_prom_total_nuevo > inv_prom_total_actual
                            and inv_prom_total_actual <= cap)

    if lo_causa_este_ajuste:
        avisos.append(("rojo",
            f"Este ajuste hace que el inventario promedio esperado en bodega "
            f"(TOR + Verde/2) pase de {inv_prom_total_actual:,.0f} a "
            f"{inv_prom_total_nuevo:,.0f} TM, superando la capacidad de {cap:,.0f} TM."))
    elif excede_con_cambio:
        exceso_heredado = inv_prom_total_actual - cap
        avisos.append(("amarillo",
            f"El inventario promedio esperado ya está {exceso_heredado:,.0f} TM sobre la "
            f"capacidad de bodega desde antes -- "
            "no es por este ajuste, no bloquea guardarlo, pero el exceso general del catálogo "
            "requiere revisión aparte."))

    # Criterio único: ¿el TOG propuesto cubre el percentil de demanda real
    # (bootstrap empírico) que la propia economía del SKU (cuantil_objetivo
    # = Cu/(Cu+Co), igual que en clasificar_mts_mto.py) dice que debe
    # cubrir? Sustituye las reglas separadas de rotación alta/baja por
    # días fijos -- esas no tenían relación matemática entre sí; esto sí
    # está anclado al mismo newsvendor que ya gobierna TOR/TOY/TOG en todo
    # el pipeline, así que el semáforo del modal nunca contradice la
    # lógica que corre `clasificar_mts_mto.py`.
    avisos.extend(_evaluar_consistencia_newsvendor(conn, clave, tor, toy, tog))

    orden_severidad = {"rojo": 2, "amarillo": 1, "verde": 0}
    if avisos:
        semaforo = max(avisos, key=lambda a: orden_severidad[a[0]])[0]
        mensaje = " · ".join(m for _, m in avisos)
    else:
        semaforo = "verde"
        mensaje = "Consistente con su rotación actual."

    return {
        "sku": clave, "adu": round(adu, 2), "adu_fuente": "hibrido_mes_actual" if _tabla_existe(conn, "adu_mes_actual_hibrido") else "posicionamiento_pt",
        "nivel_rotacion": nivel_rotacion or None,
        "valores_actuales": {"tor": fila["tor"], "toy": fila["toy"], "tog": fila["tog"]},
        "valores_propuestos": {"tor": tor, "toy": toy, "tog": tog},
        "dias_cobertura": {"tor": dias_tor, "toy": dias_toy, "tog": dias_tog},
        "capacidad_pt_tm": cap,
        "tog_total_actual_tm": round(tog_total_actual, 1),
        "tog_total_propuesto_tm": round(tog_total_nuevo, 1),
        "inventario_promedio_total_actual_tm": round(inv_prom_total_actual, 1),
        "inventario_promedio_total_propuesto_tm": round(inv_prom_total_nuevo, 1),
        "criterio_bodega": "inventario_promedio_tor_mas_verde_medio",
        "semaforo": semaforo,
        "mensaje": mensaje,
        "avisos": [{"semaforo": s, "mensaje": m} for s, m in avisos],
        "recomendacion_sistema": {
            "tor": fila["tor_recomendado_sistema"], "toy": fila["toy_recomendado_sistema"], "tog": fila["tog_recomendado_sistema"],
        },
    }


class EvaluarOverrideBufferRequest(BaseModel):
    sku: str
    tor: float
    toy: float
    tog: float


@app.post("/api/v1/ddmrp/overrides/evaluar")
def evaluar_override_buffer(body: EvaluarOverrideBufferRequest):
    conn = _conn()
    try:
        return _evaluar_override_buffer(conn, body.sku, body.tor, body.toy, body.tog)
    finally:
        conn.close()


class GuardarOverrideBufferRequest(BaseModel):
    sku: str
    tor: float
    toy: float
    tog: float
    actualizado_por: str = ""


@app.post("/api/v1/ddmrp/overrides/guardar")
def guardar_override_buffer(body: GuardarOverrideBufferRequest):
    conn_ro = _conn()
    try:
        evaluacion = _evaluar_override_buffer(conn_ro, body.sku, body.tor, body.toy, body.tog)
    finally:
        conn_ro.close()

    clave = evaluacion["sku"]
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE posicionamiento_pt SET tor = ?, toy = ?, tog = ? WHERE sku = ?",
            (body.tor, body.toy, body.tog, clave),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"SKU '{body.sku}' no encontrado en posicionamiento_pt.")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ddmrp_overrides_auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                tor_anterior REAL, toy_anterior REAL, tog_anterior REAL,
                tor_nuevo REAL, toy_nuevo REAL, tog_nuevo REAL,
                semaforo TEXT, mensaje TEXT,
                actualizado_por TEXT, actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        va = evaluacion["valores_actuales"]
        conn.execute(
            "INSERT INTO ddmrp_overrides_auditoria "
            "(sku, tor_anterior, toy_anterior, tog_anterior, tor_nuevo, toy_nuevo, tog_nuevo, "
            "semaforo, mensaje, actualizado_por) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (clave, va["tor"], va["toy"], va["tog"], body.tor, body.toy, body.tog,
             evaluacion["semaforo"], evaluacion["mensaje"], body.actualizado_por or "planificador"),
        )
        conn.commit()
        return {"ok": True, "evaluacion": evaluacion}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


class FactoresManualesRequest(BaseModel):
    sku: str
    daf: float = 1.0
    zaf: float = 1.0
    ltaf: float = 1.0
    actualizado_por: str = ""


@app.get("/api/v1/ddmrp/factores-manuales/{sku}")
def obtener_factores_manuales(sku: str):
    clave = str(sku).replace(".0", "").strip()
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_factores_manuales"):
            return {"sku": clave, "daf": 1.0, "zaf": 1.0, "ltaf": 1.0}
        fila = conn.execute(
            "SELECT daf, zaf, ltaf FROM ddmrp_factores_manuales WHERE sku = ?", (clave,)
        ).fetchone()
        if not fila:
            return {"sku": clave, "daf": 1.0, "zaf": 1.0, "ltaf": 1.0}
        return {"sku": clave, "daf": fila["daf"], "zaf": fila["zaf"], "ltaf": fila["ltaf"]}
    finally:
        conn.close()


@app.post("/api/v1/ddmrp/factores-manuales")
def guardar_factores_manuales(body: FactoresManualesRequest):
    clave = str(body.sku).replace(".0", "").strip()
    for nombre, valor in (("daf", body.daf), ("zaf", body.zaf), ("ltaf", body.ltaf)):
        if valor < 0.5 or valor > 3.0:
            raise HTTPException(status_code=400, detail=f"{nombre.upper()} debe estar entre 0.5 y 3.0.")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ddmrp_factores_manuales (
                sku TEXT PRIMARY KEY,
                daf REAL NOT NULL DEFAULT 1.0,
                zaf REAL NOT NULL DEFAULT 1.0,
                ltaf REAL NOT NULL DEFAULT 1.0,
                actualizado_por TEXT,
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO ddmrp_factores_manuales (sku, daf, zaf, ltaf, actualizado_por, actualizado_en)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(sku) DO UPDATE SET
                daf = excluded.daf, zaf = excluded.zaf, ltaf = excluded.ltaf,
                actualizado_por = excluded.actualizado_por, actualizado_en = excluded.actualizado_en
        """, (clave, body.daf, body.zaf, body.ltaf, body.actualizado_por or "planificador"))
        conn.commit()
        return {"ok": True, "sku": clave, "daf": body.daf, "zaf": body.zaf, "ltaf": body.ltaf}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


class FactoresManualesMpRequest(BaseModel):
    sku: str
    zaf: float = 1.0
    ltaf: float = 1.0
    actualizado_por: str = ""


def _crear_tabla_factores_mp(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ddmrp_factores_manuales_mp (
            sku TEXT PRIMARY KEY,
            zaf REAL NOT NULL DEFAULT 1.0,
            ltaf REAL NOT NULL DEFAULT 1.0,
            actualizado_por TEXT,
            actualizado_en TEXT DEFAULT (datetime('now'))
        )
    """)


@app.get("/api/v1/ddmrp/factores-manuales-mp")
def listar_factores_manuales_mp():
    """Todas las MP con ZAF/LTAF distinto de 1 (para marcar la tabla de MP)."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_factores_manuales_mp"):
            return []
        return _df_to_records(pd.read_sql(
            "SELECT sku, zaf, ltaf, actualizado_por, actualizado_en "
            "FROM ddmrp_factores_manuales_mp ORDER BY sku", conn))
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/factores-manuales-mp/{sku}")
def obtener_factores_manuales_mp(sku: str):
    """ZAF y LTAF de una MP. El DAF de la MP no se edita: se hereda de los
    DAF de los PT por la fórmula (ver ajustar_buffer_mp.py) y se informa
    aquí como delta de ADU si ya se corrió el ajuste."""
    clave = str(sku).replace(".0", "").strip()
    conn = _conn()
    try:
        resp = {"sku": clave, "zaf": 1.0, "ltaf": 1.0,
                "delta_adu_daf_pt": 0.0, "pt_con_daf": None}
        if _tabla_existe(conn, "ddmrp_factores_manuales_mp"):
            fila = conn.execute(
                "SELECT zaf, ltaf FROM ddmrp_factores_manuales_mp WHERE sku = ?", (clave,)
            ).fetchone()
            if fila:
                resp["zaf"], resp["ltaf"] = fila["zaf"], fila["ltaf"]
        if _tabla_existe(conn, "ddmrp_ajuste_mp"):
            f2 = conn.execute(
                "SELECT delta_adu_daf_pt, pt_con_daf FROM ddmrp_ajuste_mp WHERE sku = ?", (clave,)
            ).fetchone()
            if f2:
                resp["delta_adu_daf_pt"] = f2["delta_adu_daf_pt"] or 0.0
                resp["pt_con_daf"] = f2["pt_con_daf"]
        return resp
    finally:
        conn.close()


@app.post("/api/v1/ddmrp/factores-manuales-mp")
def guardar_factores_manuales_mp(body: FactoresManualesMpRequest):
    clave = str(body.sku).replace(".0", "").strip()
    for nombre, valor in (("zaf", body.zaf), ("ltaf", body.ltaf)):
        if valor < 0.5 or valor > 3.0:
            raise HTTPException(status_code=400, detail=f"{nombre.upper()} debe estar entre 0.5 y 3.0.")
    conn = sqlite3.connect(DB_PATH)
    try:
        _crear_tabla_factores_mp(conn)
        conn.execute("""
            INSERT INTO ddmrp_factores_manuales_mp (sku, zaf, ltaf, actualizado_por, actualizado_en)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(sku) DO UPDATE SET
                zaf = excluded.zaf, ltaf = excluded.ltaf,
                actualizado_por = excluded.actualizado_por, actualizado_en = excluded.actualizado_en
        """, (clave, body.zaf, body.ltaf, body.actualizado_por or "planificador"))
        conn.commit()
        return {"ok": True, "sku": clave, "zaf": body.zaf, "ltaf": body.ltaf,
                "nota": "Se aplica al correr ajustar_buffer_mp.py (pipeline DDMRP)."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# Lead time manual de MP (corrección del dato real del proveedor) --
# independiente de LTAF: LTAF sigue siendo un FACTOR sobre el lead time
# base (ej. "este proveedor es 20% menos confiable, dale más colchón");
# esto es la corrección del DATO BASE en sí (ej. "el proveedor pasó de
# cotizar 90 a 95 días"). ajustar_buffer_mp.py aplica este valor a
# dlt_base ANTES de LTAF y del cálculo de zonas -- ver el bloque "Lead
# time manual" ahí. Mismo patrón evaluar/guardar/revertir que
# overrides-mp (TOR/TOY/TOG), pero sin semáforo: es un dato de catálogo,
# no una decisión de buffer a evaluar contra bodega o rotación.
# ------------------------------------------------------------------
class LeadTimeManualMpRequest(BaseModel):
    sku: str
    lead_time_dias: Optional[float] = None
    actualizado_por: str = ""


@app.get("/api/v1/ddmrp/lead-time-manual-mp")
def listar_lead_time_manual_mp():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_lead_time_manual_mp"):
            return []
        return _df_to_records(pd.read_sql(
            "SELECT sku, lead_time_dias, actualizado_por, actualizado_en "
            "FROM ddmrp_lead_time_manual_mp ORDER BY sku", conn))
    finally:
        conn.close()


@app.get("/api/v1/ddmrp/lead-time-manual-mp/{sku}")
def obtener_lead_time_manual_mp(sku: str):
    """lead_time_manual_dias: el override vigente (None si no hay).
    lead_time_sistema_dias: el dlt_base que calculó el sistema ANTES de
    cualquier override (dlt_base_sistema en ddmrp_buffer_mp, guardado por
    ajustar_buffer_mp.py) -- para que el modal muestre "Sistema: X días"
    y pueda revertir a ese valor."""
    clave = str(sku).replace(".0", "").strip()
    conn = _conn()
    try:
        manual = None
        if _tabla_existe(conn, "ddmrp_lead_time_manual_mp"):
            f = conn.execute(
                "SELECT lead_time_dias FROM ddmrp_lead_time_manual_mp WHERE sku = ?", (clave,)
            ).fetchone()
            manual = f[0] if f else None
        sistema = None
        if _tabla_existe(conn, "ddmrp_buffer_mp"):
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ddmrp_buffer_mp)").fetchall()}
            col_sistema = "dlt_base_sistema" if "dlt_base_sistema" in cols else "dlt_dias"
            f = conn.execute(
                f"SELECT {col_sistema} FROM ddmrp_buffer_mp WHERE sku = ?", (clave,)
            ).fetchone()
            sistema = f[0] if f else None
        return {"sku": clave, "lead_time_manual_dias": manual, "lead_time_sistema_dias": sistema}
    finally:
        conn.close()


@app.post("/api/v1/ddmrp/lead-time-manual-mp")
def guardar_lead_time_manual_mp(body: LeadTimeManualMpRequest):
    """lead_time_dias vacío o 0 = revertir (borra el override; la próxima
    corrida de ajustar_buffer_mp.py vuelve a usar el lead time del
    sistema). Se aplica recién en la siguiente corrida del pipeline, no
    al guardar -- igual que ZAF/LTAF y los overrides de TOR/TOY/TOG."""
    clave = str(body.sku).replace(".0", "").strip()
    if body.lead_time_dias is not None and body.lead_time_dias <= 0:
        raise HTTPException(status_code=400, detail="El lead time debe ser mayor que 0.")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ddmrp_lead_time_manual_mp (
                sku TEXT PRIMARY KEY,
                lead_time_dias REAL NOT NULL,
                actualizado_por TEXT,
                actualizado_en TEXT DEFAULT (datetime('now'))
            )
        """)
        if not body.lead_time_dias:
            conn.execute("DELETE FROM ddmrp_lead_time_manual_mp WHERE sku = ?", (clave,))
        else:
            conn.execute("""
                INSERT INTO ddmrp_lead_time_manual_mp (sku, lead_time_dias, actualizado_por, actualizado_en)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(sku) DO UPDATE SET
                    lead_time_dias = excluded.lead_time_dias,
                    actualizado_por = excluded.actualizado_por,
                    actualizado_en = excluded.actualizado_en
            """, (clave, body.lead_time_dias, body.actualizado_por or "planificador"))
        conn.commit()
        return {"ok": True, "sku": clave, "lead_time_dias": body.lead_time_dias or None,
                "nota": "Se aplica al correr ajustar_buffer_mp.py (pipeline DDMRP)."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# Ajuste manual de buffer MP (TOR/TOY/TOG) -- mismo flujo que PT:
# evaluar -> guardar; y "revertir" vuelve a la recomendación DDMRP.
# ajustar_buffer_mp.py reaplica el ajuste vigente en cada corrida.
# ------------------------------------------------------------------
class OverrideMpRequest(BaseModel):
    sku: str
    tor: float
    toy: float
    tog: float
    actualizado_por: str = ""


class RevertirMpRequest(BaseModel):
    sku: str
    actualizado_por: str = ""


def _cols_tabla(conn, tabla):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}


def _capacidad_mp(conn):
    if _tabla_existe(conn, "config_capacidad_bodega"):
        f = conn.execute("SELECT capacidad_mp_tm FROM config_capacidad_bodega WHERE id = 1").fetchone()
        if f and f["capacidad_mp_tm"] is not None:
            return float(f["capacidad_mp_tm"])
    return None


def _evaluar_override_mp(conn, sku: str, tor: float, toy: float, tog: float) -> dict:
    clave = str(sku).replace(".0", "").strip()
    if not _tabla_existe(conn, "ddmrp_buffer_mp"):
        raise HTTPException(status_code=503, detail="Tabla ddmrp_buffer_mp no disponible.")
    cols = _cols_tabla(conn, "ddmrp_buffer_mp")
    rec = [c if c in cols else f"{c.replace('_recomendado_sistema', '')} AS {c}"
           for c in ("tor_recomendado_sistema", "toy_recomendado_sistema", "tog_recomendado_sistema")]
    fila = conn.execute(
        f"SELECT sku, adu, estado, tor, toy, tog, {', '.join(rec)} FROM ddmrp_buffer_mp WHERE sku = ?",
        (clave,)).fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail=f"MP '{sku}' no encontrada en ddmrp_buffer_mp.")

    avisos = []
    if min(tor, toy, tog) < 0 or not (tor <= toy <= tog):
        avisos.append(("rojo", "Los valores deben cumplir 0 ≤ TOR ≤ TOY ≤ TOG."))

    adu = float(fila["adu"] or 0)
    dias = {k: (round(v / adu, 1) if adu > 0 else None) for k, v in (("tor", tor), ("toy", toy), ("tog", tog))}

    def prom(a, b, c):
        a, b, c = (float(x or 0) for x in (a, b, c))
        return a + max(c - b, 0) / 2.0

    total_actual = conn.execute(
        "SELECT SUM(COALESCE(tor,0) + MAX(COALESCE(tog,0) - COALESCE(toy,0), 0) / 2.0) "
        "FROM ddmrp_buffer_mp WHERE estado = 'ok'").fetchone()[0] or 0.0
    propio_actual = prom(fila["tor"], fila["toy"], fila["tog"]) if fila["estado"] == "ok" else 0.0
    total_nuevo = total_actual - propio_actual + prom(tor, toy, tog)
    cap = _capacidad_mp(conn)
    if cap is not None and total_nuevo > cap:
        if total_actual <= cap:
            avisos.append(("rojo", f"Con este ajuste el inventario promedio MP (TOR + Verde/2) pasa de "
                                   f"{total_actual:,.0f} a {total_nuevo:,.0f} TM y supera la bodega "
                                   f"de {cap:,.0f} TM."))
        else:
            avisos.append(("amarillo", f"La bodega MP ya estaba {total_actual - cap:,.0f} TM sobre su "
                                       f"capacidad antes de este ajuste."))

    tor_rec = fila["tor_recomendado_sistema"]
    if tor_rec is not None and tor < float(tor_rec):
        avisos.append(("amarillo", f"TOR por debajo de la recomendación DDMRP ({float(tor_rec):,.0f} TM): "
                                   f"menos protección contra el lead time y la variabilidad del proveedor."))
    if fila["estado"] != "ok":
        avisos.append(("amarillo", "El sistema no pudo calcular buffer para esta MP: este ajuste manual "
                                   "será su único buffer."))

    orden = {"rojo": 2, "amarillo": 1, "verde": 0}
    semaforo = max(avisos, key=lambda a: orden[a[0]])[0] if avisos else "verde"
    return {
        "sku": clave, "adu": round(adu, 2),
        "valores_actuales": {"tor": fila["tor"], "toy": fila["toy"], "tog": fila["tog"]},
        "valores_propuestos": {"tor": tor, "toy": toy, "tog": tog},
        "recomendacion_sistema": {"tor": fila["tor_recomendado_sistema"],
                                  "toy": fila["toy_recomendado_sistema"],
                                  "tog": fila["tog_recomendado_sistema"]},
        "dias_cobertura": dias,
        "capacidad_mp_tm": cap,
        "inventario_promedio_total_actual_tm": round(total_actual, 1),
        "inventario_promedio_total_propuesto_tm": round(total_nuevo, 1),
        "semaforo": semaforo,
        "mensaje": " · ".join(m for _, m in avisos) if avisos else "Consistente con la recomendación DDMRP.",
        "avisos": [{"semaforo": a, "mensaje": m} for a, m in avisos],
    }


def _crear_tablas_override_mp(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS ddmrp_overrides_mp (
        sku TEXT PRIMARY KEY, tor REAL NOT NULL, toy REAL NOT NULL, tog REAL NOT NULL,
        actualizado_por TEXT, actualizado_en TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ddmrp_overrides_mp_auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL, accion TEXT,
        tor_anterior REAL, toy_anterior REAL, tog_anterior REAL,
        tor_nuevo REAL, toy_nuevo REAL, tog_nuevo REAL,
        actualizado_por TEXT, actualizado_en TEXT DEFAULT (datetime('now')))""")
    cols = _cols_tabla(conn, "ddmrp_buffer_mp")
    if "ajuste_manual" not in cols:
        conn.execute("ALTER TABLE ddmrp_buffer_mp ADD COLUMN ajuste_manual INTEGER DEFAULT 0")
    for c in ("tor", "toy", "tog"):
        if f"{c}_recomendado_sistema" not in cols:
            conn.execute(f"ALTER TABLE ddmrp_buffer_mp ADD COLUMN {c}_recomendado_sistema REAL")
            conn.execute(f"UPDATE ddmrp_buffer_mp SET {c}_recomendado_sistema = {c}")
    if "estado_sistema" not in cols:
        conn.execute("ALTER TABLE ddmrp_buffer_mp ADD COLUMN estado_sistema TEXT")
        conn.execute("UPDATE ddmrp_buffer_mp SET estado_sistema = estado")


@app.get("/api/v1/ddmrp/overrides-mp")
def listar_overrides_mp():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "ddmrp_overrides_mp"):
            return []
        return _df_to_records(pd.read_sql("SELECT * FROM ddmrp_overrides_mp ORDER BY sku", conn))
    finally:
        conn.close()


@app.post("/api/v1/ddmrp/overrides-mp/evaluar")
def evaluar_override_mp(body: OverrideMpRequest):
    conn = _conn()
    try:
        return _evaluar_override_mp(conn, body.sku, body.tor, body.toy, body.tog)
    finally:
        conn.close()


@app.post("/api/v1/ddmrp/overrides-mp/guardar")
def guardar_override_mp(body: OverrideMpRequest):
    conn_ro = _conn()
    try:
        ev = _evaluar_override_mp(conn_ro, body.sku, body.tor, body.toy, body.tog)
    finally:
        conn_ro.close()
    if min(body.tor, body.toy, body.tog) < 0 or not (body.tor <= body.toy <= body.tog):
        raise HTTPException(status_code=400, detail="Los valores deben cumplir 0 ≤ TOR ≤ TOY ≤ TOG.")
    clave = ev["sku"]
    conn = sqlite3.connect(DB_PATH)
    try:
        _crear_tablas_override_mp(conn)
        conn.execute("""INSERT INTO ddmrp_overrides_mp (sku, tor, toy, tog, actualizado_por, actualizado_en)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(sku) DO UPDATE SET tor = excluded.tor, toy = excluded.toy, tog = excluded.tog,
                actualizado_por = excluded.actualizado_por, actualizado_en = excluded.actualizado_en""",
            (clave, body.tor, body.toy, body.tog, body.actualizado_por or "planificador"))
        conn.execute("""UPDATE ddmrp_buffer_mp SET tor = ?, toy = ?, tog = ?, green = ?,
            estado = 'ok', ajuste_manual = 1 WHERE sku = ?""",
            (body.tor, body.toy, body.tog, body.tog - body.toy, clave))
        va = ev["valores_actuales"]
        conn.execute("""INSERT INTO ddmrp_overrides_mp_auditoria (sku, accion, tor_anterior, toy_anterior,
            tog_anterior, tor_nuevo, toy_nuevo, tog_nuevo, actualizado_por)
            VALUES (?, 'ajuste', ?, ?, ?, ?, ?, ?, ?)""",
            (clave, va["tor"], va["toy"], va["tog"], body.tor, body.toy, body.tog,
             body.actualizado_por or "planificador"))
        conn.commit()
        return {"ok": True, "evaluacion": ev,
                "nota": "El flujo neto de MP se actualiza al correr calcular_nfp.py."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.post("/api/v1/ddmrp/overrides-mp/revertir")
def revertir_override_mp(body: RevertirMpRequest):
    """Quita el ajuste manual y vuelve a la recomendación DDMRP del sistema."""
    clave = str(body.sku).replace(".0", "").strip()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _crear_tablas_override_mp(conn)
        fila = conn.execute("""SELECT tor, toy, tog, tor_recomendado_sistema AS tr,
            toy_recomendado_sistema AS yr, tog_recomendado_sistema AS gr, estado_sistema
            FROM ddmrp_buffer_mp WHERE sku = ?""", (clave,)).fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail=f"MP '{body.sku}' no encontrada.")
        conn.execute("DELETE FROM ddmrp_overrides_mp WHERE sku = ?", (clave,))
        green = (fila["gr"] - fila["yr"]) if fila["gr"] is not None and fila["yr"] is not None else None
        conn.execute("""UPDATE ddmrp_buffer_mp SET tor = ?, toy = ?, tog = ?, green = ?,
            estado = COALESCE(?, estado), ajuste_manual = 0 WHERE sku = ?""",
            (fila["tr"], fila["yr"], fila["gr"], green, fila["estado_sistema"], clave))
        conn.execute("""INSERT INTO ddmrp_overrides_mp_auditoria (sku, accion, tor_anterior, toy_anterior,
            tog_anterior, tor_nuevo, toy_nuevo, tog_nuevo, actualizado_por)
            VALUES (?, 'revertido', ?, ?, ?, ?, ?, ?, ?)""",
            (clave, fila["tor"], fila["toy"], fila["tog"], fila["tr"], fila["yr"], fila["gr"],
             body.actualizado_por or "planificador"))
        conn.commit()
        return {"ok": True, "sku": clave,
                "valores": {"tor": fila["tr"], "toy": fila["yr"], "tog": fila["gr"]}}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ------------------------------------------------------------------
# Plan de compras MP (plan_compras_mp.py). Única fuente de las señales de
# compra de MP: Centro de decisiones, Formulación e Inventario Estratégico.
# ------------------------------------------------------------------
@app.get("/api/v1/compras-mp/plan")
def plan_compras_mp():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "plan_compras_mp"):
            raise HTTPException(status_code=503, detail="Correr plan_compras_mp.py.")
        plan = pd.read_sql("SELECT * FROM plan_compras_mp", conn)
        avisos = (pd.read_sql("SELECT * FROM plan_compras_avisos", conn)
                  if _tabla_existe(conn, "plan_compras_avisos") else pd.DataFrame())
        conteo = plan["senal"].value_counts().to_dict() if not plan.empty else {}
        return {
            "fecha_calculo": plan["fecha_calculo"].max() if not plan.empty else None,
            "conteo": conteo,
            "n_compra_detenida": int(conteo.get("NO_COMPRAR_HASTA", 0) + conteo.get("RIESGO_VENCIMIENTO", 0)),
            "plan": _df_to_records(plan),
            "avisos": _df_to_records(avisos),
        }
    finally:
        conn.close()


@app.get("/api/v1/compras-mp/proyeccion/{sku}")
def proyeccion_compras_mp(sku: str):
    clave = str(sku).replace(".0", "").strip()
    conn = _conn()
    try:
        if not _tabla_existe(conn, "proyeccion_mp_diaria"):
            raise HTTPException(status_code=503, detail="Correr plan_compras_mp.py.")
        proy = pd.read_sql("SELECT * FROM proyeccion_mp_diaria WHERE sku = ? ORDER BY fecha",
                           conn, params=(clave,))
        sug = pd.read_sql("SELECT * FROM compras_sugeridas_mp WHERE sku = ? ORDER BY fecha_lanzar",
                          conn, params=(clave,))
        oc = pd.DataFrame()
        if _tabla_existe(conn, "oc_importacion_mp"):
            oc = pd.read_sql("SELECT * FROM oc_importacion_mp WHERE sku_mp = ? ORDER BY fecha_llegada",
                             conn, params=(clave,))
        dem = pd.DataFrame()
        if _tabla_existe(conn, "demanda_mp_diaria"):
            dem = pd.read_sql("SELECT fecha, demanda_tm, fuente FROM demanda_mp_diaria WHERE sku = ? "
                              "ORDER BY fecha", conn, params=(clave,))
        resumen = pd.read_sql("SELECT * FROM plan_compras_mp WHERE sku = ?", conn, params=(clave,))
        return {"sku": clave,
                "resumen": _df_to_records(resumen)[0] if not resumen.empty else None,
                "proyeccion": _df_to_records(proy), "compras_sugeridas": _df_to_records(sug),
                "oc_en_camino": _df_to_records(oc), "demanda": _df_to_records(dem)}
    finally:
        conn.close()


@app.get("/api/v1/compras-mp/exportar")
def exportar_plan_compras_mp():
    """Excel para Compras: hoja Plan (una fila por MP) + Compras sugeridas + Avisos."""
    import io
    from fastapi.responses import StreamingResponse
    conn = _conn()
    try:
        if not _tabla_existe(conn, "plan_compras_mp"):
            raise HTTPException(status_code=503, detail="Correr plan_compras_mp.py.")
        plan = pd.read_sql("SELECT * FROM plan_compras_mp", conn)
        sug = pd.read_sql("SELECT * FROM compras_sugeridas_mp", conn)
        av = (pd.read_sql("SELECT * FROM plan_compras_avisos", conn)
              if _tabla_existe(conn, "plan_compras_avisos") else pd.DataFrame())
    finally:
        conn.close()
    etiquetas = {"COMPRAR_HOY": "Comprar hoy", "COMPRAR_ANTES_DE": "Comprar a más tardar",
                 "CUBIERTO": "Cubierto", "NO_COMPRAR_HASTA": "No comprar hasta",
                 "RIESGO_VENCIMIENTO": "Riesgo de vencimiento"}
    hoja = plan.assign(senal=plan["senal"].map(etiquetas).fillna(plan["senal"]))[[
        "sku", "descripcion", "senal", "mensaje", "fecha_referencia", "cantidad_sugerida_tm",
        "fecha_llegada_sugerida", "en_planta_tm", "en_camino_tm", "cobertura_dias",
        "lead_time_dias", "adu", "fecha_quiebre_sin_comprar"]].rename(columns={
        "sku": "Código MP", "descripcion": "Descripción", "senal": "Acción", "mensaje": "Detalle",
        "fecha_referencia": "Fecha", "cantidad_sugerida_tm": "Cantidad sugerida (TM)",
        "fecha_llegada_sugerida": "Llegada estimada", "en_planta_tm": "En planta (TM)",
        "en_camino_tm": "En camino (TM)", "cobertura_dias": "Cobertura (días)",
        "lead_time_dias": "Lead time (días)", "adu": "Consumo diario (TM)",
        "fecha_quiebre_sin_comprar": "Quiebre si no se compra"})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        hoja.to_excel(w, sheet_name="Plan de compras", index=False)
        sug.to_excel(w, sheet_name="Compras sugeridas", index=False)
        if not av.empty:
            av.to_excel(w, sheet_name="Avisos", index=False)
    buf.seek(0)
    nombre = f"plan_compras_mp_{plan['fecha_calculo'].max() if not plan.empty else 'sin_fecha'}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


class VidaUtilMpRequest(BaseModel):
    sku: str
    vida_util_dias: Optional[float] = None
    actualizado_por: str = ""


@app.get("/api/v1/config/vida-util-mp")
def listar_vida_util_mp():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "config_vida_util_mp"):
            return []
        return _df_to_records(pd.read_sql("SELECT * FROM config_vida_util_mp ORDER BY sku", conn))
    finally:
        conn.close()


@app.post("/api/v1/config/vida-util-mp")
def guardar_vida_util_mp(body: VidaUtilMpRequest):
    """Vida útil opcional por MP. Vacío o 0 = se borra (la alerta no aplica)."""
    clave = str(body.sku).replace(".0", "").strip()
    if body.vida_util_dias is not None and body.vida_util_dias < 0:
        raise HTTPException(status_code=400, detail="La vida útil no puede ser negativa.")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS config_vida_util_mp (
            sku TEXT PRIMARY KEY, vida_util_dias REAL,
            actualizado_por TEXT, actualizado_en TEXT DEFAULT (datetime('now')))""")
        if not body.vida_util_dias:
            conn.execute("DELETE FROM config_vida_util_mp WHERE sku = ?", (clave,))
        else:
            conn.execute("""INSERT INTO config_vida_util_mp (sku, vida_util_dias, actualizado_por, actualizado_en)
                VALUES (?, ?, ?, datetime('now')) ON CONFLICT(sku) DO UPDATE SET
                vida_util_dias = excluded.vida_util_dias, actualizado_por = excluded.actualizado_por,
                actualizado_en = excluded.actualizado_en""",
                (clave, body.vida_util_dias, body.actualizado_por or "planificador"))
        conn.commit()
        return {"ok": True, "sku": clave, "vida_util_dias": body.vida_util_dias or None,
                "nota": "Se aplica al correr plan_compras_mp.py."}
    finally:
        conn.close()


class TipoProductoRequest(BaseModel):
    sku: str
    tipo: str


class SetupTiempoRequest(BaseModel):
    tipo_origen: str
    tipo_destino: str
    minutos: float


class LoteMaximoRequest(BaseModel):
    lote_maximo_tm: Optional[float] = None


def _crear_tablas_config(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS configuracion_tipo_producto (sku TEXT PRIMARY KEY, tipo TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS configuracion_setup_tiempos (
        tipo_origen TEXT, tipo_destino TEXT, tiempo_horas REAL,
        PRIMARY KEY(tipo_origen, tipo_destino))""")
    conn.execute("CREATE TABLE IF NOT EXISTS configuracion_general (clave TEXT PRIMARY KEY, valor TEXT)")


@app.get("/api/v1/config/tipos-producto")
def obtener_tipos_producto():
    with sqlite3.connect(DB_PATH) as conn:
        _crear_tablas_config(conn)
        filas = conn.execute("SELECT sku, tipo FROM configuracion_tipo_producto").fetchall()
    return [{"sku": f[0], "tipo": f[1]} for f in filas]


@app.post("/api/v1/config/tipos-producto")
def guardar_tipo_producto(req: TipoProductoRequest):
    with sqlite3.connect(DB_PATH) as conn:
        _crear_tablas_config(conn)
        conn.execute("""INSERT INTO configuracion_tipo_producto (sku, tipo) VALUES (?, ?)
            ON CONFLICT(sku) DO UPDATE SET tipo = excluded.tipo""",
            (req.sku, req.tipo.strip().upper()))
        conn.commit()
    return {"ok": True}


@app.delete("/api/v1/config/tipos-producto/{sku}")
def eliminar_tipo_producto(sku: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM configuracion_tipo_producto WHERE sku = ?", (sku,))
        conn.commit()
    return {"ok": True}


@app.get("/api/v1/config/setup-tiempos")
def obtener_setup_tiempos():
    with sqlite3.connect(DB_PATH) as conn:
        _crear_tablas_config(conn)
        filas = conn.execute("SELECT tipo_origen, tipo_destino, tiempo_horas FROM configuracion_setup_tiempos").fetchall()
    return [{"tipo_origen": f[0], "tipo_destino": f[1], "minutos": round(f[2] * 60, 1)} for f in filas]


@app.post("/api/v1/config/setup-tiempos")
def guardar_setup_tiempo(req: SetupTiempoRequest):
    with sqlite3.connect(DB_PATH) as conn:
        _crear_tablas_config(conn)
        conn.execute("""INSERT INTO configuracion_setup_tiempos (tipo_origen, tipo_destino, tiempo_horas)
            VALUES (?, ?, ?)
            ON CONFLICT(tipo_origen, tipo_destino) DO UPDATE SET tiempo_horas = excluded.tiempo_horas""",
            (req.tipo_origen, req.tipo_destino, req.minutos / 60.0))
        conn.commit()
    return {"ok": True}


@app.delete("/api/v1/config/setup-tiempos/{origen}/{destino}")
def eliminar_setup_tiempo(origen: str, destino: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM configuracion_setup_tiempos WHERE tipo_origen = ? AND tipo_destino = ?",
            (origen, destino))
        conn.commit()
    return {"ok": True}


@app.get("/api/v1/config/lote-maximo")
def obtener_lote_maximo():
    with sqlite3.connect(DB_PATH) as conn:
        _crear_tablas_config(conn)
        fila = conn.execute("SELECT valor FROM configuracion_general WHERE clave = 'lote_maximo_tm'").fetchone()
    return {"lote_maximo_tm": float(fila[0]) if fila and fila[0] not in (None, '') else None}


@app.post("/api/v1/config/lote-maximo")
def guardar_lote_maximo(req: LoteMaximoRequest):
    with sqlite3.connect(DB_PATH) as conn:
        _crear_tablas_config(conn)
        valor = str(req.lote_maximo_tm) if req.lote_maximo_tm is not None else ''
        conn.execute("""INSERT INTO configuracion_general (clave, valor) VALUES ('lote_maximo_tm', ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor""", (valor,))
        conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# FLETE: en qué planta producir (calcular_flete_pedidos.py + optimizar_producción.py)
# ------------------------------------------------------------------
@app.get("/api/v1/flete/resumen")
def flete_resumen():
    """Resumen del flete del plan vigente: TM por motivo de planta, flete y sobrecosto (solo corridas PEDIDO)."""
    conn = _conn()
    try:
        out = {"fecha_calculo": None, "por_estado": [], "por_motivo_planta": [],
               "flete_estimado_usd": None, "sobrecosto_usd": None, "valor_max_elegir_bien_usd": None}
        if _tabla_existe(conn, "flete_pedidos"):
            fp = pd.read_sql("SELECT * FROM flete_pedidos", conn)
            if not fp.empty:
                out["fecha_calculo"] = str(fp["fecha_calculo"].max())
                out["por_estado"] = _df_to_records(
                    fp.groupby("estado", as_index=False)["tm_faltante"].sum().round(1))
                ok = fp[(fp["estado"] == "ELEGIBLE") & (fp["tm_faltante"] > 0)
                        & fp["usd_tm_DURAN"].notna() & fp["usd_tm_GUAYAQUIL"].notna()]
                out["valor_max_elegir_bien_usd"] = round(float(
                    ((ok["usd_tm_GUAYAQUIL"] - ok["usd_tm_DURAN"]).abs() * ok["tm_faltante"]).sum()), 0)
        if _tabla_existe(conn, "plan_produccion"):
            pl = pd.read_sql("SELECT * FROM plan_produccion", conn)
            if "motivo_planta" in pl.columns and not pl.empty:
                ped = pl[pl["motivo"] == "PEDIDO"]
                out["por_motivo_planta"] = _df_to_records(
                    ped.groupby("motivo_planta", as_index=False)["tm"].sum().round(1))
                con_f = ped[ped["flete_usd_tm"].notna()]
                out["flete_estimado_usd"] = round(float((con_f["flete_usd_tm"] * con_f["tm"]).sum()), 0)
                out["sobrecosto_usd"] = round(float(ped["sobrecosto_flete_usd"].fillna(0).sum()), 0)
        return out
    finally:
        conn.close()


@app.get("/api/v1/flete/pedidos")
def flete_pedidos(sku: str = Query(None), estado: str = Query(None)):
    """Detalle por línea de pedido: región, USD/TM desde cada planta, planta más barata y estado."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "flete_pedidos"):
            return []
        q, prm = "SELECT * FROM flete_pedidos WHERE 1=1", []
        if sku:
            q += " AND sku = ?"; prm.append(str(sku).replace(".0", "").strip())
        if estado:
            q += " AND estado = ?"; prm.append(estado)
        return _df_to_records(pd.read_sql(q + " ORDER BY fecha_entrega, sku", conn, params=prm))
    finally:
        conn.close()


@app.get("/api/v1/flete/avisos")
def flete_avisos():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "flete_avisos"):
            return []
        return _df_to_records(pd.read_sql("SELECT * FROM flete_avisos", conn))
    finally:
        conn.close()


@app.get("/api/v1/config/flete-regiones")
def listar_flete_regiones():
    """Ship To terrestres con su región (DESTINO), cómo se encontró y la corrección manual si existe."""
    conn = _conn()
    try:
        if not _tabla_existe(conn, "flete_ship_to_region"):
            return {"ship_to": [], "destinos": []}
        st = pd.read_sql("SELECT * FROM flete_ship_to_region", conn)
        if _tabla_existe(conn, "flete_region_override"):
            ov = pd.read_sql("SELECT ship_to, destino AS destino_manual, actualizado_por, actualizado_en "
                             "FROM flete_region_override", conn)
            st = st.merge(ov, on="ship_to", how="left")
        dest = (pd.read_sql("SELECT destino FROM flete_destinos ORDER BY destino", conn)["destino"].tolist()
                if _tabla_existe(conn, "flete_destinos") else [])
        st = st.sort_values(["region", "ship_to_desc"], key=lambda c: c.astype(str))
        return {"ship_to": _df_to_records(st), "destinos": dest}
    finally:
        conn.close()


class FleteRegionRequest(BaseModel):
    ship_to: str
    destino: Optional[str] = None      # vacío = quitar la corrección manual
    actualizado_por: Optional[str] = None


@app.post("/api/v1/config/flete-regiones")
def guardar_flete_region(body: FleteRegionRequest):
    clave = str(body.ship_to).replace(".0", "").strip().lstrip("0")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS flete_region_override (
            ship_to TEXT PRIMARY KEY, destino TEXT NOT NULL,
            actualizado_por TEXT, actualizado_en TEXT)""")
        destino = (body.destino or "").strip().upper()
        if not destino:
            conn.execute("DELETE FROM flete_region_override WHERE ship_to = ?", (clave,))
        else:
            if _tabla_existe(conn, "flete_destinos"):
                validos = {r[0] for r in conn.execute("SELECT destino FROM flete_destinos")}
                if validos and destino not in validos:
                    raise HTTPException(status_code=400,
                                        detail=f"'{destino}' no es un DESTINO del tabulador.")
            conn.execute("""INSERT INTO flete_region_override (ship_to, destino, actualizado_por, actualizado_en)
                VALUES (?, ?, ?, datetime('now')) ON CONFLICT(ship_to) DO UPDATE SET
                destino = excluded.destino, actualizado_por = excluded.actualizado_por,
                actualizado_en = excluded.actualizado_en""",
                (clave, destino, body.actualizado_por or "planificador"))
        conn.commit()
        return {"ok": True, "ship_to": clave, "destino": destino or None,
                "nota": "Se aplica al correr calcular_flete_pedidos.py."}
    finally:
        conn.close()


class TemporadaInvernal(BaseModel):
    fecha_inicio: str
    fecha_fin: str
    activo: int = 1


class GuardarTemporadasRequest(BaseModel):
    temporadas: List[TemporadaInvernal]


@app.get("/api/v1/config/temporada-invernal")
def listar_temporada_invernal():
    conn = _conn()
    try:
        if not _tabla_existe(conn, "config_temporada_invernal"):
            return {"temporadas": []}
        return {"temporadas": _df_to_records(pd.read_sql(
            "SELECT fecha_inicio, fecha_fin, activo FROM config_temporada_invernal ORDER BY fecha_inicio", conn))}
    finally:
        conn.close()


@app.post("/api/v1/config/temporada-invernal")
def guardar_temporada_invernal(body: GuardarTemporadasRequest):
    """Reemplaza las temporadas de lluvia. En esas fechas de despacho se usa la RESTRICCION INVERNAL."""
    for t in body.temporadas:
        try:
            ini, fin = pd.Timestamp(t.fecha_inicio), pd.Timestamp(t.fecha_fin)
        except Exception:
            raise HTTPException(status_code=400, detail="Fechas inválidas (usar AAAA-MM-DD).")
        if fin < ini:
            raise HTTPException(status_code=400, detail="La fecha fin no puede ser anterior a la de inicio.")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS config_temporada_invernal (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_inicio TEXT NOT NULL, fecha_fin TEXT NOT NULL,
            activo INTEGER DEFAULT 1, actualizado_en TEXT)""")
        conn.execute("DELETE FROM config_temporada_invernal")
        conn.executemany("INSERT INTO config_temporada_invernal (fecha_inicio, fecha_fin, activo, actualizado_en) "
                         "VALUES (?, ?, ?, datetime('now'))",
                         [(t.fecha_inicio, t.fecha_fin, int(t.activo)) for t in body.temporadas])
        conn.commit()
        return {"ok": True, "guardadas": len(body.temporadas),
                "nota": "Se aplica al correr calcular_flete_pedidos.py."}
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT)