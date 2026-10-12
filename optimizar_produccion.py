"""
optimizar_producción.py
Cargill Ecuador - Aquanutrition
Módulo 4 - Plan de producción (MILP)
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import numpy as np
import sqlite3

# Windows por defecto usa cp1252 para la consola, que no soporta
# emojis (⚠️) ni algunos caracteres especiales -- cualquier print con
# esos caracteres tumbaba el script a mitad de corrida, dejando el plan
# desactualizado sin ningún aviso claro de que había fallado. Forzar
# UTF-8 aquí evita que vuelva a pasar, sin importar qué se imprima.
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import DB_PATH, LEAD_TIME_PRODUCCION_DIAS
from pedidos_manuales import cargar_pedidos_manuales

try:
    from config import CAPACIDAD_BODEGA_PT_TM
except ImportError:
    CAPACIDAD_BODEGA_PT_TM = None

try:
    import pulp
except ImportError:
    print("[ERROR] Falta PuLP: pip install pulp")
    sys.exit(1)

HORIZONTE_DIAS = 14
HORAS_DIA_LINEA = 20.0
PENALIZACION_EXCESO_BODEGA_USD_TM = 300

# Pesos del objetivo (ficticios, solo ordenan prioridades -- NO son plata real):
#   PESO_PEDIDO_FIRME (10 000) >> bodega (300) >> PESO_BUFFER (100)
# Así un pedido en firme SIEMPRE le gana a una reposición de buffer, y el
# buffer nunca justifica pasarse de la capacidad de bodega.
PESO_PEDIDO_FIRME = 10000.0
PESO_BUFFER = 100.0

# Flete (calcular_flete_pedidos.py -> flete_sku_planta): DESEMPATE por debajo del buffer.
# Se penaliza solo el SOBRECOSTO de producir un SKU en la planta más cara (USD/TM sobre
# la planta más barata), nunca el hecho de producir. El peso se ajusta en cada corrida
# para que la penalización máxima por TM quede SIEMPRE por debajo del premio mínimo del
# buffer (PESO_BUFFER x urgencia mínima 0.05 = 5): el flete nunca deja un buffer sin
# reponer ni atrasa un pedido; solo elige planta cuando lo demás empata.
PESO_FLETE_MAX = 0.1
TOPE_PENALIZACION_FLETE_TM = 4.0

HORAS_DIA_LINEA_REAL = {
    "DURAN|L1": 22.0, "DURAN|L2": 20.0, "DURAN|L3": 20.0,
    "GUAYAQUIL|L1": 20.0, "GUAYAQUIL|L3": 20.0, "GUAYAQUIL|L4": 15.0,
    "GUAYAQUIL|L5": 22.0, "GUAYAQUIL|L6": 22.0,
}
# Lote mínimo "normal" por corrida. Se relaja por SKU:
#   MTS -> min(50, max(TOG-NFP, pedido neto))
#   MTO -> min(50, pedido neto)   (el MTO produce SOLO lo del pedido)
LOTE_MINIMO_TM = 50.0

CICLO_REPOSICION_BAJA_ROTACION_DIAS = 4
UMBRAL_CAMBIO_TM = 1.0
TOLERANCIA_CALIBRE_MM = 0.05

HOY = pd.Timestamp(date.today())
LUNES_ACTUAL = HOY - pd.Timedelta(days=HOY.dayofweek)

def _existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False

def _etapa_por_sku(conn) -> dict:
    """Etapa (STARTER/GROWER, ...) por SKU, solo informativa en el plan.
    Fuente: ventas_raw.etapa (Excel de ventas, columna 'Etapa'); si un SKU
    está en configuracion_tipo_producto, esa etapa manual manda."""
    etapa = {}
    if "etapa" in _columnas_tabla(conn, "ventas_raw"):
        v = pd.read_sql("SELECT sku, etapa FROM ventas_raw WHERE etapa IS NOT NULL", conn)
        v["sku"] = v["sku"].astype(str).str.strip().str.replace(".0", "", regex=False)
        v["etapa"] = v["etapa"].astype(str).str.strip().str.upper()
        v = v[~v["etapa"].isin(["", "NAN", "NONE"])]
        moda = (v.groupby(["sku", "etapa"]).size().reset_index(name="n")
                 .sort_values(["sku", "n"], ascending=[True, False])
                 .drop_duplicates("sku"))
        etapa = dict(zip(moda["sku"], moda["etapa"]))
    if _existe(conn, "configuracion_tipo_producto"):
        o = pd.read_sql("SELECT sku, tipo FROM configuracion_tipo_producto", conn)
        for r in o.itertuples():
            t = str(r.tipo).strip().upper()
            if t and t != "NAN":
                etapa[str(r.sku).strip()] = t
    return etapa

def _ultimo(conn, t, col="fecha_snapshot"):
    return conn.execute(f"SELECT MAX({col}) FROM {t}").fetchone()[0]

def _columnas_tabla(conn, t) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
    except Exception:
        return set()

def cargar_restriccion_simulacion(ruta: str) -> dict:
    if not ruta:
        return None
    p = Path(ruta)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def cargar_config_lineas_dinamica(conn) -> tuple:
    """
    Fuente única de horas, forma y rate por recurso -- todo lo que el
    planificador llena en Configuración -> Líneas de producción.

    Devuelve (horas_extra, forma_extra, rate_extra):
      horas_extra: dict "PLANTA|LINEA" -> horas/día
      forma_extra: dict "PLANTA|LINEA" -> {"AP"|"AX", ...}
      rate_extra:  dict ("PLANTA|LINEA", tamano_mm) -> rate_tm_hora
    """
    horas_extra, forma_extra, rate_extra = {}, {}, {}
    if not _existe(conn, "config_lineas_produccion"):
        print("  [AVISO] No existe 'config_lineas_produccion'. El planificador "
              "debe llenar Configuración -> Líneas de producción en la app.")
        return horas_extra, forma_extra, rate_extra

    try:
        cfg = pd.read_sql(
            "SELECT planta, linea, tipo_producto, tamano_mm, horas_dia, "
            "rate_tm_hora FROM config_lineas_produccion WHERE activo = 1", conn)
    except Exception as e:
        print(f"  [AVISO] No se pudo leer config_lineas_produccion, se ignora: {e}")
        return horas_extra, forma_extra, rate_extra

    if cfg.empty:
        print("  [AVISO] 'config_lineas_produccion' está vacía o sin filas activas.")
        return horas_extra, forma_extra, rate_extra

    for _, r in cfg.iterrows():
        planta = str(r["planta"]).strip().upper().replace("Á", "A")
        linea = str(r["linea"]).strip().upper()
        if not planta or not linea or planta == "NAN" or linea == "NAN":
            continue
        recurso = planta + "|" + (linea if linea.startswith("L") else "L" + linea)

        try:
            horas = float(r["horas_dia"])
            if horas > 0:
                horas_extra[recurso] = horas
        except (TypeError, ValueError):
            pass

        tipo = str(r.get("tipo_producto", "")).strip().upper()
        # Sin depender de tildes: EXTRUÍDO / EXTRUIDO deben matchear igual.
        tipo_sin_tilde = (tipo.replace("Í", "I").replace("É", "E")
                          .replace("Á", "A").replace("Ó", "O").replace("Ú", "U"))
        forma = "AP" if tipo_sin_tilde == "PELLET" else ("AX" if tipo_sin_tilde == "EXTRUIDO" else None)
        if forma:
            forma_extra.setdefault(recurso, set()).add(forma)

        try:
            calibre = float(str(r["tamano_mm"]).replace(",", "."))
            rate = float(r["rate_tm_hora"])
            if rate > 0:
                rate_extra[(recurso, calibre)] = rate
        except (TypeError, ValueError):
            continue

    print(f"  Config de líneas (desde la app): {len(horas_extra)} recurso(s) con "
          f"horas, {len(forma_extra)} con forma, {len(rate_extra)} combinación(es) "
          f"recurso+calibre con rate.")
    return horas_extra, forma_extra, rate_extra

def cargar_paradas_programadas(conn) -> list:
    """Paradas/mantenimiento programado: Configuración -> Paradas programadas
    en la app. A diferencia de horas_extra (fijo para todo el horizonte),
    cada fila aquí SOLO aplica entre fecha_inicio y fecha_fin -- fuera de esa
    ventana la línea sigue con su horario normal. horas_dia = HORAS DE PARADA
    por día (la línea no produce en ese tiempo); 24 = parada total.
    Devuelve una lista de dicts: {recurso, fecha_inicio, fecha_fin, horas_dia}."""
    if not _existe(conn, "paradas_programadas"):
        return []
    try:
        df = pd.read_sql(
            "SELECT planta, linea, fecha_inicio, fecha_fin, horas_dia "
            "FROM paradas_programadas", conn)
    except Exception as e:
        print(f"  [AVISO] No se pudo leer paradas_programadas, se ignora: {e}")
        return []

    paradas = []
    for _, r in df.iterrows():
        planta = str(r["planta"]).strip().upper().replace("Á", "A")
        linea = str(r["linea"]).strip().upper()
        if not planta or not linea or planta == "NAN" or linea == "NAN":
            continue
        recurso = planta + "|" + (linea if linea.startswith("L") else "L" + linea)
        try:
            paradas.append({
                "recurso": recurso,
                "fecha_inicio": pd.Timestamp(r["fecha_inicio"]).normalize(),
                "fecha_fin": pd.Timestamp(r["fecha_fin"]).normalize(),
                "horas_dia": float(r["horas_dia"]),
            })
        except (TypeError, ValueError):
            continue

    if paradas:
        print(f"  {len(paradas)} parada(s) programada(s) activa(s) en Configuración.")
    return paradas

def cargar_exclusiones_sku_planta(conn) -> set:
    """SKU que NO deben producirse en cierta planta/línea por tema de
    negocio (Configuración -> Exclusiones). Devuelve un set de tuplas
    (sku, "PLANTA|LINEA"), o (sku, "PLANTA|*") cuando no se especificó
    línea (excluye toda la planta para ese SKU)."""
    if not _existe(conn, "sku_exclusion_planta"):
        return set()
    try:
        df = pd.read_sql(
            "SELECT sku, planta_excluida, linea_excluida FROM sku_exclusion_planta "
            "WHERE activo = 1", conn)
    except Exception as e:
        print(f"  [AVISO] No se pudo leer sku_exclusion_planta, se ignora: {e}")
        return set()

    exclusiones = set()
    for _, r in df.iterrows():
        sku = str(r["sku"]).strip()
        planta = str(r["planta_excluida"]).strip().upper().replace("Á", "A")
        linea_raw = str(r["linea_excluida"] or "").strip().upper()
        if linea_raw and linea_raw != "NAN":
            linea = linea_raw if linea_raw.startswith("L") else "L" + linea_raw
            exclusiones.add((sku, f"{planta}|{linea}"))
        else:
            exclusiones.add((sku, f"{planta}|*"))
    if exclusiones:
        print(f"  {len(exclusiones)} exclusión(es) SKU-planta/línea activa(s) en Configuración.")
    return exclusiones

def cargar_flete_sku_planta(conn) -> dict:
    """USD/TM de flete por (sku, planta), de calcular_flete_pedidos.py (ponderado por
    las TM que faltan producir de pedidos terrestres). Si no existe, el plan corre igual
    sin flete."""
    if not _existe(conn, "flete_sku_planta"):
        print("  [AVISO] No existe 'flete_sku_planta' (correr calcular_flete_pedidos.py). "
              "El plan se arma sin considerar flete.")
        return {}
    try:
        f = pd.read_sql("SELECT sku, planta, usd_tm, fecha_calculo FROM flete_sku_planta", conn)
    except Exception as e:
        print(f"  [AVISO] No se pudo leer flete_sku_planta, se ignora: {e}")
        return {}
    if f.empty:
        return {}
    ult = f["fecha_calculo"].max()
    if str(ult) != HOY.strftime("%Y-%m-%d"):
        print(f"  [AVISO] flete_sku_planta es del {ult} (no de hoy): correr calcular_flete_pedidos.py antes.")
    f["sku"] = f["sku"].astype(str).str.strip().str.replace(".0", "", regex=False)
    f["planta"] = f["planta"].astype(str).str.strip().str.upper().str.replace("Á", "A")
    f = f.dropna(subset=["usd_tm"])
    print(f"  Flete cargado: {f['sku'].nunique()} SKU con costo por planta.")
    return {(r.sku, r.planta): float(r.usd_tm) for r in f.itertuples()}

def cargar_sku_calibre(conn) -> pd.DataFrame:
    """
    SKU -> forma, calibre representativo, desde ventas_raw (Ingesta_Ventas_PT.py).
    Se usa el más frecuente por SKU; si hay más de uno, se marca inconsistente.
    """
    if not _existe(conn, "ventas_raw"):
        print("[AVISO] No existe 'ventas_raw'. Correr Ingesta_Ventas_PT.py primero.")
        return pd.DataFrame(columns=["sku", "forma", "calibre_mm"])

    df = pd.read_sql(
        "SELECT DISTINCT sku, forma, calibre FROM ventas_raw "
        "WHERE calibre IS NOT NULL AND calibre != '' AND calibre != 'nan'",
        conn,
    )
    if df.empty:
        print("[AVISO] Sin datos de calibre en 'ventas_raw'.")
        return pd.DataFrame(columns=["sku", "forma", "calibre_mm"])

    df["sku"] = df["sku"].astype(str).str.strip()
    df["forma"] = df["forma"].astype(str).str.strip().str.upper()
    df["calibre"] = df["calibre"].astype(str).str.strip().str.replace(",", ".", regex=False)
    df["calibre_mm"] = pd.to_numeric(df["calibre"], errors="coerce")
    df = df.dropna(subset=["calibre_mm"])

    if df.empty:
        print("[AVISO] Columna 'calibre' en ventas_raw no se pudo convertir a número.")
        return pd.DataFrame(columns=["sku", "forma", "calibre_mm"])

    n_calibres_por_sku = df.groupby("sku")["calibre_mm"].nunique()
    skus_inconsistentes = set(n_calibres_por_sku[n_calibres_por_sku > 1].index)

    moda = (
        df.groupby(["sku", "calibre_mm"])
        .size()
        .reset_index(name="n")
        .sort_values(["sku", "n"], ascending=[True, False])
        .drop_duplicates("sku", keep="first")
    )

    forma_por_sku = df.drop_duplicates("sku", keep="last")[["sku", "forma"]]
    resultado = moda.merge(forma_por_sku, on="sku")[["sku", "forma", "calibre_mm"]]
    resultado["calibre_inconsistente"] = resultado["sku"].isin(skus_inconsistentes)

    n_incons = int(resultado["calibre_inconsistente"].sum())
    if n_incons:
        print(f"  [AVISO] {n_incons} SKU con más de un calibre distinto en el "
              f"histórico -- se usó el más frecuente, revisar manualmente: "
              f"{resultado.loc[resultado['calibre_inconsistente'], 'sku'].tolist()[:15]}")

    print(f"  {len(resultado)} SKU con calibre identificado desde ventas_raw.")
    return resultado[["sku", "forma", "calibre_mm"]]

def cargar_datos(conn) -> dict:
    d = {}

    if not _existe(conn, "posicionamiento_pt"):
        raise RuntimeError("Falta 'posicionamiento_pt'. Correr clasificar_mts_mto.py.")
    pos = pd.read_sql("SELECT * FROM posicionamiento_pt", conn)
    pos["sku"] = (pos["sku"].astype(str).str.strip().str.replace(".0", "", regex=False))
    d["pos"] = pos

    if _existe(conn, "ddmrp_flujo_neto"):
        f = _ultimo(conn, "ddmrp_flujo_neto", "fecha_calculo")
        cols_disp = _columnas_tabla(conn, "ddmrp_flujo_neto")
        col_transito = "transito_tm" if "transito_tm" in cols_disp else None
        campos = "sku, nfp_tm, disponible_tm" + (", transito_tm" if col_transito else "") + ", tor, toy, tog"
        nfp = pd.read_sql(
            f"SELECT {campos} FROM ddmrp_flujo_neto WHERE fecha_calculo = ? AND nodo = 'PT'",
            conn, params=(f,),
        )
        nfp["sku"] = (nfp["sku"].astype(str).str.strip().str.replace(".0", "", regex=False))
        if col_transito:
            # PT: el "tránsito" en SAP es producto ya en piso, pendiente de
            # reclasificación -- es stock físico real, se suma como uno solo.
            # OJO: desde aquí disponible_tm YA incluye el tránsito. No volver
            # a sumar transito_tm en ningún otro lado del script.
            nfp["disponible_tm"] = nfp["disponible_tm"].fillna(0.0) + nfp["transito_tm"].fillna(0.0)
        else:
            print("  [AVISO] 'ddmrp_flujo_neto' no tiene 'transito_tm' todavía -- "
                  "correr calcular_nfp.py para regenerarla con el esquema completo. "
                  "Por ahora se usa solo disponible_tm.")
            nfp["disponible_tm"] = nfp["disponible_tm"].fillna(0.0)
        d["nfp"] = nfp
    else:
        d["nfp"] = pd.DataFrame()

    if _existe(conn, "pedidos_pendientes_pt"):
        f = _ultimo(conn, "pedidos_pendientes_pt")
        ped = pd.read_sql(
            "SELECT material AS sku, fecha_entrega, cantidad_tm, cliente_nombre "
            "FROM pedidos_pendientes_pt WHERE fecha_snapshot = ?",
            conn, params=(f,),
        )
        ped["sku"] = (
            ped["sku"].astype(str).str.strip().str.replace(".0", "", regex=False)
        )
    else:
        ped = pd.DataFrame(columns=["sku", "fecha_entrega", "cantidad_tm", "cliente_nombre"])

    manuales = cargar_pedidos_manuales(conn)
    if not manuales.empty:
        man = manuales.rename(columns={"material": "sku"})[["sku", "fecha_entrega", "cantidad_tm", "cliente_nombre"]]
        ped = pd.concat([ped, man], ignore_index=True)

    ped["fecha_entrega"] = pd.to_datetime(ped["fecha_entrega"], errors="coerce")
    d["pedidos"] = ped.dropna(subset=["fecha_entrega"])

    print("=== Cargando calibre por SKU (ventas_raw) ===")
    sku_calibre = cargar_sku_calibre(conn)
    if sku_calibre.empty:
        raise RuntimeError("Sin calibres cargados desde 'ventas_raw'. No se puede "
                           "armar ninguna línea de producción.")
    d["sku_calibre"] = sku_calibre

    try:
        d["capacidad"] = pd.read_sql(
            "SELECT planta, linea, tm_dia_p95 FROM capacidad_linea "
            "WHERE capacidad_confiable = 1", conn)
    except Exception:
        d["capacidad"] = pd.DataFrame()

    if "margen_usd_tm" in pos.columns:
        d["margen"] = pos[["sku", "margen_usd_tm"]].dropna()
    else:
        d["margen"] = pd.DataFrame(columns=["sku", "margen_usd_tm"])

    if _existe(conn, "ventas_demanda"):
        v = pd.read_sql("SELECT cliente, ventas_tm FROM ventas_demanda WHERE ventas_tm > 0", conn)
        vol = v.groupby("cliente")["ventas_tm"].sum()
        d["peso_clientes"] = (vol / vol.sum()).to_dict()
    else:
        d["peso_clientes"] = {}

    print("=== Cargando config de líneas de producción (app) ===")
    horas_extra, forma_extra, rate_extra = cargar_config_lineas_dinamica(conn)
    d["horas_extra"] = horas_extra
    d["forma_extra"] = forma_extra
    d["rate_extra"] = rate_extra
    d["paradas"] = cargar_paradas_programadas(conn)
    d["exclusiones"] = cargar_exclusiones_sku_planta(conn)
    d["etapa_por_sku"] = _etapa_por_sku(conn)
    d["flete"] = cargar_flete_sku_planta(conn)

    return d

def construir_lineas_por_calibre(sku_calibre: pd.DataFrame, rate_extra: dict,
                                 forma_extra: dict,
                                 capacidad: pd.DataFrame = None,
                                 exclusiones: set = None) -> pd.DataFrame:
    ALIAS = {"T3": "L5", "TORRE3": "L5", "TORRE 3": "L5", "GR": "L6"}
    exclusiones = exclusiones or set()

    cap_obs = {}
    if capacidad is not None and not capacidad.empty:
        for _, c in capacidad.iterrows():
            pl = str(c["planta"]).strip().upper().replace("Á", "A")
            ln = str(c["linea"]).strip().upper().split(".")[-1]
            ln = ALIAS.get(ln, ln)
            cap_obs[f"{pl}|{ln}"] = float(c["tm_dia_p95"])

    if not rate_extra:
        print("  [ERROR] Sin ninguna combinación recurso+calibre con rate en "
              "config_lineas_produccion. El planificador debe llenar Configuración "
              "-> Líneas de producción (Tamaño mm + Rate TM/h) para cada línea.")
        return pd.DataFrame()

    filas = []
    descartados = []

    for _, r in sku_calibre.iterrows():
        sku, forma, calibre = r["sku"], r["forma"], r["calibre_mm"]
        forma_ok = forma in {"AP", "AX"}
        match_encontrado = False

        for (recurso, cal_tabla), rate in rate_extra.items():
            if abs(cal_tabla - calibre) > TOLERANCIA_CALIBRE_MM:
                continue
            formas_recurso = forma_extra.get(recurso, set())
            if formas_recurso and forma_ok and forma not in formas_recurso:
                continue
            if (sku, recurso) in exclusiones or (sku, f"{recurso.split('|')[0]}|*") in exclusiones:
                continue

            filas.append({
                "sku": sku, "planta": recurso.split("|")[0],
                "linea": recurso.split("|")[1], "recurso": recurso,
                "forma": forma, "calibre_mm": calibre,
                "rate_tm_hora": rate,
                "cap_observada_tm_dia": cap_obs.get(recurso),
                "forma_conocida": forma_ok,
            })
            match_encontrado = True

        if not match_encontrado:
            descartados.append((sku, calibre))

    if descartados:
        calibres_sin_config = sorted({c for _, c in descartados})
        print(f"  [AVISO] {len(descartados)} SKU sin línea/rate configurado para su "
              f"calibre en config_lineas_produccion. Calibres sin configurar: "
              f"{calibres_sin_config}. No se programan hasta que el planificador "
              f"agregue esa fila en la app.")

    df = pd.DataFrame(filas)
    if df.empty:
        return df

    df = (df.sort_values("rate_tm_hora", ascending=False)
          .drop_duplicates(["sku", "recurso"], keep="first")
          .reset_index(drop=True))

    n_multi = int((df.groupby("sku")["planta"].nunique() > 1).sum())
    if n_multi:
        print(f"  {n_multi} SKU pueden producirse en más de una planta: el modelo "
              f"elige según capacidad disponible y rate.")

    print(f"  {df['recurso'].nunique()} recursos: {sorted(df['recurso'].unique())}")

    sin_cap = sorted({r for r in df['recurso'].unique() if r not in cap_obs})
    if sin_cap:
        print(f"  [AVISO] Sin capacidad observada en {sin_cap}: se usa solo el "
              f"límite teórico de horas x rate.")

    return df

def aplicar_overrides_escenario(d: dict, overrides: dict) -> dict:
    if not overrides:
        return d

    d = dict(d)  # copia superficial

    # --- Pestaña 1: capacidad (horas por recurso) ---
    horas_extra_escenario = dict(d.get("horas_extra", {}))
    for recurso, horas in overrides.get("horas_override_extra", {}).items():
        horas_extra_escenario[recurso] = float(horas)
    d["horas_extra"] = horas_extra_escenario

    # --- Pestaña 1: shock de demanda (%) ---
    shock = overrides.get("shock_demanda_pct")
    if shock and not d["pedidos"].empty:
        pedidos = d["pedidos"].copy()
        if isinstance(shock, dict):
            for sku, pct in shock.items():
                mask = pedidos["sku"] == str(sku)
                pedidos.loc[mask, "cantidad_tm"] = pedidos.loc[mask, "cantidad_tm"] * (1 + float(pct) / 100.0)
        else:
            pedidos["cantidad_tm"] = pedidos["cantidad_tm"] * (1 + float(shock) / 100.0)
        d["pedidos"] = pedidos

    # --- Pestaña 2: prioridad manual de cliente ---
    prioridad = overrides.get("prioridad_cliente", {})
    if prioridad:
        d["prioridad_cliente_override"] = prioridad

    # --- Pestaña 3: mover demanda entre fechas ---
    mover = overrides.get("mover_demanda", [])
    if mover and not d["pedidos"].empty:
        pedidos = d["pedidos"].copy()
        for mv in mover:
            sku, f_origen, f_destino, tm = mv["sku"], mv["fecha_origen"], mv["fecha_destino"], float(mv["tm"])
            mask = (pedidos["sku"] == str(sku)) & (pedidos["fecha_entrega"] == pd.Timestamp(f_origen))
            restante = tm
            for idx in pedidos[mask].index:
                if restante <= 0:
                    break
                disponible = pedidos.loc[idx, "cantidad_tm"]
                mover_tm = min(disponible, restante)
                pedidos.loc[idx, "cantidad_tm"] -= mover_tm
                restante -= mover_tm
                nueva_fila = pedidos.loc[idx].copy()
                nueva_fila["cantidad_tm"] = mover_tm
                nueva_fila["fecha_entrega"] = pd.Timestamp(f_destino)
                pedidos = pd.concat([pedidos, nueva_fila.to_frame().T], ignore_index=True)
        d["pedidos"] = pedidos

    return d

def optimizar(d: dict, restriccion: dict = None, congelado: dict = None,
              horas_override: dict = None, desactivar: set = None,
              paradas: list = None) -> tuple:
    """
    LÓGICA:

    El objetivo de reposición de cada SKU MTS sale DIRECTO del NFP ya
    calculado por calcular_nfp.py:

        objetivo_reposicion[sku] = TOG - NFP   si NFP <= TOY (rojo/amarillo)
        objetivo_reposicion[sku] = 0            si NFP >  TOY (verde/sobre_tog)

    Tope de producción:
      - MTS: max(objetivo_reposicion, pedido neto)  -> un pedido en firme
             siempre puede producirse aunque el SKU esté en verde.
      - MTO: pedido neto (pedidos - stock). Sin pedido = 0.

    Prioridad del objetivo: pedidos en firme >> bodega >> buffer (rojo primero).
    Nunca puede volverse Infeasible por los topes: son techos (<=).
    """
    desactivar = desactivar or set()
    pos = d["pos"]
    lineas = construir_lineas_por_calibre(
        d["sku_calibre"], d.get("rate_extra", {}), d.get("forma_extra", {}),
        d.get("capacidad"), d.get("exclusiones"))

    if lineas.empty:
        raise RuntimeError("No se pudo construir ninguna línea de producción. "
                           "Revisar config_lineas_produccion en la app.")

    dias_todos = [LUNES_ACTUAL + timedelta(days=i) for i in range(HORIZONTE_DIAS)]
    dias = [t for t in dias_todos if t.normalize() >= HOY.normalize()]

    es_mts_bool = pd.to_numeric(pos["es_mts"], errors="coerce").fillna(0).astype(int).astype(bool)
    mts = set(pos.loc[es_mts_bool, "sku"])
    mto = set(pos.loc[~es_mts_bool, "sku"])

    skus = sorted(set(lineas["sku"]) & set(pos["sku"]))
    if not skus:
        raise RuntimeError("Ningún SKU del posicionamiento tiene línea/rate configurado.")

    margen = d["margen"].set_index("sku")["margen_usd_tm"].to_dict()
    margen_mediano = float(np.median(list(margen.values()))) if margen else 100.0

    nfp = d["nfp"].set_index("sku") if not d["nfp"].empty else pd.DataFrame()
    posi = pos.set_index("sku")

    # --- Objetivo de reposición por SKU, directo del NFP ya calculado ---
    objetivo_reposicion = {}
    for s in skus:
        obj = 0.0
        if s in mts and s in posi.index and s in nfp.index and "tog_cap" not in desactivar:
            tog = float(posi.loc[s, "tog"])
            toy = float(posi.loc[s, "toy"])
            nfp_actual = float(nfp.loc[s, "nfp_tm"]) if "nfp_tm" in nfp.columns else 0.0
            if tog > 0 and nfp_actual <= toy:
                obj = max(tog - nfp_actual, 0.0)
        objetivo_reposicion[s] = obj

    n_con_objetivo = sum(1 for v in objetivo_reposicion.values() if v > 0)
    print(f"  {n_con_objetivo} de {len(skus)} SKU con objetivo de reposición real "
          f"(NFP en rojo/amarillo) -- el resto queda en 0 producción por buffer.")

    # --- Urgencia DDMRP: 1 - NFP/TOG (rojo ~1, amarillo menor) ---
    # Solo MTS con objetivo de reposición. Ordena el buffer por penetración,
    # NO por margen (el margen solo es real con un pedido detrás).
    urgencia = {}
    for s in skus:
        if objetivo_reposicion.get(s, 0.0) > 0:
            tog = float(posi.loc[s, "tog"])
            nfp_a = float(nfp.loc[s, "nfp_tm"]) if (s in nfp.index and "nfp_tm" in nfp.columns) else 0.0
            urgencia[s] = min(max(1.0 - nfp_a / tog, 0.05), 1.0)

    dem = {(s, t): 0.0 for s in skus for t in dias}
    peso_dia = {}
    if not d["pedidos"].empty:
        p = d["pedidos"][d["pedidos"]["sku"].isin(skus)].copy()
        print("TM después cruce SKU:", p["cantidad_tm"].sum())
        print("Pedidos después cruce SKU:", len(p))

        p["dia"] = p["fecha_entrega"].dt.normalize()
        p.loc[p["dia"] < dias[0], "dia"] = dias[0]
        p = p[p["dia"] <= dias[-1]]
        for _, r in p.iterrows():
            k = (r["sku"], r["dia"])
            if k in dem:
                dem[k] += float(r["cantidad_tm"])

        peso_clientes = d.get("peso_clientes", {})
        if peso_clientes and "cliente_nombre" in p.columns:
            p["_peso_cliente"] = p["cliente_nombre"].map(peso_clientes).fillna(0.0)
            for (s, t), g in p.groupby(["sku", "dia"]):
                tot = g["cantidad_tm"].sum()
                if tot > 0:
                    peso_dia[(s, t)] = float((g["_peso_cliente"] * g["cantidad_tm"]).sum() / tot)

        prioridad_override = d.get("prioridad_cliente_override", {})
        if prioridad_override and "cliente_nombre" in p.columns:
            for (s, t), g in p.groupby(["sku", "dia"]):
                sku_overrides = prioridad_override.get(s, {})
                if not sku_overrides:
                    continue
                factor = max(
                    (sku_overrides.get(cli, 1.0) for cli in g["cliente_nombre"].unique()),
                    default=1.0)
                peso_dia[(s, t)] = peso_dia.get((s, t), 0.0) * factor

    print("\n=== DEBUG PEDIDOS ===")
    print("Pedidos pendientes:", len(d["pedidos"]))
    print("TM pedidos:", d["pedidos"]["cantidad_tm"].sum())
    print("TM demanda horizonte:", sum(dem.values()))

    # --- Stock inicial y pedido neto por SKU ---
    # disponible_tm YA incluye el tránsito (se sumó en cargar_datos).
    inicial_sku = {
        s: (float(nfp.loc[s, "disponible_tm"]) if s in nfp.index else 0.0)
        for s in skus
    }
    pedido_neto_sku = {
        s: max(0.0, sum(dem.get((s, t), 0.0) for t in dias) - inicial_sku[s])
        for s in skus
    }

    prob = pulp.LpProblem("plan_produccion", pulp.LpMaximize)

    pares = [(a, b) for a, b in zip(lineas["sku"], lineas["recurso"]) if a in skus]
    rate = {
        (r["sku"], r["recurso"]): r["rate_tm_hora"]
        for _, r in lineas.iterrows() if r["sku"] in skus
    }

    # --- Flete: sobrecosto USD/TM de cada (sku, recurso) frente a la planta más barata ---
    flete = d.get("flete", {}) or {}
    flete_rel, flete_barata = {}, {}
    for s in skus:
        plantas_s = sorted({b.split("|")[0] for (a, b) in pares if a == s})
        costos = {pl: flete[(s, pl)] for pl in plantas_s if (s, pl) in flete}
        if not costos:
            continue
        barata_todas = [pl for pl in ("DURAN", "GUAYAQUIL") if (s, pl) in flete]
        flete_barata[s] = min(barata_todas, key=lambda pl: flete[(s, pl)])
        if len(costos) < 2:
            continue                       # una sola planta posible: el flete no decide nada
        minimo = min(costos.values())
        for (a, b) in pares:
            if a == s and b.split("|")[0] in costos:
                flete_rel[(a, b)] = costos[b.split("|")[0]] - minimo
    dif_max = max(flete_rel.values(), default=0.0)
    peso_flete = min(PESO_FLETE_MAX, TOPE_PENALIZACION_FLETE_TM / dif_max) if dif_max > 0 else 0.0
    if flete_rel:
        n_sku_flete = len({a for (a, b) in flete_rel})
        print(f"  Flete como desempate: {n_sku_flete} SKU con 2 plantas posibles | "
              f"sobrecosto máx {dif_max:,.2f} USD/TM | peso {peso_flete:.4f}")

    dias_pasados = [t for t in dias_todos if t.normalize() < HOY.normalize()]
    if dias_pasados:
        print(f"  {len(dias_pasados)} día(s) de esta semana ya pasaron -- se "
              f"muestran en el reporte tal como se produjeron, pero el modelo "
              f"NUNCA los recalcula: no son variables de decisión.")
    dias_extras = [dias[-1] + timedelta(days=k) for k in range(1, LEAD_TIME_PRODUCCION_DIAS + 1)]
    dias_calculo = dias + dias_extras

    x = pulp.LpVariable.dicts("prod", (pares, dias), lowBound=0)
    y = pulp.LpVariable.dicts("corre", (pares, dias), cat="Binary")
    inv = pulp.LpVariable.dicts("inv", (skus, dias_calculo), lowBound=0)
    # Backlog: pedido vencido que AÚN no se entrega. No desaparece: se arrastra
    # día a día hasta que la producción lo cubre (entrega tarde > no entregar).
    bl = pulp.LpVariable.dicts("backlog", (skus, dias_calculo), lowBound=0)
    exceso_bodega = pulp.LpVariable.dicts("exceso_bodega", dias, lowBound=0)

    # --- Objetivo: pedidos en firme >> bodega >> buffer ---
    prob += (
        # 1) Pedidos en firme: cada TM atrasada cuesta muchísimo POR CADA DÍA
        #    de atraso -> el modelo entrega lo antes posible; entre pedidos
        #    gana el de más margen / cliente con más peso.
        - pulp.lpSum(
            (PESO_PEDIDO_FIRME
             + margen.get(s, margen_mediano) * (1.0 + peso_dia.get((s, t), 0.0))) * bl[s][t]
            for s in skus for t in dias_calculo)
        # 2) Bodega
        - PENALIZACION_EXCESO_BODEGA_USD_TM * pulp.lpSum(exceso_bodega[t] for t in dias)
        # 3) Buffer: según penetración (rojo primero), NO según margen
        + pulp.lpSum(PESO_BUFFER * urgencia.get(s, 0.0) * x[(s, l)][t]
                     for (s, l) in pares for t in dias)
        # desempate: producir el buffer antes que después
        + 0.01 * pulp.lpSum((len(dias) - i) * x[(s, l)][t]
                            for (s, l) in pares for i, t in enumerate(dias)
                            if s in urgencia)
        # 4) Flete (debajo del buffer): sobrecosto de producir en la planta más cara
        - peso_flete * pulp.lpSum(flete_rel[(s, l)] * x[(s, l)][t]
                                  for (s, l) in pares if flete_rel.get((s, l), 0.0) > 0
                                  for t in dias)
    )

    fechas_pasadas_str = {dd.strftime("%Y-%m-%d") for dd in dias_pasados}

    for s in skus:
        inicial = inicial_sku[s]
        lineas_s = [(a, b) for (a, b) in pares if a == s]

        # SKU contra pedido (MTO) SIN ningún pedido real: cero producción,
        # sin excepción.
        demanda_mto_sku = sum(dem.get((s, t), 0.0) for t in dias)
        if s in mto and "mto_cap" not in desactivar and demanda_mto_sku <= 0:
            for (a, b) in lineas_s:
                for t in dias:
                    prob += x[(a, b)][t] == 0

        tope_mto = None
        if s in mto and "mto_cap" not in desactivar and demanda_mto_sku > 0:
            tm_congelada_mto = 0.0
            if congelado:
                tm_congelada_mto = sum(
                    v for (sk, ln, fe), v in congelado.items()
                    if sk == s and fe in fechas_pasadas_str)
            # MTO: nunca guardar más de lo pedido. Sin el piso de 50 TM:
            # el MTO produce solo por pedido, no llena bodega.
            tope_mto = max(inicial, demanda_mto_sku, tm_congelada_mto)

        for i, t in enumerate(dias_calculo):
            prev_inv = inicial if i == 0 else inv[s][dias_calculo[i - 1]]
            prev_bl = 0 if i == 0 else bl[s][dias_calculo[i - 1]]
            t_disp = i - LEAD_TIME_PRODUCCION_DIAS
            entra = (pulp.lpSum(x[(a, b)][dias_calculo[t_disp]] for (a, b) in lineas_s)
                     if 0 <= t_disp < len(dias) else 0)
            # stock - backlog de hoy = stock - backlog de ayer + llega - pedidos de hoy
            prob += inv[s][t] - bl[s][t] == prev_inv - prev_bl + entra - dem.get((s, t), 0.0)

            if tope_mto is not None and t.normalize() >= HOY.normalize():
                prob += inv[s][t] <= tope_mto

        produccion_total_s = pulp.lpSum(x[(a, b)][t] for (a, b) in lineas_s for t in dias)

        if s in mts:
            # Un MTS con pedido en firme debe poder producirse aunque esté en verde.
            prob += produccion_total_s <= max(objetivo_reposicion.get(s, 0.0),
                                              pedido_neto_sku[s])

        if s in mto:
            prob += produccion_total_s <= pedido_neto_sku[s]

    cap_tm = {}
    for _, r in lineas.iterrows():
        if pd.notna(r.get("cap_observada_tm_dia")):
            cap_tm[r["recurso"]] = float(r["cap_observada_tm_dia"])

    horas_override = horas_override or {}
    horas_por_recurso = {
        ln: horas_override.get(ln, HORAS_DIA_LINEA_REAL.get(ln, HORAS_DIA_LINEA))
        for ln in sorted(set(b for (a, b) in pares))
    }

    paradas = paradas or []

    def horas_del_dia(ln, t):
        f = t.normalize()
        # paradas_programadas.horas_dia = HORAS DE PARADA ese día (la línea no
        # produce en ese tiempo). 24 = parada total. Si hay varias paradas
        # el mismo día, se toma la más larga.
        aplicables = [p["horas_dia"] for p in paradas
                      if p["recurso"] == ln and p["fecha_inicio"] <= f <= p["fecha_fin"]]
        if aplicables:
            return max(0.0, horas_por_recurso[ln] - max(aplicables))
        return horas_por_recurso[ln]

    for ln in sorted(set(b for (a, b) in pares)):
        if ln in cap_tm:
            for t in dias:
                prob += pulp.lpSum(x[(a, b)][t] for (a, b) in pares if b == ln) <= cap_tm[ln]

        for t in dias:
            prob += (pulp.lpSum(x[(a, b)][t] / rate[(a, b)] for (a, b) in pares if b == ln)
                     <= horas_del_dia(ln, t))

    if CAPACIDAD_BODEGA_PT_TM:
        for t in dias:
            prob += pulp.lpSum(inv[s][t] for s in skus) <= CAPACIDAD_BODEGA_PT_TM + exceso_bodega[t]

    # --- Lote mínimo por SKU ---
    #   MTS -> min(50, max(TOG - NFP, pedido neto)): si el buffer entero es
    #          menor a 50 TM, se permite una corrida del tamaño del buffer.
    #   MTO -> min(50, pedido neto): produce solo lo del pedido.
    lote_min_sku = {}
    for s in skus:
        if s in mts:
            lote_min_sku[s] = min(LOTE_MINIMO_TM,
                                  max(objetivo_reposicion.get(s, 0.0), pedido_neto_sku[s]))
        else:
            lote_min_sku[s] = min(LOTE_MINIMO_TM, pedido_neto_sku[s])

    for s, l in pares:
        cap_dia = rate[(s, l)] * horas_por_recurso[l]
        for t in dias:
            prob += x[(s, l)][t] <= cap_dia * y[(s, l)][t]
            prob += x[(s, l)][t] >= lote_min_sku[s] * y[(s, l)][t]

    if horas_override:
        print(f"  Horas/día override: {horas_override}")

    if "nivel_rotacion" in posi.columns and "ciclo_baja_rotacion" not in desactivar:
        skus_baja = [s for s in skus if s in posi.index and posi.loc[s, "nivel_rotacion"] == "baja"
                     and objetivo_reposicion.get(s, 0.0) > 0]
        for s in skus_baja:
            lineas_s = [(a, b) for (a, b) in pares if a == s]
            if len(lineas_s) < 2 and len(dias) < 2:
                continue
            for i in range(len(dias) - CICLO_REPOSICION_BAJA_ROTACION_DIAS + 1):
                ventana = [t for t in dias[i:i + CICLO_REPOSICION_BAJA_ROTACION_DIAS]
                           if t.normalize() >= HOY.normalize()]
                if len(ventana) < 2:
                    continue
                prob += pulp.lpSum(y[(a, b)][t] for (a, b) in lineas_s for t in ventana) <= 1
        if skus_baja:
            print(f"  {len(skus_baja)} SKU de baja rotación con objetivo real: máximo 1 corrida "
                  f"cada {CICLO_REPOSICION_BAJA_ROTACION_DIAS} días.")

    if restriccion:
        s = restriccion["sku"]
        f_dest = pd.Timestamp(restriccion["fecha_destino"])
        l_dest = restriccion["linea_destino"]
        f_orig = pd.Timestamp(restriccion["fecha_origen"])
        l_orig = restriccion["linea_origen"]

        recurso_dest = next((rec for (sk, rec) in pares if sk == s and rec.endswith(l_dest)), None)
        recurso_orig = next((rec for (sk, rec) in pares if sk == s and rec.endswith(l_orig)), None)

        if recurso_dest and f_dest in dias:
            prob += x[(s, recurso_dest)][f_dest] >= lote_min_sku.get(s, LOTE_MINIMO_TM)
            print(f"  [SIMULACIÓN] Restricción aplicada: {s} forzado a "
                  f"{recurso_dest} el {f_dest.date()} (motivo: "
                  f"{restriccion.get('motivo_usuario', 'sin especificar')})")
        if recurso_orig and f_orig in dias:
            prob += x[(s, recurso_orig)][f_orig] == 0

    print("  Resolviendo...")
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=300))
    estado = pulp.LpStatus[prob.status]

    if estado != "Optimal":
        print(f"\n  [MODELO {estado}] Diagnóstico de restricciones:")
        inv_ini = sum(inicial_sku.values())
        dem_tot = sum(dem.values())
        print(f"    Inventario inicial: {inv_ini:,.0f} TM")
        if CAPACIDAD_BODEGA_PT_TM:
            print(f"    Tope de bodega:     {CAPACIDAD_BODEGA_PT_TM:,.0f} TM "
                  f"({'YA EXCEDIDO' if inv_ini > CAPACIDAD_BODEGA_PT_TM else 'ok'})")
        print(f"    Demanda del horizonte: {dem_tot:,.0f} TM ({dem_tot/HORIZONTE_DIAS:,.0f} TM/día)")
        cap_dia = sum(
            horas_por_recurso.get(ln, HORAS_DIA_LINEA)
            * max(rate[(a, b)] for (a, b) in pares if b == ln)
            for ln in sorted(set(b for (a, b) in pares)))
        print(f"    Capacidad diaria:      {cap_dia:,.0f} TM/día")
        return pd.DataFrame(), pd.DataFrame(), estado, lineas, None

    print(f"  Estado: {estado}")

    exceso_hoy = exceso_bodega[dias[0]].value() or 0.0
    if exceso_hoy > 1:
        print(f"\n  ⚠️   BODEGA COLAPSADA: inventario inicial excede la capacidad en "
              f"{exceso_hoy:,.0f} TM. El modelo no produce nada nuevo de los SKU sanos "
              f"(su objetivo de reposición ya es 0), pero el exceso HEREDADO no se "
              f"resuelve solo -- requiere decisión operativa: liberar bodega, mover a "
              f"otra ubicación, o esperar consumo natural.")

    plan = []
    for s, l in pares:
        for t in dias:
            v = x[(s, l)][t].value() or 0.0
            if v > 0.01:
                plan.append({
                    "fecha": t.strftime("%Y-%m-%d"), "sku": s,
                    "planta": l.split("|")[0], "linea": l.split("|")[1], "recurso": l,
                    "tm": round(v, 2), "horas": round(v / rate[(s, l)], 2),
                    "politica": "MTS" if s in mts else "MTO",
                    "margen_usd_tm": round(margen.get(s, margen_mediano), 2),
                    "etapa": d.get("etapa_por_sku", {}).get(s),
                })

    df_plan = pd.DataFrame(plan)
    if not df_plan.empty:
        if not d["pedidos"].empty:
            dem_sku = d["pedidos"][d["pedidos"]["sku"].isin(skus)].groupby("sku")["cantidad_tm"].sum().to_dict()
        else:
            dem_sku = {}
        necesario = {sk: dem_sku.get(sk, 0.0) for sk in skus}

        df_plan = df_plan.sort_values(["sku", "fecha"]).reset_index(drop=True)
        df_plan["_acum_despues"] = df_plan.groupby("sku")["tm"].cumsum()
        df_plan["_acum_antes"] = df_plan["_acum_despues"] - df_plan["tm"]
        df_plan["motivo"] = np.where(
            df_plan["_acum_antes"] < df_plan["sku"].map(necesario).fillna(0),
            "PEDIDO", "BUFFER")
        df_plan = df_plan.drop(columns=["_acum_despues", "_acum_antes"])

        # --- Flete: planta elegida vs planta más barata y por qué ---
        exclusiones = d.get("exclusiones") or set()
        plantas_por_sku = {}
        for (a, b) in pares:
            plantas_por_sku.setdefault(a, set()).add(b.split("|")[0])

        def _motivo_planta(r):
            s, pl = r["sku"], r["planta"]
            barata = flete_barata.get(s)
            if not barata:
                return "SIN_DATO_FLETE"
            if pl == barata:
                return "FLETE" if len(plantas_por_sku.get(s, set())) > 1 else "UNICA_PLANTA"
            if barata not in plantas_por_sku.get(s, set()):
                excl_barata = any(sk == s and rec.split("|")[0] == barata for (sk, rec) in exclusiones)
                return "POR_NEGOCIO" if excl_barata else "SIN_LINEA_EN_PLANTA_BARATA"
            return "CAPACIDAD"

        df_plan["flete_usd_tm"] = [round(flete[(s, pl)], 2) if (s, pl) in flete else None
                                   for s, pl in zip(df_plan["sku"], df_plan["planta"])]
        df_plan["planta_flete_barata"] = df_plan["sku"].map(flete_barata)
        df_plan["motivo_planta"] = df_plan.apply(_motivo_planta, axis=1)
        # El flete sale de los PEDIDOS: solo se explica la planta en las corridas de pedido.
        # Las de BUFFER no tienen cliente asignado todavía.
        es_buffer = df_plan["motivo"] == "BUFFER"
        df_plan.loc[es_buffer, "motivo_planta"] = "BUFFER_SIN_CLIENTE"
        df_plan["sobrecosto_flete_usd"] = [
            round((flete[(s, pl)] - flete[(s, b)]) * tm, 2)
            if isinstance(b, str) and (s, pl) in flete and (s, b) in flete else 0.0
            for s, pl, b, tm in zip(df_plan["sku"], df_plan["planta"],
                                    df_plan["planta_flete_barata"], df_plan["tm"])]
        df_plan.loc[es_buffer, "sobrecosto_flete_usd"] = 0.0

        semaforo_map = (posi["dimensionamiento_confiable"].to_dict()
                        if "dimensionamiento_confiable" in posi.columns else {})
        df_plan["forecast_confiable"] = df_plan["sku"].map(semaforo_map).fillna(False).astype(bool)
        no_confiable_mask = df_plan["forecast_confiable"] == False

        df_plan["advertencia"] = np.where(
            no_confiable_mask, "SKU sin forecast validado -- revisar antes de ejecutar", "")

        n_no_confiable = int(df_plan.loc[no_confiable_mask, "sku"].nunique())
        tm_no_confiable = df_plan.loc[no_confiable_mask, "tm"].sum()
        print(f"\n  [AVISO] {n_no_confiable} SKU en el plan SIN forecast validado "
              f"({tm_no_confiable:,.0f} TM) -- revisar antes de copiar y pegar.")

        n_sin_etapa = int(df_plan.loc[df_plan["etapa"].isna(), "sku"].nunique())
        if n_sin_etapa:
            skus_sin_etapa = sorted(df_plan.loc[df_plan["etapa"].isna(), "sku"].unique())
            print(f" [AVISO] {n_sin_etapa} SKU planificados sin Etapa (no aparecen en "
                  f"ventas_raw): {skus_sin_etapa[:15]} -- optimizar_secuencia_produccion.py "
                  f"les aplicará el peor setup.")

    # Riesgo: TM de pedidos que NO se entregan en su fecha (se atrasan o no se
    # alcanzan a cubrir). Se registra el día en que el backlog CRECE, así la
    # suma de tm_no_servida = TM totales que salen tarde o no salen.
    # tm_pendiente_fin_horizonte = lo que ni siquiera se alcanza a producir.
    riesgo = []
    for s in skus:
        pendiente_fin = bl[s][dias_calculo[-1]].value() or 0.0
        previo = 0.0
        for t in dias_calculo:
            v = bl[s][t].value() or 0.0
            nuevo = v - previo
            previo = v
            if nuevo > 0.01:
                riesgo.append({
                    "fecha": t.strftime("%Y-%m-%d"), "sku": s,
                    "tm_no_servida": round(nuevo, 2),
                    "tm_pendiente_fin_horizonte": round(pendiente_fin, 2),
                    "politica": "MTS" if s in mts else "MTO",
                })

    for r in riesgo:
        r["causa"] = "CAPACIDAD_O_LEAD_TIME"

    # Pedidos de SKU que el modelo NO puede planificar (sin línea/rate para su
    # calibre-forma, o fuera de posicionamiento_pt). Antes quedaban fuera del
    # riesgo en silencio; ahora se reportan con su causa.
    if not d["pedidos"].empty:
        fuera = d["pedidos"][~d["pedidos"]["sku"].isin(skus)].copy()
        fuera["dia"] = fuera["fecha_entrega"].dt.normalize()
        fuera = fuera[fuera["dia"] <= dias[-1]]
        skus_linea = set(lineas["sku"])
        skus_pos = set(pos["sku"])
        for sku_f, g in fuera.groupby("sku"):
            total = float(g["cantidad_tm"].sum())
            if total <= 0.01:
                continue
            causa = ("SIN_POSICIONAMIENTO" if sku_f not in skus_pos
                     else "SIN_LINEA_CONFIGURADA" if sku_f not in skus_linea
                     else "FUERA_DEL_MODELO")
            for dia_f, gd in g.groupby("dia"):
                riesgo.append({
                    "fecha": max(dia_f, dias[0]).strftime("%Y-%m-%d"), "sku": sku_f,
                    "tm_no_servida": round(float(gd["cantidad_tm"].sum()), 2),
                    "tm_pendiente_fin_horizonte": round(total, 2),
                    "politica": "MTS" if sku_f in mts else "MTO",
                    "causa": causa,
                })
        n_fuera = fuera.loc[fuera["cantidad_tm"] > 0, "sku"].nunique()
        if n_fuera:
            print(f"  [AVISO] {n_fuera} SKU con pedidos en el horizonte que el modelo NO puede "
                  f"planificar (sin línea configurada o sin posicionamiento) -- van a plan_riesgo.")

    valor_objetivo = pulp.value(prob.objective)

    return df_plan, pd.DataFrame(riesgo), estado, lineas, valor_objetivo

def correr_escenario(nombre: str, overrides: dict) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        d = cargar_datos(conn)
        d = aplicar_overrides_escenario(d, overrides)

        congelado = {}
        if _existe(conn, "plan_produccion"):
            anterior = pd.read_sql("SELECT sku, recurso, fecha, tm FROM plan_produccion", conn)
            for _, r in anterior.iterrows():
                congelado[(r["sku"], r["recurso"], r["fecha"])] = float(r["tm"])

        plan, riesgo, estado, lineas, valor_objetivo = optimizar(
            d, congelado=congelado, horas_override=d.get("horas_extra"),
            paradas=d.get("paradas"))

        # --- KPIs con plata REAL (no los pesos internos del modelo) ---
        # El margen solo es real cuando hay un pedido en firme que se sirve.
        # Se consideran solo los pedidos que el modelo realmente planificó:
        # SKU con línea configurada y fecha dentro del horizonte.
        mg = d["margen"].set_index("sku")["margen_usd_tm"].to_dict()
        mg_med = float(np.median(list(mg.values()))) if mg else 0.0

        fin_horizonte = LUNES_ACTUAL + timedelta(days=HORIZONTE_DIAS - 1)
        skus_modelo = (set(lineas["sku"]) & set(d["pos"]["sku"])) if not lineas.empty else set()
        ped = d["pedidos"]
        if not ped.empty:
            ped = ped[ped["sku"].isin(skus_modelo)
                      & (ped["fecha_entrega"].dt.normalize() <= fin_horizonte)]

        tm_riesgo = float(riesgo["tm_no_servida"].sum()) if not riesgo.empty else 0.0
        margen_pedidos = float(sum(float(r["cantidad_tm"]) * mg.get(r["sku"], mg_med)
                                   for _, r in ped.iterrows())) if not ped.empty else 0.0
        margen_en_riesgo = float(sum(float(r["tm_no_servida"]) * mg.get(r["sku"], mg_med)
                                     for _, r in riesgo.iterrows())) if not riesgo.empty else 0.0
        margen_real = margen_pedidos - margen_en_riesgo

        return {
            "escenario": nombre,
            "estado": estado,
            # Margen de pedidos en firme que SÍ se sirven (no incluye buffer).
            "margen_bruto_usd": round(margen_real, 0),
            "z_usd": round(valor_objetivo, 0) if valor_objetivo is not None else None,
            "z_nota": ("Z usa pesos internos de prioridad (pedidos >> bodega >> "
                       "buffer), no es utilidad contable -- usar 'margen_bruto_usd' "
                       "para lectura de negocio. Z solo sirve para comparar "
                       "escenarios entre sí."),
            "tm_producidas": round(float(plan["tm"].sum()), 1) if not plan.empty else 0.0,
            "tm_riesgo": round(tm_riesgo, 1),
            # Margen REAL que se pierde por pedidos no servidos a tiempo.
            "penalizacion_riesgo_estimada_usd": round(margen_en_riesgo, 0),
            "n_sku": int(plan["sku"].nunique()) if not plan.empty else 0,
            "plan_detalle": plan.to_dict(orient="records") if not plan.empty else [],
        }
    finally:
        conn.close()

def detectar_cambios(conn, df_plan_nuevo: pd.DataFrame, nfp: pd.DataFrame,
                     posi: pd.DataFrame, pedidos: pd.DataFrame) -> list:
    if not _existe(conn, "plan_produccion"):
        return [{"tipo": "primera_corrida",
                 "mensaje": "Primer plan generado — no hay corrida anterior con la que comparar."}]

    anterior = pd.read_sql("SELECT sku, fecha, tm FROM plan_produccion", conn)
    if anterior.empty:
        return [{"tipo": "primera_corrida",
                 "mensaje": "Primer plan generado — no hay corrida anterior con la que comparar."}]

    anterior["fecha"] = pd.to_datetime(anterior["fecha"], errors="coerce").dt.strftime("%Y-%m-%d")
    anterior["sku"] = anterior["sku"].astype(str).str.strip()
    anterior_agg = anterior.groupby(["sku", "fecha"], as_index=False)["tm"].sum()
    nuevo_agg = (df_plan_nuevo.groupby(["sku", "fecha"], as_index=False)["tm"].sum()
                 if not df_plan_nuevo.empty else pd.DataFrame(columns=["sku", "fecha", "tm"]))

    comp = anterior_agg.merge(nuevo_agg, on=["sku", "fecha"], how="outer",
                              suffixes=("_antes", "_ahora")).fillna(0.0)
    comp["diff"] = comp["tm_ahora"] - comp["tm_antes"]
    comp = comp[comp["diff"].abs() >= UMBRAL_CAMBIO_TM].sort_values(
        "diff", key=abs, ascending=False)

    if comp.empty:
        return [{"tipo": "sin_cambios",
                 "mensaje": "Se mantiene el plan como al inicio: sin cambios relevantes desde la última corrida."}]

    dem_por_sku_fecha = set()
    if not pedidos.empty:
        for _, r in pedidos.iterrows():
            dem_por_sku_fecha.add((r["sku"], r["fecha_entrega"].strftime("%Y-%m-%d")))

    posi_idx = posi.set_index("sku") if "sku" in posi.columns else posi
    nfp_idx = nfp.set_index("sku") if not nfp.empty and "sku" not in nfp.index.names else nfp

    mensajes = []
    for _, r in comp.head(10).iterrows():
        sku, fecha, antes, ahora = r["sku"], r["fecha"], r["tm_antes"], r["tm_ahora"]
        razon = "ajuste del modelo"
        if ahora > antes:
            if (sku, fecha) in dem_por_sku_fecha:
                razon = "pedido nuevo detectado para esta fecha"
            elif sku in nfp_idx.index and sku in posi_idx.index:
                disp = float(nfp_idx.loc[sku, "disponible_tm"]) if "disponible_tm" in nfp_idx.columns else None
                tor = float(posi_idx.loc[sku, "tor"]) if "tor" in posi_idx.columns else None
                if disp is not None and tor is not None and disp < tor:
                    razon = "SKU en riesgo de quiebre (stock bajo el TOR), se prioriza"
                else:
                    razon = "sube la prioridad frente a otros SKU del horizonte"
        else:
            razon = "se reduce -- pedido ya cubierto o se prioriza otro SKU"

        mensajes.append({
            "tipo": "cambio",
            "sku": sku, "fecha": fecha,
            "tm_antes": round(antes, 1), "tm_ahora": round(ahora, 1),
            "mensaje": f"{sku}, {fecha}: {round(antes,1)} -> {round(ahora,1)} TM -- {razon}",
        })

    return mensajes

def guardar_cambios(conn, cambios: list, fecha_calculo: str):
    df = pd.DataFrame(cambios)
    df["fecha_calculo"] = fecha_calculo
    df.to_sql("plan_cambios", conn, if_exists="replace", index=False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restricciones", default=None,
                        help="Ruta a JSON con una restricción de simulación (opcional).")
    args = parser.parse_args()
    restriccion = cargar_restriccion_simulacion(args.restricciones)

    conn = sqlite3.connect(DB_PATH)
    semana1_ini = LUNES_ACTUAL.date()
    semana2_fin = (LUNES_ACTUAL + timedelta(days=13)).date()
    print(f"Plan de producción — semana {LUNES_ACTUAL.isocalendar()[1]} "
          f"({semana1_ini} a {semana2_fin}, 2 semanas)")

    try:
        d = cargar_datos(conn)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        conn.close()
        return

    pos = d["pos"]
    print(f"  {int(pos['es_mts'].astype(bool).sum())} SKU MTS · "
          f"{int((~pos['es_mts'].astype(bool)).sum())} MTO")
    print(f"  {len(d['pedidos'])} pedidos pendientes")

    congelado = {}
    if _existe(conn, "plan_produccion"):
        anterior_completo = pd.read_sql(
            "SELECT sku, recurso, fecha, tm FROM plan_produccion", conn)
        for _, r in anterior_completo.iterrows():
            congelado[(r["sku"], r["recurso"], r["fecha"])] = float(r["tm"])

    try:
        plan, riesgo, estado, lineas, valor_objetivo = optimizar(
            d, restriccion=restriccion, congelado=congelado,
            horas_override=d.get("horas_extra"), paradas=d.get("paradas"))

    except RuntimeError as e:
        print(f"[ERROR] {e}")
        conn.close()
        return

    sin_forma = lineas[~lineas["forma_conocida"]]
    if len(sin_forma):
        print(f"\n  [AVISO] {sin_forma['sku'].nunique()} SKU con forma no reconocida "
              f"(ni AP ni AX): se permiten en todas las líneas configuradas para su calibre.")

    if plan.empty:
        print("\n  El modelo no propone producción. Puede que el inventario ya "
              "cubra la demanda del horizonte.")
        conn.close()
        return

    cambios = detectar_cambios(conn, plan, d["nfp"], pos, d["pedidos"])
    guardar_cambios(conn, cambios, HOY.strftime("%Y-%m-%d"))

    plan["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    plan.to_sql("plan_produccion", conn, if_exists="replace", index=False)
    # SIEMPRE se reemplaza: si esta corrida no tiene riesgo, la tabla queda
    # vacía (antes quedaba el riesgo de la corrida anterior y la app lo mostraba).
    if riesgo.empty:
        riesgo = pd.DataFrame(columns=["fecha", "sku", "tm_no_servida",
                                       "tm_pendiente_fin_horizonte", "politica", "causa"])
    riesgo["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    riesgo.to_sql("plan_riesgo", conn, if_exists="replace", index=False)
    conn.commit()

    print("\n" + "=" * 60)
    print("QUÉ CAMBIÓ DESDE LA ÚLTIMA CORRIDA")
    print("=" * 60)
    for c in cambios:
        print(f"  {c['mensaje']}")

    print("\n" + "=" * 60)
    print("PLAN PROPUESTO")
    print("=" * 60)
    print(f"  {plan['tm'].sum():,.0f} TM en {plan['sku'].nunique()} SKU")
    print(f"  MTO (obligatorio): {plan.loc[plan.politica=='MTO','tm'].sum():,.0f} TM")
    print(f"  MTS (objetivo):    {plan.loc[plan.politica=='MTS','tm'].sum():,.0f} TM")

    if "motivo_planta" in plan.columns and plan["flete_usd_tm"].notna().any():
        con_f = plan[plan["flete_usd_tm"].notna() & (plan["motivo"] == "PEDIDO")]
        print("\n--- Flete (desempate debajo del buffer) ---")
        print(f"  TM para PEDIDOS con costo de flete: {con_f['tm'].sum():,.0f} | flete estimado: "
              f"{(con_f['flete_usd_tm'] * con_f['tm']).sum():,.0f} USD")
        print(f"  Sobrecosto por no producir en la planta más barata: "
              f"{plan['sobrecosto_flete_usd'].sum():,.0f} USD")
        print(plan.groupby("motivo_planta")["tm"].sum().round(1).to_string())

    pedidos_tm = d["pedidos"]["cantidad_tm"].sum() if not d["pedidos"].empty else 0
    inventario_tm = (
        d["nfp"]["disponible_tm"].sum()
        if not d["nfp"].empty and "disponible_tm" in d["nfp"].columns
        else 0
    )
    deficit_tm = max(pedidos_tm - inventario_tm, 0)

    print("\n" + "=" * 60)
    print("RESUMEN EJECUTIVO")
    print("=" * 60)
    print(f"  Pedidos pendientes:    {pedidos_tm:,.0f} TM")
    print(f"  Inventario disponible: {inventario_tm:,.0f} TM")
    print(f"  Déficit neto:          {deficit_tm:,.0f} TM")

    if "motivo" in plan.columns:
        ped = plan.loc[plan.motivo == "PEDIDO", "tm"].sum()
        buf = plan.loc[plan.motivo == "BUFFER", "tm"].sum()
        print(f"\n  Producción para pedidos: {ped:,.0f} TM")
        print(f"  Producción para buffer:  {buf:,.0f} TM")
        print("   Las corridas de BUFFER se pueden postergar sin incumplir a")
        print("   nadie. Las de PEDIDO, no.")

    col_rec = "recurso" if "recurso" in plan.columns else "linea"
    print("\n--- Carga por línea (TM / horas reales disponibles) ---")
    car = (plan.groupby([col_rec, "fecha"]).agg(tm=("tm", "sum"), h=("horas", "sum")).reset_index())
    res = (car.groupby(col_rec)
           .agg(tm_total=("tm", "sum"), h_dia_prom=("h", "mean"), h_dia_max=("h", "max")).round(1))
    horas_dinamicas = d.get("horas_extra") or {}
    res["horas_disponibles"] = [
        horas_dinamicas.get(ln, HORAS_DIA_LINEA_REAL.get(ln, HORAS_DIA_LINEA)) for ln in res.index]
    res["utilizacion"] = (res["h_dia_prom"] / res["horas_disponibles"]).round(2)
    print(res.to_string())

    print("\n--- Primeros 3 días ---")
    for f in sorted(plan["fecha"].unique())[:3]:
        dd = plan[plan["fecha"] == f]
        print(f"\n  {f} — {dd['tm'].sum():,.0f} TM")
        print(dd[[c for c in ["linea", "sku", "tm", "horas", "politica", "motivo"] if c in dd.columns]]
              .sort_values(["linea", "tm"], ascending=[True, False]).to_string(index=False))

    if not riesgo.empty:
        print("\n" + "=" * 60)
        print("DEMANDA EN RIESGO")
        print("=" * 60)
        print(f"  {riesgo['tm_no_servida'].sum():,.1f} TM de pedidos salen tarde o no salen")
        pend = riesgo.drop_duplicates("sku")["tm_pendiente_fin_horizonte"].sum()
        print(f"  {pend:,.1f} TM ni siquiera se alcanzan a producir en el horizonte")
        print(f"  {riesgo['sku'].nunique()} SKU afectados")
        print("\n  El modelo NO elige a qué cliente postergar: Consultar al Simulador")
        print("  Estos son los faltantes para que se resuelva con el")
        print("  dato delante.")
        print(riesgo.groupby(["sku", "politica"])["tm_no_servida"].sum()
              .sort_values(ascending=False).head(15).round(1).to_string())
    else:
        print("\n  Toda la demanda del horizonte se puede cumplir.")

    print("\n[OK] Tablas: plan_produccion, plan_cambios, plan_riesgo")
    conn.close()

if __name__ == "__main__":
    if "--escenario-json" in sys.argv:
        def _limpiar_nan(obj):
            if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
                return None
            if isinstance(obj, dict):
                return {k: _limpiar_nan(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_limpiar_nan(v) for v in obj]
            return obj

        ruta_escenario = sys.argv[sys.argv.index("--escenario-json") + 1]
        with open(ruta_escenario, "r", encoding="utf-8") as f:
            datos_escenario = json.load(f)
        resultado = correr_escenario(
            datos_escenario.get("nombre", "Escenario"),
            datos_escenario.get("overrides", {}) or {})
        print("###RESULTADO_ESCENARIO_JSON###")
        print(json.dumps(_limpiar_nan(resultado), default=str, allow_nan=False))
    else:
        main()