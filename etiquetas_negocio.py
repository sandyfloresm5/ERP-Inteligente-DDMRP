"""
etiquetas_negocio.py
Cargill Ecuador - Aquanutrition
"""

# ------------------------------------------------------------------
# Tipo de demanda
# ------------------------------------------------------------------

TIPO_DEMANDA_LABEL = {
    "smooth": "Demanda Estable",
    "erratic": "Demanda Variable",
    "intermittent": "Demanda Esporádica",
    "lumpy": "Demanda Irregular",
    "sin_historia": "Producto Nuevo / Sin Datos",
    "insuficiente_data": "Datos Insuficientes",
}

# ------------------------------------------------------------------
# Método ganador -> etiqueta genérica + detalle técnico aparte
# ------------------------------------------------------------------

def etiqueta_metodo(metodo_ganador: str) -> dict:
    """
    Devuelve {label, detalle_tecnico} -- label es lo que ve el
    planificador por defecto, detalle_tecnico queda disponible detrás
    de un ícono "ⓘ" para quien quiera el nombre real del modelo.
    """
    if metodo_ganador is None:
        return {"label": "Sin Modelo Asignado", "detalle_tecnico": None}

    m = metodo_ganador

    if m.startswith("Naive_ColdStart") or m.startswith("Seasonal_Naive_ColdStart"):
        return {"label": "Sin Historial Suficiente", "detalle_tecnico": m}

    if m in ("SBA", "TSB") or (m.startswith("Blend(") and ("SBA" in m or "TSB" in m)
                                and not any(t in m for t in ("Prophet", "Holt", "SES", "Tendencia", "Media_Movil"))):
        return {"label": "Modelo de Demanda Esporádica", "detalle_tecnico": m}

    if m in ("Naive", "Seasonal_Naive"):
        return {"label": "Estimación Simple", "detalle_tecnico": m}

    # Prophet, Holt, Holt_Winters, SES, Tendencia_Lineal, Media_Movil_*,
    # y blends mixtos -- se agrupan como "Modelo de Tendencia".
    if m.startswith("Blend("):
        return {"label": "Modelo de Tendencia (combinado)", "detalle_tecnico": m}

    return {"label": "Modelo de Tendencia", "detalle_tecnico": m}


# ------------------------------------------------------------------
# Semáforo Capa 1 -- Confianza del Pronóstico
# ------------------------------------------------------------------

SEMAFORO_CAPA1_LABEL = {
    "🟢": "Confianza Alta",
    "🟡": "Confianza Media",
    "🔴": "Confianza Baja",
    "⚪": "No Aplica",
}

SEMAFORO_CAPA1_MENSAJE = {
    "🟢": "El modelo acertó consistentemente contra el histórico real. Puede usar este número con tranquilidad.",
    "🟡": "El modelo acertó de forma aceptable, pero con margen de error moderado. Úselo como referencia, no como cifra exacta.",
    "🔴": "El modelo tuvo error alto o no hay suficiente historial para confiar en el número. Revisar antes de tomar decisiones de compra o producción con esta cifra.",
    "⚪": "No hay suficiente información para evaluar la confianza de este pronóstico.",
}

# ------------------------------------------------------------------
# Semáforo Capa 2 -- Cambio a Revisar (antes "Alerta de Negocio")
# ------------------------------------------------------------------

SEMAFORO_CAPA2_LABEL = {
    "🟢": "Sin Cambios Relevantes",
    "🟡": "Cambio Moderado",
    "🔴": "Cambio Importante",
    "⚪": "No Aplica",
}

SEMAFORO_CAPA2_MENSAJE = {
    "🟢": "El pronóstico está en línea con el comportamiento reciente de este cliente/SKU.",
    "🟡": "El pronóstico se aleja moderadamente de lo habitual. Vale la pena confirmar si hay una razón conocida (promoción, cambio de temporada, nuevo pedido).",
    "🔴": "El pronóstico difiere fuertemente de lo habitual para este cliente/SKU. Dar seguimiento operativo: confirmar con el cliente o el equipo comercial si el cambio es real antes de ajustar producción o inventario.",
    "⚪": "No hay suficiente historial para comparar contra lo habitual.",
}

# ------------------------------------------------------------------
# Mensaje combinado (Capa 1 x Capa 2) -- lo que ve el planificador
# como resumen de una serie, en una sola frase accionable.
# ------------------------------------------------------------------

