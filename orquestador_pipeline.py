"""
orquestador_pipeline.py
Cargill Ecuador - Aquanutrition
"""

import subprocess
import sys
import os
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import LOGS_DIR, EMAIL_ALERTAS, DB_PATH

LOG_FILE = LOGS_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

PASOS = [
    # --- Ingesta ---
    (r"Pipeline\01_Ingesta_Ventas.py", True),

    # --- Forecast: provisto por Comercial, ya no se genera internamente ---
    ("Ingesta_Forecast_Comercial.py", True),
    ("completar_forecast_faltante.py", True),
    ("calcular_adu_mes_actual.py", False),

    # --- Consumo MP (necesario ANTES del buffer, para el ADU de MP) ---
    ("Ingesta_Consumo_MP.py", False),
    ("Ingesta_RMB_MP.py", False),

    # --- Buffer, inventario, clasificación ---
    ("calcular_buffer_ddmrp_dinamico.py", False),
    (r"Pipeline\Ingesta_Inventario.py", False),
    ("ajustar_buffer_daf_zaf.py", False),
    ("ajustar_buffer_mp.py", False),
    ("clasificar_mts_mto.py", True),
    ("calcular_nfp.py", False),
    ("detectar_picos_demanda.py", False),
    
    # --- Plan de producción (necesita posicionamiento_pt ya listo) ---
    ("calcular_flete_pedidos.py", False),
    ("optimizar_produccion.py", True),
    ("plan_compras_mp.py", False),
    ("optimizar_secuencia_produccion.py", True),

    # --- MP vs Plan (necesita plan_produccion YA generado) ---
    ("Ingesta_MP_Importacion_BOM.py", False),
    ("alertar_mp_vs_plan.py", False),
    ("validar_buffer_vs_capacidad.py", False),
    ("validar_plan_vs_buffer.py", True),

    ("alertar_urgencias_plan.py", True),
]

def _validar_pasos():
    """
    Chequeo de arranque: scripts inexistentes o duplicados. Un duplicado
    no rompe nada pero hace trabajo repetido; un script faltante aborta
    el pipeline en mitad de la corrida, después de gastar media hora.
    """
    base = Path(__file__).resolve().parent
    problemas = []

    vistos = {}
    for script, _ in PASOS:
        vistos[script] = vistos.get(script, 0) + 1
    duplicados = [s for s, n in vistos.items() if n > 1]
    if duplicados:
        problemas.append(f"Scripts duplicados en PASOS: {duplicados}")

    faltantes = [s for s, _ in PASOS if not (base / s).exists()]
    if faltantes:
        problemas.append(f"Scripts que no existen en disco: {faltantes}")

    return problemas

def _enviar_alerta(asunto: str, cuerpo: str):
    """
    Notificación de fallo. Si el envío falla (ej. sin SMTP configurado),
    no debe romper el orquestador -- solo se loguea.
    """
    try:
        msg = MIMEText(cuerpo)
        msg["Subject"] = asunto
        msg["From"] = "simulador@cargill.com"
        msg["To"] = EMAIL_ALERTAS
        # NOTA: ajustar host/puerto SMTP real de Cargill antes de producción.
        with smtplib.SMTP("smtp.cargill.com", 25, timeout=10) as server:
            server.send_message(msg)
        logger.info(f"Alerta enviada a {EMAIL_ALERTAS}")
    except Exception as e:
        logger.warning(f"No se pudo enviar alerta por correo: {e}")

def registrar_corrida(estado: str, duracion_seg: float, detalle: str = ""):
    """
    Constancia de la corrida. La API lee esto para mostrar 'última
    actualización' y el estado de salud del pipeline.
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            fecha_inicio TEXT, fecha_fin TEXT, duracion_seg REAL,
            estado TEXT, detalle TEXT, log_file TEXT
        )
    """)
    conn.execute(
        "INSERT INTO pipeline_runs VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.now().isoformat(), datetime.now().isoformat(),
         duracion_seg, estado, detalle, str(LOG_FILE)),
    )
    conn.commit()
    conn.close()

