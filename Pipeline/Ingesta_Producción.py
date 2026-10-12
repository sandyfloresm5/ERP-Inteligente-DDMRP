"""
Ingesta_Produccion.py
Cargill Ecuador - Aquanutrition
Módulo 4 - Insumos de capacidad para el MILP
"""

import sys
import shutil
import tempfile
import unicodedata
from pathlib import Path
from datetime import date

import pandas as pd
import numpy as np
import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import (
    DB_PATH,
    EXCEL_RATIO_PLANIFICACION, SHEET_RATIO_PLANIFICACION,
    EXCEL_PLANIFICACION_PT_DURAN, SHEET_PLANIFICACION_PT_DURAN,
    EXCEL_PLANIFICACION_PT_GYE, SHEET_PLANIFICACION_PT_GYE,
)

# Ventana para estimar capacidad. Más atrás la configuración de planta
# puede haber cambiado y el dato deja de representar la línea de hoy.
MESES_VENTANA_CAPACIDAD = 12

# Percentil de la producción diaria que se toma como capacidad efectiva.
# El máximo es un día irrepetible; el promedio mide demanda, no capacidad.
PERCENTIL_CAPACIDAD = 0.95

# Mínimo de días con producción para que una línea tenga capacidad
# estimable. Con menos, el percentil es ruido.
MIN_DIAS_LINEA = 20

HOY = date.today().isoformat()

# ---------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------

def _norm(t) -> str:
    if t is None:
        return ""
    s = str(t).strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    for ch in ".,;:()[]{}/\\-_\n\r\t":
        s = s.replace(ch, " ")
    return " ".join(s.split())

def _ruta_legible(ruta: Path) -> Path:
    """Si el archivo está abierto en Excel, Windows lo bloquea."""
    try:
        with open(ruta, "rb"):
            return ruta
    except PermissionError:
        tmp = Path(tempfile.gettempdir()) / f"_sim_{ruta.name}"
        shutil.copy2(ruta, tmp)
        print(f"  [INFO] Archivo abierto en Excel -- se lee una copia temporal.")
        return tmp

def _col(df, alias, requerida=True, nombre=""):
    """Coincidencia exacta antes que parcial."""
    mapa = {_norm(c): c for c in df.columns}
    for a in alias:
        if _norm(a) in mapa:
            return mapa[_norm(a)]
    for a in alias:
        na = _norm(a)
        for k, real in mapa.items():
            if na and na in k:
                return real
    if requerida:
        raise KeyError(f"No se encontró '{nombre or alias[0]}'.\n"
                       f"  Alias: {alias}\n  Columnas: {list(df.columns)}")
    return None

def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")

# ---------------------------------------------------------------------
# 1. Ratios de planificación
# ---------------------------------------------------------------------