def mensaje_accion_serie(semaforo_confiabilidad: str, semaforo_negocio: str,
                          baja_confianza: bool = False) -> str:
    """
    Combina ambas capas en UNA recomendación de acción clara. Prioriza
    decirle al planificador qué hacer, no solo describir el estado.
    """
    if baja_confianza:
        return ("Sin historial suficiente para generar un pronóstico confiable. "
                "Tratar como estimación preliminar -- confirmar con el cliente o comercial "
                "antes de comprometer producción o inventario.")

    if semaforo_confiabilidad == "🔴":
        return ("El modelo no está prediciendo bien esta serie. No usar esta cifra para "
                "decisiones de compra sin validar primero con el histórico real o con comercial.")

    if semaforo_confiabilidad in ("🟢", "🟡") and semaforo_negocio == "🔴":
        return ("El modelo es confiable, pero el número proyectado es muy distinto a lo "
                "habitual. Dar seguimiento operativo: confirmar con el cliente o comercial "
                "si el cambio es real antes de ajustar producción o inventario.")

    if semaforo_confiabilidad in ("🟢", "🟡") and semaforo_negocio == "🟡":
        return ("Pronóstico razonablemente confiable con un cambio moderado respecto a lo "
                "habitual. Monitorear, sin acción inmediata requerida.")

    return "Pronóstico confiable y en línea con el comportamiento habitual. Sin acción requerida."


# ------------------------------------------------------------------
# Variables externas (ONI / precio) -- mensajes de seguimiento de mercado
# ------------------------------------------------------------------

def mensaje_seguimiento_oni(alert_status: str, porcentaje_persistencia, frase_original: str) -> str:
    """
    Traduce el estado climático oficial de NOAA a un mensaje de
    seguimiento de mercado para el planificador -- no es una acción
    directa sobre una serie, es contexto para anticipar tendencias
    generales de consumo en las próximas semanas/meses.
    """
    if alert_status is None or "Not Active" in str(alert_status):
        return ("Condiciones climáticas del Pacífico en fase neutral. Sin señal de "
                "El Niño/La Niña activa -- no se anticipan efectos relevantes en el "
                "consumo por esta variable en el corto plazo.")

    fenomeno = "El Niño" if "Niño" in str(alert_status) else "La Niña"
    pct_txt = f" ({porcentaje_persistencia}% de probabilidad, según el último reporte de NOAA)" if porcentaje_persistencia else ""

    return (f"Fenómeno {fenomeno} activo{pct_txt}. Este fenómeno históricamente se asocia "
            f"con cambios en la producción camaronera. Dar seguimiento a este indicador "
            f"junto con el equipo comercial/técnico en los próximos meses -- no genera una "
            f"acción inmediata por sí solo, es contexto de mercado a monitorear.")


def mensaje_seguimiento_precio(cambio_pct: float, umbral: float = 0.05) -> str:
    """Mensaje de seguimiento de mercado sobre el precio de exportación."""
    if cambio_pct is None:
        return "Sin datos suficientes de precio de exportación para evaluar tendencia."

    if abs(cambio_pct) < umbral:
        return ("El precio de exportación de camarón se mantiene estable respecto al mes "
                "anterior. Sin señal relevante de mercado por ahora.")

    direccion = "subió" if cambio_pct > 0 else "bajó"
    return (f"El precio de exportación de camarón {direccion} {abs(cambio_pct)*100:.1f}% "
            f"respecto al mes anterior. Este tipo de cambios históricamente se relaciona, "
            f"con cierto rezago, a variaciones en la demanda de algunos clientes. "
            f"Dar seguimiento junto con comercial -- no es una señal definitiva por sí sola.")


# ------------------------------------------------------------------
# Aplicar traducción completa a una fila de diagnostico_final
# ------------------------------------------------------------------

def traducir_serie(fila: dict) -> dict:
    """
    Toma una fila cruda de diagnostico_final (nombres técnicos) y
    devuelve una versión enriquecida con etiquetas y mensaje de acción,
    SIN perder los campos técnicos originales (quedan disponibles bajo
    el ícono de detalle para quien los necesite).
    """
    fila = dict(fila)  # no mutar el original

    fila["tipo_demanda_label"] = TIPO_DEMANDA_LABEL.get(fila.get("tipo_demanda"), fila.get("tipo_demanda"))

    metodo_info = etiqueta_metodo(fila.get("metodo_ganador"))
    fila["metodo_label"] = metodo_info["label"]
    fila["metodo_detalle_tecnico"] = metodo_info["detalle_tecnico"]

    c1 = fila.get("semaforo_confiabilidad_estadistica")
    c2 = fila.get("semaforo_alerta_negocio")

    fila["confianza_pronostico_label"] = SEMAFORO_CAPA1_LABEL.get(c1, c1)
    fila["confianza_pronostico_mensaje"] = SEMAFORO_CAPA1_MENSAJE.get(c1, "")

    fila["cambio_a_revisar_label"] = SEMAFORO_CAPA2_LABEL.get(c2, c2)
    fila["cambio_a_revisar_mensaje"] = SEMAFORO_CAPA2_MENSAJE.get(c2, "")

    fila["mensaje_accion"] = mensaje_accion_serie(
        c1, c2, baja_confianza=bool(fila.get("baja_confianza", False))
    )

    return fila