"""
Ingesta_Inventario.py
Cargill Ecuador - Aquanutrition
Módulo 3 - DDMRP: posición de inventario
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
    EXCEL_STOCK_PT, SHEET_STOCK_PT,
    EXCEL_PEDIDOS_PENDIENTES_PT, SHEET_PEDIDOS_PENDIENTES_PT,
    EXCEL_MP_STOCK, SHEET_MP_STOCK,
)

TOLERANCIA_SUMA = 0.02
MAX_FILAS_BUSCAR_HEADER = 40

HOY = date.today().isoformat()


def _norm(texto) -> str:
    if texto is None:
        return ""
    s = str(texto).strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    for ch in ".,;:()[]{}/\\-_\n\r\t":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _ruta_legible(ruta: Path) -> Path:
    """Si el archivo está abierto en Excel, Windows lo bloquea: se lee una copia."""
    try:
        with open(ruta, "rb"):
            return ruta
    except PermissionError:
        tmp = Path(tempfile.gettempdir()) / f"_simulador_{ruta.name}"
        shutil.copy2(ruta, tmp)
        print(f"  [INFO] Archivo abierto en Excel -- se lee una copia temporal.")
        return tmp


def leer_hoja(ruta: Path, hoja: str, columnas_clave: list) -> pd.DataFrame:
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
        mejor, mejor_n = None, 0
        for i in range(len(crudo)):
            celdas = [v for v in crudo.iloc[i].tolist()
                      if isinstance(v, str) and v.strip()]
            if len(celdas) > mejor_n:
                mejor, mejor_n = i, len(celdas)
        fila_header = mejor if mejor_n >= 4 else 0
        print(f"  [AVISO] No se reconocieron {columnas_clave}. "
              f"Se asume encabezado en la fila {fila_header + 1}.")
    elif fila_header > 0:
        print(f"  Encabezados detectados en la fila {fila_header + 1} del Excel")

    df = pd.read_excel(ruta, sheet_name=hoja, header=fila_header)
    return df.loc[:, [c for c in df.columns
                      if not str(c).startswith("Unnamed") or df[c].notna().any()]]


def _buscar_columna(df, alias, requerida=True, nombre_logico=""):
    """Exacta antes que parcial: 'Libre utilización' vs 'Libre utilización [TM]'."""
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
        raise KeyError(f"No se encontró '{nombre_logico or alias[0]}'.\n"
                       f"  Alias: {alias}\n  Columnas: {list(df.columns)}")
    return None


def _num(serie) -> pd.Series:
    return pd.to_numeric(serie, errors="coerce").fillna(0.0)


def guardar_snapshot(conn, tabla: str, df: pd.DataFrame, claves: list):
    """
    Append borrando las filas del mismo día. Si el esquema de la tabla no
    coincide con el DataFrame (columnas nuevas en una versión posterior del
    script), se recrea: un insert contra un esquema viejo falla entero.
    """
    df = df.copy()
    df["fecha_snapshot"] = HOY

    existe = True
    try:
        conn.execute(f"SELECT 1 FROM {tabla} LIMIT 1").fetchone()
    except Exception:
        existe = False

    if existe:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
        faltantes = set(df.columns) - cols
        if "fecha_snapshot" not in cols or faltantes:
            if faltantes:
                print(f"  [MIGRACIÓN] '{tabla}' no tiene {sorted(faltantes)} -- "
                      f"se recrea.")
            conn.execute(f"DROP TABLE {tabla}")
        else:
            n = conn.execute(f"DELETE FROM {tabla} WHERE fecha_snapshot = ?",
                             (HOY,)).rowcount
            if n:
                print(f"  Reemplazando {n} filas del snapshot de hoy en '{tabla}'")

    df.to_sql(tabla, conn, if_exists="append", index=False)

    # Índice NO único: un pedido tiene varias posiciones y la clave se
    # repite legítimamente. El borrado por fecha ya evita duplicados.
    try:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_snap "
                     f"ON {tabla}({', '.join(claves)}, fecha_snapshot)")
    except Exception:
        pass


# ---------------------------------------------------------------------
# Inventario (mismo layout PT y MP)
# ---------------------------------------------------------------------

COLUMNAS_CLAVE_INVENTARIO = ["material", "libre utilizacion", "bloqueado"]


def leer_inventario(ruta: Path, hoja: str, etiqueta: str) -> pd.DataFrame:
    print(f"\n=== Inventario en curso: {etiqueta} ===")
    print(f"  {Path(ruta).name} / hoja '{hoja}'")

    df = leer_hoja(ruta, hoja, COLUMNAS_CLAVE_INVENTARIO)
    print(f"  {len(df)} filas, {len(df.columns)} columnas")

    c_material = _buscar_columna(df, ["material"], nombre_logico="Material")
    c_desc = _buscar_columna(df, ["texto breve material", "descripcion"], False)
    c_centro = _buscar_columna(df, ["centro"], False)
    c_almacen = _buscar_columna(df, ["almacen"], False)
    c_libre = _buscar_columna(df, ["libre utilizacion"], nombre_logico="Libre utilización")
    c_bloq = _buscar_columna(df, ["bloqueado"], False)
    c_insp = _buscar_columna(df, ["inspecc de calidad", "inspeccion de calidad"], False)
    c_trans = _buscar_columna(df, ["stock en transito", "transito"], False)
    c_tras = _buscar_columna(df, ["en traslado centro", "en traslado"], False)
    c_total_kg = _buscar_columna(df, ["total stock kg"], False)
    c_total_tm = _buscar_columna(df, ["total stock tm"], False)

    if c_trans is None:
        print("  [AVISO] Sin columna de 'Stock en tránsito'. El suministro entrante "
              "queda en 0 y el NFP subestima la posición.")

    material = df[c_material].astype(str).str.strip()
    validas = material.notna() & ~material.isin(["", "nan", "None", "Total", "TOTAL"])
    df = df.loc[validas].copy()
    material = material.loc[validas]

    libre = _num(df[c_libre])
    bloq = _num(df[c_bloq]) if c_bloq else pd.Series(0.0, index=df.index)
    insp = _num(df[c_insp]) if c_insp else pd.Series(0.0, index=df.index)
    trans = _num(df[c_trans]) if c_trans else pd.Series(0.0, index=df.index)
    tras = _num(df[c_tras]) if c_tras else pd.Series(0.0, index=df.index)

    # Factor derivado del archivo, no supuesto
    factor = 1000.0
    if c_total_kg and c_total_tm:
        kg, tm = _num(df[c_total_kg]), _num(df[c_total_tm])
        validos = (tm > 0) & (kg > 0)
        if validos.sum() > 0:
            factor = float(np.median(kg[validos] / tm[validos]))
    print(f"  Factor de conversión: {factor:.1f} (unidad base -> TM)")
    if abs(factor - 1000.0) > 1.0:
        print("  [AVISO] El factor no es 1000. Verificar la unidad de medida base.")

    if c_total_kg:
        suma = libre + bloq + insp + trans + tras
        total = _num(df[c_total_kg])
        comp = total > 0
        if comp.sum() > 0:
            desvio = ((suma[comp] - total[comp]).abs() / total[comp])
            n_desc = int((desvio > TOLERANCIA_SUMA).sum())
            print(f"  [AVISO] En {n_desc} de {int(comp.sum())} filas las categorías "
                  f"no suman el Total Stock." if n_desc
                  else "  Validación de suma de categorías: OK")

    detalle = pd.DataFrame({
        "material": material.values,
        "descripcion": (df[c_desc].astype(str).str.strip().values if c_desc else ""),
        "centro": (df[c_centro].astype(str).str.strip().values if c_centro else ""),
        "almacen": (df[c_almacen].astype(str).str.strip().values if c_almacen else ""),
        "disponible_tm": (libre / factor).values,
        "bloqueado_tm": (bloq / factor).values,
        "inspeccion_tm": (insp / factor).values,
        "transito_tm": ((trans + tras) / factor).values,
    })
    detalle["total_tm"] = (detalle["disponible_tm"] + detalle["bloqueado_tm"]
                           + detalle["inspeccion_tm"] + detalle["transito_tm"])

    # Filas repetidas: si la hoja trae detalle por lote es legítimo sumarlas
    # (10 sacos de 1000 kg); si el reporte está duplicado, el stock queda
    # inflado. Sin columna de lote no se puede distinguir: se reporta.
    claves_dup = ["material", "centro", "almacen", "disponible_tm"]
    n_dup = int(detalle.duplicated(subset=claves_dup, keep=False).sum())
    if n_dup:
        tm_dup = detalle.loc[detalle.duplicated(subset=claves_dup, keep="first"),
                             "disponible_tm"].sum()
        print(f"  [VERIFICAR] {n_dup} filas con material+ubicación+cantidad "
              f"repetidos ({tm_dup:,.1f} TM en las repeticiones).")
        print(f"              Detalle por lote -> correcto sumarlas.")
        print(f"              Reporte duplicado -> el stock queda inflado.")

    agregado = detalle.groupby("material", as_index=False).agg(
        descripcion=("descripcion", "first"),
        disponible_tm=("disponible_tm", "sum"),
        bloqueado_tm=("bloqueado_tm", "sum"),
        inspeccion_tm=("inspeccion_tm", "sum"),
        transito_tm=("transito_tm", "sum"),
        total_tm=("total_tm", "sum"),
        n_filas=("centro", "count"),
    )

    print(f"  {len(agregado)} materiales distintos")
    print(f"  Disponible total: {agregado['disponible_tm'].sum():,.1f} TM")
    print(f"  En tránsito:      {agregado['transito_tm'].sum():,.1f} TM")
    no_util = agregado["bloqueado_tm"].sum() + agregado["inspeccion_tm"].sum()
    if no_util > 0:
        pct = no_util / max(agregado["total_tm"].sum(), 1e-9)
        print(f"  Bloqueado + inspección: {no_util:,.1f} TM ({pct:.1%}) "
              f"-- NO cuenta como disponible")

    return agregado


# ---------------------------------------------------------------------
# Pedidos pendientes PT
# ---------------------------------------------------------------------

COLUMNAS_CLAVE_PEDIDOS = ["material", "documento de ventas", "solicitante"]


def leer_pedidos_pendientes() -> pd.DataFrame:
    print(f"\n=== Pedidos pendientes PT (demanda calificada) ===")
    print(f"  {Path(EXCEL_PEDIDOS_PENDIENTES_PT).name} / "
          f"hoja '{SHEET_PEDIDOS_PENDIENTES_PT}'")

    df = leer_hoja(EXCEL_PEDIDOS_PENDIENTES_PT, SHEET_PEDIDOS_PENDIENTES_PT,
                   COLUMNAS_CLAVE_PEDIDOS)
    print(f"  {len(df)} filas, {len(df.columns)} columnas")

    c_material = _buscar_columna(df, ["material"], nombre_logico="Material")

    # 'Fe.prefer.entrega' es la fecha MÁXIMA comprometida, no exacta: la
    # demanda puede caer antes. NO se usa 'Creado el' (fecha de ingreso).
    c_fecha = _buscar_columna(
        df, ["fe prefer entrega", "fecha de entrega", "fecha entrega",
             "fecha requerida", "fecha de despacho"],
        nombre_logico="Fecha de entrega")

    # 'Pedido [TM]' es el pedido del cliente ya convertido (saco = 25 kg).
    c_tm = _buscar_columna(df, ["pedido tm", "pedidos tm", "cantidad tm"], False)
    c_cant = _buscar_columna(df, ["cantidad confirmada posicion",
                                  "cantidad de pedido posicion"], False)
    if c_tm is None and c_cant is None:
        raise KeyError(f"Sin columna de cantidad.\n  Columnas: {list(df.columns)}")

    c_cliente = _buscar_columna(df, ["solicitante", "cliente"], False)
    c_nombre = _buscar_columna(df, ["nombre del solicitante"], False)
    c_centro = _buscar_columna(df, ["centro"], False)
    c_doc = _buscar_columna(df, ["documento de ventas"], False)
    c_mes = _buscar_columna(df, ["mes pedido"], False)

    out = pd.DataFrame()
    out["material"] = df[c_material].astype(str).str.strip()
    out["fecha_entrega"] = pd.to_datetime(df[c_fecha], errors="coerce", dayfirst=True)

    if c_tm:
        out["cantidad_tm"] = _num(df[c_tm])
        print(f"  Cantidad tomada de '{c_tm}' (ya en TM)")
    else:
        out["cantidad_tm"] = _num(df[c_cant]) * 0.025   # saco = 25 kg
        print(f"  [AVISO] Sin columna en TM. Se usa '{c_cant}' x 0.025 "
              f"(saco de 25 kg).")

    out["documento"] = df[c_doc].astype(str).str.strip() if c_doc else ""
    out["cliente"] = df[c_cliente].astype(str).str.strip() if c_cliente else ""
    out["cliente_nombre"] = df[c_nombre].astype(str).str.strip() if c_nombre else ""
    out["centro"] = df[c_centro].astype(str).str.strip() if c_centro else ""
    out["mes_pedido"] = df[c_mes].astype(str).str.strip() if c_mes else ""

    n_sin_fecha = int(out["fecha_entrega"].isna().sum())
    if n_sin_fecha:
        # Sin fecha no se puede ubicar en el horizonte. Se trata como de hoy:
        # omitirlo subestimaría la demanda comprometida, que es el error caro.
        print(f"  [AVISO] {n_sin_fecha} pedidos sin fecha válida -- se tratan como "
              f"de hoy para no subestimar la demanda calificada.")
        out.loc[out["fecha_entrega"].isna(), "fecha_entrega"] = pd.Timestamp(HOY)

    inicio_mes = pd.Timestamp(HOY).replace(day=1)
    fin_mes = inicio_mes + pd.offsets.MonthBegin(1)

    out = out[
        (out["cantidad_tm"] > 0) &
        (out["fecha_entrega"] >= inicio_mes) &
        (out["fecha_entrega"] < fin_mes)
    ].reset_index(drop=True)

    out["fecha_entrega"] = out["fecha_entrega"].dt.strftime("%Y-%m-%d")

    print(f"  {len(out)} pedidos con cantidad > 0")
    print(f"  {out['material'].nunique()} materiales")
    print(f"  Cantidad total pendiente: {out['cantidad_tm'].sum():,.1f} TM")

    if len(out):
        print(f"  Rango de fechas: {out['fecha_entrega'].min()} a "
              f"{out['fecha_entrega'].max()}")
        # Fecha pasada pero aún en la hoja = POSTERGADO dentro del mes, no
        # incumplimiento: al cierre la hoja debe quedar en cero, así que
        # sigue siendo demanda comprometida.
        post = out[out["fecha_entrega"] < HOY]
        if len(post):
            print(f"  {len(post)} pedidos POSTERGADOS (fecha pasada, siguen "
                  f"pendientes) por {post['cantidad_tm'].sum():,.1f} TM")
        if out["mes_pedido"].nunique() > 1:
            print(f"  Meses en la hoja: {sorted(out['mes_pedido'].unique())}")

    return out


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    conn = sqlite3.connect(DB_PATH)
    resumen = {}
    print(f"Snapshot del {HOY}")

    for ruta, etiqueta in [(EXCEL_STOCK_PT, "Análisis de PT"),
                           (EXCEL_MP_STOCK, "Análisis de MP")]:
        if not Path(ruta).exists():
            print(f"[ERROR] No existe el archivo de {etiqueta}:\n  {ruta}\n")

    try:
        inv_pt = leer_inventario(EXCEL_STOCK_PT, SHEET_STOCK_PT, "PT")
        inv_pt["nodo"] = "PT"
        guardar_snapshot(conn, "posicion_inventario_pt", inv_pt, ["material"])
        resumen["posicion_inventario_pt"] = len(inv_pt)
    except Exception as e:
        print(f"  [ERROR] Inventario PT: {type(e).__name__}: {e}")

    try:
        pedidos = leer_pedidos_pendientes()
        guardar_snapshot(conn, "pedidos_pendientes_pt", pedidos,
                         ["material", "cliente", "centro", "fecha_entrega"])
        resumen["pedidos_pendientes_pt"] = len(pedidos)
    except Exception as e:
        print(f"  [ERROR] Pedidos pendientes PT: {type(e).__name__}: {e}")

    try:
        inv_mp = leer_inventario(EXCEL_MP_STOCK, SHEET_MP_STOCK, "MP")
        inv_mp["nodo"] = "MP"
        guardar_snapshot(conn, "posicion_inventario_mp", inv_mp, ["material"])
        resumen["posicion_inventario_mp"] = len(inv_mp)
    except Exception as e:
        print(f"  [ERROR] Inventario MP: {type(e).__name__}: {e}")

    conn.commit()

    print("\n" + "=" * 62)
    print("COBERTURA CONTRA EL BUFFER")
    print("=" * 62)

    for tb, tp, et in [("ddmrp_buffer_pt", "posicion_inventario_pt", "PT"),
                       ("ddmrp_buffer_mp", "posicion_inventario_mp", "MP")]:
        try:
            buf = pd.read_sql(f"SELECT DISTINCT sku FROM {tb}", conn)
            pos = pd.read_sql(f"SELECT DISTINCT material FROM {tp} "
                              f"WHERE fecha_snapshot = '{HOY}'", conn)
        except Exception:
            print(f"  {et}: no se pudo comparar (falta alguna tabla)")
            continue

        skus = set(buf["sku"].astype(str).str.strip())
        mats = set(pos["material"].astype(str).str.strip())
        print(f"\n  {et}: {len(skus & mats)} de {len(skus)} con posición")

        if not (skus & mats) and skus and mats:
            print(f"    ⚠️  CERO coincidencias -- los códigos no cruzan.")
            print(f"    Buffer: {sorted(skus)[:3]} · Inventario: {sorted(mats)[:3]}")
        elif skus - mats:
            print(f"    {len(skus - mats)} sin posición: {sorted(skus - mats)[:10]}"
                  f"{' …' if len(skus - mats) > 10 else ''}")
            print(f"    Sin posición NO es stock cero: puede ser brecha del reporte.")
        if mats - skus:
            print(f"    {len(mats - skus)} materiales con stock pero SIN buffer.")

    print("\n" + "=" * 62)
    print("SNAPSHOTS ACUMULADOS")
    print("=" * 62)
    for t in ["posicion_inventario_pt", "posicion_inventario_mp",
              "pedidos_pendientes_pt"]:
        try:
            r = pd.read_sql(f"SELECT COUNT(DISTINCT fecha_snapshot) n, "
                            f"MIN(fecha_snapshot) d, MAX(fecha_snapshot) h "
                            f"FROM {t}", conn).iloc[0]
            print(f"  {t}: {int(r['n'])} día(s) — {r['d']} a {r['h']}")
        except Exception:
            print(f"  {t}: sin datos")

    print("\n  La trayectoria del flujo neto necesita varios días. Corriendo a")
    print("  diario se arma la curva.")

    print("\n" + "=" * 62)
    print("TABLAS ACTUALIZADAS")
    print("=" * 62)
    for t, n in resumen.items():
        print(f"  {t}: {n} filas hoy")
    faltan = {"posicion_inventario_pt", "pedidos_pendientes_pt",
              "posicion_inventario_mp"} - set(resumen)
    print(f"\n⚠️  Faltan: {sorted(faltan)}" if faltan
          else "\nSiguiente paso: calcular_nfp.py")

    conn.close()


if __name__ == "__main__":
    main()