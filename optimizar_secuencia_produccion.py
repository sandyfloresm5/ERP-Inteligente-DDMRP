"""
optimizar_secuencia_produccion.py
Cargill Ecuador - Aquanutrition

Módulo 5 - Secuenciación dentro del plan de producción (Módulo 4, MILP).
"""

import sys
import sqlite3
from datetime import date
from itertools import permutations
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

ROOT_DIR = Path(__file__).resolve().parent
# Igual que el Módulo 4: config.py puede estar en esta carpeta o en la de arriba.
for _p in (ROOT_DIR, ROOT_DIR.parent):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

from config import DB_PATH

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)

HORAS_DIA_LINEA_REAL = {
    "DURAN|L1": 22.0, "DURAN|L2": 20.0, "DURAN|L3": 20.0,
    "GUAYAQUIL|L1": 20.0, "GUAYAQUIL|L3": 20.0, "GUAYAQUIL|L4": 15.0,
    "GUAYAQUIL|L5": 22.0, "GUAYAQUIL|L6": 22.0,
}
HORAS_DIA_LINEA_DEFAULT = 20.0

# Las horas de Configuración -> Líneas (20, 21, 22 h) son PRODUCCIÓN NETA:
# no incluyen el setup. El setup se hace en el resto del día calendario.
#   tiempo total del día = 24 h - (horas que la parada le quita a la línea)
#   alerta si  producción + setup  >  tiempo total del día
HORAS_CALENDARIO_DIA = 24.0

# Si algún valor de la matriz es mayor a esto, se asume que se guardó en
# MINUTOS (la app captura minutos) y se convierte a horas.
UMBRAL_MINUTOS_SOSPECHOSO = 12.0

# Con hasta este número de SKU en una línea-día se prueban TODAS las
# secuencias (óptimo exacto). Por encima, heurística NN + 2-opt.
MAX_SKU_EXACTO = 8

SIN_ETAPA = "SIN_ETAPA"


def _existe(conn, t):
    try:
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _norm(txt):
    return str(txt).strip().upper()


def cargar_etapa_por_sku(conn) -> dict:
    """Etapa (STARTER, GROWER, ...) de cada PT.
    1) ventas_raw.etapa -- viene del Excel de ventas (columna 'Etapa'),
       la carga Ingesta_Ventas_PT.py. Si un SKU tiene más de una etapa en
       el histórico, se usa la más frecuente y se avisa.
    2) configuracion_tipo_producto -- solo EXCEPCIONES manuales: si un SKU
       está ahí, esa etapa manda sobre la de ventas_raw."""
    etapa = {}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ventas_raw)").fetchall()} \
        if _existe(conn, "ventas_raw") else set()
    if "etapa" in cols:
        v = pd.read_sql("SELECT sku, etapa FROM ventas_raw WHERE etapa IS NOT NULL", conn)
        v["sku"] = v["sku"].astype(str).str.strip().str.replace(".0", "", regex=False)
        v["etapa"] = v["etapa"].map(_norm)
        v = v[~v["etapa"].isin(["", "NAN", "NONE"])]
        n_etapas = v.groupby("sku")["etapa"].nunique()
        incons = sorted(n_etapas[n_etapas > 1].index)
        if incons:
            print(f"[AVISO] {len(incons)} SKU con más de una etapa en ventas_raw -- se usó "
                  f"la más frecuente: {incons[:15]}")
        moda = (v.groupby(["sku", "etapa"]).size().reset_index(name="n")
                 .sort_values(["sku", "n"], ascending=[True, False])
                 .drop_duplicates("sku"))
        etapa = dict(zip(moda["sku"], moda["etapa"]))
    else:
        print("[AVISO] ventas_raw no tiene columna 'etapa' -- correr Ingesta_Ventas_PT.py "
              "con el Excel que trae la columna 'Etapa'.")

    if _existe(conn, "configuracion_tipo_producto"):
        o = pd.read_sql("SELECT sku, tipo FROM configuracion_tipo_producto", conn)
        n_over = 0
        for r in o.itertuples():
            sku, tipo = str(r.sku).strip(), _norm(r.tipo)
            if tipo and tipo != "NAN":
                etapa[sku] = tipo
                n_over += 1
        if n_over:
            print(f"  {n_over} SKU con etapa manual (configuracion_tipo_producto) -- "
                  f"reemplaza la de ventas_raw.")
    return etapa


