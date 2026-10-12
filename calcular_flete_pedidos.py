"""
calcular_flete_pedidos.py
Cargill Ecuador - Aquanutrition
Flete terrestre por pedido y por planta (DURAN vs GUAYAQUIL).

Corre ANTES de optimizar_producción.py. No decide nada: deja el costo de flete
por SKU y planta para que el optimizador lo use como DESEMPATE (debajo del buffer).

Cálculo (validado con diagnostico/costeo_flete_diagnostico.py):
  1. Ship To terrestre -> región (= DESTINO del tabulador):
       override manual (tabla flete_region_override) > Direccion exacta > DESTINO dentro de la
       Direccion > DESTINO en el nombre del Ship To > Ship To resuelto más cercano por GPS (<= 15 km)
       > texto después de "VIA" (nombre de carretera, último recurso).
  2. Tarifa = USD POR CAMIÓN según el RANGO TM de su carga (promedio de transportistas que cobran > 0).
  3. Por pedido (documento + fecha + cliente), con el pedido COMPLETO (sale junto lo que ya está en
     stock y lo recién producido):
       carga máx = RESTRICCION del Ship To (RESTRICCION INVERNAL si el despacho cae en temporada de
                   lluvia activa; vacía -> CAPACIDAD_CAMION_SIN_RESTRICCION)
       camiones  = techo(TM pedido / carga máx);  TM por camión = TM pedido / camiones -> RANGO
       USD/TM    = camiones x tarifa / TM pedido
     Cliente con varios Ship To terrestres: promedio entre ellos (y se marca si la planta barata cambia).
  4. Solo cuenta lo que FALTA producir: el stock (disponible + tránsito PT) se asigna a los pedidos
     por fecha de entrega; el flete se pondera con las TM faltantes.

Tablas que escribe:
  flete_pedidos      una fila por línea de pedido (detalle y motivo)
  flete_sku_planta   sku, planta, usd_tm (ponderado por TM faltantes), tm_faltante
  flete_avisos       Ship To sin región, destinos sin tarifa, archivos usados
Tablas de configuración que crea si no existen:
  flete_region_override      (ship_to, destino)            corrección manual de región
  config_temporada_invernal  (fecha_inicio, fecha_fin, activo)
"""

import sys, re, math, sqlite3, unicodedata
from math import radians, sin, cos, asin, sqrt
from pathlib import Path
from datetime import date, timedelta

import pandas as pd

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

AQUI = Path(__file__).resolve().parent
for _p in (AQUI, AQUI.parent):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import config
from config import DB_PATH

PLANTAS = ["DURAN", "GUAYAQUIL"]
CAPACIDAD_CAMION_SIN_RESTRICCION = 30.0   # TM: la RESTRICCION más alta en SAP Customers
KM_MAX_VECINO = 15.0
HORIZONTE_DIAS = 14                        # igual que optimizar_producción.py

HOY = pd.Timestamp(date.today())
LUNES_ACTUAL = HOY - pd.Timedelta(days=HOY.dayofweek)
FIN_HORIZONTE = LUNES_ACTUAL + pd.Timedelta(days=HORIZONTE_DIAS - 1)


# ----------------------------------------------------------------- utilidades
def norm_col(c): return re.sub(r"\s+", " ", str(c)).strip()
def norm_txt(s):
    if s is None or (isinstance(s, float) and pd.isna(s)): return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip().upper()
def norm_cod(x):
    if x is None or (isinstance(x, float) and pd.isna(x)): return ""
    x = str(x).strip()
    if x.endswith(".0"): x = x[:-2]
    return x.lstrip("0")
def sku_opt(x):
    """Misma normalización de SKU que optimizar_producción.py (para que los códigos crucen igual)."""
    return str(x).strip().replace(".0", "")
def col(df, *nombres):
    for n in nombres:
        for c in df.columns:
            if norm_txt(c) == norm_txt(n): return c
    return None