def leer_ratios() -> pd.DataFrame:
    print("\n=== Ratio de planificación (TM/hora) ===")
    print(f"  {Path(EXCEL_RATIO_PLANIFICACION).name} / "
          f"hoja '{SHEET_RATIO_PLANIFICACION}'")

    df = pd.read_excel(_ruta_legible(Path(EXCEL_RATIO_PLANIFICACION)),
                       sheet_name=SHEET_RATIO_PLANIFICACION)
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
    print(f"  {len(df)} filas")

    c_planta = _col(df, ["planta"], nombre="Planta")
    c_linea = _col(df, ["linea de producto"], nombre="Linea de Producto")
    c_sap = _col(df, ["cod sap"], nombre="cod Sap")
    c_nis = _col(df, ["sku actual"], requerida=False)
    c_nombre = _col(df, ["nombre sku actual"], requerida=False)
    c_rate = _col(df, ["rate planificacion"], nombre="Rate Planificación")
    c_etapa = _col(df, ["etapa"], requerida=False)
    c_calibre = _col(df, ["calibre"], requerida=False)
    c_forma = _col(df, ["forma"], requerida=False)
    c_peso = _col(df, ["peso unitario"], requerida=False)

    out = pd.DataFrame({
        "planta": df[c_planta].astype(str).str.strip(),
        "linea_producto": df[c_linea].astype(str).str.strip(),
        "sku_sap": df[c_sap].map(lambda v: str(v).strip().replace(".0", "")
                                 if pd.notna(v) else ""),

        "sku_nis": df[c_nis].map(lambda v: str(v).strip().replace(".0", "")
                                 if pd.notna(v) else ""),
        "descripcion": (df[c_nombre].astype(str).str.strip() if c_nombre else ""),
        "etapa": (df[c_etapa].astype(str).str.strip() if c_etapa else ""),
        "calibre": (df[c_calibre].astype(str).str.strip() if c_calibre else ""),
        "forma": (df[c_forma].astype(str).str.strip() if c_forma else ""),
        "peso_unitario_kg": _num(df[c_peso]) if c_peso else np.nan,
        "rate_tm_hora": _num(df[c_rate]),
    })

    # SAP exporta los códigos rellenos a 18 dígitos ('000000000107708597')
    # pero la base de ratios ya los trae limpios ('107708597'). Aplicar
    # lstrip("0") indiscriminadamente corta ceros legítimos de códigos que
    # no están rellenos: eso llevó el cruce contra el buffer de 51 a 0 SKU.
    # Se normaliza SOLO cuando la longitud delata el relleno.
    out["sku_sap"] = out["sku_sap"].astype(str).str.strip()
    _largos = out["sku_sap"].str.len() > 12
    out.loc[_largos, "sku_sap"] = out.loc[_largos, "sku_sap"].str.lstrip("0")

    # 'cod Sap' viene con #N/D en varias filas: esos SKU no se pueden
    # conectar con el buffer y quedan fuera.
    sin_sap = out["sku_sap"].isin(["#N/D", "#N/A", "nan", "", "None"])
    n_sin = int(sin_sap.sum())
    if n_sin:
        print(f"  [AVISO] {n_sin} de {len(out)} filas sin código SAP en la base de "
              f"ratios. Esos SKU no se pueden cruzar con el buffer.")
    out = out[~sin_sap].copy()

    out = out[out["rate_tm_hora"] > 0]

    # Un SKU con ratio distinto por línea NO es un error: es el dato que
    # hace que dónde producir sea una decisión con consecuencia.
    var = out.groupby("sku_sap")["rate_tm_hora"].nunique()
    n_var = int((var > 1).sum())

    print(f"  {len(out)} filas con SAP y ratio válido")
    print(f"  {out['sku_sap'].nunique()} SKU distintos")
    print(f"  {out['planta'].nunique()} plantas: {sorted(out['planta'].unique())}")
    print(f"  {out['linea_producto'].nunique()} líneas de producto")
    if n_var:
        print(f"  {n_var} SKU tienen ratio DISTINTO según la línea. El tiempo de")
        print(f"  máquina depende de dónde se produzca, así que la asignación de")
        print(f"  línea es una decisión con impacto en capacidad.")
    print(f"  Ratio: mediana {out['rate_tm_hora'].median():.1f} TM/h "
          f"(rango {out['rate_tm_hora'].min():.1f}-{out['rate_tm_hora'].max():.1f})")

    return out.reset_index(drop=True)

# ---------------------------------------------------------------------
# 2. Puente NIS -> SAP desde la base de ratios
# ---------------------------------------------------------------------

