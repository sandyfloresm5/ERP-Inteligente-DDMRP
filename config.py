# Config.py

from pathlib import Path
from tkinter import ON

# ============================================================
# CONFIGURACIÓN GENERAL DEL PROYECTO
# Simulador Inteligente - Cargill Aquanutrition Ecuador
# ============================================================

# ------------------------------------------------------------
# Ruta Base del Proyecto
# ------------------------------------------------------------

BASE_DIR = Path(r"C:\Users\s545890\OneDrive - Cargill Inc\Simulador Inteligente")
ONEDRIVE_SIMULADOR = Path(r"C:\Users\s545890\OneDrive - Cargill Inc\Simulador Inteligente")
ONEDRIVE_PLANNING  = Path(r"C:\Users\s545890\OneDrive - Cargill Inc\ANH EC Supply Chain - Business Intelligence - PLANNING")
ONEDRIVE_SAP = Path(r"C:\Users\s545890\OneDrive - Cargill Inc\Archivos de Adriana Espinoza Pinzon - Trainning materials SAP (Usuarios finales)")

DATA_RAW   = BASE_DIR / "data" / "raw"
DATA_EXT   = BASE_DIR / "data" / "external"
DATA_PROC  = BASE_DIR / "data" / "processed"
MODELS_DIR = BASE_DIR / "models" / "trained"
LOGS_DIR   = BASE_DIR / "logs"

# DB de producción HOY: OneDrive (datos reales ya cargados y validados).
# El día que se migre a servidor, cambiar esta línea únicamente:
DB_PATH = ONEDRIVE_SIMULADOR / "db" / "simulador.db"
# DB_PATH = BASE_DIR / "db" / "supply_chain.db"   # <- ruta de producción futura