def _crear_tablas_config_si_no_existen(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS configuracion_tipo_producto (
            sku TEXT PRIMARY KEY,
            tipo TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS configuracion_setup_tiempos (
            tipo_origen TEXT NOT NULL,
            tipo_destino TEXT NOT NULL,
            tiempo_horas REAL NOT NULL,
            PRIMARY KEY (tipo_origen, tipo_destino)
        )
    """)
    conn.commit()


def _horas_disponibles_por_recurso_fecha(conn):
    """Mismas horas que ya usó el MILP del Módulo 4 para esa línea ese día
    (config_lineas_produccion, con paradas_programadas por encima)."""
    horas_extra = {}
    if _existe(conn, "config_lineas_produccion"):
        try:
            cfg = pd.read_sql(
                "SELECT planta, linea, horas_dia FROM config_lineas_produccion WHERE activo = 1", conn)
            for _, r in cfg.iterrows():
                planta = str(r["planta"]).strip().upper().replace("Á", "A")
                linea = str(r["linea"]).strip().upper()
                if not planta or not linea or planta == "NAN" or linea == "NAN":
                    continue
                recurso = planta + "|" + (linea if linea.startswith("L") else "L" + linea)
                try:
                    h = float(r["horas_dia"])
                    if h > 0:
                        horas_extra[recurso] = h
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass

    paradas = []
    if _existe(conn, "paradas_programadas"):
        try:
            df = pd.read_sql(
                "SELECT planta, linea, fecha_inicio, fecha_fin, horas_dia FROM paradas_programadas", conn)
            for _, r in df.iterrows():
                planta = str(r["planta"]).strip().upper().replace("Á", "A")
                linea = str(r["linea"]).strip().upper()
                if not planta or not linea or planta == "NAN" or linea == "NAN":
                    continue
                recurso = planta + "|" + (linea if linea.startswith("L") else "L" + linea)
                paradas.append({
                    "recurso": recurso,
                    "fecha_inicio": pd.Timestamp(r["fecha_inicio"]).normalize(),
                    "fecha_fin": pd.Timestamp(r["fecha_fin"]).normalize(),
                    "horas_dia": float(r["horas_dia"]),
                })
        except Exception:
            pass

    def horas(recurso, fecha):
        """Devuelve (horas_netas_produccion, horas_totales_del_dia).
        Netas = horas normales - horas de parada. Totales = 24 h - horas de
        parada; ahí caben producción + setup."""
        f = pd.Timestamp(fecha).normalize()
        normales = horas_extra.get(recurso, HORAS_DIA_LINEA_REAL.get(recurso, HORAS_DIA_LINEA_DEFAULT))
        # horas_dia de la parada = HORAS DE PARADA (24 = parada total).
        aplicables = [p["horas_dia"] for p in paradas
                      if p["recurso"] == recurso and p["fecha_inicio"] <= f <= p["fecha_fin"]]
        horas_parada = min(max(aplicables), HORAS_CALENDARIO_DIA) if aplicables else 0.0
        netas = max(0.0, normales - horas_parada)
        totales = max(0.0, HORAS_CALENDARIO_DIA - horas_parada)
        return netas, totales

    return horas


class MatrizSetup:
    """Tiempos de setup por etapa, con reglas de respaldo y registro de
    los pares que faltan (para avisar al planificador)."""

    def __init__(self, df: pd.DataFrame):
        self.lookup = {}
        for r in df.itertuples():
            try:
                self.lookup[(_norm(r.tipo_origen), _norm(r.tipo_destino))] = float(r.tiempo_horas)
            except (TypeError, ValueError):
                continue

        self.convertido_de_minutos = False
        if self.lookup and max(self.lookup.values()) > UMBRAL_MINUTOS_SOSPECHOSO:
            # La app captura minutos; si el front no convirtió, lo hacemos aquí.
            self.lookup = {k: v / 60.0 for k, v in self.lookup.items()}
            self.convertido_de_minutos = True

        diag = [v for (a, b), v in self.lookup.items() if a == b]
        self.prom_misma_etapa = float(np.mean(diag)) if diag else 0.0
        self.peor_caso = max(self.lookup.values()) if self.lookup else 1.0
        self.faltantes = {}   # (a, b) -> valor usado

    def tiempo(self, sku_a, tipo_a, sku_b, tipo_b):
        if sku_a == sku_b:
            return 0.0
        if tipo_a == SIN_ETAPA or tipo_b == SIN_ETAPA:
            return self.peor_caso
        k = (tipo_a, tipo_b)
        if k in self.lookup:
            return self.lookup[k]
        if tipo_a == tipo_b:
            usado = self.prom_misma_etapa
        elif (tipo_b, tipo_a) in self.lookup:
            usado = self.lookup[(tipo_b, tipo_a)]
        else:
            usado = self.peor_caso
        self.faltantes[k] = usado
        return usado

    def desde_etapa(self, tipo_prev, sku_b, tipo_b):
        """Setup desde la última etapa del día anterior (sin SKU conocido:
        se asume SKU distinto)."""
        if tipo_prev is None:
            return 0.0
        return self.tiempo("__prev__", tipo_prev, sku_b, tipo_b)


def _setup_de_orden(filas_en_orden, matriz: MatrizSetup, tipo_prev) -> float:
    """Horas de setup de una secuencia dada (incluye el cambio desde la
    etapa con la que terminó la línea el día anterior)."""
    total, prev_sku, prev_tipo = 0.0, None, tipo_prev
    for k, f in enumerate(filas_en_orden):
        if k == 0:
            total += matriz.desde_etapa(tipo_prev, f["sku"], f["tipo"])
        else:
            total += matriz.tiempo(prev_sku, prev_tipo, f["sku"], f["tipo"])
        prev_sku, prev_tipo = f["sku"], f["tipo"]
    return total


def _secuenciar_grupo(filas, matriz: MatrizSetup, tipo_prev):
    """Devuelve el orden (índices) que minimiza:
         setup(etapa del día anterior -> primero) + Σ setups consecutivos."""
    n = len(filas)
    if n == 0:
        return []

    inicio = [matriz.desde_etapa(tipo_prev, f["sku"], f["tipo"]) for f in filas]
    costo = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                costo[i, j] = matriz.tiempo(filas[i]["sku"], filas[i]["tipo"],
                                            filas[j]["sku"], filas[j]["tipo"])

    def total(sec):
        return inicio[sec[0]] + sum(costo[sec[k], sec[k + 1]] for k in range(len(sec) - 1))

    if n == 1:
        return [0]

    # Exacto: pocas corridas por línea-día -> probar todas las secuencias.
    if n <= MAX_SKU_EXACTO:
        return list(min(permutations(range(n)), key=total))

    # Heurística: vecino más cercano desde cada inicio posible + 2-opt.
    mejor = None
    for s0 in range(n):
        orden = [s0]
        rest = set(range(n)) - {s0}
        while rest:
            nxt = min(rest, key=lambda j: costo[orden[-1], j])
            orden.append(nxt)
            rest.discard(nxt)
        if mejor is None or total(orden) < total(mejor):
            mejor = orden

    orden = mejor
    mejorado = True
    while mejorado:
        mejorado = False
        for i in range(0, n - 1):
            for j in range(i + 1, n):
                nuevo = orden[:i] + orden[i:j + 1][::-1] + orden[j + 1:]
                if total(nuevo) < total(orden) - 1e-9:
                    orden = nuevo
                    mejorado = True
    return orden


def main():
    conn = sqlite3.connect(DB_PATH)
    _crear_tablas_config_si_no_existen(conn)

    if not _existe(conn, "plan_produccion"):
        print("[ERROR] No existe 'plan_produccion'. Correr primero optimizar_producción.py (Módulo 4).")
        conn.close()
        return

    plan = pd.read_sql("SELECT fecha, sku, recurso, tm, horas, politica FROM plan_produccion", conn)
    if plan.empty:
        print("[AVISO] 'plan_produccion' está vacía -- no hay nada que secuenciar.")
        conn.close()
        return
    plan["sku"] = plan["sku"].astype(str).str.strip()

    # --- SKU -> etapa ---
    etapa_por_sku = cargar_etapa_por_sku(conn)
    plan["tipo"] = plan["sku"].map(etapa_por_sku)
    sin_etapa = sorted(plan.loc[plan["tipo"].isna(), "sku"].unique())
    plan["tipo"] = plan["tipo"].fillna(SIN_ETAPA)
    if sin_etapa:
        print(f"[AVISO] {len(sin_etapa)} SKU del plan SIN etapa (no están en ventas_raw ni "
              f"en configuracion_tipo_producto) -- se les aplica el peor setup de la matriz: "
              f"{sin_etapa[:20]}")

    # --- Matriz de setup ---
    m = pd.read_sql("SELECT tipo_origen, tipo_destino, tiempo_horas FROM configuracion_setup_tiempos", conn)
    matriz = MatrizSetup(m)
    if not matriz.lookup:
        print("[AVISO] configuracion_setup_tiempos está vacía -- todos los cambios se asumen "
              "de 1.0 h hasta que se cargue la matriz real.")
    if matriz.convertido_de_minutos:
        print(f"[AVISO] La matriz de setup tiene valores > {UMBRAL_MINUTOS_SOSPECHOSO:.0f}: se "
              f"asumió que están en MINUTOS y se convirtieron a horas (÷60). Revisar que el "
              f"frontend guarde en horas en 'tiempo_horas'.")

    horas_disponibles = _horas_disponibles_por_recurso_fecha(conn)

    # --- Secuenciar: por línea, día por día en orden (arrastre de etapa) ---
    resultado_filas = []
    resumen = []
    for recurso, g_linea in plan.groupby("recurso"):
        tipo_prev = None        # etapa con la que terminó la línea el día anterior
        tipo_prev_base = None   # ídem, pero siguiendo el orden SIN optimizar
        for fecha in sorted(g_linea["fecha"].unique()):
            filas = g_linea[g_linea["fecha"] == fecha].to_dict("records")
            # Línea base "sin optimizar": el orden en que llega el plan del
            # Módulo 4 (por código de SKU), con su propio arrastre entre días.
            setup_base = _setup_de_orden(filas, matriz, tipo_prev_base)
            tipo_prev_base = filas[-1]["tipo"]
            orden_idx = _secuenciar_grupo(filas, matriz, tipo_prev)
            ordenadas = [filas[i] for i in orden_idx]

            h_netas, h_total = horas_disponibles(recurso, fecha)
            horas_acum = setup_dia = prod_dia = 0.0
            for pos_i, f in enumerate(ordenadas):
                if pos_i == 0:
                    etapa_previa = tipo_prev
                    setup = matriz.desde_etapa(tipo_prev, f["sku"], f["tipo"])
                else:
                    ant = ordenadas[pos_i - 1]
                    etapa_previa = ant["tipo"]
                    setup = matriz.tiempo(ant["sku"], ant["tipo"], f["sku"], f["tipo"])
                horas_acum += setup + f["horas"]
                setup_dia += setup
                prod_dia += f["horas"]
                resultado_filas.append({
                    "fecha": fecha, "recurso": recurso, "orden": pos_i + 1,
                    "sku": f["sku"], "tipo": f["tipo"], "etapa_previa": etapa_previa,
                    "tm": f["tm"], "horas_produccion": f["horas"],
                    "tiempo_setup_previo_horas": round(setup, 2),
                    "horas_acumuladas": round(horas_acum, 2),
                    "horas_netas_linea": h_netas,
                    "horas_totales_dia": h_total,
                    "excede_capacidad": int(horas_acum > h_total + 1e-6),
                })

            resumen.append({
                "fecha": fecha, "recurso": recurso, "n_corridas": len(ordenadas),
                "horas_produccion": round(prod_dia, 2), "horas_setup": round(setup_dia, 2),
                "setup_sin_optimizar_horas": round(setup_base, 2),
                "ahorro_setup_horas": round(setup_base - setup_dia, 2),
                "horas_netas_linea": h_netas,
                "horas_totales_dia": h_total,
                "holgura_setup_horas": round(h_total - prod_dia, 2),
                "exceso_horas": round(max(0.0, prod_dia + setup_dia - h_total), 2),
            })
            tipo_prev = ordenadas[-1]["tipo"]

    resultado = pd.DataFrame(resultado_filas).sort_values(["fecha", "recurso", "orden"])
    resumen = pd.DataFrame(resumen).sort_values(["fecha", "recurso"])
    excedidos = resumen[resumen["exceso_horas"] > 0]

    print("=== Secuencia dentro del plan de producción (Módulo 4) ===")
    print(f"{plan['sku'].nunique()} SKU · {len(resumen)} combinaciones día-línea secuenciadas.")
    s_base = resumen["setup_sin_optimizar_horas"].sum()
    s_opt = resumen["horas_setup"].sum()
    ahorro = s_base - s_opt
    print(f"\n  Setup sin optimizar:  {s_base:6.1f} h   (orden del plan, por código de SKU)")
    print(f"  Setup optimizado:     {s_opt:6.1f} h   "
          f"({s_opt / max(resumen['horas_produccion'].sum(), 1e-9):.1%} del tiempo de producción)")
    print(f"  Ahorro:               {ahorro:6.1f} h   "
          f"({(ahorro / s_base if s_base > 0 else 0):.1%} menos setup)")
    por_linea = (resumen.groupby("recurso")[["setup_sin_optimizar_horas", "horas_setup",
                                             "ahorro_setup_horas"]].sum().round(1)
                 .rename(columns={"setup_sin_optimizar_horas": "sin_optimizar_h",
                                  "horas_setup": "optimizado_h", "ahorro_setup_horas": "ahorro_h"}))
    print("\n  Por línea:")
    print(por_linea.to_string())

    if matriz.faltantes:
        print("\n[AVISO] Pares de etapas SIN tiempo en la matriz -- se usó un valor de respaldo. "
              "Cargarlos en Configuración -> Tipos y Tiempos de Setup:")
        for (a, b), v in sorted(matriz.faltantes.items()):
            regla = ("promedio de cambios dentro de la misma etapa" if a == b
                     else ("valor del sentido inverso" if (b, a) in matriz.lookup else "peor caso"))
            print(f"    {a} -> {b}: se usó {v * 60:.0f} min ({regla})")

    if not excedidos.empty:
        print(f"\n[ALERTA] {len(excedidos)} día(s)-línea: producción + setup no caben en el día "
              f"({HORAS_CALENDARIO_DIA:.0f} h menos paradas). Puede requerir mover una corrida "
              f"a otro día o línea:")
        print(excedidos[["fecha", "recurso", "n_corridas", "horas_produccion", "horas_setup",
                         "horas_totales_dia", "holgura_setup_horas",
                         "exceso_horas"]].to_string(index=False))
    else:
        print(f"\nTodas las líneas-día caben: producción neta + setup <= {HORAS_CALENDARIO_DIA:.0f} h "
              f"(menos paradas).")

    print()
    print(resultado.head(30).to_string(index=False))

    resultado.to_sql("secuencia_produccion_pt", conn, if_exists="replace", index=False)
    # Resumen por día-línea (para el frontend: setup sin optimizar / optimizado / ahorro)
    resumen.to_sql("secuencia_resumen_pt", conn, if_exists="replace", index=False)
    conn.close()

    salida = ROOT_DIR / f"secuencia_produccion_{date.today():%Y%m%d}.csv"
    resultado.to_csv(salida, index=False, encoding="utf-8-sig")
    print(f"\n[OK] Guardado en tablas secuencia_produccion_pt, secuencia_resumen_pt y en: {salida}")


if __name__ == "__main__":
    main()