def puente_desde_ratios(ratios: pd.DataFrame) -> pd.DataFrame:
    """
    Traducción NIS -> SAP construida desde la base de ratios.

    POR QUÉ NO SE USA EL TRADUCTOR SAP-NIS PARA PRODUCTO TERMINADO
    --------------------------------------------------------------
    Ese archivo cubre materia prima, empaques y activos fijos: sus códigos
    NIS (11580, 900275) pertenecen a otro universo que los del plan de
    producción (220352AP). Cruzarlos dio CERO coincidencias sobre 16.556
    filas y 1,6 millones de TM.

    La base de ratios sí es la fuente correcta: es el archivo sobre el que
    el planificador de producción trabaja, y trae 'SKU actual' (el código
    NIS del plan) junto a 'cod Sap' en la misma fila. Todo SKU que se
    planifica está ahí por construcción.

    Se normaliza quitando ceros a la izquierda y espacios: SAP exporta los
    códigos rellenos a 18 dígitos y un espacio invisible basta para que el
    merge falle sin avisar.
    """
    if ratios.empty or "sku_nis" not in ratios.columns:
        return pd.DataFrame(columns=["sku_nis", "sku_sap"])

    p = ratios[["sku_nis", "sku_sap"]].copy()
    p["sku_nis"] = p["sku_nis"].astype(str).str.strip()
    p["sku_sap"] = p["sku_sap"].astype(str).str.strip()
    _largos = p["sku_sap"].str.len() > 12
    p.loc[_largos, "sku_sap"] = p.loc[_largos, "sku_sap"].str.lstrip("0")
    p = p[~p["sku_nis"].isin(["", "nan", "None", "#N/D", "#N/A"])]
    p = p[~p["sku_sap"].isin(["", "nan", "None", "#N/D", "#N/A"])]

    # Un mismo NIS aparece en varias líneas con ratios distintos, pero con
    # el mismo SAP: para el puente basta una fila por NIS.
    dup = p.groupby("sku_nis")["sku_sap"].nunique()
    ambiguos = dup[dup > 1]
    if len(ambiguos):
        print(f"  ⚠️  {len(ambiguos)} códigos NIS con más de un SAP en la base de "
              f"ratios -- se toma el primero:")
        for nis in list(ambiguos.index)[:5]:
            saps = sorted(p.loc[p["sku_nis"] == nis, "sku_sap"].unique())
            print(f"      {nis} -> {saps}")

    return p.drop_duplicates("sku_nis", keep="first").reset_index(drop=True)


# ---------------------------------------------------------------------
# 3. Histórico de producción
# ---------------------------------------------------------------------

def archivos_historicos(ruta_ejemplo: Path) -> list:
    """
    Busca por patrón en vez de ruta fija: los archivos se llaman
    'Data historica FY 25-26.xlsx' y cambian cada año fiscal. Así el
    próximo entra solo, sin tocar config.
    """
    carpeta = Path(ruta_ejemplo).parent
    if not carpeta.exists():
        return []
    return sorted(carpeta.glob("Data historica FY *.xlsx"))

def leer_produccion(ruta: Path, hoja: str, etiqueta: str) -> pd.DataFrame:
    df = pd.read_excel(_ruta_legible(Path(ruta)), sheet_name=hoja)
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

    c_fecha = _col(df, ["fecha"], nombre="Fecha")
    c_linea = _col(df, ["rtduet linea", "linea"], nombre="Linea")
    c_sku = _col(df, ["sku"], nombre="SKU")
    c_ens = _col(df, ["ensacado"], nombre="Ensacado")
    c_plan = _col(df, ["planificado"], requerida=False)
    c_vel = _col(df, ["velocidad maxima"], requerida=False)
    c_planta = _col(df, ["planta"], requerida=False)
    c_fy = _col(df, ["fiscal year"], requerida=False)
    c_sem = _col(df, ["semana"], requerida=False)
    c_forma = _col(df, ["forma"], requerida=False)
    c_desc = _col(df, ["descripcion"], requerida=False)

    out = pd.DataFrame({
        "fecha": pd.to_datetime(df[c_fecha], errors="coerce", dayfirst=True),
        "linea": df[c_linea].astype(str).str.strip(),
        "sku_nis": df[c_sku].astype(str).str.strip(),
        "descripcion": (df[c_desc].astype(str).str.strip() if c_desc else ""),
        "forma": (df[c_forma].astype(str).str.strip() if c_forma else ""),
        "ensacado_tm": _num(df[c_ens]),
        "planificado_tm": _num(df[c_plan]) if c_plan else np.nan,
        "velocidad_maxima": _num(df[c_vel]) if c_vel else np.nan,
        "planta": (df[c_planta].astype(str).str.strip() if c_planta
                   else etiqueta),
        "fiscal_year": (df[c_fy].astype(str).str.strip() if c_fy else ""),
        "semana": _num(df[c_sem]) if c_sem else np.nan,
    })

    out = out.dropna(subset=["fecha"])
    out = out[~out["sku_nis"].isin(["", "nan", "None"])]
    out["planta"] = out["planta"].replace({"nan": etiqueta, "": etiqueta})
    return out