def correr_paso(script: str, critico: bool) -> bool:
    ruta = Path(__file__).resolve().parent / script
    logger.info(f"--- Iniciando: {script} (crítico={critico}) ---")
    inicio = datetime.now()

    # Windows usa cp1252 para stdout capturado y no puede codificar los
    # emojis de los semáforos. Eso rompía el subproceso DESPUÉS de que el
    # trabajo real ya había terminado y guardado (Torneo_Forecast.py
    # guardaba forecast_final bien y tronaba en el print del resumen).
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        resultado = subprocess.run(
            [sys.executable, str(ruta)],
            capture_output=True, text=True, timeout=3600, env=env,
            encoding="utf-8", errors="replace",
        )
        duracion = (datetime.now() - inicio).total_seconds()

        if resultado.stdout:
            logger.info(resultado.stdout[-3000:])
        if resultado.stderr:
            logger.warning(resultado.stderr[-3000:])

        if resultado.returncode != 0:
            logger.error(f"{script} terminó con código {resultado.returncode} "
                         f"({duracion:.0f}s)")
            return False

        logger.info(f"{script} OK ({duracion:.0f}s)")
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"{script} excedió el tiempo límite (1h) -- abortado")
        return False
    except Exception as e:
        logger.error(f"{script} falló con excepción: {e}")
        return False

def main():
    inicio_total = datetime.now()
    logger.info(f"=== INICIO PIPELINE {inicio_total.isoformat()} ===")

    problemas = _validar_pasos()
    for p in problemas:
        logger.warning(f"[VALIDACIÓN DE PASOS] {p}")
    if any("no existen" in p for p in problemas):
        logger.error("Hay scripts inexistentes. Se aborta antes de empezar para no "
                     "fallar en mitad de una corrida larga.")
        sys.exit(1)

    logger.info(f"{len(PASOS)} pasos configurados.")

    pasos_fallidos_no_criticos = []

    for i, (script, critico) in enumerate(PASOS, start=1):
        logger.info(f"[{i}/{len(PASOS)}]")
        ok = correr_paso(script, critico)
        if not ok:
            if critico:
                duracion = (datetime.now() - inicio_total).total_seconds()
                mensaje = f"Pipeline abortado en paso crítico: {script}"
                logger.error(mensaje)
                registrar_corrida("FALLO_CRITICO", duracion, mensaje)
                _enviar_alerta("🔴 Simulador Cargill: pipeline FALLÓ",
                               f"{mensaje}\n\nRevisar log: {LOG_FILE}")
                sys.exit(1)
            else:
                pasos_fallidos_no_criticos.append(script)
                logger.warning(f"{script} falló pero no es crítico -- se continúa "
                               f"(debería tener su propio fallback interno)")

    duracion_total = (datetime.now() - inicio_total).total_seconds()
    detalle = (f"Pasos no críticos con fallo: {pasos_fallidos_no_criticos}"
               if pasos_fallidos_no_criticos else "Todos los pasos OK")

    logger.info(f"=== PIPELINE COMPLETO ({duracion_total:.0f}s) -- {detalle} ===")

    # Aviso explícito si la calibración no corrió: sin ella el buffer
    # queda subdimensionado y el VaR subestimado, y eso no se nota
    # mirando los números (salen "bien", solo que angostos).
    if "Calibrar_bandas.py" in pasos_fallidos_no_criticos:
        logger.warning("⚠️  La calibración de bandas NO corrió. El buffer DDMRP y el "
                       "VaR de esta corrida están calculados sobre bandas sin "
                       "calibrar: el TOR queda subdimensionado (riesgo de quiebre) "
                       "y el VaR subestimado. No presentar esos números sin aclararlo.")

    registrar_corrida("OK" if not pasos_fallidos_no_criticos else "OK_CON_AVISOS",
                      duracion_total, detalle)

    if pasos_fallidos_no_criticos:
        _enviar_alerta(
            "🟡 Simulador Cargill: pipeline OK con avisos",
            f"Pipeline completó, pero estos pasos no críticos fallaron "
            f"(revisar si el fallback fue adecuado):\n{pasos_fallidos_no_criticos}\n\n"
            f"Log: {LOG_FILE}",
        )

if __name__ == "__main__":
    main()