# Crear directorios de proyecto si no existen (no toca OneDrive)
for d in [DATA_RAW, DATA_EXT, DATA_PROC, MODELS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Rutas de archivos fuente (Excel/xlsm) ───────────────────────────────────
EXCEL_VENTAS      = ONEDRIVE_SIMULADOR / "Data" / "raw" / "Histórico Ventas.xlsx"
EXCEL_BOM         = ONEDRIVE_PLANNING / "Base de Datos MP - BI" / "Plan de Produccion.xlsx"
EXCEL_MP_STOCK    = ONEDRIVE_PLANNING / "Base de Datos MP - BI" / "MP" / "Análisis de MP - 2026.xlsm"
EXCEL_MP_CONSUMOS_2    = ONEDRIVE_PLANNING / "Base de Datos MP - BI" / "MP" / "Análisis de MP - 2026.xlsm"
EXCEL_MP_CONSUMOS_1 = ONEDRIVE_PLANNING / "Base de Datos MP - BI" / "MARA MP" / "Consumos 2026.xlsx"
EXCEL_MP_LEAD_TIME_MP = ONEDRIVE_PLANNING / "Base de Datos MP - BI" / "MARA MP" / "Lead Time MP.xlsx"
EXCEL_MP_CONSOLIDADO = ONEDRIVE_PLANNING / "Base de Datos MP - BI" / "MP" / "Análisis de MP - 2026.xlsm"
EXCEL_PT = ONEDRIVE_PLANNING / "PRODUCTO TERMINADO" / "Anual" / "2026" / "Análisis de PT - 2026.xlsm"
EXCEL_ORDENES_COMPRAS = ONEDRIVE_PLANNING / "IMPORTACIONES" / "OC_IMP_STATUS - SAP.xlsx"
EXCEL_STOCK_PT = ONEDRIVE_PLANNING / "PRODUCTO TERMINADO" / "Anual" / "2026" / "Análisis de PT - 2026.xlsm"
EXCEL_PEDIDOS_PENDIENTES_PT = ONEDRIVE_PLANNING / "PRODUCTO TERMINADO" / "Anual" / "2026" / "Análisis de PT - 2026.xlsm"
EXCEL_RATIO_PLANIFICACION = ONEDRIVE_SIMULADOR / "Data" / "raw" / "Ratio de Planificación.xlsx"
EXCEL_PLANIFICACION_PT_GYE = ONEDRIVE_PLANNING / "PLANIFICACION_PRODUCCION" / "PLANTA_GUAYAQUIL" / "Data historica FY 25-26.xlsx"
EXCEL_PLANIFICACION_PT_DURAN = ONEDRIVE_PLANNING / "PLANIFICACION_PRODUCCION" / "PLANTA_DURAN" / "Data historica FY 25-26.xlsx"
EXCEL_PT_NIS_SAP = ONEDRIVE_SAP / "Códigos centros Latam" / "TRADUCTOR SAP-NIS.xlsx"
EXCEL_PT_FORECAST = ONEDRIVE_PLANNING / "PRODUCTO TERMINADO" / "Anual" / "2026" / "Análisis de PT - 2026.xlsm"
EXCEL_RMB_MP = ONEDRIVE_PLANNING / "Base de Datos MP - BI" / "MP" / "Análisis de MP - 2026.xlsm"
EXCEL_PT_COMPLETOS = ONEDRIVE_PLANNING / "PRODUCTO TERMINADO" / "Anual" / "2026" / "Análisis de PT - 2026.xlsm"
EXCEL_SAP_CUSTOMER = ONEDRIVE_SIMULADOR/ "Data" / "raw" / "SAP Customers.xlsx"
EXCEL_TARIFA_TRANSPORTE = ONEDRIVE_SIMULADOR / "Data" / "raw" / "Tabulador de Transporte JUL 2026.xlsx"

SHEET_VENTAS   = "Histórico Ventas"
SHEET_BOM      = "CONSUMOS"
SHEET_MP_STOCK = "Inventario MP"
SHEET_MP_CONSUMOS_1 = "Consumo MP"
SHEET_MP_CONSUMOS_2 = "Consumo MP"
SHEET_MP_LEAD_TIME = "Lead Time MP"
SHEET_PT = "Análisis Mensual"
SHEET_ORDENES_MP = "CDI"
SHEET_PEDIDOS_PENDIENTES_PT = "Pedido Pendientes PT"
SHEET_STOCK_PT = "Inventario en Curso PT"
SHEET_RATIO_PLANIFICACION = "Base"
SHEET_PLANIFICACION_PT_GYE = "Produccion CANE"
SHEET_PLANIFICACION_PT_DURAN = "Resumen"
SHEET_PT_NIS_SAP = "CODIGOS DE MATERIAL"
SHEET_PT_FORECAST = "Proyecciones PT"
SHEET_MP_CONSOLIDADO = "Análisis MP"
SHEET_RMB_MP = "RMB - MP"
SHEET_PT_CCOMPLETOS = "Base Completa PT"
SHEET_SAP_CUSTOMER = "Ship To DATA"
SHEET_TARIFA_TRANSPORTE = "TABU_MASTER"

# ── Parámetros de negocio ───────────────────────────────────────────────────
RENTABILIDAD          = 0.30
COSTO_LINEA_HORA      = 100.0
HORAS_DIA             = 24
DIAS_MES              = 30
HORAS_MES_DISPONIBLES = HORAS_DIA * DIAS_MES

HORIZON_FORECAST_MESES = 6
MILP_TIME_LIMIT_SEG    = 120

# ── Lag ONI ──────────────────────────────────────────────────────────────────
ONI_LAG_MESES = 4

# ── Exógena temperatura (NASA POWER) ──────────────────────────────────────────────────────────────────
TEMP_PHI_MIN = 0.0
TEMP_PHI_MAX = 0.95
TEMP_SUAVIZAR_CLIM = True
TEMP_MIN_MESES_HIST = 3

# ── Umbrales clasificación SKU (ADI/CV² - Syntetos-Boylan) ─────────────────
ADI_UMBRAL      = 1.32
MIN_FOLDS_CV = 3
ONI_PHI_DECAY = 0.85
CV2_UMBRAL      = 0.49
P_CERO_UMBRAL   = 0.10
MESES_MIN_HIST  = 12     # meses mínimos de historia para confiar en Prophet
MIN_OBS_PROPHET = 15     # periodos mínimos con demanda para habilitar Prophet
RECENCIA_DECAY_TORNEO = 0.5

# ── Candidato SARIMAX ─────────────────
SARIMAX_ORDER = (1,1,1)
SARIMAX_SEASONAL_ORDER = (0,0,0,0)
SARIMAX_FOURIER_K = 1
SARIMAX_MIN_HIST = 18
SARIMAX_OBS_POR_PARAM = 3
SARIMAX_REGRESORES = ["temp_anom", "precio_promedio_libra"]

# --- ADIDA (agregación temporal para series intermitentes) ---
ADIDA_AGG_NIVEL = 2      # agrega mensual -> trimestral antes de pronosticar
ADIDA_MIN_AGG_OBS = 4    # mínimo de puntos agregados para ajustar SES

# Newsvendor Cu/Co
TASA_COSTO_CAPITAL_ANUAL = 0.15
TASA_ALMACENAMIENTO_ANUAL = 0.03
TASA_OBSOLESCENCIA_ANUAL = 0.05
CO_TASA_ANUAL_ESTIMADA = TASA_COSTO_CAPITAL_ANUAL + TASA_ALMACENAMIENTO_ANUAL + TASA_OBSOLESCENCIA_ANUAL
CO_ES_ESTIMADO = True  # hasta que finanzas confirme la tasa real

# ============================================================
# DDMRP
# ============================================================
# Horizonte de picos: hasta cuántos días adelante se buscan picos.
# Por defecto el DLT del nodo.

# Umbrales de la alerta de On-Hand (stock físico contra el TOR).
ONHAND_CRITICO = 0.50
ONHAND_ALERTA = 1.00
UMBRAL_PICO_FRACCION_TOR = 0.50

# --- Capacidad de bodega de MP (TM) ---
# Dos tipos de almacenamiento distintos:
#   - Bodegas externas (AVIPAZ, CARGOMAR, CD DAJAHU, CD DENCER, ECUABULK,
#     INASA, PAXOS, TLD, QC TERMINALES, CARGOPORT): red que alimenta de MP
#     a ambas plantas, cada una con su propia asignación de espacio.
#   - "PLANTA DURÁN" (800 TM): almacenamiento propio EN la planta de
#     Durán, distinto de las bodegas externas -- espacio físico del sitio
#     de producción, no de la red logística.
# 1FZE = Durán, 1FZO = Guayaquil. Se suman todas las líneas de cada
# planta (bodegas + almacenamiento propio donde aplique).
CAPACIDAD_BODEGA_MP_DURAN_TM = 128631      # 1FZE (incluye Planta Durán 800)
CAPACIDAD_BODEGA_MP_GUAYAQUIL_TM = 95331   # 1FZO
CAPACIDAD_BODEGA_MP_TM = (CAPACIDAD_BODEGA_MP_DURAN_TM
                          + CAPACIDAD_BODEGA_MP_GUAYAQUIL_TM)  # 223,962 TM

# ============================================================
# Umbrales del semáforo de diagnóstico (validar_forecast_prophet.py)
# ============================================================

# --- WAPE de validación ---
UMBRAL_WAPE_VERDE = 0.15      # WAPE <= 15% -> 🟢
UMBRAL_WAPE_AMARILLO = 0.30   # WAPE <= 30% -> 🟡 ; > 30% -> 🔴

# --- Razón P50 proyectado vs promedio histórico últimos 12 meses ---
# General (clases B, C y series sin clase ABC ranqueada)
UMBRAL_RAZON_P50_VERDE = 0.15      # desviación <= 15% -> 🟢
UMBRAL_RAZON_P50_AMARILLO = 0.30   # desviación <= 30% -> 🟡 ; > 30% -> 🔴

# Más estricto para clase A (series de mayor rentabilidad, cualquier
# salto brusco tiene impacto financiero desproporcionado)
UMBRAL_RAZON_P50_VERDE_CLASE_A = 0.10      # desviación <= 10% -> 🟢
UMBRAL_RAZON_P50_AMARILLO_CLASE_A = 0.20   # desviación <= 20% -> 🟡 ; > 20% -> 🔴

# --- Amplitud de banda de incertidumbre (P90-P10) relativa al P50 ---
UMBRAL_BANDA_VERDE = 0.50      # (P90-P10)/P50 <= 50% -> 🟢
UMBRAL_BANDA_AMARILLO = 1.00   # <= 100% -> 🟡 ; > 100% -> 🔴

# --- nRMSE de validación (penaliza errores puntuales grandes, no solo
# el error promedio -- detecta "infartos operativos") ---
# nRMSE suele ser mayor que WAPE para la misma serie porque eleva al
# cuadrado los errores; umbrales más laxos que los de WAPE por diseño.
UMBRAL_NRMSE_VERDE = 0.25
UMBRAL_NRMSE_AMARILLO = 0.50

# --- Tolerancia para verificar convergencia geométrica del ONI proyectado ---
ONI_TOLERANCIA_CONVERGENCIA = 0.01

# --- Umbral de bias absoluto (TM) para diagnóstico forense ---
# Usado en diagnostico_forense_prophet.py para aislar "villanos". OJO:
# es un umbral en TONELADAS ABSOLUTAS -- útil para priorizar impacto de
# capital de trabajo en series de alto volumen (clase A), pero NO
# reemplaza al WAPE para decidir qué series necesitan ajuste algorítmico.
# Ver ambas lecturas juntas (WAPE = prioridad de código, TM = prioridad
# financiera) antes de decidir dónde actuar primero.
BIAS_VILLANO_TM = -100.0

# ── Parámetros Demand Driven ────────────────────────────────────────────────
Z_SCORE_95 = 1.645
LEAD_TIME_PRODUCCION_DIAS = 3
HORIZONTE_PICOS_PT_DIAS = LEAD_TIME_PRODUCCION_DIAS
DEMANDA_CALIFICADA_MES_COMPLETO = True

# --- Ciclo de pedido PT ---
# Producción trabaja 24/7 y produce SKU todos los días: el ciclo de
# reposición es diario.
#
# La zona verde de DDMRP representa el TAMAÑO DE LOTE -- cada cuánto se
# repone -- no una segunda capa de protección. La fórmula por defecto
# (ADU x DLT x LTF = 2.4 días) duplicaba la cobertura: la zona roja ya
# protege el lead time con factor, y la verde volvía a protegerlo con el
# mismo factor. Con ciclo declarado, ese dato manda sobre la fórmula.
#
# Efecto medido: TOG de 8.7 -> 6.3 días de cobertura. El objetivo total
# pasa de 17.346 TM (que NO cabe en los 13.281 TM de bodega) a ~12.500 TM.
CICLO_PEDIDO_PT_DIAS = 1

# --- Método del Factor de Variabilidad ---
# "perfil": (p90-p10)/p50 dividido por 2.5631, que lleva el ancho de banda
#           a unidades de desviación estándar -- la escala en que el DDMRP
#           define el FV (baja 0.2-0.4, media 0.41-0.6, alta 0.61-1.0).
# "banda":  el ancho relativo crudo. Queda ~2.5x inflado respecto de esa
#           escala y multiplica la zona roja de más.
VAR_FACTOR_METODO = "perfil"

# --- Capacidad de bodega de PT (TM) ---
# Suma de las 4 localidades: CD Dajahu 3.900, CD Naturisa 5.400,
# Planta Cane 1.981, Planta Durán 2.000.
# DDMRP dimensiona cada SKU por separado y nunca pregunta si la suma cabe.
# Este número es la restricción física que el buffer ignora por diseño, y
# sirve para declarar el exceso en vez de presentar un objetivo imposible.
CAPACIDAD_BODEGA_PT_TM = 15931

# ── Nodo MP ────────────────────────────────────────────────
VENTANA_MOVIL_MP_MESES = 6
LT_FACTOR_CORTE_CORTO = 20
LT_FACTOR_CORTE_MEDIO = 75

# Corte por Distribución real de Lead Time MP

LT_FACTOR_VALOR_CORTO = 0.80
LT_FACTOR_VALOR_MEDIO = 0.50
LT_FACTOR_VALOR_LARGO = 0.30

# MOQ PT:

MOQ_PT_TM = 50

# MOQ MP:
MOQ_MP_MIN_ORDENES_CONFIABLE = 3

# ── Módulo 2 - Forecast: grillas de hiperparámetros ─────────────────────────
N_SIMULACIONES_BOOTSTRAP = 500

ALPHA_GRID_SBA = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

ALPHA_GRID_TSB = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
BETA_GRID_TSB  = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

# Grilla completa -- se usa para series 'erratic' (donde la volatilidad
# real puede justificar más flexibilidad de tendencia).
# --- CAMBIO (2026-07-08): se eliminó 0.5 de la grilla ---
# Diagnóstico forense confirmó "Club del 0.5": series de bajo volumen que
# ganaron changepoint_prior_scale=0.5 mostraron overfitting severo al
# ruido reciente (WAPE 94%-117%). Se acota el máximo a 0.1.
PROPHET_PARAM_GRID = {
    "changepoint_prior_scale": [0.001, 0.01, 0.05, 0.1],
    "seasonality_prior_scale": [0.01, 0.1, 1.0, 10.0],
    "seasonality_mode": ["additive", "multiplicative"],
}

# --- Grilla restringida para series 'smooth' ---
# Diagnóstico forense (diagnostico_forense_prophet.py) confirmó: los
# "villanos" con bias_validacion muy negativo (sobreestimación severa)
# son series smooth donde el grid search eligió changepoint_prior_scale
# alto (flexible), enganchándose a una subida o pico reciente y
# proyectándola como tendencia sostenida a 6 meses -- overfitting a los
# últimos meses, no una falla de la librería.
# Series smooth son por definición estables (CV² bajo, ver ADI_UMBRAL/
# CV2_UMBRAL) -- no deberían necesitar tendencias flexibles. Se restringe
# su grilla a valores rígidos (<=0.05), dejando la grilla completa solo
# para erratic.
PROPHET_PARAM_GRID_SMOOTH = {
    "changepoint_prior_scale": [0.001, 0.01, 0.05],
    "seasonality_prior_scale": [0.01, 0.1, 1.0, 10.0],
    "seasonality_mode": ["additive", "multiplicative"],
}

# --- Growth logístico con techo de capacidad ---
# Diagnóstico forense encontró "Club del 0.05": series con
# changepoint_prior_scale RÍGIDO que igual sobreestimaron severamente
# (bias hasta -538 TM), porque Prophet con growth='linear' no tiene
# freno -- si la pendiente reciente apunta hacia arriba, la proyecta
# sin límite 6 meses hacia adelante. En un negocio con capacidad física
# de consumo (piscinas camaroneras), eso es estructuralmente irreal.
#
# Se usa growth='logistic' con un techo (cap) dinámico por serie:
#   cap = max(demanda histórica de la serie) * CAP_MULTIPLICADOR_HISTORICO
# Esto permite crecimiento, pero lo amortigua conforme se acerca al
# techo -- en vez de una recta infinita.
CAP_MULTIPLICADOR_HISTORICO = 1.3

MIN_PERIODOS_VALIDACION = 12

# ============================================================
# Torneo de modelos (forecast_engine_torneo.py)
# ============================================================
# Reemplaza la asignación rígida por tipo_demanda -> motor único.
# Para cada serie se corre un torneo entre los modelos candidatos
# elegibles según su historia disponible, y se selecciona el de menor
# WAPE (con bias como desempate) en validación cruzada rolling-origin.

N_FOLDS_TORNEO = 3          # folds objetivo para el rolling-origin CV
TEST_PERIODS_TORNEO = 3     # meses de test por fold
MIN_HIST_TORNEO = 3        # por debajo de esto, no se corre el torneo:
                             # fallback directo a Naive/Seasonal Naive
                             # (cold start -- ver diagnosticar_causa en
                             # diagnostico_forense_prophet.py, el mismo
                             # principio de "no confiar en evidencia
                             # insuficiente" aplicado aquí)
MIN_HIST_HOLT_WINTERS = 24  # requiere 2 ciclos estacionales completos

# --- CAMBIO (2026-07-11): ensamble condicional top-2 ---
# Si el 2do lugar del torneo tiene WAPE dentro de este % relativo del
# 1ro, se promedian (ponderado por 1/WAPE) en vez de quedarse solo con
# el ganador. Evidencia M4/M5: promediar modelos razonablemente buenos
# reduce ruido; promediar uno bueno con uno mediocre lo empeora -- por
# eso el umbral, no se ensambla todo el torneo indiscriminadamente.
UMBRAL_BLEND_RELATIVO = 0.15  # 15% de diferencia relativa en WAPE

# ── API externa ──────────────────────────────────────────────────────────────
NOAA_ONI_URL   = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
NASA_LAT = -1.8
NASA_LON = -80.7

# ── Alertas ──────────────────────────────────────────────────────────────────
UMBRAL_ANOMALIA_DATO = 0.50
EMAIL_ALERTAS = "Sandy_Flores@cargill.com"

# ── FastAPI ──────────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 8000

# ============================================================
# Proyección de precio de camarón (proyeccion_precio_camaron.py)
# ============================================================
# No existe fuente pública oficial con proyecciones numéricas de precio
# de exportación -- se genera una proyección propia (Holt amortiguado),
# validada por CV, y usada como regresor SOLO si ablacion_regresores_
# prophet.py confirma mejora real de WAPE (mismo criterio ya usado).
MIN_HIST_PRECIO_PROYECCION = 12  # meses mínimos de historia CNA para
                                   # intentar proyectar precio con
                                   # confianza (por debajo de esto, se
                                   # sigue usando precio plano)

# ============================================================
# Calibración dinámica de ONI_PHI_DECAY (Ingesta_ENSO_Forecast.py)
# ============================================================
URL_ENSODISC = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
# Fracción del valor actual de ONI que se asume "queda" justo al final
# de la ventana de persistencia declarada por NOAA (ver docstring del
# script para el detalle de cómo se usa esto para calibrar phi).
FRACCION_RESIDUAL_AL_FIN_PERSISTENCIA = 0.30