def leer_todo_historico() -> pd.DataFrame:
    print("\n=== Histórico de producción ===")
    partes = []

    for ruta_base, hoja, etiqueta in [
        (EXCEL_PLANIFICACION_PT_DURAN, SHEET_PLANIFICACION_PT_DURAN, "DURAN"),
        (EXCEL_PLANIFICACION_PT_GYE, SHEET_PLANIFICACION_PT_GYE, "CANE"),
    ]:
        archivos = archivos_historicos(ruta_base)
        if not archivos:
            print(f"  [AVISO] Sin archivos 'Data historica FY *' en "
                  f"{Path(ruta_base).parent}")
            continue

        print(f"\n  {etiqueta}: {len(archivos)} archivo(s)")
        for a in archivos:
            try:
                d = leer_produccion(a, hoja, etiqueta)
                partes.append(d)
                print(f"    {a.name}: {len(d)} filas "
                      f"({d['fecha'].min():%Y-%m} a {d['fecha'].max():%Y-%m})")
            except Exception as e:
                print(f"    [ERROR] {a.name}: {type(e).__name__}: {e}")

    if not partes:
        return pd.DataFrame()

    todo = pd.concat(partes, ignore_index=True)

    # Los archivos de distintos años fiscales traen las mismas columnas con
    # tipos distintos: 'linea' viene como 'L1' en unos y como 1.0 en otros,
    # 'planta' a veces numérica. Al concatenar quedan columnas mixtas y
    # cualquier groupby o sorted revienta con TypeError. Se normalizan a
    # texto antes de usarlas como claves.
    # Los archivos de distintos años fiscales traen las mismas columnas con
    # tipos distintos: 'linea' viene como 'L1' en unos y como 1.0 en otros.
    # Al concatenar queda una columna object con str y float mezclados, y
    # cualquier sorted() o groupby revienta con TypeError.
    #
    # .astype(str).str.replace() NO alcanza: sobre una columna object con
    # floats deja NaN reales en las celdas no-string y la columna vuelve a
    # quedar mixta. .map(str) convierte celda por celda sin esa trampa.
    for col in ["linea", "planta", "sku_nis", "forma", "fiscal_year"]:
        if col in todo.columns:
            todo[col] = todo[col].map(lambda v: str(v).strip())
            todo[col] = todo[col].map(
                lambda v: v[:-2] if v.endswith(".0") else v)

    todo["planta"] = todo["planta"].replace({"nan": "", "None": ""})
    todo.loc[todo["planta"] == "", "planta"] = "SIN_PLANTA"

    print(f"\n  Total: {len(todo)} filas, "
          f"{todo['fecha'].min():%Y-%m-%d} a {todo['fecha'].max():%Y-%m-%d}")
    print(f"  Plantas: {sorted(todo['planta'].unique())}")
    print(f"  Líneas: {sorted(todo['linea'].unique())}")
    return todo


# ---------------------------------------------------------------------
# 4. Capacidad por línea
# ---------------------------------------------------------------------