def _existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone(); return True
    except Exception:
        return False
def parse_coord(s):
    if s is None or (isinstance(s, float) and pd.isna(s)): return (None, None)
    nums = [float(x.replace(",", ".")) for x in re.findall(r"-?\d+(?:[.,]\d+)?", str(s))]
    if len(nums) < 2: return (None, None)
    for lat, lon in ((nums[0], nums[1]), (nums[1], nums[0])):
        if -6 <= lat <= 2 and -82 <= lon <= -74: return (lat, lon)
    return (None, None)
def km(a, b):
    la1, lo1, la2, lo2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


# ----------------------------------------------------------------- configuración
def crear_tablas_config(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS flete_region_override (
        ship_to TEXT PRIMARY KEY, destino TEXT NOT NULL,
        actualizado_por TEXT, actualizado_en TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS config_temporada_invernal (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_inicio TEXT NOT NULL, fecha_fin TEXT NOT NULL,
        activo INTEGER DEFAULT 1, actualizado_en TEXT)""")
    conn.commit()

def importar_correcciones_excel(conn):
    """Si en match_region_ship_to.xlsx agregaste la columna 'region_corregida', se carga como override."""
    ruta = next((r / "match_region_ship_to.xlsx" for r in (AQUI, AQUI.parent)
                 if (r / "match_region_ship_to.xlsx").exists()), None)
    if ruta is None: return 0
    try:
        x = pd.read_excel(ruta, dtype=str)
    except Exception as e:
        print(f"  [AVISO] No pude leer {ruta.name}: {e}"); return 0
    if "region_corregida" not in x.columns: return 0
    x = x[x["region_corregida"].map(norm_txt) != ""]
    for _, r in x.iterrows():
        conn.execute("INSERT OR REPLACE INTO flete_region_override (ship_to, destino, actualizado_por, actualizado_en) "
                     "VALUES (?,?,?,?)", (norm_cod(r["ship_to"]), norm_txt(r["region_corregida"]),
                                          "match_region_ship_to.xlsx", HOY.strftime("%Y-%m-%d")))
    conn.commit()
    return len(x)

def temporadas_invernales(conn):
    try:
        t = pd.read_sql("SELECT fecha_inicio, fecha_fin FROM config_temporada_invernal WHERE activo = 1", conn)
        return [(pd.Timestamp(a).normalize(), pd.Timestamp(b).normalize()) for a, b in t.values]
    except Exception:
        return []


# ----------------------------------------------------------------- tabulador
def ruta_tabulador():
    ruta = Path(config.EXCEL_TARIFA_TRANSPORTE)
    cand = sorted(ruta.parent.glob("Tabulador de Transporte*.xlsx"), key=lambda f: f.stat().st_mtime) if ruta.parent.exists() else []
    if cand and (not ruta.exists() or cand[-1].stat().st_mtime > ruta.stat().st_mtime):
        print(f"  Tabulador más reciente en la carpeta: {cand[-1].name} (config dice {ruta.name})")
        return cand[-1]
    return ruta

def cargar_tarifas(avisos):
    ruta = ruta_tabulador()
    tb = pd.read_excel(ruta, sheet_name=config.SHEET_TARIFA_TRANSPORTE)
    tb.columns = [norm_col(c) for c in tb.columns]
    co, cd, cr = col(tb, "ORIGEN"), col(tb, "DESTINO"), col(tb, "RANGO TM")
    fijas = {"ID MASTER", "ORIGEN", "DESTINO", "RANGO TM"}
    carriers = [c for c in tb.columns if norm_txt(c) not in fijas and not c.upper().startswith("UNNAMED")]
    tb["O"], tb["D"] = tb[co].map(norm_txt), tb[cd].map(norm_txt)
    tb = tb[tb["O"] != ""]
    destinos = sorted({d for d in tb["D"] if d}, key=len, reverse=True)
    tb = tb[tb["O"].isin(PLANTAS)].copy()
    v = tb[carriers].apply(pd.to_numeric, errors="coerce")
    tb["tarifa"] = v.where(v > 0).mean(axis=1)
    tb["desde"] = tb[cr].map(lambda r: float(re.findall(r"\d+(?:[.,]\d+)?", str(r))[0].replace(",", "."))
                             if re.findall(r"\d+(?:[.,]\d+)?", str(r)) else None)
    tb = tb.dropna(subset=["desde"])
    tar = {k: g.sort_values("desde")[["desde", cr, "tarifa"]].values.tolist() for k, g in tb.groupby(["O", "D"])}
    avisos.append({"tipo": "ARCHIVO", "detalle": f"Tabulador usado: {ruta.name}"})
    print(f"  Tabulador: {ruta.name} | {len(tar)} combinaciones planta-destino | {len(destinos)} destinos")
    return tar, destinos

def tarifa_camion(tar, origen, destino, carga):
    filas = tar.get((origen, destino))
    if not filas: return None, None
    e = filas[0]
    for f in filas:
        if carga >= f[0]: e = f
    return (None if pd.isna(e[2]) else float(e[2])), str(e[1])


# ----------------------------------------------------------------- Ship To -> región
def regiones_ship_to(conn, destinos, avisos):
    sc = pd.read_excel(config.EXCEL_SAP_CUSTOMER, sheet_name=config.SHEET_SAP_CUSTOMER)
    sc.columns = [norm_col(c) for c in sc.columns]
    cso, cst, ctd = col(sc, "Sold To"), col(sc, "Ship To"), col(sc, "Tipo de despacho")
    cdir, cnom, cgps = col(sc, "Direccion"), col(sc, "Ship To Description"), col(sc, "Coordenadas")
    cr, cri = col(sc, "RESTRICCION"), col(sc, "RESTRICCION INVERNAL")
    sc = sc[sc[ctd].map(norm_txt) == "TERRESTRE"].copy()
    ovr = dict(pd.read_sql("SELECT ship_to, destino FROM flete_region_override", conn).values)

    pats = [(d, re.compile(r"(?<![A-Z0-9])" + re.escape(d) + r"(?![A-Z0-9])")) for d in destinos]
    def buscar(t):
        fu, de = [], []
        if not t: return fu, de
        v = re.search(r"\bVIA\b", t)
        for d, rx in pats:
            ms = list(rx.finditer(t))
            if not ms: continue
            (de if v and all(m.start() > v.start() for m in ms) else fu).append(d)
        return fu, de

    filas = []
    for _, r in sc.iterrows():
        st = norm_cod(r[cst]); dir_ = norm_txt(r[cdir]) if cdir else ""; nom = norm_txt(r[cnom]) if cnom else ""
        lat, lon = parse_coord(r[cgps]) if cgps else (None, None)
        reg, met, deb = "", "", []
        if st in ovr:
            reg, met = norm_txt(ovr[st]), "0_MANUAL"
        elif dir_ in destinos:
            reg, met = dir_, "1_EXACTO"
        else:
            fu, deb = buscar(dir_)
            if fu: reg, met = fu[0], "2_TEXTO_DIR"
            else:
                fu, _ = buscar(nom)
                if fu: reg, met = fu[0], "3_TEXTO_NOMBRE"
        filas.append(dict(sold_to=norm_cod(r[cso]), ship_to=st, ship_to_desc=r[cnom] if cnom else "",
                          lat=lat, lon=lon, region=reg, metodo=met, via=deb[0] if deb else "",
                          restriccion=pd.to_numeric(r[cr], errors="coerce") if cr else None,
                          restriccion_invernal=pd.to_numeric(r[cri], errors="coerce") if cri else None))
    m = pd.DataFrame(filas)
    base = m[(m["region"] != "") & m["lat"].notna()]
    for i in m.index[(m["region"] == "") & m["lat"].notna()]:
        if base.empty: break
        d = base.apply(lambda b: km((m.at[i, "lat"], m.at[i, "lon"]), (b["lat"], b["lon"])), axis=1)
        if d.min() <= KM_MAX_VECINO:
            m.at[i, "region"], m.at[i, "metodo"] = base.at[d.idxmin(), "region"], "4_VECINO_GPS"
    sinr = (m["region"] == "") & (m["via"] != "")
    m.loc[sinr, "region"], m.loc[sinr, "metodo"] = m.loc[sinr, "via"], "5_TEXTO_VIA"
    for _, r in m[m["region"] == ""].iterrows():
        avisos.append({"tipo": "SHIP_TO_SIN_REGION", "detalle": f"{r['ship_to']} {r['ship_to_desc']} (Sold To {r['sold_to']})"})
    print(f"  Ship To terrestres: {len(m)} | con región: {(m['region'] != '').sum()} | "
          f"overrides manuales: {(m['metodo'] == '0_MANUAL').sum()}")
    m = m[m["region"] != ""]
    return {so: g.to_dict("records") for so, g in m.groupby("sold_to")}


# ----------------------------------------------------------------- pedidos y faltante
def cargar_pedidos(conn):
    f = conn.execute("SELECT MAX(fecha_snapshot) FROM pedidos_pendientes_pt").fetchone()[0]
    ped = pd.read_sql("SELECT documento, material, fecha_entrega, cantidad_tm, cliente, cliente_nombre, centro "
                      "FROM pedidos_pendientes_pt WHERE fecha_snapshot = ?", conn, params=(f,))
    ped["origen_pedido"] = "SAP"
    try:
        from pedidos_manuales import cargar_pedidos_manuales
        man = cargar_pedidos_manuales(conn)
        if not man.empty:
            man = man.rename(columns={"sku": "material"})
            man["origen_pedido"] = "MANUAL"
            ped = pd.concat([ped, man[[c for c in ["material", "fecha_entrega", "cantidad_tm", "cliente_nombre", "origen_pedido"]
                                       if c in man.columns]]], ignore_index=True)
    except Exception as e:
        print(f"  [AVISO] Pedidos manuales no cargados (solo afectan el reparto de stock): {e}")
    ped["sku"] = ped["material"].map(sku_opt)
    ped["so"] = ped["cliente"].map(norm_cod) if "cliente" in ped else ""
    ped["cantidad_tm"] = pd.to_numeric(ped["cantidad_tm"], errors="coerce").fillna(0)
    ped["fecha_entrega"] = pd.to_datetime(ped["fecha_entrega"], errors="coerce")
    ped = ped[(ped["cantidad_tm"] > 0) & ped["fecha_entrega"].notna()]
    ped = ped[ped["fecha_entrega"].dt.normalize() <= FIN_HORIZONTE].copy()   # mismo horizonte que el optimizador
    print(f"  Pedidos (snapshot {f}, hasta {FIN_HORIZONTE.date()}): {len(ped)} líneas, {ped['cantidad_tm'].sum():,.1f} TM")
    return ped

def asignar_stock(conn, ped):
    """Stock PT (disponible + tránsito) se reparte a los pedidos por fecha de entrega. Devuelve TM faltante por línea."""
    stock = {}
    if _existe(conn, "ddmrp_flujo_neto"):
        f = conn.execute("SELECT MAX(fecha_calculo) FROM ddmrp_flujo_neto").fetchone()[0]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ddmrp_flujo_neto)").fetchall()}
        campos = "sku, disponible_tm" + (", transito_tm" if "transito_tm" in cols else "")
        n = pd.read_sql(f"SELECT {campos} FROM ddmrp_flujo_neto WHERE fecha_calculo = ? AND nodo = 'PT'", conn, params=(f,))
        n["tot"] = n["disponible_tm"].fillna(0) + (n["transito_tm"].fillna(0) if "transito_tm" in n else 0)
        stock = n.groupby(n["sku"].map(sku_opt))["tot"].sum().to_dict()
    ped = ped.sort_values(["sku", "fecha_entrega"]).copy()
    falt = []
    for s, g in ped.groupby("sku", sort=False):
        rest = max(stock.get(s, 0.0), 0.0)
        for idx, tm in zip(g.index, g["cantidad_tm"]):
            usa = min(rest, tm); rest -= usa
            falt.append((idx, tm - usa))
    ped["tm_faltante"] = pd.Series(dict(falt))
    return ped


# ----------------------------------------------------------------- cálculo
def calcular(conn):
    avisos = []
    crear_tablas_config(conn)
    n_imp = importar_correcciones_excel(conn)
    if n_imp: print(f"  {n_imp} corrección(es) de región importadas desde match_region_ship_to.xlsx")
    tar, destinos = cargar_tarifas(avisos)
    ship = regiones_ship_to(conn, destinos, avisos)
    inviernos = temporadas_invernales(conn)
    ped = asignar_stock(conn, cargar_pedidos(conn))

    excl = {}
    if _existe(conn, "sku_exclusion_planta"):
        ex = pd.read_sql("SELECT sku, planta_excluida, linea_excluida FROM sku_exclusion_planta WHERE activo = 1", conn)
        for _, r in ex.iterrows():
            if norm_txt(r["linea_excluida"]) in ("", "NAN", "NONE"):
                pl = norm_txt(r["planta_excluida"])
                if pl in PLANTAS: excl.setdefault(sku_opt(r["sku"]), set()).add(pl)

    ped["tm_pedido"] = ped.groupby(["documento", "fecha_entrega", "so"], dropna=False)["cantidad_tm"].transform("sum")
    filas = []
    for _, r in ped.iterrows():
        b = dict(documento=r.get("documento"), sku=r["sku"], sold_to=r["so"], cliente=r.get("cliente_nombre"),
                 fecha_entrega=r["fecha_entrega"].strftime("%Y-%m-%d"), tm=round(r["cantidad_tm"], 3),
                 tm_faltante=round(r["tm_faltante"], 3), tm_pedido=round(r["tm_pedido"], 3), centro=r.get("centro"))
        if r["origen_pedido"] == "MANUAL":
            filas.append({**b, "estado": "PEDIDO_MANUAL_SIN_CLIENTE_SAP"}); continue
        sts = ship.get(r["so"])
        if not sts:
            filas.append({**b, "estado": "SIN_SHIP_TO_TERRESTRE_CON_REGION"}); continue
        despacho = r["fecha_entrega"].normalize() - pd.Timedelta(days=1)
        invierno = any(a <= despacho <= z for a, z in inviernos)
        usd = {pl: [] for pl in PLANTAS}; ganadores = set(); regiones = set(); camiones_l = set()
        for s in sts:
            lim = s["restriccion_invernal"] if invierno and pd.notna(s["restriccion_invernal"]) and s["restriccion_invernal"] > 0 \
                else s["restriccion"]
            lim = lim if pd.notna(lim) and lim > 0 else CAPACIDAD_CAMION_SIN_RESTRICCION
            cam = math.ceil(r["tm_pedido"] / lim - 1e-9); carga = r["tm_pedido"] / cam
            t = {pl: tarifa_camion(tar, pl, s["region"], carga)[0] for pl in PLANTAS}
            for pl in PLANTAS:
                if t[pl] is not None: usd[pl].append(cam * t[pl] / r["tm_pedido"])
            if all(t[pl] is not None for pl in PLANTAS): ganadores.add(min(PLANTAS, key=lambda pl: t[pl]))
            regiones.add(s["region"]); camiones_l.add(cam)
        for pl in PLANTAS:
            b[f"usd_tm_{pl}"] = round(sum(usd[pl]) / len(usd[pl]), 4) if usd[pl] else None
        b.update(regiones=" | ".join(sorted(regiones)), camiones=max(camiones_l), temporada_invernal=int(invierno))
        if any(b[f"usd_tm_{pl}"] is None for pl in PLANTAS):
            for d in regiones:
                for pl in PLANTAS:
                    if (pl, d) not in tar: avisos.append({"tipo": "DESTINO_SIN_TARIFA", "detalle": f"{pl} -> {d}"})
            filas.append({**b, "estado": "SIN_TARIFA_EN_UNA_PLANTA"}); continue
        barata = min(PLANTAS, key=lambda pl: b[f"usd_tm_{pl}"])
        permit = [pl for pl in PLANTAS if pl not in excl.get(r["sku"], set())]
        b["planta_mas_barata"] = barata
        b["robusta"] = int(len(ganadores) <= 1)
        b["planta_permitida"] = " | ".join(permit)
        b["estado"] = ("EXCLUIDO_EN_AMBAS" if not permit else
                       "ELEGIBLE" if len(permit) == 2 else
                       "POR_NEGOCIO_Y_FLETE" if permit[0] == barata else "POR_NEGOCIO")
        filas.append(b)

    det = pd.DataFrame(filas)
    for c in ["estado", "tm_faltante", "usd_tm_DURAN", "usd_tm_GUAYAQUIL"]:
        if c not in det: det[c] = None
    det["fecha_calculo"] = HOY.strftime("%Y-%m-%d")

    # USD/TM por SKU y planta, ponderado por TM FALTANTES (lo que se va a producir)
    agg = []
    ok = det[det["usd_tm_DURAN"].notna() & det["usd_tm_GUAYAQUIL"].notna()
             & (pd.to_numeric(det["tm_faltante"], errors="coerce").fillna(0) > 0)]
    for s, g in ok.groupby("sku"):
        w = g["tm_faltante"]
        for pl in PLANTAS:
            agg.append({"sku": s, "planta": pl, "usd_tm": round(float((g[f"usd_tm_{pl}"] * w).sum() / w.sum()), 4),
                        "tm_faltante": round(float(w.sum()), 3), "n_lineas": len(g),
                        "fecha_calculo": HOY.strftime("%Y-%m-%d")})
    sp = pd.DataFrame(agg, columns=["sku", "planta", "usd_tm", "tm_faltante", "n_lineas", "fecha_calculo"])

    av = pd.DataFrame(avisos).drop_duplicates() if avisos else pd.DataFrame(columns=["tipo", "detalle"])
    av["fecha_calculo"] = HOY.strftime("%Y-%m-%d")
    return det, sp, av


def main():
    print(f"Flete por pedido — horizonte {LUNES_ACTUAL.date()} a {FIN_HORIZONTE.date()}")
    conn = sqlite3.connect(DB_PATH)
    try:
        det, sp, av = calcular(conn)
        det.to_sql("flete_pedidos", conn, if_exists="replace", index=False)
        sp.to_sql("flete_sku_planta", conn, if_exists="replace", index=False)
        av.to_sql("flete_avisos", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()

    print("\nTM faltantes por estado:")
    print(det.groupby("estado")["tm_faltante"].sum().round(1).to_string())
    if not sp.empty:
        pv = sp.pivot(index="sku", columns="planta", values="usd_tm")
        pv["dif"] = (pv["GUAYAQUIL"] - pv["DURAN"]).abs()
        tm = sp.drop_duplicates("sku").set_index("sku")["tm_faltante"]
        print(f"\nSKU con flete: {len(pv)} | TM faltantes: {tm.sum():,.1f} | "
              f"valor máximo de elegir bien: {(pv['dif'] * tm).sum():,.0f} USD")
    n_av = av["tipo"].value_counts().to_dict() if not av.empty else {}
    print(f"Avisos: {n_av}")
    print("[OK] Tablas: flete_pedidos, flete_sku_planta, flete_avisos")


if __name__ == "__main__":
    main()