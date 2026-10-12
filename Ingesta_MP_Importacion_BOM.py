"""
Ingesta_MP_Importacion_BOM.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP: OC de importación de MP + BOM semanal por PT-centro
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

from config import DB_PATH, EXCEL_ORDENES_COMPRAS, SHEET_ORDENES_MP, EXCEL_BOM, SHEET_BOM

HOY = date.today().isoformat()
TOLERANCIA_SUMA_PCT = 2.0
MAX_FILAS_BUSCAR_HEADER = 40

STATUS_EXCLUIR_STEMS = ["anulad", "entregad", "nacionalizad", "retiro parcial"]
STATUS_EN_CAMINO_STEMS = ["por embarcar", "produccion prov", "transito", "en puerto"]


def _ruta_legible(ruta: Path) -> Path:
    """Si el archivo está abierto en Excel, Windows lo bloquea: se lee
    una copia temporal en vez de fallar."""
    try:
        with open(ruta, "rb"):
            return ruta
    except PermissionError:
        tmp = Path(tempfile.gettempdir()) / f"_simulador_{ruta.name}"
        shutil.copy2(ruta, tmp)
        print(f"  [INFO] Archivo abierto en Excel -- se lee una copia temporal.")
        return tmp


def _norm(s):
    if pd.isna(s):
        return ""
    t = str(s).strip().lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t)
                if unicodedata.category(c) != "Mn")
    return t


def _status_en(status_norm, stems):
    return any(stem in status_norm for stem in stems)


def _num(serie):
    return pd.to_numeric(serie, errors="coerce")


def _col(df, nombre_esperado):
    def _limpiar(s):
        return " ".join(str(s).strip().lower().split())
    mapa = {_limpiar(c): c for c in df.columns}
    real = mapa.get(_limpiar(nombre_esperado))
    if real is None:
        raise KeyError(f"No se encontró '{nombre_esperado}'. "
                       f"Columnas disponibles: {list(df.columns)}")
    return real


def leer_con_header_dinamico(ruta: Path, hoja: str, columnas_clave: list) -> pd.DataFrame:
    """Busca la fila real de encabezado dentro de las primeras filas del
    Excel (algunos reportes traen títulos/filas vacías antes de la tabla)."""
    ruta = _ruta_legible(Path(ruta))
    crudo = pd.read_excel(ruta, sheet_name=hoja, header=None,
                          nrows=MAX_FILAS_BUSCAR_HEADER)

    claves = [_norm(c) for c in columnas_clave]
    fila_header = None
    for i in range(len(crudo)):
        celdas = [_norm(v) for v in crudo.iloc[i].tolist()]
        celdas = [c for c in celdas if c and c != "nan"]
        if sum(1 for k in claves if any(k in c for c in celdas)) >= 2:
            fila_header = i
            break

    if fila_header is None:
        raise KeyError(f"No se encontró fila de encabezado con {columnas_clave} "
                       f"en las primeras {MAX_FILAS_BUSCAR_HEADER} filas de '{hoja}'.")
    if fila_header > 0:
        print(f"  Encabezados detectados en la fila {fila_header + 1} del Excel")

    df = pd.read_excel(ruta, sheet_name=hoja, header=fila_header)
    df.columns = [str(c).strip() for c in df.columns]
    return df.loc[:, [c for c in df.columns
                      if not str(c).startswith("Unnamed") or df[c].notna().any()]]


def cargar_oc_importacion() -> pd.DataFrame:
    print(f"\n=== OC de importación de MP ===")
    print(f"  {Path(EXCEL_ORDENES_COMPRAS).name} / hoja '{SHEET_ORDENES_MP}'")

    df = leer_con_header_dinamico(EXCEL_ORDENES_COMPRAS, SHEET_ORDENES_MP,
                                  ["status", "cod", "cantidad"])
    print(f"  {len(df)} filas leídas")

    c_status = _col(df, "STATUS")
    c_cod = _col(df, "COD")
    c_cant = _col(df, "CANTIDAD\nEMBARCADA")
    c_ingr = _col(df, "INGREDIENTE")
    c_eta_real = _col(df, "ETA REAL")
    c_eta_est = _col(df, "ETA ESTIMADO")
    c_prov = _col(df, "PROVEEDOR")
    c_oc = _col(df, "OC")
    c_booking = _col(df, "BOOKING")
    c_nave = _col(df, "NAVE")
    c_naviera = _col(df, "NAVIERA")


    df["status_norm"] = df[c_status].apply(_norm)
    df["sku_mp"] = df[c_cod].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["cantidad_tm"] = _num(df[c_cant])

    eta_real = pd.to_datetime(df[c_eta_real], errors="coerce", dayfirst=True)
    eta_est = pd.to_datetime(df[c_eta_est], errors="coerce", dayfirst=True)
    df["fecha_llegada"] = eta_real.fillna(eta_est)
    df["fuente_fecha"] = np.where(eta_real.notna(), "ETA_REAL",
                                  np.where(eta_est.notna(), "ETA_ESTIMADO", "SIN_FECHA"))

    n_sin_fecha = int((df["fuente_fecha"] == "SIN_FECHA").sum())
    if n_sin_fecha:
        print(f"  [AVISO] {n_sin_fecha} OC sin ETA real ni estimado -- quedan sin "
              f"fecha de llegada, no se pueden proyectar.")

    df["excluir"] = df["status_norm"].apply(lambda s: _status_en(s, STATUS_EXCLUIR_STEMS))
    df["en_camino"] = df["status_norm"].apply(lambda s: _status_en(s, STATUS_EN_CAMINO_STEMS))

    n_excl = int(df["excluir"].sum())
    n_camino = int(df["en_camino"].sum())
    n_otro = len(df) - n_excl - n_camino
    status_no_reconocido = sorted(set(
        df.loc[~df["excluir"] & ~df["en_camino"], c_status].dropna().astype(str).unique()))
    print(f"  {n_camino} OC con status en camino · {n_excl} excluidas "
          f"(entregadas/anuladas) · {n_otro} con status no reconocido: "
          f"{status_no_reconocido}")

    out = df[df["en_camino"] & df["cantidad_tm"].notna() & (df["cantidad_tm"] > 0)][
        ["sku_mp", c_ingr, c_status, "cantidad_tm", "fecha_llegada",
        "fuente_fecha", c_prov, c_oc,
        c_naviera, c_nave, c_booking]
    ].rename(columns={
        c_ingr: "descripcion",
        c_status: "status",
        c_prov: "proveedor",
        c_oc: "oc",
        c_naviera: "naviera",
        c_nave: "nave",
        c_booking: "booking"
    })
    out = out.dropna(subset=["fecha_llegada"])
    n_antes_oc = len(out)

    out = out.drop_duplicates(
        subset=[
            "sku_mp",
            "status",
            "cantidad_tm",
            "fecha_llegada",
            "proveedor",
            "naviera",
            "nave",
            "booking"
        ]
    )

    if n_antes_oc - len(out):
        print(f"  {n_antes_oc - len(out)} OC exactamente repetidas -- descartadas.")

    # --- Regla: si la fecha de llegada ya pasó, el material ya llegó ---
    # (está en el stock de bodega). No se cuenta como tránsito.
    hoy = pd.Timestamp(HOY)
    ya_llego = out["fecha_llegada"] < hoy
    if ya_llego.any():
        llegadas = out[ya_llego]
        print(f"\n  [EXCLUIDAS] {len(llegadas)} OC con fecha de llegada anterior a hoy "
              f"({llegadas['cantidad_tm'].sum():,.1f} TM) -- se asumen recibidas, NO "
              f"cuentan como tránsito. Actualizar su status en el Excel:")
        vista = llegadas[["oc", "sku_mp", "descripcion", "status", "cantidad_tm",
                          "fecha_llegada", "fuente_fecha"]].copy()
        vista["fecha_llegada"] = vista["fecha_llegada"].dt.strftime("%Y-%m-%d")
        vista["cantidad_tm"] = vista["cantidad_tm"].round(1)
        print(vista.sort_values("fecha_llegada").head(30).to_string(index=False))
        llegadas_out = llegadas.copy()
        llegadas_out["fecha_llegada"] = llegadas_out["fecha_llegada"].dt.strftime("%Y-%m-%d")
        llegadas_out["fecha_snapshot"] = HOY
    else:
        llegadas_out = pd.DataFrame()
    out = out[~ya_llego].copy()

    out["fecha_llegada"] = out["fecha_llegada"].dt.strftime("%Y-%m-%d")
    out["fecha_snapshot"] = HOY

    print(f"\n  {len(out)} OC en tránsito real (llegan hoy o después) "
          f"por {out['cantidad_tm'].sum():,.1f} TM")
    print(f"  {out['sku_mp'].nunique()} MP distintas con algo en camino")

    return out, llegadas_out


def cargar_bom_semanal() -> pd.DataFrame:
    print(f"\n=== BOM semanal por PT-centro-corrida ===")
    print(f"  {Path(EXCEL_BOM).name} / hoja '{SHEET_BOM}'")

    df = leer_con_header_dinamico(EXCEL_BOM, SHEET_BOM,
                                  ["codigo sap", "centro", "mp"])
    print(f"  {len(df)} filas leídas")

    c_sku_pt = _col(df, "CODIGO SAP")
    c_centro = _col(df, "CENTRO")
    c_mp = _col(df, "MP")
    c_pct = _col(df, "%")
    c_fecha = _col(df, "FECHA PLANEADA")
    c_calibre = _col(df, "calibre")

    df["sku_pt"] = df[c_sku_pt].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["centro"] = df[c_centro].astype(str).str.strip()
    df["sku_mp"] = df[c_mp].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["pct"] = _num(df[c_pct])
    df["fecha_planeada"] = pd.to_datetime(df[c_fecha], errors="coerce", dayfirst=True)
    df["calibre"] = df[c_calibre].astype(str).str.strip()

    if df["pct"].dropna().median() <= 1.5:
        df["pct"] = df["pct"] * 100
        print("  % detectado en fracción (0-1) -- convertido a porcentaje.")

    # La fórmula es POR CORRIDA Y CALIBRE (PT + centro + fecha + calibre).
    # Filas literalmente repetidas se descartan ANTES de agrupar (bug del
    # 500%); solo se suma cuando el % es distinto (aporte parcial legítimo).
    n_crudo = len(df)
    df = df.drop_duplicates(subset=["sku_pt", "centro", "fecha_planeada", "calibre",
                                    "sku_mp", "pct"])
    n_repetidas = n_crudo - len(df)
    if n_repetidas:
        print(f"  {n_repetidas} filas EXACTAMENTE repetidas (mismo % ya visto) -- "
              f"descartadas, no sumadas.")

    n_antes = len(df)
    col_desc_mp = "Tabla8.DESCRIPCION"
    tiene_desc = col_desc_mp in df.columns
    agg_cols = {"pct": ("pct", "sum")}
    if tiene_desc:
        agg_cols["descripcion_mp"] = (col_desc_mp, "first")
    agregado = (df.groupby(["sku_pt", "centro", "fecha_planeada", "calibre", "sku_mp"],
                           as_index=False)
                  .agg(**agg_cols))
    if not tiene_desc:
        agregado["descripcion_mp"] = agregado["sku_mp"]
    n_dup = n_antes - len(agregado)
    if n_dup:
        print(f"  {n_dup} filas de MP duplicada por PT-centro-fecha-calibre -- "
              f"sumadas, no descartadas.")

    suma_pt = (agregado.groupby(["sku_pt", "centro", "fecha_planeada", "calibre"])["pct"]
               .sum().reset_index())
    suma_pt["sospechoso"] = (suma_pt["pct"] - 100).abs() > TOLERANCIA_SUMA_PCT
    n_sosp = int(suma_pt["sospechoso"].sum())
    if n_sosp:
        print(f"  [AVISO] {n_sosp} de {len(suma_pt)} corridas (PT-centro-fecha-calibre) "
              f"NO suman ~100% (tolerancia ±{TOLERANCIA_SUMA_PCT}pp) -- revisar antes "
              f"de confiar en la explosión de consumo.")
        peores = suma_pt[suma_pt["sospechoso"]].copy()
        peores["desvio"] = (peores["pct"] - 100).abs()
        print(peores.sort_values("desvio", ascending=False).head(5)
              .drop(columns="desvio").round(1).to_string(index=False))
    else:
        print(f"  Las {len(suma_pt)} corridas suman ~100% -- OK.")

    agregado = agregado.merge(
        suma_pt[["sku_pt", "centro", "fecha_planeada", "calibre", "sospechoso"]],
        on=["sku_pt", "centro", "fecha_planeada", "calibre"], how="left")
    agregado["fecha_snapshot"] = HOY

    print(f"  {agregado['sku_pt'].nunique()} PT · {agregado['sku_mp'].nunique()} MP "
          f"distintas · {suma_pt.shape[0]} corridas (fecha+calibre) en el BOM de esta semana")

    return agregado


def main():
    conn = sqlite3.connect(DB_PATH)

    try:
        oc, llegadas = cargar_oc_importacion()
        conn.execute("DROP TABLE IF EXISTS oc_importacion_mp")
        oc.to_sql("oc_importacion_mp", conn, if_exists="replace", index=False)
        # Auditoría: OC con fecha vencida que se asumieron recibidas.
        # Se crea SIEMPRE (vacía si no hay OC vencidas) para que la
        # validación distinga "0 vencidas" de "ingesta no corrida".
        conn.execute("DROP TABLE IF EXISTS oc_importacion_mp_vencidas")
        if llegadas.empty:
            llegadas = pd.DataFrame(columns=["sku_mp", "descripcion", "status", "cantidad_tm",
                                             "fecha_llegada", "fuente_fecha", "proveedor",
                                             "oc", "fecha_snapshot"])
        llegadas.to_sql("oc_importacion_mp_vencidas", conn, if_exists="replace", index=False)
    except Exception as e:
        print(f"  [ERROR] OC de importación: {type(e).__name__}: {e}")

    try:
        bom = cargar_bom_semanal()
        conn.execute("DROP TABLE IF EXISTS bom_semanal_pt")
        bom.to_sql("bom_semanal_pt", conn, if_exists="replace", index=False)
    except Exception as e:
        print(f"  [ERROR] BOM semanal: {type(e).__name__}: {e}")

    conn.commit()
    conn.close()
    print("\n[OK] Tablas: oc_importacion_mp (solo tránsito real), "
          "oc_importacion_mp_vencidas (auditoría), bom_semanal_pt")


if __name__ == "__main__":
    main()