def calcular_capacidad(prod: pd.DataFrame) -> pd.DataFrame:
    """
    Capacidad efectiva por línea, desde la producción diaria observada.

    Se usa el percentil 95 y no el máximo: el máximo es un día
    excepcional que no se puede sostener, y planificar contra él garantiza
    incumplir. Tampoco el promedio, porque incluye días de baja demanda --
    la línea no produjo más porque no había qué producir, no porque no
    pudiera. El p95 se acerca a "lo que la línea logra cuando hay trabajo".
    """
    print(f"\n=== Capacidad por línea "
          f"(últimos {MESES_VENTANA_CAPACIDAD} meses) ===")

    if prod.empty:
        return pd.DataFrame()

    corte = prod["fecha"].max() - pd.DateOffset(months=MESES_VENTANA_CAPACIDAD)
    v = prod[prod["fecha"] > corte].copy()
    print(f"  Ventana: {corte:%Y-%m-%d} a {prod['fecha'].max():%Y-%m-%d} "
          f"({len(v)} de {len(prod)} filas)")

    diario = (v[v["ensacado_tm"] > 0]
              .groupby(["planta", "linea", "fecha"], as_index=False)["ensacado_tm"]
              .sum())

    cap = (diario.groupby(["planta", "linea"])["ensacado_tm"]
           .agg(dias_con_produccion="count",
                tm_dia_mediana="median",
                tm_dia_p95=lambda s: s.quantile(PERCENTIL_CAPACIDAD),
                tm_dia_max="max")
           .reset_index())

    # Velocidad nominal declarada, para contrastar con lo observado.
    if "velocidad_maxima" in v.columns and v["velocidad_maxima"].notna().any():
        nominal = (v.groupby(["planta", "linea"])["velocidad_maxima"]
                   .max().reset_index()
                   .rename(columns={"velocidad_maxima": "velocidad_nominal_tm_h"}))
        cap = cap.merge(nominal, on=["planta", "linea"], how="left")

    confiable = cap["dias_con_produccion"] >= MIN_DIAS_LINEA
    cap["capacidad_confiable"] = confiable

    n_pocas = int((~confiable).sum())
    if n_pocas:
        print(f"  [AVISO] {n_pocas} línea(s) con menos de {MIN_DIAS_LINEA} días de "
              f"producción: su capacidad es un percentil sobre pocos datos y no "
              f"debería usarse como restricción dura.")

    print(f"\n  {len(cap)} líneas con capacidad estimada")
    cols = ["planta", "linea", "dias_con_produccion", "tm_dia_mediana",
            "tm_dia_p95", "tm_dia_max"]
    print(cap[cols].round(1).to_string(index=False))

    total = cap.loc[confiable, "tm_dia_p95"].sum()
    print(f"\n  Capacidad efectiva total (p95, líneas confiables): "
          f"{total:,.0f} TM/día")

    por_planta = (cap[confiable].groupby("planta")["tm_dia_p95"].sum()
                  .sort_values(ascending=False))
    if len(por_planta):
        print("\n  Por planta (TM/día):")
        print(por_planta.round(0).to_string())

    return cap

def calcular_adherencia(prod: pd.DataFrame) -> pd.DataFrame:
    """
    Adherencia al plan por línea: cuánto de lo planificado se cumplió.

    Es el dato que dice qué tan confiable es un plan de producción en esa
    línea. Un MILP que propone un programa sobre una línea con 60% de
    adherencia está proponiendo algo que históricamente no se cumple.
    """
    if prod.empty or "planificado_tm" not in prod.columns:
        return pd.DataFrame()

    corte = prod["fecha"].max() - pd.DateOffset(months=MESES_VENTANA_CAPACIDAD)
    v = prod[(prod["fecha"] > corte) & (prod["planificado_tm"] > 0)].copy()
    if v.empty:
        return pd.DataFrame()

    ad = (v.groupby(["planta", "linea"])
          .agg(planificado_tm=("planificado_tm", "sum"),
               ensacado_tm=("ensacado_tm", "sum"),
               n_registros=("fecha", "count"))
          .reset_index())
    ad["adherencia"] = ad["ensacado_tm"] / ad["planificado_tm"].replace(0, np.nan)

    print(f"\n=== Adherencia al plan (últimos {MESES_VENTANA_CAPACIDAD} meses) ===")
    print(ad[["planta", "linea", "planificado_tm", "ensacado_tm", "adherencia"]]
          .round(2).to_string(index=False))

    baja = ad[ad["adherencia"] < 0.80]
    if len(baja):
        print(f"\n    {len(baja)} línea(s) con adherencia bajo 80%. Un plan sobre")
        print(f"      esas líneas históricamente no se cumple: conviene descontar")
        print(f"      esa brecha al fijar su capacidad en el modelo.")

    return ad

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"Ingesta de producción — {HOY}")


    try:
        ratios = leer_ratios()
        ratios.to_sql("ratio_planificacion", conn, if_exists="replace", index=False)
    except Exception as e:
        print(f"  [ERROR] Ratios: {type(e).__name__}: {e}")
        ratios = pd.DataFrame()

    try:
        prod = leer_todo_historico()
    except Exception as e:
        print(f"  [ERROR] Histórico: {type(e).__name__}: {e}")
        prod = pd.DataFrame()

    # --- Traducir NIS -> SAP usando la base de ratios ---
    if not prod.empty and not ratios.empty:
        print("\n=== Traducción NIS -> SAP (desde la base de ratios) ===")
        puente = puente_desde_ratios(ratios)
        print(f"  {len(puente)} equivalencias de producto terminado")

        prod["sku_nis"] = prod["sku_nis"].astype(str).str.strip()
        prod = prod.merge(puente, on="sku_nis", how="left")

        sin_trad = prod["sku_sap"].isna()
        n_sin = int(sin_trad.sum())
        tm_ok = float(prod.loc[~sin_trad, "ensacado_tm"].sum())
        tm_sin = float(prod.loc[sin_trad, "ensacado_tm"].sum())

        print(f"  Traducidas: {len(prod) - n_sin} filas ({tm_ok:,.0f} TM)")

        if n_sin:
            faltantes = sorted(prod.loc[sin_trad, "sku_nis"].unique())
            pct = tm_sin / max(tm_ok + tm_sin, 1e-9)
            print(f"  Sin traducir: {n_sin} filas ({tm_sin:,.0f} TM, {pct:.1%})")
            print(f"    {len(faltantes)} códigos: {faltantes[:10]}"
                  f"{' …' if len(faltantes) > 10 else ''}")
            if pct > 0.20:
                print(f"    ⚠️  Más del 20% del tonelaje sin traducir: revisar si el")
                print(f"        formato de código cambió en algún año fiscal.")
            else:
                # SKU que se produjeron alguna vez pero ya no están en la
                # base de ratios: descontinuados o reemplazados. No es un
                # error del cruce, y no entran al modelo, que solo planifica
                # lo que hoy está vigente.
                print(f"    Son SKU que ya no se planifican (descontinuados o")
                print(f"    reemplazados). Quedan fuera del modelo.")

        prod.to_sql("produccion_historica", conn, if_exists="replace", index=False)
    elif not prod.empty:
        print("\n  [AVISO] Sin base de ratios: no se puede traducir NIS -> SAP.")
        prod.to_sql("produccion_historica", conn, if_exists="replace", index=False)

    cap = calcular_capacidad(prod)
    if not cap.empty:
        cap.to_sql("capacidad_linea", conn, if_exists="replace", index=False)

    ad = calcular_adherencia(prod)
    if not ad.empty:
        ad.to_sql("adherencia_linea", conn, if_exists="replace", index=False)

    conn.commit()

    # -----------------------------------------------------------------
    print("\n" + "=" * 62)
    print("COBERTURA CONTRA EL BUFFER")
    print("=" * 62)

    try:
        buf = pd.read_sql(
            "SELECT DISTINCT sku FROM ddmrp_buffer_pt WHERE estado='ok'", conn)
        skus_buffer = set(buf["sku"].astype(str).str.strip())

        if not ratios.empty:
            con_ratio = skus_buffer & set(ratios["sku_sap"])
            print(f"  {len(con_ratio)} de {len(skus_buffer)} SKU del buffer tienen "
                  f"ratio de producción")
            faltan = sorted(skus_buffer - set(ratios["sku_sap"]))
            if faltan:
                print(f"  {len(faltan)} sin ratio: {faltan[:8]}"
                      f"{' …' if len(faltan) > 8 else ''}")
                print(f"  Sin ratio no se puede calcular cuánto tiempo de línea")
                print(f"  consume ese SKU, así que no puede entrar al modelo.")

        if not prod.empty and "sku_sap" in prod.columns:
            producidos = set(prod["sku_sap"].dropna().astype(str))
            print(f"\n  {len(skus_buffer & producidos)} de {len(skus_buffer)} SKU "
                  f"del buffer aparecen en el histórico de producción")
    except Exception as e:
        print(f"  No se pudo comparar: {e}")

    print("\n" + "=" * 62)
    print("TABLAS GENERADAS")
    print("=" * 62)
    for t in ["ratio_planificacion", "produccion_historica",
              "capacidad_linea", "adherencia_linea"]:
        try:
            n = pd.read_sql(f"SELECT COUNT(*) n FROM {t}", conn).iloc[0]["n"]
            print(f"  {t}: {int(n)} filas")
        except Exception:
            print(f"  {t}: no generada")

    conn.close()

if __name__ == "__main__":
    main()