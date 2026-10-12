/* =====================================================================
 * dashboard.js
 * Cargill Ecuador - Aquanutrition
 * Simulador Inteligente de Abastecimiento
 * ===================================================================== */

const DASHBOARD_VERSION = '20260925a-simulador-riesgo-unificado';
console.log('%c dashboard.js ' + DASHBOARD_VERSION + ' ',
            'background:#134D3B;color:#fff;font-weight:bold;');

const API_BASE = `http://${window.location.hostname}:8000`;
let LT_PRODUCCION_HOY = 3;

let DATA = {
  catalogoSkus: new Map(),
  pipelineEstado: null,
};
let CHARTS = {};

function chartDisponible(canvas) {
  if (typeof Chart !== 'undefined') return true;
  if (canvas && canvas.parentElement) {
    canvas.parentElement.innerHTML = '<p style="color:var(--gray-400);font-size:12.5px;padding:20px 0;text-align:center;">Gráfico no disponible sin conexión a internet (Chart.js no cargó desde el CDN). El resto de la información sigue funcionando normalmente.</p>';
  }
  return false;
}

function fmtNum(v, dec) {
  dec = dec == null ? 1 : dec;
  let s = v.toFixed(dec);
  if (s.indexOf('.') !== -1) s = s.replace(/0+$/, '').replace(/\.$/, '');
  return s === '-0' ? '0' : s;
}
function fmtTM(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Math.round(v).toLocaleString() + ' TM';
}
function fmtUSD(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  const signo = v < 0 ? '-' : '';
  v = Math.abs(v);
  if (v >= 1e6) return signo + '$' + fmtNum(v / 1e6) + 'M';
  if (v >= 1e3) return signo + '$' + fmtNum(v / 1e3) + 'K';
  return signo + '$' + fmtNum(v, 0);
}
function fmtPct(v) { return (v === null || v === undefined || isNaN(v)) ? '—' : fmtNum(v * 100) + '%'; }
function fmtDias(v) { return (v === null || v === undefined || isNaN(v)) ? '—' : fmtNum(v) + ' d'; }
function fmtEntero(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Math.round(v).toLocaleString();
}

function horasDesde(fechaISO) {
  if (!fechaISO) return 'sin datos';
  const ms = Date.now() - new Date(fechaISO).getTime();
  const h = Math.round(ms / 3600000);
  if (h < 1) return 'hace instantes';
  if (h < 24) return `hace ${h}h`;
  return `hace ${Math.round(h / 24)}d`;
}

function hoyStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function sumarDiaStr(fechaStr, dias) {
  const d = parseFechaLocal(fechaStr);
  if (!d) return fechaStr;
  d.setDate(d.getDate() + dias);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const NOMBRES_MES = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
const NOMBRES_MES_LARGO = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];

function parseFechaLocal(fechaStr) {
  if (!fechaStr) return null;
  const soloFecha = String(fechaStr).slice(0, 10);
  const partes = soloFecha.split('-').map(Number);
  const [y, m, d] = partes;
  if (!y || !m) return null;
  return new Date(y, m - 1, d || 1);
}
function diasDesdeHoy(fechaStr) {
  const hoy = new Date();
  hoy.setHours(0, 0, 0, 0);
  const f = parseFechaLocal(fechaStr);
  if (!f) return null;
  return Math.round((f - hoy) / 86400000);
}
function sumarDias(fecha, dias) {
  const d = new Date(fecha);
  d.setDate(d.getDate() + dias);
  return d;
}
function fechaCorta(d) {
  if (!d) return '—';
  return `${String(d.getDate()).padStart(2,'0')}/${String(d.getMonth()+1).padStart(2,'0')}`;
}

let _anclaCache = null;
function getAncla() {
  if (_anclaCache) return _anclaCache;
  if (DATA.pipelineEstado && DATA.pipelineEstado.fecha_fin) {
    const fc = parseFechaLocal(DATA.pipelineEstado.fecha_fin);
    if (fc) { _anclaCache = new Date(fc.getFullYear(), fc.getMonth(), 1); return _anclaCache; }
  }
  if (HOY.datos && HOY.datos.fecha) {
    const fc = parseFechaLocal(HOY.datos.fecha);
    if (fc) { _anclaCache = new Date(fc.getFullYear(), fc.getMonth(), 1); return _anclaCache; }
  }
  const hoy = new Date();
  _anclaCache = new Date(hoy.getFullYear(), hoy.getMonth(), 1);
  return _anclaCache;
}
function offsetAFecha(mesOffset, ancla) {
  const a = ancla || getAncla();
  return new Date(a.getFullYear(), a.getMonth() + mesOffset, 1);
}

async function fetchJSON(path) {
  const resp = await fetch(API_BASE + path);
  if (!resp.ok) throw new Error(`HTTP ${resp.status} en ${path}`);
  return resp.json();
}
function descripcionSku(sku) {
  return DATA.catalogoSkus.get(String(sku)) || '';
}
function actualizarEstadoConexion(texto, ok) {
  document.querySelectorAll('.updated').forEach(el => {
    el.textContent = ok ? `actualizado ${horasDesde(DATA.pipelineEstado?.fecha_fin)}` : texto;
  });
}
async function cargarInicial() {
  try {
    const [catalogoSkus, pipelineEstado] = await Promise.all([
      fetchJSON('/api/v1/catalogo/skus').catch(() => []),
      fetchJSON('/api/v1/pipeline/estado').catch(() => null),
      cargarCapacidadBodegaBackend(),
    ]);

    DATA.catalogoSkus = new Map(
      (Array.isArray(catalogoSkus) ? catalogoSkus : [])
        .map(s => [String(s.sku), s.sku_descripcion]));
    DATA.pipelineEstado = pipelineEstado;
    actualizarEstadoConexion('Conectado', true);
  } catch (err) {
    console.error(err);
    actualizarEstadoConexion(`Sin conexión a la API: ${err.message}`, false);
  }
}

const CFG_KEY = 'cargill_cfg_v1';
function cfgPorDefecto() {
  return {
    capacidadPtTm: null,
    capacidadMpTm: null,
    lineas: [
      { bu: 'AQUA', tipo: 'Pellet',    planta: 'DURAN',     linea: '1', tamano: 0.2, horas: 22, rate: 10   },
      { bu: 'AQUA', tipo: 'Extruído',  planta: 'DURAN',     linea: '2/3', tamano: 0.6, horas: 20, rate: 3  },
      { bu: 'AQUA', tipo: 'Extruído',  planta: 'DURAN',     linea: '2/3', tamano: 0.8, horas: 20, rate: 6  },
      { bu: 'AQUA', tipo: 'Extruído',  planta: 'GUAYAQUIL', linea: '2/3', tamano: 1.2, horas: 20, rate: 10.5 },
      { bu: 'AQUA', tipo: 'Extruído',  planta: 'GUAYAQUIL', linea: '2/3', tamano: 1.6, horas: 20, rate: 11 },
    ],
    compatibilidad: [],
  };
}
let CFG = cfgPorDefecto();
function cargarCfg() {
  try {
    const raw = localStorage.getItem(CFG_KEY);
    if (raw) CFG = Object.assign(cfgPorDefecto(), JSON.parse(raw));
  } catch (err) {
    console.warn('No se pudo leer la configuración local', err);
    CFG = cfgPorDefecto();
  }
}
function guardarCfg() {
  localStorage.setItem(CFG_KEY, JSON.stringify(CFG));
}
async function cargarCapacidadBodegaBackend() {
  try {
    const r = await fetchJSON('/api/v1/config/capacidad-bodega');
    if (r.capacidad_pt_tm != null) CFG.capacidadPtTm = r.capacidad_pt_tm;
    if (r.capacidad_mp_tm != null) CFG.capacidadMpTm = r.capacidad_mp_tm;
    guardarCfg();
  } catch (err) {
    console.warn('No se pudo cargar la capacidad de bodega del backend, se usa la del navegador', err);
  }
}
async function guardarCapacidadBodegaEnBackend() {
  try {
    const resp = await fetch(API_BASE + '/api/v1/config/capacidad-bodega', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        capacidad_pt_tm: CFG.capacidadPtTm != null ? Number(CFG.capacidadPtTm) : null,
        capacidad_mp_tm: CFG.capacidadMpTm != null ? Number(CFG.capacidadMpTm) : null,
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (err) {
    console.error('No se pudo guardar la capacidad de bodega en el backend', err);
    return false;
  }
}

let CFG_BACKEND = { intentado: false, conectado: false, aviso: null };
function formaDeTipo(tipo) {
  return String(tipo || '').toLowerCase().startsWith('pellet') ? 'AP' : 'AX';
}
async function sincronizarCfgLineasConBackend() {
  CFG_BACKEND.intentado = true;
  try {
    const r = await fetchJSON('/api/v1/config/lineas-produccion');
    CFG_BACKEND.conectado = true;
    CFG_BACKEND.aviso = r.aviso || null;
    if (Array.isArray(r.lineas) && r.lineas.length) {
      CFG.lineas = r.lineas.map(l => ({
        bu: l.bu || '', tipo: l.tipo_producto || '', planta: l.planta || '',
        linea: l.linea || '', tamano: l.tamano_mm ?? null,
        horas: l.horas_dia ?? 20, rate: l.rate_tm_hora ?? null,
      }));
    }
  } catch (err) {
    CFG_BACKEND.conectado = false;
    CFG_BACKEND.aviso = null;
  }
  return CFG_BACKEND.conectado;
}
async function guardarCfgLineasEnBackend() {
  const body = {
    lineas: CFG.lineas.map(l => ({
      bu: l.bu || '', tipo_producto: l.tipo || '', forma: formaDeTipo(l.tipo),
      planta: l.planta || '', linea: String(l.linea || ''),
      tamano_mm: l.tamano || null, horas_dia: Number(l.horas) || 20,
      rate_tm_hora: l.rate || null, activo: true,
    })),
  };
  try {
    const resp = await fetch(API_BASE + '/api/v1/config/lineas-produccion', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (err) {
    console.error('No se pudo guardar la configuración de líneas en el backend', err);
    return false;
  }
}
function actualizarCfgNote() {
  const box = document.getElementById('cfgNoteBox');
  const icono = document.getElementById('cfgNoteIcono');
  const texto = document.getElementById('cfgNoteTexto');
  if (!box) return;
  if (CFG_BACKEND.conectado && !CFG_BACKEND.aviso) {
    box.classList.add('conectado');
    if (icono) icono.textContent = '✓';
    if (texto) texto.innerHTML = `<b>Conectado al backend.</b> Los cambios en "Líneas de producción" que guardes
      aquí sí alimentan la próxima corrida de <code>optimizar_produccion.py</code> (horas/día y forma por línea).
      La capacidad de bodega (PT/MP) sigue siendo solo para el estimado de ocupación de esta app.`;
  } else {
    box.classList.remove('conectado');
    if (icono) icono.textContent = '⚠';
    if (texto) {
      texto.innerHTML = CFG_BACKEND.intentado && CFG_BACKEND.aviso
        ? `<b>Backend conectado, pero falta un paso.</b> ${CFG_BACKEND.aviso} Hasta correrlo, estos valores
           NO afectan el cálculo real — se guardan solo en este navegador (localStorage).`
        : `<b>Estos valores NO afectan el cálculo real todavía.</b> Se guardan solo en este
           navegador (localStorage) y no están conectados al motor de optimización
           (<code>optimizar_produccion.py</code>) ni al cálculo DDMRP. Cambiar aquí la capacidad
           de una línea o bodega <b>no modifica el plan de producción ni los buffers reales</b> —
           es un respaldo de referencia, no una fuente de verdad operativa. Para que un cambio de
           capacidad o compatibilidad se refleje en el plan, debe actualizarse en el sistema de
           origen que alimenta al motor de optimización.`;
    }
  }
}
function horasLineaConfigurada(recurso) {
  if (!recurso || !recurso.includes('|')) return null;
  const [planta, linea] = recurso.split('|');
  const fila = CFG.lineas.find(l =>
    String(l.planta).toUpperCase() === String(planta).toUpperCase() &&
    String(l.linea).toUpperCase() === String(linea).toUpperCase());
  return fila ? Number(fila.horas) : null;
}

function initNav() {
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
      item.classList.add('active');
      document.getElementById(item.dataset.screen).classList.add('active');
      renderPantallaActiva();
    });
  });
}
function pantallaActivaId() {
  const activa = document.querySelector('.screen.active');
  return activa ? activa.id : 'hoy';
}
function renderPantallaActiva() {
  const id = pantallaActivaId();
  if (id === 'hoy') { renderHoy(); return; }
  if (id === 'produccion') { renderProduccion(); return; }
  if (id === 'compras') { renderComprasMp(); return; }
  if (id === 'ddmrp') { renderDDMRP(); return; }
  if (id === 'simulador') { renderSimuladorScreen(); return; }
  if (id === 'config') { renderConfigScreen(); return; }
}
function initSubTabs(root) {
  (root || document).querySelectorAll('[data-subtabs]').forEach(wrap => {
    if (wrap.dataset.wired) return;
    wrap.dataset.wired = '1';
    const contenedor = wrap.parentElement;
    wrap.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        wrap.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        contenedor.querySelectorAll(':scope > .nodo-panel[data-subpanel]').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        const panel = contenedor.querySelector(`:scope > .nodo-panel[data-subpanel="${tab.dataset.subtab}"]`);
        if (panel) panel.classList.add('active');
      });
    });
  });
}
function initColapsables(root) {
  (root || document).querySelectorAll('.colapsable-head[data-toggle]').forEach(head => {
    if (head.dataset.wired) return;
    head.dataset.wired = '1';
    head.addEventListener('click', () => {
      const el = document.getElementById(head.dataset.toggle);
      if (el) el.classList.toggle('cerrado');
    });
  });
}
function initCombobox(inputId, getOptions, onSeleccion) {
  const input = document.getElementById(inputId);
  const panel = document.getElementById(`${inputId}_panel`);
  if (!input || !panel || input.dataset.wired) return;
  input.dataset.wired = '1';
  function render(filtroTexto) {
    const opts = getOptions();
    const q = (filtroTexto || '').toLowerCase();
    const filtradas = q ? opts.filter(o => o.label.toLowerCase().includes(q)) : opts;
    if (filtradas.length === 0) {
      panel.innerHTML = '<div class="combo-empty">Sin resultados</div>';
    } else {
      panel.innerHTML = filtradas.slice(0, 200).map(o =>
        `<div class="combo-item" data-value="${o.label.replace(/"/g, '&quot;')}">${o.label}</div>`
      ).join('');
    }
    panel.classList.add('open');
  }
  input.addEventListener('focus', () => render(''));
  input.addEventListener('click', () => render(''));
  input.addEventListener('input', () => render(input.value));
  panel.addEventListener('mousedown', (e) => {
    const item = e.target.closest('.combo-item');
    if (!item) return;
    input.value = item.dataset.value;
    panel.classList.remove('open');
    onSeleccion();
  });
  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !panel.contains(e.target)) panel.classList.remove('open');
  });
}
/* Plan de compras MP (plan_compras_mp.py / /api/v1/compras-mp/plan) --
   ÚNICA fuente de señales de compra de MP. Centro de decisiones, Hoy,
   Formulación Semanal (PT) e Inventario Estratégico (MP) leen de aquí; nada
   de "reponer" ni "sobrestock" se calcula en el frontend. */
let COMPRAS_MP = { fecha_calculo: null, conteo: {}, n_compra_detenida: 0,
                    plan: [], avisos: [], mapa: new Map(), cargado: false };
async function cargarComprasMp(forzar) {
  if (COMPRAS_MP.cargado && !forzar) return;
  try {
    const r = await fetchJSON('/api/v1/compras-mp/plan');
    COMPRAS_MP.fecha_calculo = r.fecha_calculo;
    COMPRAS_MP.conteo = r.conteo || {};
    COMPRAS_MP.n_compra_detenida = r.n_compra_detenida || 0;
    COMPRAS_MP.plan = Array.isArray(r.plan) ? r.plan : [];
    COMPRAS_MP.avisos = Array.isArray(r.avisos) ? r.avisos : [];
    COMPRAS_MP.mapa = new Map(COMPRAS_MP.plan.map(p => [String(p.sku).replace('.0', '').trim(), p]));
  } catch (err) {
    console.error('No se pudo cargar el plan de compras MP', err);
    COMPRAS_MP.fecha_calculo = null; COMPRAS_MP.conteo = {}; COMPRAS_MP.n_compra_detenida = 0;
    COMPRAS_MP.plan = []; COMPRAS_MP.avisos = []; COMPRAS_MP.mapa = new Map();
  }
  COMPRAS_MP.cargado = true;
}
function compraMpDeSku(sku) {
  return COMPRAS_MP.mapa.get(String(sku).replace('.0', '').trim()) || null;
}
const SENAL_COMPRA_ETIQUETA = {
  COMPRAR_HOY: 'Comprar hoy', COMPRAR_ANTES_DE: 'Comprar a más tardar',
  CUBIERTO: 'Cubierto', NO_COMPRAR_HASTA: 'No comprar hasta',
  RIESGO_VENCIMIENTO: 'Riesgo de vencimiento',
};
const SENAL_COMPRA_COLOR = {
  COMPRAR_HOY: ['var(--red-100)', 'var(--red-700)'],
  COMPRAR_ANTES_DE: ['var(--amber-100)', 'var(--amber-700)'],
  CUBIERTO: ['var(--emerald-100)', 'var(--emerald-700)'],
  NO_COMPRAR_HASTA: ['rgba(46,96,152,.12)', 'var(--blue-700)'],
  RIESGO_VENCIMIENTO: ['rgba(124,58,172,.12)', '#7C3AAC'],
};
function colorSenalCompra(senal) { return (SENAL_COMPRA_COLOR[senal] || ['var(--gray-100)', 'var(--gray-600)'])[1]; }
function bgSenalCompra(senal) { return (SENAL_COMPRA_COLOR[senal] || ['var(--gray-100)', 'var(--gray-600)'])[0]; }
// fechaTexto: cuando se pasa, se añade a la etiqueta (para la columna
// "Acción" de las tablas). Sin ella, la etiqueta queda genérica (para las
// tarjetas, que ya muestran la fecha aparte).
function pillSenalCompra(senal, fechaTexto) {
  const [bg, fg] = SENAL_COMPRA_COLOR[senal] || ['var(--gray-100)', 'var(--gray-600)'];
  let etiqueta = SENAL_COMPRA_ETIQUETA[senal] || senal || 'Sin buffer';
  if (fechaTexto && (senal === 'COMPRAR_ANTES_DE' || senal === 'NO_COMPRAR_HASTA')) etiqueta += ` ${fechaTexto}`;
  return `<span style="font-size:11px;white-space:nowrap;background:${bg};color:${fg};padding:2px 9px;border-radius:5px;font-weight:bold;">${etiqueta}</span>`;
}

let DDMRP = { bufferPt: [], bufferMp: [], resumen: null, clasificacionSku: [],
              factoresMp: new Map(),
              skuSeleccionado: null, mpSeleccionada: null, nodoActivo: 'pt', cargado: false };

async function cargarDDMRP() {
  if (DDMRP.cargado) return;
  try {
    const [bufferPt, bufferMp, resumen, clasSku, factoresMp] = await Promise.all([
      fetchJSON('/api/v1/ddmrp/buffer-pt'),
      fetchJSON('/api/v1/ddmrp/buffer-mp').catch(() => []),
      fetchJSON('/api/v1/ddmrp/resumen'),
      fetchJSON('/api/v1/clasificacion/nivel-sku').catch(() => []),
      fetchJSON('/api/v1/ddmrp/factores-manuales-mp').catch(() => []),
    ]);
    DDMRP.bufferPt = Array.isArray(bufferPt) ? bufferPt : [];
    DDMRP.bufferMp = Array.isArray(bufferMp) ? bufferMp : [];
    DDMRP.resumen = resumen;
    DDMRP.clasificacionSku = Array.isArray(clasSku) ? clasSku : [];
    DDMRP.factoresMp = new Map(
      (Array.isArray(factoresMp) ? factoresMp : [])
        .map(f => [String(f.sku).replace('.0', '').trim(), f]));
    DDMRP.cargado = true;
  } catch (err) {
    console.error('No se pudo cargar DDMRP', err);
    DDMRP.bufferPt = [];
  }
}

let POS = { datos: [], resumen: null, cargado: false };
async function cargarPosicionamiento() {
  if (POS.cargado) return;
  try {
    const [datos, resumen] = await Promise.all([
      fetchJSON('/api/v1/ddmrp/posicionamiento'),
      fetchJSON('/api/v1/ddmrp/posicionamiento/resumen'),
    ]);
    POS.datos = Array.isArray(datos) ? datos : [];
    POS.resumen = resumen;
    POS.cargado = true;
  } catch (err) {
    console.error('No se pudo cargar el posicionamiento', err);
    POS.datos = [];
  }
}
function rotacionDeSku(sku) {
  const key = String(sku).replace('.0', '').trim();
  const p = POS.datos.find(x => String(x.sku).replace('.0', '').trim() === key);
  return p ? p.nivel_rotacion : null;
}
function politicaDeSku(sku) {
  const key = String(sku).replace('.0', '').trim();
  const p = POS.datos.find(x => String(x.sku).replace('.0', '').trim() === key);
  return p ? p.politica : null;
}

let NFP = { datos: [], resumen: null, mapa: new Map(), cargado: false, error: null };
async function cargarNFP(forzar) {
  if (NFP.cargado && !forzar) return;
  NFP.error = null;
  try {
    const [flujo, resumen] = await Promise.all([
      fetchJSON('/api/v1/ddmrp/flujo-neto?nodo=PT&solo_accionables=true'),
      fetchJSON('/api/v1/ddmrp/flujo-neto/resumen'),
    ]);
    const datosBrutos = (flujo && flujo.datos) ? flujo.datos : [];
    NFP.resumen = resumen;
    NFP.mapa = new Map(datosBrutos.map(r => [String(r.sku).replace('.0', '').trim(), r]));
    NFP.datos = [...NFP.mapa.values()];
    NFP.cargado = true;
  } catch (err) {
    console.error('No se pudo cargar el flujo neto', err);
    NFP.error = String(err && err.message ? err.message : err);
    NFP.datos = [];
    NFP.resumen = null;
    NFP.mapa = new Map();
    NFP.cargado = true;
  }
}
function nfpDeSku(sku) { return NFP.mapa.get(String(sku).replace('.0', '').trim()) || null; }

let NFP_MP = { datos: [], mapa: new Map(), cargado: false };
async function cargarFlujoNetoMp(forzar) {
  if (NFP_MP.cargado && !forzar) return;
  try {
    const flujo = await fetchJSON('/api/v1/ddmrp/flujo-neto?nodo=MP&solo_accionables=true');
    NFP_MP.datos = (flujo && flujo.datos) ? flujo.datos : [];
    NFP_MP.mapa = new Map(NFP_MP.datos.map(r => [String(r.sku).replace('.0', '').trim(), r]));
  } catch (err) {
    console.error('No se pudo cargar el flujo neto de MP', err);
    NFP_MP.datos = [];
    NFP_MP.mapa = new Map();
  }
  NFP_MP.cargado = true;
}
function nfpMpDeSku(sku) { return NFP_MP.mapa.get(String(sku).replace('.0', '').trim()) || null; }

let NFP_MP_TODAS = { total: null, nMateriales: null, mapa: new Map(), cargado: false };
async function cargarFlujoNetoMpTodas(forzar) {
  if (NFP_MP_TODAS.cargado && !forzar) return;
  try {
    const r = await fetchJSON('/api/v1/kpis/inventario_total_mp');
    NFP_MP_TODAS.total = r.total_tm;
    NFP_MP_TODAS.nMateriales = r.n_materiales;
    NFP_MP_TODAS.mapa = new Map((r.detalle || []).map(d => [String(d.material).replace('.0', '').trim(), d]));
  } catch (err) {
    console.error('No se pudo cargar el inventario total real de MP', err);
    NFP_MP_TODAS.total = null;
    NFP_MP_TODAS.nMateriales = null;
    NFP_MP_TODAS.mapa = new Map();
  }
  NFP_MP_TODAS.cargado = true;
}
const CAMPOS_MP_AVISADOS = new Set();
function avisarCampoFaltante(campo) {
  if (CAMPOS_MP_AVISADOS.has(campo)) return;
  CAMPOS_MP_AVISADOS.add(campo);
  console.warn(`Campo "${campo}" no llegó del backend -- no se muestra.`);
}
/* Fuente ÚNICA de "inventario actual" de MP, usada en Hoy y en Inventario
   Estratégico para que los dos números siempre cuadren. total_tm (de
   NFP_MP_TODAS, /api/v1/kpis/inventario_total_mp) = disponible + bloqueado +
   inspección + tránsito SAP -- NO incluye OC en barco. skus=null => todas
   las MP (también las sin buffer); un array restringe a esas MP. Los días
   de cobertura usan el ADU de las MISMAS MP (buffer-mp con adu > 0, tengan
   o no buffer): nunca mezclar el stock de todas con el ADU de solo algunas. */
function inventarioMp(skus) {
  const set = skus ? new Set(skus.map(s => String(s).replace('.0', '').trim())) : null;
  let totalTm = 0, nMateriales = 0;
  for (const [key, d] of NFP_MP_TODAS.mapa.entries()) {
    if (set && !set.has(key)) continue;
    totalTm += d.total_tm || 0;
    nMateriales++;
  }
  const aduTotal = (DDMRP.bufferMp || [])
    .filter(m => (m.adu || 0) > 0 && (!set || set.has(String(m.sku).replace('.0', '').trim())))
    .reduce((a, m) => a + (m.adu || 0), 0);
  return { totalTm, nMateriales, aduTotal, dias: aduTotal > 0 ? totalTm / aduTotal : null };
}

let PROD = { plan: [], resumen: null, cargado: false, filtroRecurso: 'TODOS' };
async function cargarProduccion() {
  if (PROD.cargado) return;
  try {
    const [plan, resumen] = await Promise.all([
      fetchJSON('/api/v1/produccion/plan'),
      fetchJSON('/api/v1/produccion/resumen'),
    ]);
    PROD.plan = Array.isArray(plan) ? plan : [];
    PROD.resumen = resumen;
    if (resumen && resumen.lead_time_produccion_dias != null) {
      LT_PRODUCCION_HOY = Number(resumen.lead_time_produccion_dias) || LT_PRODUCCION_HOY;
    }
    PROD.cargado = true;
  } catch (err) {
    console.error('No se pudo cargar el plan', err);
    PROD.plan = [];
    PROD.resumen = null;
  }
}

/* Flete (calcular_flete_pedidos.py + optimizar_producción.py): en qué
   planta producir cada pedido. No se calcula nada aquí -- todo sale de
   /api/v1/flete/*. plan_produccion ya trae flete_usd_tm/motivo_planta/
   sobrecosto_flete_usd por fila (se leen directo de PROD.plan). */
let FLETE = { resumen: null, cargado: false };
async function cargarFleteResumen() {
  if (FLETE.cargado) return;
  try {
    FLETE.resumen = await fetchJSON('/api/v1/flete/resumen');
  } catch (err) {
    FLETE.resumen = null;
  }
  FLETE.cargado = true;
}
const FLETE_PEDIDOS_CACHE = new Map();
async function fletePedidosDeSku(sku) {
  const key = String(sku).replace('.0', '').trim();
  if (FLETE_PEDIDOS_CACHE.has(key)) return FLETE_PEDIDOS_CACHE.get(key);
  try {
    const r = await fetchJSON('/api/v1/flete/pedidos?sku=' + encodeURIComponent(key));
    const filas = Array.isArray(r) ? r : [];
    FLETE_PEDIDOS_CACHE.set(key, filas);
    return filas;
  } catch (err) {
    return [];
  }
}
let FLETE_AVISOS = { datos: [], cargado: false };
async function cargarFleteAvisos() {
  if (FLETE_AVISOS.cargado) return;
  try {
    const r = await fetchJSON('/api/v1/flete/avisos');
    FLETE_AVISOS.datos = Array.isArray(r) ? r : [];
  } catch (err) {
    FLETE_AVISOS.datos = [];
  }
  FLETE_AVISOS.cargado = true;
}
function claseCeldaMotivo(motivo) {
  if (motivo === 'PEDIDO') return 'ped';
  if (motivo === 'YA PRODUCIDO') return 'pasado';
  return 'buf';
}
function pillMotivo(motivo) {
  if (motivo === 'PEDIDO') return 'pill-pedido';
  if (motivo === 'YA PRODUCIDO') return 'pill-pasado';
  return 'pill-buffer';
}
function corridaDelDiaPorSku(sku, fecha) {
  const key = String(sku).replace('.0', '').trim();
  const f = fecha || hoyStr();
  return (PROD.plan || []).filter(p =>
    String(p.sku).replace('.0', '').trim() === key &&
    p.fecha === f && p.motivo !== 'YA PRODUCIDO');
}
function primeraCorridaFuturaPorSku(sku) {
  const key = String(sku).replace('.0', '').trim();
  const filas = (PROD.plan || []).filter(p =>
    String(p.sku).replace('.0', '').trim() === key && p.motivo !== 'YA PRODUCIDO' && (p.tm || 0) > 0);
  if (!filas.length) return null;
  const fecha = filas.map(p => p.fecha).sort()[0];
  return { fecha, corridas: filas.filter(p => p.fecha === fecha) };
}

const PEDIDOS_CACHE = new Map();
async function pedidosDeSku(sku) {
  if (PEDIDOS_CACHE.has(sku)) return PEDIDOS_CACHE.get(sku);
  try {
    const p = await fetchJSON(`/api/v1/ddmrp/pedidos/${encodeURIComponent(sku)}`);
    const arr = Array.isArray(p) ? p : [];
    PEDIDOS_CACHE.set(sku, arr);
    return arr;
  } catch (err) {
    PEDIDOS_CACHE.set(sku, []);
    return [];
  }
}

async function evaluarCoberturaReal(sku, disponibleFisicoTM) {
  const pedidos = await pedidosDeSku(sku);
  const pendientes = (pedidos || [])
    .filter(p => { const f = parseFechaLocal(p.fecha_entrega); return f && (p.cantidad_tm || 0) > 0; })
    .sort((a, b) => String(a.fecha_entrega).localeCompare(String(b.fecha_entrega)));
  let acum = 0, primerDiaDescubierto = null, tmDescubierto = 0, tmTotal = 0;
  pendientes.forEach(p => {
    const antes = acum;
    acum += (p.cantidad_tm || 0);
    tmTotal += (p.cantidad_tm || 0);
    const cubierto = Math.max(0, Math.min(p.cantidad_tm || 0, disponibleFisicoTM - antes));
    if (cubierto < (p.cantidad_tm || 0)) {
      tmDescubierto += (p.cantidad_tm || 0) - cubierto;
      if (primerDiaDescubierto === null) primerDiaDescubierto = diasDesdeHoy(p.fecha_entrega);
    }
  });
  return { cubiertoTotal: tmDescubierto <= 0, primerDiaDescubierto, tmDescubierto, tmTotal };
}

async function evaluarCoberturaDetallePorPedido(sku, disponibleFisicoTM) {
  const pedidos = await pedidosDeSku(sku);
  const pendientes = (pedidos || [])
    .filter(p => { const f = parseFechaLocal(p.fecha_entrega); return f && (p.cantidad_tm || 0) > 0; })
    .sort((a, b) => String(a.fecha_entrega).localeCompare(String(b.fecha_entrega)));
  let acum = 0;
  return pendientes.map(p => {
    const antes = acum;
    acum += (p.cantidad_tm || 0);
    const cubierto = Math.max(0, Math.min(p.cantidad_tm || 0, disponibleFisicoTM - antes));
    return { ...p, cubierto, faltante: (p.cantidad_tm || 0) - cubierto };
  });
}

async function evaluarCoberturaConPlan(sku, disponibleFisicoTM) {
  const key = String(sku).replace('.0', '').trim();
  const pedidos = await pedidosDeSku(sku);
  const pendientes = (pedidos || [])
    .filter(p => { const f = parseFechaLocal(p.fecha_entrega); return f && (p.cantidad_tm || 0) > 0; })
    .sort((a, b) => String(a.fecha_entrega).localeCompare(String(b.fecha_entrega)));

  const corridas = (PROD.plan || [])
    .filter(p => String(p.sku).replace('.0', '').trim() === key && p.motivo !== 'YA PRODUCIDO' && (p.tm || 0) > 0)
    .map(p => {
      const fCorrida = parseFechaLocal(p.fecha);
      const fDisponible = fCorrida ? new Date(fCorrida.getTime() + LT_PRODUCCION_HOY * 86400000) : null;
      return { tm: p.tm || 0, fecha_disponible: fDisponible };
    })
    .filter(c => c.fecha_disponible);

  const supplyTotalPlan = corridas.reduce((a, c) => a + c.tm, 0);
  let cumDemanda = 0;
  let prevStock = 0, prevATiempo = 0, prevEventual = 0;
  return pendientes.map(p => {
    const q = p.cantidad_tm || 0;
    cumDemanda += q;
    const fEntrega = parseFechaLocal(p.fecha_entrega);
    const supplyATiempo = disponibleFisicoTM + corridas
      .filter(c => c.fecha_disponible <= fEntrega)
      .reduce((a, c) => a + c.tm, 0);
    const supplyEventual = disponibleFisicoTM + supplyTotalPlan;
    const cumStock = Math.min(cumDemanda, disponibleFisicoTM);
    const cumATiempo = Math.min(cumDemanda, supplyATiempo);
    const cumEventual = Math.min(cumDemanda, supplyEventual);

    const cubiertoStock = Math.max(0, cumStock - prevStock);
    const cubiertoATiempoTotal = Math.max(0, cumATiempo - prevATiempo);
    const cubiertoEventualTotal = Math.max(0, cumEventual - prevEventual);

    const cubiertoPlan = Math.max(0, cubiertoATiempoTotal - cubiertoStock);
    const cubiertoTarde = Math.max(0, cubiertoEventualTotal - cubiertoATiempoTotal);
    const sinCobertura = Math.max(0, q - cubiertoEventualTotal);

    prevStock = cumStock; prevATiempo = cumATiempo; prevEventual = cumEventual;
    return { ...p, cubiertoStock, cubiertoPlan, cubiertoTarde, sinCobertura };
  });
}

/* Punto 1 del ajuste "una sola fuente de riesgo": usa evaluarCoberturaConPlan
   (misma función que usa el Plan de Producción) en vez de
   evaluarCoberturaDetallePorPedido (que ignoraba el plan y solo miraba stock).
   Así el Simulador y el Plan de Producción nunca se contradicen entre sí. */
async function calcularRiesgoRealSkus() {
  await cargarProduccion();
  const bufferHoy = (DDMRP.bufferPt || []).filter(b => b.mes_offset === 0 && b.estado === 'ok');
  const margenes = await cargarMargenSku();
  const detalle = [];
  const clientesMap = new Map();

  await Promise.all(bufferHoy.map(async b => {
    const n = nfpDeSku(b.sku);
    const disponible = n ? ((n.disponible_tm || 0) + (n.transito_tm || 0)) : 0;
    const filas = await evaluarCoberturaConPlan(b.sku, disponible);
    const margenUnit = margenes.get(String(b.sku).trim()) ?? 0;
    filas.forEach(f => {
      const faltante = f.sinCobertura + f.cubiertoTarde;
      if (faltante <= 0.01) return;
      const tipo = f.sinCobertura > 0 ? 'SIN_COBERTURA' : 'SE_PRODUCE_TARDE';
      const cliente = f.cliente_nombre || f.cliente || '—';
      detalle.push({
        sku: b.sku, descripcion: descripcionSku(b.sku) || n?.descripcion || '',
        fecha: f.fecha_entrega, dias: diasDesdeHoy(f.fecha_entrega),
        tm: faltante, cliente, valor: faltante * margenUnit, tipo,
      });
      const prev = clientesMap.get(cliente) || { tm: 0, margen: 0, fechaMin: null };
      prev.tm += faltante;
      prev.margen += faltante * margenUnit;
      if (f.fecha_entrega && (!prev.fechaMin || String(f.fecha_entrega).slice(0, 10) < prev.fechaMin)) {
        prev.fechaMin = String(f.fecha_entrega).slice(0, 10);
      }
      clientesMap.set(cliente, prev);
    });
  }));

  detalle.sort((a, b) => (a.dias ?? 999) - (b.dias ?? 999));
  const skusAfectados = new Set(detalle.map(d => d.sku));
  const deficitTotal = detalle.reduce((a, d) => a + d.tm, 0);
  return { detalle, skusAfectados, clientesMap, deficitTotal };
}

function fechaLimiteOrden(primerDiaDescubierto, leadTimeDias) {
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  if (primerDiaDescubierto == null) return null;
  const diasParaLanzar = primerDiaDescubierto - leadTimeDias;
  return sumarDias(hoy, Math.max(diasParaLanzar, 0));
}

let COB = { margenSku: null };
async function cargarMargenSku() {
  if (COB.margenSku) return COB.margenSku;
  try {
    const r = await fetchJSON('/api/v1/margen/sku');
    COB.margenSku = new Map(
      (Array.isArray(r) ? r : []).map(x => [String(x.sku).trim(), x.margen_usd_tm]));
  } catch (err) {
    COB.margenSku = new Map();
  }
  return COB.margenSku;
}

let ALERTAS_MP = { filas: [], cargado: false };
// Señal del plan de compras (plan_compras_mp.py) -> "estado" que ya
// consumen las tarjetas/KPI de Hoy. Antes esto se recalculaba aquí mismo
// (TOG - stock, día de quiebre contra /mp/proyeccion-vs-plan): esa cuenta
// podía contradecir lo que decía Materia Prima (NFP/flujo neto/OC en
// camino). Ahora es un simple mapeo 1 a 1 de la señal del backend.
const SENAL_A_ESTADO_HOY = {
  COMPRAR_HOY: 'emergencia', COMPRAR_ANTES_DE: 'porvencer',
  NO_COMPRAR_HASTA: 'sobrestock', RIESGO_VENCIMIENTO: 'sobrestock', CUBIERTO: 'ok',
};
async function cargarAlertasMP() {
  if (ALERTAS_MP.cargado) return ALERTAS_MP.filas;
  await cargarComprasMp();
  const filas = COMPRAS_MP.plan.map(p => ({
    sku: p.sku, descripcion: p.descripcion || descripcionSku(p.sku),
    senal: p.senal, mensaje: p.mensaje,
    tor: p.tor, toy: p.toy, tog: p.tog, consumoDiario: p.adu,
    stockHoy: p.en_planta_tm, enCamino: p.en_camino_tm,
    cantidadAPedir: p.cantidad_sugerida_tm,
    fechaLimite: p.fecha_referencia ? parseFechaLocal(p.fecha_referencia) : null,
    fechaLlegadaSugerida: p.fecha_llegada_sugerida, coberturaDias: p.cobertura_dias,
    leadTime: p.lead_time_dias, estado: SENAL_A_ESTADO_HOY[p.senal] || 'ok',
  }));
  filas.sort((a, b) => {
    const orden = { emergencia: 0, porvencer: 1, sobrestock: 2, ok: 3 };
    return (orden[a.estado] ?? 9) - (orden[b.estado] ?? 9);
  });
  ALERTAS_MP.filas = filas;
  ALERTAS_MP.cargado = true;
  return ALERTAS_MP.filas;
}

let MP_PT_INDEX = null;
let MP_PT_INDEX_PROMISE = null;
async function construirIndiceMpPt() {
  if (MP_PT_INDEX) return MP_PT_INDEX;
  if (MP_PT_INDEX_PROMISE) return MP_PT_INDEX_PROMISE;
  MP_PT_INDEX_PROMISE = (async () => {
    await cargarDDMRP();
    const skusPt = [...new Set((DDMRP.bufferPt || []).map(b => String(b.sku).replace('.0', '').trim()))];
    const indice = new Map();
    const LOTE = 8;
    for (let i = 0; i < skusPt.length; i += LOTE) {
      const chunk = skusPt.slice(i, i + LOTE);
      await Promise.all(chunk.map(async sku => {
        try {
          const r = await fetchJSON('/api/v1/mp/por-pt/' + encodeURIComponent(sku));
          (r.mp || []).forEach(m => {
            if (!indice.has(m.sku_mp)) indice.set(m.sku_mp, []);
            indice.get(m.sku_mp).push({ sku_pt: sku, pct: m.pct, estado: m.estado });
          });
        } catch (err) { /* PT sin fórmula esta semana: se ignora */ }
      }));
    }
    indice.forEach((consumidores, skuMp) => {
      const porPt = new Map();
      consumidores.forEach(c => {
        const prev = porPt.get(c.sku_pt);
        if (!prev || (c.pct || 0) > (prev.pct || 0)) porPt.set(c.sku_pt, c);
      });
      indice.set(skuMp, [...porPt.values()]);
    });
    MP_PT_INDEX = indice;
    return indice;
  })();
  return MP_PT_INDEX_PROMISE;
}
function tmSemanaPlanPorSku(sku) {
  const key = String(sku).replace('.0', '').trim();
  return (PROD.plan || [])
    .filter(p => String(p.sku).replace('.0', '').trim() === key && p.motivo !== 'YA PRODUCIDO')
    .reduce((a, p) => a + (p.tm || 0), 0);
}
async function ptQueConsumen(skuMp) {
  const [idx] = await Promise.all([construirIndiceMpPt(), cargarProduccion()]);
  return (idx.get(skuMp) || [])
    .map(c => {
      const tmSemana = tmSemanaPlanPorSku(c.sku_pt);
      return { ...c, tmSemana, consumoTm: tmSemana * (c.pct || 0) / 100 };
    })
    .filter(c => c.tmSemana > 0)
    .sort((a, b) => (b.consumoTm || 0) - (a.consumoTm || 0));
}
let MP_DEL_PLAN_SET = new Set();
async function mpConsumidasPorElPlan() {
  await cargarProduccion();
  const skusPlan = new Set((PROD.plan || []).map(p => String(p.sku).replace('.0', '').trim()));
  const idx = await construirIndiceMpPt();
  const mpSet = new Set();
  idx.forEach((consumidores, skuMp) => {
    if (consumidores.some(c => skusPlan.has(String(c.sku_pt).replace('.0', '').trim()))) mpSet.add(skuMp);
  });
  MP_DEL_PLAN_SET = mpSet;
  return mpSet;
}
let HOY = { datos: null, cargado: false, emergenciasRealPT: null };
async function cargarHoy() {
  if (HOY.cargado) return;
  try {
    HOY.datos = await fetchJSON('/api/v1/hoy');
    HOY.cargado = true;
  } catch (err) {
    console.error('No se pudo cargar el panel Hoy', err);
    HOY.datos = null;
  }
}
async function cargarVentasMesActual() {
  try {
    const r = await fetchJSON('/api/v1/kpis/ventas_mes_actual');
    DATA.ventasMesActual = { total_tm: r.total_tm, dias_transcurridos: r.dias_transcurridos };
    DATA.ventasMesActualPorSku = new Map(
      (r.detalle || []).map(d => [String(d.sku).replace('.0', '').trim(), d.venta_real_mtd]));
  } catch (err) {
    DATA.ventasMesActual = null;
    DATA.ventasMesActualPorSku = new Map();
  }
}
async function cargarConsumoMesActualMp() {
  try {
    const r = await fetchJSON('/api/v1/kpis/consumo_mes_actual_mp');
    DATA.consumoMesActualMp = { total_tm: r.total_tm, dias_transcurridos: r.dias_transcurridos };
    DATA.consumoMesActualPorSkuMp = new Map(
      (r.detalle || []).map(d => [String(d.sku).replace('.0', '').trim(), d.consumo_mtd]));
  } catch (err) {
    DATA.consumoMesActualMp = null;
    DATA.consumoMesActualPorSkuMp = new Map();
  }
}
function aduSkuHoy(sku) {
  const key = String(sku).replace('.0', '').trim();
  const b = (DDMRP.bufferPt || []).find(x => x.mes_offset === 0 && x.estado === 'ok' &&
    String(x.sku).replace('.0', '').trim() === key);
  return b ? b.adu : null;
}
function irASkuEnBuffer(sku) {
  document.querySelectorAll('.nav-item').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.querySelector('[data-screen="ddmrp"]').classList.add('active');
  document.getElementById('ddmrp').classList.add('active');
  document.querySelectorAll('#ddmrp > .tabs > .tab').forEach(t => t.classList.toggle('active', t.dataset.nodo === 'pt'));
  document.querySelectorAll('#ddmrp .nodo-panel').forEach(p => p.classList.toggle('active', p.id === 'nodoPT'));
  DDMRP.nodoActivo = 'pt';
  DDMRP.skuSeleccionado = String(sku).replace('.0', '').trim();
  const inp = document.getElementById('skuBuscar_ddmrp');
  if (inp) { const d = descripcionSku(sku); inp.value = d ? `${sku} — ${d}` : String(sku); }
  sincronizarRotacionConSku();
  renderDDMRP();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function irAMpEnBuffer(skuMp) {
  document.querySelectorAll('.nav-item').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.querySelector('[data-screen="ddmrp"]').classList.add('active');
  document.getElementById('ddmrp').classList.add('active');
  document.querySelectorAll('#ddmrp > .tabs > .tab').forEach(t => t.classList.toggle('active', t.dataset.nodo === 'mp'));
  document.querySelectorAll('#ddmrp .nodo-panel').forEach(p => p.classList.toggle('active', p.id === 'nodoMP'));
  DDMRP.nodoActivo = 'mp';
  DDMRP.mpSeleccionada = skuMp;
  const inp = document.getElementById('mpBuscar_ddmrp');
  if (inp) inp.value = String(skuMp);
  renderDDMRP();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function nodoHoyActivo() {
  const tab = document.querySelector('#hoyNodoTabs .tab.active');
  return tab ? tab.dataset.hoynodo : 'pt';
}
function renderHoyHeroKpisPT(d) {
  const kpis = document.getElementById('hoyKpis');
  if (!kpis) return;
  const k = (d && d.kpis) || {};
  const totalEmergencias = HOY.emergenciasRealPT;
  const texto = totalEmergencias == null ? '…' : totalEmergencias;
  kpis.innerHTML = `
    <div class="k ${totalEmergencias ? 'alerta' : ''}" id="kpiPtRiesgoHoyCard"><div class="n" id="kpiPtRiesgoHoy">${texto}</div><div class="l">PT con riesgo de quiebre hoy</div></div>
    <div class="k"><div class="n">${k.requieren_reposicion ?? '—'}</div><div class="l">PT requieren reposición</div></div>
    <div class="k"><div class="n">${k.tm_a_reponer != null ? k.tm_a_reponer.toLocaleString() : '—'}</div><div class="l">TM de PT a reponer</div></div>
    <div class="k ${k.margen_en_riesgo_usd > 0 ? 'alerta' : ''}"><div class="n">${k.margen_en_riesgo_usd ? fmtUSD(k.margen_en_riesgo_usd) : '—'}</div><div class="l">margen en riesgo (PT)</div></div>`;
}
function renderHoyHeroKpisMP() {
  const kpis = document.getElementById('hoyKpis');
  if (!kpis) return;
  const filas = ALERTAS_MP.filas || [];
  const tmAPedir = filas.reduce((a, f) => a + (f.cantidadAPedir || 0), 0);
  // Conteos directo de COMPRAS_MP.conteo (plan_compras_mp.py) -- nada
  // recalculado filtrando ALERTAS_MP en el frontend.
  const conteo = COMPRAS_MP.conteo || {};
  const comprarHoy = conteo.COMPRAR_HOY || 0;
  const comprarAntesDe = conteo.COMPRAR_ANTES_DE || 0;
  kpis.innerHTML = `
    <div class="k ${comprarHoy ? 'alerta' : ''}"><div class="n">${comprarHoy}</div><div class="l">MP con orden de compra vencida</div></div>
    <div class="k"><div class="n">${comprarAntesDe}</div><div class="l">MP con fecha límite próxima</div></div>
    <div class="k"><div class="n">${tmAPedir > 0 ? fmtTM(tmAPedir) : '0 TM'}</div><div class="l">TM de MP a pedir</div></div>
    <div class="k"><div class="n">${COMPRAS_MP.n_compra_detenida || 0}</div><div class="l">MP con compra detenida</div></div>`;
}
async function renderHoyKpisGlobalesPT() {
  const cont = document.getElementById('hoyKpisGlobales');
  if (!cont) return;
  const bufferPt = (DDMRP.bufferPt || []).filter(b => b.mes_offset === 0 && b.estado === 'ok');
  const nfpItems = (NFP.datos || []);
  const invTotalReal = nfpItems.reduce((a, r) => a + (r.disponible_tm || 0) + (r.transito_tm || 0), 0);
  const nMaterialesTotal = nfpItems.length;
  const aduTotal = bufferPt.reduce((a, b) => a + (b.adu || 0), 0);
  const coberturaProm = aduTotal > 0 ? invTotalReal / aduTotal : null;
  const ventasMes = DATA.ventasMesActual ? DATA.ventasMesActual.total_tm : null;
  const diasMesTexto = DATA.ventasMesActual && DATA.ventasMesActual.dias_transcurridos != null
    ? `mes en curso · día ${DATA.ventasMesActual.dias_transcurridos}` : 'mes en curso';
  const plan = (PROD.plan || []);
  const porPlanta = new Map();
  plan.forEach(p => {
    const pl = p.planta || (p.recurso || '').split('|')[0] || '—';
    porPlanta.set(pl, (porPlanta.get(pl) || 0) + (p.tm || 0));
  });
  const capacidadTexto = [...porPlanta.entries()].map(([pl, tm]) => `${pl}: ${fmtTM(tm)}`).join(' · ') || '—';
  cont.innerHTML = `
    <div class="card"><p class="kpi-label">Inventario Actual PT</p>
      <p class="kpi-value neutral">${fmtEntero(invTotalReal)} TM</p>
      <p class="kpi-sub">${nMaterialesTotal} materiales (disp.+tránsito)</p></div>
    <div class="card"><p class="kpi-label">Ventas del Mes</p>
      <p class="kpi-value neutral">${ventasMes != null ? fmtEntero(ventasMes) + ' TM' : '—'}</p>
      <p class="kpi-sub">${diasMesTexto}</p></div>
    <div class="card"><p class="kpi-label">Cobertura Promedio</p>
      <p class="kpi-value neutral">${coberturaProm != null ? fmtNum(coberturaProm) + ' días' : '—'}</p>
      <p class="kpi-sub">Inventario actual / ADU global</p></div>
    <div class="card"><p class="kpi-label">Producción por Planta</p>
      <p class="kpi-value neutral" style="font-size:16px;">${capacidadTexto}</p>
      <p class="kpi-sub">TM programadas, plan vigente</p></div>`;
}
function renderHoyKpisGlobalesMP() {
  const cont = document.getElementById('hoyKpisGlobales');
  if (!cont) return;
  const filas = ALERTAS_MP.filas || [];
  // Misma fuente que Inventario Estratégico (inventarioMp -> NFP_MP_TODAS):
  // antes esta tarjeta dividía el stock de TODAS las MP para el ADU de solo
  // las que tienen buffer, lo que inflaba la cobertura (170+ días).
  const inv = inventarioMp(null);
  const urgentes = filas.filter(f => f.estado === 'emergencia').length;
  cont.innerHTML = `
    <div class="card"><p class="kpi-label">Inventario Actual MP</p>
      <p class="kpi-value neutral">${fmtTM(inv.totalTm)}</p>
      <p class="kpi-sub">${inv.nMateriales} MP totales (incluye sin buffer)</p></div>
    <div class="card"><p class="kpi-label">Consumos del Mes</p>
      <p class="kpi-value neutral">${DATA.consumoMesActualMp && DATA.consumoMesActualMp.total_tm != null ? fmtTM(DATA.consumoMesActualMp.total_tm) : '—'}</p>
      <p class="kpi-sub">${DATA.consumoMesActualMp && DATA.consumoMesActualMp.dias_transcurridos != null ? `mes en curso · día ${DATA.consumoMesActualMp.dias_transcurridos}` : 'mes en curso'}</p></div>
    <div class="card"><p class="kpi-label">Cobertura Promedio</p>
      <p class="kpi-value neutral">${inv.dias != null ? fmtNum(inv.dias) + ' días' : '—'}</p>
      <p class="kpi-sub">Inventario actual / ADU global</p></div>
    <div class="card"><p class="kpi-label">Órdenes urgentes</p>
      <p class="kpi-value ${urgentes ? 'danger' : 'neutral'}">${urgentes}</p>
      <p class="kpi-sub">deben lanzarse hoy</p></div>`;
}
function resumenCambios(lista) {
  const relevantes = (lista || []).filter(c => c.tipo !== 'sin_cambios' && c.tipo !== 'primera_corrida');
  const total = relevantes.length;
  if (!total) return null;
  let nuevoRiesgo = 0, fechaMovida = 0, otros = 0;
  relevantes.forEach(c => {
    const m = (c.mensaje || '').toLowerCase();
    if (m.includes('riesgo') || m.includes('quiebre')) nuevoRiesgo++;
    else if (m.includes('fecha') || m.includes('reprogram') || m.includes('movi')) fechaMovida++;
    else otros++;
  });
  const partes = [];
  if (nuevoRiesgo) partes.push(`${nuevoRiesgo} SKU nuevo${nuevoRiesgo > 1 ? 's' : ''} en riesgo`);
  if (fechaMovida) partes.push(`${fechaMovida} fecha${fechaMovida > 1 ? 's' : ''} de reposición se movi${fechaMovida > 1 ? 'eron' : 'ó'}`);
  if (otros && (nuevoRiesgo || fechaMovida)) partes.push(`${otros} cambio${otros > 1 ? 's' : ''} más`);
  const texto = partes.length
    ? `${total} cosa${total > 1 ? 's' : ''} cambiaron: ${partes.join(', ')}`
    : `${total} cosa${total > 1 ? 's' : ''} cambiaron desde tu última visita — ver detalle`;
  return { lista: relevantes, texto };
}
async function renderQueCambio() {
  const card = document.getElementById('hoyQueCambio');
  const resumenEl = document.getElementById('hoyQueCambioResumen');
  const detalleEl = document.getElementById('hoyQueCambioDetalle');
  if (!card) return;
  try {
    const cambios = await fetchJSON('/api/v1/produccion/cambios');
    const lista = Array.isArray(cambios) ? cambios : (cambios.cambios || []);
    const r = resumenCambios(lista);
    if (!r) { card.style.display = 'none'; return; }
    card.style.display = 'block';
    if (resumenEl) resumenEl.textContent = r.texto;
    if (detalleEl) {
      detalleEl.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:6px;">
          ${r.lista.slice(0, 8).map(c => `
            <div style="display:flex;gap:10px;align-items:flex-start;background:var(--gray-100);
                        border:1px solid var(--gray-200);border-radius:6px;padding:8px 12px;">
              <span style="color:var(--emerald-700);font-weight:bold;">●</span>
              <span style="font-size:13px;">${c.mensaje}</span>
            </div>`).join('')}
        </div>`;
    }
  } catch (err) { card.style.display = 'none'; }
}
function renderHoyKpisSegunNodo() {
  if (nodoHoyActivo() === 'mp') { renderHoyHeroKpisMP(); renderHoyKpisGlobalesMP(); }
  else { renderHoyHeroKpisPT(HOY.datos); renderHoyKpisGlobalesPT(); }
}
function initHoyNodoTabs() {
  const wrap = document.getElementById('hoyNodoTabs');
  if (!wrap || wrap.dataset.wired) return;
  wrap.dataset.wired = '1';
  wrap.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      wrap.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('hoyBloquePT').classList.toggle('active', tab.dataset.hoynodo === 'pt');
      document.getElementById('hoyBloqueMP').classList.toggle('active', tab.dataset.hoynodo === 'mp');
      renderHoyKpisSegunNodo();
    });
  });
}
async function renderHoy() {
  await Promise.all([
    cargarHoy(), cargarDDMRP(), cargarPosicionamiento(), cargarNFP(),
    cargarProduccion(), cargarVentasMesActual(), cargarAlertasMP(), cargarFlujoNetoMpTodas(), cargarConsumoMesActualMp(),
  ]);
  initHoyNodoTabs();
  initSubTabs(document.getElementById('hoyBloquePT'));
  initSubTabs(document.getElementById('hoyBloqueMP'));
  const d = HOY.datos;
  const fechaEl = document.getElementById('hoyFecha');
  const hoy = new Date();
  const dias = ['domingo','lunes','martes','miércoles','jueves','viernes','sábado'];
  if (fechaEl) {
    fechaEl.textContent = d
      ? `${dias[hoy.getDay()]} ${hoy.getDate()} de ${NOMBRES_MES_LARGO[hoy.getMonth()]} · posición calculada el ${d.fecha}`
      : 'Sin datos. Correr Ingesta_Inventario.py y calcular_nfp.py.';
  }
  renderQueCambio();
  renderHoyKpisSegunNodo();
  if (d) await renderHoyPT(d);
  renderHoyMP();
}
function envolverCola(containerId, clase, titulo, sub, itemsHtml) {
  const cont = document.getElementById(containerId);
  if (!cont) return;
  if (!itemsHtml) { cont.innerHTML = ''; return; }
  cont.innerHTML = `<div class="cola ${clase}"><h3>${titulo}</h3><p class="sub">${sub}</p>${itemsHtml}</div>`;
  cont.querySelectorAll('[data-goto]').forEach(el => {
    el.addEventListener('click', () => irASkuEnBuffer(el.dataset.goto));
  });
  cont.querySelectorAll('[data-gotomp]').forEach(el => {
    el.addEventListener('click', (e) => { e.stopPropagation(); irAMpEnBuffer(el.dataset.gotomp); });
  });
  cont.querySelectorAll('[data-detalle-pedido]').forEach(el => {
    el.addEventListener('click', () => toggleDetalleInlinePT(
      el.dataset.detallePedido, el.dataset.cont, Number(el.dataset.disponible) || 0));
  });
  cont.querySelectorAll('[data-detalle-mp]').forEach(el => {
    el.addEventListener('click', () => toggleDetalleMp(el.dataset.detalleMp, el.dataset.cont));
  });
}
async function renderDetalleInlinePT(sku, disponible, mostrarTodos) {
  const pedidos = await pedidosDeSku(sku);
  const hoy0 = new Date(); hoy0.setHours(0, 0, 0, 0);
  const acumulado = new Map();
  (pedidos || []).forEach(p => {
    const f = parseFechaLocal(p.fecha_entrega);
    if (!f || !(p.cantidad_tm > 0)) return;
    const cliente = p.cliente_nombre || p.cliente || '—';
    const key = cliente + '|' + p.fecha_entrega;
    const dias = diasDesdeHoy(p.fecha_entrega);
    const vencido = f < hoy0;
    const prev = acumulado.get(key);
    acumulado.set(key, {
      cliente, fecha: p.fecha_entrega, dias, vencido,
      tm: (prev ? prev.tm : 0) + p.cantidad_tm
    });
  });
  const filas = [...acumulado.values()].sort((a, b) => (a.dias ?? 999) - (b.dias ?? 999) || b.tm - a.tm);
  let acum = 0;
  const filasEval = filas.map(f => {
    const antes = acum;
    acum += f.tm;
    const cubierto = Math.max(0, Math.min(f.tm, disponible - antes));
    const pct = f.tm > 0 ? cubierto / f.tm : 1;
    return { ...f, _cub: cubierto, _pct: pct };
  });
  const filasMostrar = mostrarTodos ? filasEval : filasEval.filter(f => f._pct < 1);
  if (!filasMostrar.length) {
    return mostrarTodos
      ? '<div style="padding:8px 18px 10px;font-size:12px;color:var(--ink-faint);">Este SKU no tiene pedidos de clientes vigentes registrados.</div>'
      : '<div style="padding:8px 18px 10px;font-size:12px;color:var(--ink-faint);">Todos los pedidos vigentes de este SKU están cubiertos con stock.</div>';
  }
  return `
    <table style="margin:0 18px 10px;width:calc(100% - 36px);">
      <thead><tr><th>Fecha entrega</th><th>Cliente</th><th class="num-right">Toneladas</th>
        <th class="num-right">Cobertura</th><th class="num-right">Urgencia</th></tr></thead>
      <tbody>
        ${filasMostrar.map(f => {
          const cubierto = f._pct >= 1, parcial = f._pct > 0 && f._pct < 1;
          const estadoPill = cubierto ? '<span class="pill pill-green">Cubierto</span>'
            : parcial ? '<span class="pill pill-amber">Parcial</span>' : '<span class="pill pill-red">Sin stock</span>';
          const urgente = !cubierto && (f.vencido || (f.dias != null && f.dias <= LT_PRODUCCION_HOY));
          const urgPill = cubierto ? '<span style="color:var(--ink-faint);">—</span>'
            : f.vencido ? `<span class="pill pill-red">VENCIDO · ${Math.abs(f.dias)} d</span>`
            : urgente ? `<span class="pill pill-red">Planificar hoy · ${f.dias} d</span>`
            : f.dias != null ? `<span style="color:var(--ink-faint);">${f.dias} d</span>` : '—';
          return `<tr ${urgente ? 'style="background:rgba(180,99,94,.06);"' : ''}>
            <td><b>${f.fecha}</b>${f.vencido ? ' ⚠️' : ''}</td><td>${f.cliente}</td><td class="num-right">${fmtTM(f.tm)}</td>
            <td class="num-right">${estadoPill}</td><td class="num-right">${urgPill}</td></tr>`;
        }).join('')}
      </tbody>
    </table>
    <p style="margin:0 18px 10px;font-size:11px;color:var(--ink-faint);">${mostrarTodos ? 'Se muestran todos los pedidos vigentes de este SKU (en sobrestock ya están cubiertos con el inventario actual).' : 'Solo se muestran pedidos sin cobertura completa.'} Cobertura calculada contra el stock disponible (${fmtTM(disponible)}). Lead time de producción: ${LT_PRODUCCION_HOY} días.</p>`;
}
async function toggleDetalleInlinePT(sku, containerId, disponible) {
  const det = document.getElementById('detalle_' + containerId + '_' + sku);
  if (!det) return;
  if (det.style.display === 'block') { det.style.display = 'none'; return; }
  const mostrarTodos = containerId === 'hoySobrestockPT';
  if (!det.dataset.loaded) {
    det.style.display = 'block';
    det.innerHTML = '<div style="padding:10px 18px;font-size:12px;color:var(--ink-faint);">Cargando pedidos…</div>';
    det.innerHTML = await renderDetalleInlinePT(sku, disponible, mostrarTodos);
    det.dataset.loaded = '1';
  } else {
    det.style.display = 'block';
  }
}
function metricaBox(valor, etiqueta, color, principal) {
  return `<div class="m${principal ? ' principal' : ''}"><div class="v" ${color ? `style="color:${color};"` : ''}>${valor}</div><div class="e">${etiqueta}</div></div>`;
}
function mailtoLink(asunto, cuerpo) {
  return `mailto:?subject=${encodeURIComponent(asunto)}&body=${encodeURIComponent(cuerpo)}`;
}
function botonAccion(texto, asunto, cuerpo) {
  return `<a href="${mailtoLink(asunto, cuerpo)}" onclick="event.stopPropagation();"
      style="font-size:10.5px;background:var(--brand-700);color:#fff;padding:5px 11px;
             border-radius:7px;white-space:nowrap;text-decoration:none;font-weight:700;
             display:inline-flex;align-items:center;gap:5px;transition:filter .12s;"
      onmouseover="this.style.filter='brightness(1.1)';" onmouseout="this.style.filter='none';">✉ ${texto}</a>`;
}
function candidatosSobrestockPT() {
  const bufferHoy = new Map((DDMRP.bufferPt || [])
    .filter(b => b.mes_offset === 0 && b.estado === 'ok')
    .map(b => [String(b.sku).replace('.0', '').trim(), b]));
  return (NFP.datos || []).map(n => {
    const key = String(n.sku).replace('.0', '').trim();
    const b = bufferHoy.get(key);
    if (!b || !(b.tog > 0)) return null;
    const disponible = (n.disponible_tm || 0) + (n.transito_tm || 0);
    if (disponible <= b.tog) return null;
    return { sku: n.sku, descripcion: n.descripcion || descripcionSku(n.sku),
             disponible, tog: b.tog, adu: b.adu };
  }).filter(Boolean).sort((a, b) => (b.disponible - b.tog) - (a.disponible - a.tog));
}
async function renderHoyPT(d) {
  const quiebre = d.quiebre_inminente || [];
  const skusQuiebre = new Set(quiebre.map(r => String(r.sku).replace('.0', '').trim()));
  const reponer = (d.reponer || []).filter(r => !skusQuiebre.has(String(r.sku).replace('.0', '').trim()));
  const mto = d.mto_pendiente || [];
  const mtoUrgente = mto.filter(r => r.dias_para_lanzar <= LT_PRODUCCION_HOY);
  const mtoPlan = mto.filter(r => r.dias_para_lanzar > LT_PRODUCCION_HOY);
  const evaluados = await Promise.all([...quiebre, ...reponer].map(async r => {
    const n = nfpDeSku(r.sku);
    const disponible = n ? ((n.disponible_tm || 0) + (n.transito_tm || 0)) : (r.disponible_tm || 0);
    const ev = await evaluarCoberturaReal(r.sku, disponible);
    return { r, ev, disponible };
  }));
  const emergencias = [];
  const porVencer = [];
  evaluados.forEach(({ r, ev, disponible }) => {
    if (ev.cubiertoTotal) return;
    const item = { ...r, _disponible: disponible, _ev: ev };
    if (ev.primerDiaDescubierto != null && ev.primerDiaDescubierto <= LT_PRODUCCION_HOY) emergencias.push(item);
    else porVencer.push(item);
  });
  emergencias.sort((a, b) => (b._ev.tmDescubierto || 0) - (a._ev.tmDescubierto || 0));
  porVencer.sort((a, b) => (b._ev.tmDescubierto || 0) - (a._ev.tmDescubierto || 0));

  function tarjetaPedidoPT(item, usarCorridaHoy, containerId) {
    const fLimite = fechaLimiteOrden(item._ev.primerDiaDescubierto, LT_PRODUCCION_HOY);
    const hoy0 = new Date(); hoy0.setHours(0, 0, 0, 0);
    const fLimiteTxt = fLimite ? (fLimite <= hoy0 ? 'HOY' : fechaCorta(fLimite)) : '—';
    const notificar = usarCorridaHoy ? botonAccion('Notificar a Producción',
      `Producción — reponer ${item.sku} (riesgo de quiebre)`,
      `SKU: ${item.sku}\nDescripción: ${item.descripcion || descripcionSku(item.sku) || ''}\n` +
      `Inventario actual: ${fmtTM(item._disponible)}\nPor abastecer: ${fmtTM(item._ev.tmDescubierto)}\n` +
      `Fecha límite de la orden: ${fLimiteTxt}\n\nSolicito priorizar la producción de este SKU para evitar quiebre de stock.`) : '';
    const verBuffer = `<a href="#" data-goto="${item.sku}" style="font-size:11px;color:var(--brand-700);white-space:nowrap;" onclick="event.stopPropagation();">Ver buffer →</a>`;
    const ventaMtd = DATA.ventasMesActualPorSku.get(String(item.sku).replace('.0', '').trim());
    return `
      <div class="item-sku" data-detalle-pedido="${item.sku}" data-cont="${containerId}" data-disponible="${item._disponible}">
        <div class="izq"><span class="cod">${item.sku}</span><span class="des">${item.descripcion || descripcionSku(item.sku) || ''}</span></div>
        <div class="der">
          ${metricaBox(fmtTM(item._ev.tmDescubierto), 'Por abastecer', 'var(--red-700)')}
          ${metricaBox(fmtTM(item._disponible), 'Inventario actual')}
          ${metricaBox(fLimiteTxt, 'Fecha límite orden', fLimite && fLimite <= hoy0 ? 'var(--red-700)' : '')}
          ${metricaBox(ventaMtd != null ? fmtEntero(ventaMtd) + ' TM' : '—', 'Ventas del mes')}
          ${usarCorridaHoy ? notificar + verBuffer : verBuffer}
        </div>
      </div>
      <div class="detalle-cliente" id="detalle_${containerId}_${item.sku}" style="display:none;"></div>`;
  }
  function tarjetaMtoPT(a, esEmergencia, containerId) {
    // Misma estructura de 4 métricas Y mismo detalle desplegable (fecha de
    // entrega / cliente / toneladas / cobertura / urgencia, vía
    // data-detalle-pedido + renderDetalleInlinePT) que tarjetaPedidoPT -- un
    // MTO también tiene pedidos de cliente reales detrás (es "contra
    // pedido"), antes solo navegaba directo al buffer y no mostraba ese
    // detalle como sí hacen las tarjetas MTS de la misma cola.
    const fLimite = a.fecha_limite_lanzamiento ? parseFechaLocal(a.fecha_limite_lanzamiento) : null;
    const vencida = a.dias_para_lanzar < 0;
    const fLimiteTxt = fLimite ? (a.dias_para_lanzar <= 0 ? 'HOY' : fechaCorta(fLimite)) : '—';
    const notificar = esEmergencia ? botonAccion('Notificar a Producción',
      `Producción — lanzar MTO ${a.sku}`,
      `SKU (MTO): ${a.sku}\nDescripción: ${descripcionSku(a.sku) || ''}\n` +
      `Inventario actual: ${fmtTM(a.stock_tm)}\nPor abastecer: ${fmtTM(a.a_producir_tm)}\n` +
      `Fecha límite de la orden: ${fLimiteTxt}\n\nSolicito lanzar esta orden contra pedido hoy para cumplir la fecha comprometida.`) : '';
    const verBuffer = `<a href="#" data-goto="${a.sku}" style="font-size:11px;color:var(--brand-700);white-space:nowrap;" onclick="event.stopPropagation();">Ver buffer →</a>`;
    const ventaMtd = DATA.ventasMesActualPorSku.get(String(a.sku).replace('.0', '').trim());
    return `
      <div class="item-sku" data-detalle-pedido="${a.sku}" data-cont="${containerId}" data-disponible="${a.stock_tm || 0}">
        <div class="izq"><span class="cod">${a.sku}</span><span class="des">${descripcionSku(a.sku) || 'MTO — contra pedido'}</span></div>
        <div class="der">
          ${metricaBox(fmtTM(a.a_producir_tm), 'Por abastecer', 'var(--red-700)')}
          ${metricaBox(fmtTM(a.stock_tm), 'Inventario actual')}
          ${metricaBox(fLimiteTxt, 'Fecha límite orden', vencida || a.dias_para_lanzar <= 0 ? 'var(--red-700)' : '')}
          ${metricaBox(ventaMtd != null ? fmtEntero(ventaMtd) + ' TM' : '—', 'Ventas del mes')}
          ${esEmergencia ? notificar + verBuffer : verBuffer}
        </div>
      </div>
      <div class="detalle-cliente" id="detalle_${containerId}_${a.sku}" style="display:none;"></div>`;
  }

  envolverCola('hoyEmergenciasPedidosPT', 'critica',
    'Pedidos en quiebre / riesgo de retraso',
    '',
    (emergencias.length || mtoUrgente.length)
      ? emergencias.map(it => tarjetaPedidoPT(it, true, 'hoyEmergenciasPedidosPT')).join('') + mtoUrgente.map(a => tarjetaMtoPT(a, true, 'hoyEmergenciasPedidosPT')).join('')
      : '');
  const totalEmergenciasReal = emergencias.length + mtoUrgente.length;
  const elKpiN = document.getElementById('kpiPtRiesgoHoy');
  const elKpiCard = document.getElementById('kpiPtRiesgoHoyCard');
  if (elKpiN) elKpiN.textContent = totalEmergenciasReal;
  if (elKpiCard) elKpiCard.classList.toggle('alerta', totalEmergenciasReal > 0);
  HOY.emergenciasRealPT = totalEmergenciasReal;
  envolverCola('hoyPorVencerPT', 'repone',
    'Fecha Límite Próxima',
    '',
    (porVencer.length || mtoPlan.length)
      ? porVencer.map(it => tarjetaPedidoPT(it, false, 'hoyPorVencerPT')).join('') + mtoPlan.map(a => tarjetaMtoPT(a, false, 'hoyPorVencerPT')).join('')
      : '');
  const sobrestock = candidatosSobrestockPT();
  envolverCola('hoySobrestockPT', 'sobrestock',
    'Sobre stock',
    '',
    sobrestock.length ? sobrestock.map(r => {
      return `
        <div class="item-sku" data-detalle-pedido="${r.sku}" data-cont="hoySobrestockPT" data-disponible="${r.disponible}">
          <div class="izq"><span class="cod">${r.sku}</span><span class="des">${r.descripcion || ''}</span></div>
          <div class="der">
            ${metricaBox(fmtTM(r.disponible), 'Inventario actual')}
            ${metricaBox(fmtTM(r.tog), 'Inventario máximo')}
            ${metricaBox(r.adu != null ? fmtNum(r.adu, 2) + ' TM/d' : '—', 'Consumo diario')}
            <a href="#" data-goto="${r.sku}" style="font-size:11px;color:var(--brand-700);white-space:nowrap;" onclick="event.stopPropagation();">Ver buffer →</a>
          </div>
        </div>
        <div class="detalle-cliente" id="detalle_hoySobrestockPT_${r.sku}" style="display:none;"></div>`;
    }).join('') : '');
}
// Contenido del detalle "PT que consumen esta MP" (misma clase de tabla que
// el detalle de pedidos de PT). Se carga perezoso al expandir, igual que
// toggleDetalleInlinePT -- no al pintar la tarjeta.
function contenidoConsumidoresPt(consumen) {
  if (!consumen.length) {
    return `<p style="margin:0;padding:10px 18px;font-size:12px;color:var(--ink-faint);">Ningún PT del plan vigente consume esta MP en su fórmula esta semana.</p>`;
  }
  return `<table style="margin:0 18px 10px;width:calc(100% - 36px);">
      <thead><tr><th>PT</th><th>Producto</th><th class="num-right">% fórmula</th>
        <th class="num-right">TM a producir</th><th class="num-right">Consumo de esta MP</th></tr></thead>
      <tbody>${consumen.map(p => `<tr class="clickable-row" data-goto="${p.sku_pt}">
          <td><b>${p.sku_pt}</b></td><td style="font-size:12px;">${(descripcionSku(p.sku_pt) || '—').slice(0, 30)}</td>
          <td class="num-right">${p.pct}%</td>
          <td class="num-right">${fmtTM(p.tmSemana)}</td>
          <td class="num-right">${fmtTM(p.consumoTm)}</td></tr>`).join('')}</tbody></table>`;
}
// Clic en la tarjeta de MP: comprime/expande el detalle, igual que
// toggleDetalleInlinePT para PT -- antes el detalle de MP salía siempre
// visible (no se podía plegar como en PT).
async function toggleDetalleMp(sku, containerId) {
  const det = document.getElementById(`detalleMp_${containerId}_${sku}`);
  if (!det) return;
  if (det.style.display === 'block') { det.style.display = 'none'; return; }
  if (!det.dataset.loaded) {
    det.style.display = 'block';
    det.innerHTML = '<p style="margin:0;padding:10px 18px;font-size:12px;color:var(--ink-faint);">Cargando PT consumidores…</p>';
    const consumen = await ptQueConsumen(sku);
    det.innerHTML = contenidoConsumidoresPt(consumen);
    det.dataset.loaded = '1';
  } else {
    det.style.display = 'block';
  }
}
function tarjetaMP(fila, mostrarConsumidores, containerId) {
  const hoy0 = new Date(); hoy0.setHours(0, 0, 0, 0);
  const fLimiteTxt = fila.fechaLimite ? (fila.fechaLimite <= hoy0 ? 'HOY' : fechaCorta(fila.fechaLimite)) : '—';
  const notificar = botonAccion('Solicitar OC',
    `Compras — orden urgente ${fila.sku}`,
    `MP: ${fila.sku}\nDescripción: ${fila.descripcion || ''}\nTM a solicitar: ${fmtTM(fila.cantidadAPedir)}\n` +
    `Inventario actual: ${fmtTM(fila.stockHoy)}\nInventario mínimo (TOR): ${fmtTM(fila.tor)}\n` +
    `Fecha límite de la orden: ${fLimiteTxt}\n\n` +
    `La orden de compra ya debía lanzarse (o vence hoy) para no interrumpir el consumo planificado.`);
  const consumoMtd = DATA.consumoMesActualPorSkuMp ? DATA.consumoMesActualPorSkuMp.get(String(fila.sku).replace('.0', '').trim()) : null;
  const attrTarjeta = mostrarConsumidores
    ? `data-detalle-mp="${fila.sku}" data-cont="${containerId}"`
    : `data-gotomp="${fila.sku}"`;
  return `
    <div class="item-sku" ${attrTarjeta}>
      <div class="izq"><span class="cod">${fila.sku}</span><span class="des">${fila.descripcion || ''}</span></div>
      <div class="der">
        ${metricaBox(fmtTM(fila.cantidadAPedir), 'Por abastecer', 'var(--red-700)')}
        ${metricaBox(fmtTM(fila.stockHoy), 'Inventario actual')}
        ${metricaBox(fLimiteTxt, 'Fecha límite orden', fila.fechaLimite && fila.fechaLimite <= hoy0 ? 'var(--red-700)' : '')}
        ${metricaBox(consumoMtd != null ? fmtEntero(consumoMtd) + ' TM' : '—', 'Consumos del mes')}
        ${notificar}
        <a href="#" data-gotomp="${fila.sku}" style="font-size:11px;color:var(--brand-700);white-space:nowrap;" onclick="event.stopPropagation();">Ver buffer →</a>
      </div>
    </div>
    ${mostrarConsumidores ? `<div class="detalle-cliente" id="detalleMp_${containerId}_${fila.sku}" style="display:none;"></div>` : ''}`;
}
async function renderHoyMP() {
  const [todas, mpDelPlan] = await Promise.all([cargarAlertasMP(), mpConsumidasPorElPlan()]);
  const filas = todas.filter(f => mpDelPlan.has(f.sku));
  const emergencias = filas.filter(f => f.estado === 'emergencia');
  const sobrestock = filas.filter(f => f.estado === 'sobrestock');
  // mostrarConsumidores=true: la demanda de esta MP es el consumo de los PT
  // que la usan en su fórmula -- comprimible/expandible igual que el detalle
  // de pedidos de PT (toggleDetalleMp), no siempre visible.
  const htmlEmer = emergencias.map(f => tarjetaMP(f, true, 'hoyEmergenciasMP'));
  envolverCola('hoyEmergenciasMP', 'critica',
    'Materia prima en riesgo de stockout',
    'La orden de compra ya debía lanzarse (o vence hoy) para no interrumpir el consumo planificado.',
    htmlEmer.join(''));
  const sobrestockHtml = sobrestock.map(r => `
      <div class="item-sku" data-detalle-mp="${r.sku}" data-cont="hoySobrestockMP">
        <div class="izq"><span class="cod">${r.sku}</span><span class="des">${r.descripcion || ''}</span></div>
        <div class="der">
          ${metricaBox(fmtTM(r.stockHoy), 'Stock actual')}
          ${metricaBox(fmtTM(r.tog), 'Stock máximo (TOG)')}
          ${metricaBox(r.consumoDiario != null ? fmtNum(r.consumoDiario, 2) + ' TM/d' : '—', 'Consumo diario')}
          <a href="#" data-gotomp="${r.sku}" style="font-size:11px;color:var(--brand-700);white-space:nowrap;" onclick="event.stopPropagation();">Ver buffer →</a>
        </div>
      </div>
      <div class="detalle-cliente" id="detalleMp_hoySobrestockMP_${r.sku}" style="display:none;"></div>`).join('');
  envolverCola('hoySobrestockMP', 'sobrestock',
    'Compra detenida',
    'MP sin compra necesaria: NFP por encima del máximo, o en riesgo de vencimiento.',
    sobrestockHtml);
}
let FILTROS_DDMRP = { sku: '', rotacion: 'todos' };
let FILTROS_MP = { mp: '', lt: 'todos' };
const MES_OFFSET_DDMRP = 0;
function diasCobertura(valorTM, adu) {
  if (!adu || adu <= 0 || valorTM == null) return null;
  return valorTM / adu;
}
function initTabsDDMRP() {
  document.querySelectorAll('#ddmrp > .tabs > .tab').forEach(tab => {
    if (tab.dataset.wired) return;
    tab.dataset.wired = '1';
    tab.addEventListener('click', () => {
      document.querySelectorAll('#ddmrp > .tabs > .tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('#ddmrp .nodo-panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      DDMRP.nodoActivo = tab.dataset.nodo;
      document.getElementById(tab.dataset.nodo === 'pt' ? 'nodoPT' : 'nodoMP').classList.add('active');
      renderDDMRP();
    });
  });
}
function opcionesSkuDDMRP() {
  const elRot = document.getElementById('rotSel_ddmrp');
  const rotFiltro = elRot ? elRot.value : 'todos';
  const vistos = new Map();
  const fuente = (POS.datos && POS.datos.length) ? POS.datos.map(p => p.sku) : DDMRP.bufferPt.map(b => b.sku);
  fuente.forEach(sku => {
    if (rotFiltro !== 'todos' && (rotacionDeSku(sku) || 'mto') !== rotFiltro) return;
    if (!vistos.has(sku)) {
      const desc = descripcionSku(sku);
      vistos.set(sku, { value: sku, label: desc ? `${sku} — ${desc}` : `${sku}` });
    }
  });
  return Array.from(vistos.values());
}
function leerFiltrosDDMRP() {
  const elSku = document.getElementById('skuBuscar_ddmrp');
  const elRot = document.getElementById('rotSel_ddmrp');
  return {
    sku: elSku ? elSku.value.trim() : '',
    rotacion: elRot ? elRot.value : 'todos',
  };
}
function filasPtFuenteFinal() {
  if (POS.datos && POS.datos.length) {
    return POS.datos.filter(p => p.politica !== 'MTO').map(p => ({
      sku: p.sku, adu: p.adu, tor: p.tor, toy: p.toy, tog: p.tog,
      green: Math.max(0, (p.tog || 0) - (p.toy || 0)),
      yellow: Math.max(0, (p.toy || 0) - (p.tor || 0)),
      semaforo: p.semaforo, mes_offset: 0, estado: 'ok',
      dimensionamiento_confiable: p.dimensionamiento_confiable,
    }));
  }
  return DDMRP.bufferPt.filter(b => b.mes_offset === MES_OFFSET_DDMRP && b.estado === 'ok');
}
function filasDDMRP() {
  const f = FILTROS_DDMRP;
  if (f.rotacion === 'mto') return [];
  return filasPtFuenteFinal().filter(b => {
    if (f.rotacion !== 'todos' && rotacionDeSku(b.sku) !== f.rotacion) return false;
    if (f.sku) {
      const cod = f.sku.split(' — ')[0].trim().toLowerCase();
      if (!String(b.sku || '').toLowerCase().includes(cod)) return false;
    }
    return true;
  }).sort((a, b) => {
    const da = (nfpDeSku(a.sku) || {}).demanda_calificada_tm || 0;
    const db = (nfpDeSku(b.sku) || {}).demanda_calificada_tm || 0;
    return db - da;
  });
}
async function renderDDMRP() {
  await cargarDDMRP();
  const badge = document.getElementById('ddmrpFuenteBanda');
  if (badge && DDMRP.resumen) {
    const cal = DDMRP.resumen.bandas_calibradas;
    badge.textContent = cal ? 'Bandas calibradas' : '⚠ Bandas sin calibrar';
    badge.style.background = cal ? 'var(--emerald-100)' : 'var(--amber-100)';
    badge.style.color = cal ? 'var(--emerald-700)' : 'var(--amber-700)';
  }
  const mesLbl = document.getElementById('ddmrpMesActualLabel');
  if (mesLbl) {
    const hoy = new Date();
    mesLbl.textContent = `${NOMBRES_MES_LARGO[hoy.getMonth()]} ${hoy.getFullYear()}`;
  }
  if (DDMRP.nodoActivo === 'pt') await renderNodoPT();
  else await renderNodoMP();
}
async function renderNodoPT() {
  FILTROS_DDMRP = leerFiltrosDDMRP();
  await Promise.all([cargarPosicionamiento(), cargarNFP(), cargarProduccion()]);
  const filas = filasDDMRP();
  const topBadge = document.getElementById('ddmrpNSkuTop');
  if (topBadge) {
    const totalSku = new Set(DDMRP.bufferPt.map(b => b.sku)).size;
    topBadge.textContent = `${totalSku} SKU con buffer activo`;
  }
  const skuBusqueda = FILTROS_DDMRP.sku ? FILTROS_DDMRP.sku.split(' — ')[0].trim().replace('.0', '') : '';
  if (skuBusqueda) {
    DDMRP.skuSeleccionado = skuBusqueda;
  } else {
    const sigueVisible = filas.some(b => String(b.sku || '').replace('.0', '').trim() === String(DDMRP.skuSeleccionado || '').replace('.0', '').trim());
    if (!sigueVisible) DDMRP.skuSeleccionado = filas.length ? filas[0].sku : null;
  }
  const mtoSel = skuMtoSeleccionado();
  if (mtoSel) { await renderKpisMto(mtoSel); } else { renderDDMRPKpis(filas); }
  renderDDMRPTabla(filas);
  renderDDMRPDetalle();
  if (!mtoSel) renderOtif(filas);
  renderMtoTabla();
  renderPosicionamiento();
}

/* Un SKU MTO nunca aparece en filasPtFuenteFinal()/filasDDMRP() (solo
   lleva pedido, no buffer) -- por eso antes, al buscarlo en Inventario
   Estratégico, todos los KPI y el buffer quedaban en '—'/0 como si no
   existiera. skuMtoSeleccionado() detecta ese caso para mostrarle al
   planificador lo que sí aplica a un MTO (inventario actual, a producir,
   % de bodega) en vez de un buffer que nunca va a tener. */
function skuMtoSeleccionado() {
  const sku = DDMRP.skuSeleccionado;
  if (!sku) return null;
  const key = String(sku).replace('.0', '').trim();
  const posRow = (POS.datos || []).find(p => String(p.sku).replace('.0', '').trim() === key);
  return (posRow && !posRow.es_mts) ? posRow : null;
}

async function renderKpisMto(posRow) {
  const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  const nfp = nfpDeSku(posRow.sku);
  const disponible = nfp ? ((nfp.disponible_tm || 0) + (nfp.transito_tm || 0)) : (posRow.stock_tm || 0);
  const alertas = await cargarAlertasMtoDetalle();
  const alerta = alertas.get(String(posRow.sku).replace('.0', '').trim());
  const aProducir = alerta ? (alerta.a_producir_tm || 0) : 0;
  const capPt = CFG.capacidadPtTm || (POS.resumen && POS.resumen.capacidad_bodega_tm) || null;
  const pct = capPt ? (disponible / capPt) * 100 : null;

  set('ddmrpTog', '—');
  set('ddmrpTogSub', 'SKU MTO: sin inventario máximo, se gestiona por pedido');
  set('ddmrpTor', '—');
  set('ddmrpTorSub', 'SKU MTO: sin inventario mínimo');
  set('ddmrpInvActual', fmtTM(disponible));
  set('ddmrpInvActualSub', 'disponible + tránsito, hoy');
  set('ddmrpAProducirHoy', aProducir > 0 ? fmtTM(aProducir) : '0 TM');
  set('ddmrpAProducirHoySub', aProducir > 0 ? 'para cubrir pedidos pendientes' : 'sin pedidos pendientes por cubrir');
  set('posOcupPt', pct != null ? `${fmtNum(pct, 1)}%` : '—');
  set('posOcupPtSub', capPt
    ? `${fmtTM(disponible)} de ${fmtTM(capPt)} · ocupación de este SKU en bodega PT`
    : 'define la capacidad de bodega PT en Configuración');
}
function renderDDMRPKpis(filas) {
  const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  const tog = filas.reduce((a, b) => a + (b.tog || 0), 0);
  const tor = filas.reduce((a, b) => a + (b.tor || 0), 0);
  const aduTotal = filas.reduce((a, b) => a + (b.adu || 0), 0);
  const nSku = new Set(filas.map(b => b.sku)).size;
  const skusSet = new Set(filas.map(b => String(b.sku).replace('.0', '').trim()));
  const sinFiltroInv = !FILTROS_DDMRP.sku && FILTROS_DDMRP.rotacion === 'todos';
  const invActual = (NFP.datos || [])
    .filter(r => sinFiltroInv || skusSet.has(String(r.sku).replace('.0', '').trim()))
    .reduce((a, r) => a + (r.disponible_tm || 0) + (r.transito_tm || 0), 0);
  const hoyF = hoyStr();
  const planHoy = (PROD.plan || []).filter(p =>
    String(p.fecha).slice(0, 10) === hoyF
    && p.motivo !== 'YA PRODUCIDO'
    && skusSet.has(String(p.sku).replace('.0', '').trim()));
  const aProducirHoy = planHoy.reduce((a, p) => a + (p.tm || 0), 0);
  const porPlantaHoy = new Map();
  planHoy.forEach(p => {
    const pl = p.planta || (p.recurso || '').split('|')[0] || '—';
    porPlantaHoy.set(pl, (porPlantaHoy.get(pl) || 0) + (p.tm || 0));
  });
  const desglosePlantaHoy = [...porPlantaHoy.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([pl, tm]) => `${pl}: ${fmtTM(tm)}`)
    .join(' · ');
  set('ddmrpTog', fmtTM(tog));
  set('ddmrpTor', fmtTM(tor));
  set('ddmrpInvActual', fmtTM(invActual));
  set('ddmrpAProducirHoy', aProducirHoy > 0 ? fmtTM(aProducirHoy) : '0 TM');
  const cobTog = diasCobertura(tog, aduTotal);
  const cobTor = diasCobertura(tor, aduTotal);
  const subTog = document.getElementById('ddmrpTogSub');
  if (subTog) subTog.textContent = cobTog != null ? `${nSku} SKU · ${fmtNum(cobTog)} días de consumo` : `${nSku} SKU en el filtro`;
  const subTor = document.getElementById('ddmrpTorSub');
  if (subTor) subTor.textContent = cobTor != null ? `${fmtNum(cobTor)} días de protección contra quiebre` : 'protección contra quiebre de stock';
  const subInv = document.getElementById('ddmrpInvActualSub');
  if (subInv) {
    const cobActual = diasCobertura(invActual, aduTotal);
    subInv.textContent = cobActual != null ? `${fmtNum(cobActual)} días de cobertura hoy` : 'disponible + tránsito, hoy';
  }
  const subProd = document.getElementById('ddmrpAProducirHoySub');
  if (subProd) subProd.textContent = aProducirHoy > 0 ? desglosePlantaHoy : 'sin producción programada hoy';
}
function renderDDMRPTabla(filas) {
  const tbody = document.getElementById('ddmrpTabla');
  if (!tbody) return;
  if (!filas.length) {
    tbody.innerHTML = `<tr><td colspan="13" style="color:var(--gray-400);">
      Sin SKU con buffer calculable para este filtro. Si filtraste por "MTO", revisa la
      sección "SKU sin buffer (MTO)" más abajo.</td></tr>`;
    return;
  }
  tbody.innerHTML = filas.map(b => {
    const politica = politicaDeSku(b.sku);
    const colorPolitica = politica === 'MTS PLENO' ? 'var(--emerald-700)'
      : politica === 'MTS MINIMO' ? '#8A5A0E' : 'var(--gray-400)';
    const nfpRow = nfpDeSku(b.sku);
    const sel = String(b.sku || '').replace('.0', '').trim() === String(DDMRP.skuSeleccionado || '').replace('.0', '').trim()
      ? ' style="background:var(--emerald-50);"' : '';
    const esCritico = nfpRow && nfpRow.alerta_onhand === 'CRITICO';
    return `
      <tr class="clickable-row" data-sku="${b.sku}"${sel}>
        <td><b>${b.sku}</b></td>
        <td>${descripcionSku(b.sku) || '—'}</td>
        <td><span style="color:${colorPolitica};font-weight:bold;font-size:11.5px;">${politica || '—'}</span></td>
        <td class="num-right">${b.adu != null ? fmtNum(b.adu, 2) : '—'}</td>
        <td>${miniBarraPosicion(b, nfpRow)}</td>
        <td class="num-right">${nfpRow && nfpRow.nfp_tm != null ? fmtTM(nfpRow.nfp_tm) : '—'}</td>
        <td class="num-right">${nfpRow && nfpRow.disponible_tm != null ? fmtTM(nfpRow.disponible_tm) : '—'}</td>
        <td class="num-right">${nfpRow && nfpRow.transito_tm != null ? fmtTM(nfpRow.transito_tm) : '—'}</td>
        <td class="num-right">${nfpRow && nfpRow.demanda_calificada_tm != null ? fmtTM(nfpRow.demanda_calificada_tm) : '—'}</td>
        <td class="num-right">${nfpRow && nfpRow.cantidad_a_ordenar_tm ? fmtTM(nfpRow.cantidad_a_ordenar_tm) : '—'}</td>
        <td>${pillZonaNfp(nfpRow ? nfpRow.zona_nfp : null)}</td>
        <td>${esCritico ? '<span style="color:var(--red-700);font-weight:700;">Alerta</span>' : '<span style="color:var(--gray-400);">OK</span>'}</td>
        <td><button class="btn btn-secondary btn-small" data-editar-buffer="${b.sku}">Editar</button></td>
      </tr>`;
  }).join('');
  tbody.querySelectorAll('tr[data-sku]').forEach(tr => {
    tr.addEventListener('click', () => {
      const sku = tr.dataset.sku;
      DDMRP.skuSeleccionado = sku;
      const inp = document.getElementById('skuBuscar_ddmrp');
      if (inp) { const d = descripcionSku(sku); inp.value = d ? `${sku} — ${d}` : String(sku); }
      sincronizarRotacionConSku();
      renderDDMRP();
    });
  });
  tbody.querySelectorAll('[data-editar-buffer]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      abrirModalEditarBuffer(btn.dataset.editarBuffer);
    });
  });
}
async function cargarFactoresManuales(sku) {
  try {
    const r = await fetch(API_BASE + '/api/v1/ddmrp/factores-manuales/' + encodeURIComponent(sku));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (err) {
    console.error('No se pudieron cargar los factores manuales', err);
    return { daf: 1.0, zaf: 1.0, ltaf: 1.0 };
  }
}
async function guardarFactoresManuales(sku, daf, zaf, ltaf) {
  try {
    const resp = await fetch(API_BASE + '/api/v1/ddmrp/factores-manuales', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku, daf, zaf, ltaf, actualizado_por: 'planificador' }),
    });
    return resp.ok;
  } catch (err) {
    console.error('No se pudieron guardar los factores manuales', err);
    return false;
  }
}
const BUFFER_EDIT = { sku: null, actuales: null, evaluacion: null };
async function abrirModalEditarBuffer(sku) {
  const clave = String(sku).replace('.0', '').trim();
  const fila = (POS.datos || []).find(d => String(d.sku).replace('.0', '').trim() === clave);
  if (!fila) { alert('No se encontraron los valores actuales de buffer para este SKU.'); return; }
  BUFFER_EDIT.sku = sku;
  BUFFER_EDIT.actuales = { tor: fila.tor, toy: fila.toy, tog: fila.tog };
  BUFFER_EDIT.evaluacion = null;
  const titulo = document.getElementById('modalEditarBufferTitulo');
  const desc = descripcionSku(sku);
  if (titulo) titulo.textContent = `Editar buffer — ${sku}${desc ? ' · ' + desc : ''}`;
  document.getElementById('modalBufferTor').value = fila.tor ?? '';
  document.getElementById('modalBufferToy').value = fila.toy ?? '';
  document.getElementById('modalBufferTog').value = fila.tog ?? '';
  const evalBox = document.getElementById('modalBufferEval');
  if (evalBox) evalBox.innerHTML = '';
  const btnConfirmar = document.getElementById('modalBufferConfirmar');
  if (btnConfirmar) { btnConfirmar.disabled = true; btnConfirmar.textContent = 'Confirmar cambio'; }
  const factores = await cargarFactoresManuales(sku);
  document.getElementById('modalBufferDaf').value = factores.daf ?? 1.0;
  document.getElementById('modalBufferZaf').value = factores.zaf ?? 1.0;
  document.getElementById('modalBufferLtaf').value = factores.ltaf ?? 1.0;
  document.getElementById('modalEditarBuffer').classList.add('open');
  await evaluarBufferModal();
}
function cerrarModalEditarBuffer() {
  const overlay = document.getElementById('modalEditarBuffer');
  if (overlay) overlay.classList.remove('open');
  BUFFER_EDIT.sku = null; BUFFER_EDIT.actuales = null; BUFFER_EDIT.evaluacion = null;
}
async function evaluarBufferModal() {
  if (!BUFFER_EDIT.sku) return;
  const tor = parseFloat(document.getElementById('modalBufferTor').value) || 0;
  const toy = parseFloat(document.getElementById('modalBufferToy').value) || 0;
  const tog = parseFloat(document.getElementById('modalBufferTog').value) || 0;
  const evalBox = document.getElementById('modalBufferEval');
  const btnConfirmar = document.getElementById('modalBufferConfirmar');
  if (evalBox) evalBox.innerHTML = '<p style="font-size:12.5px;color:var(--gray-400);margin-top:8px;">Evaluando…</p>';
  if (btnConfirmar) btnConfirmar.disabled = true;
  try {
    const resp = await fetch(API_BASE + '/api/v1/ddmrp/overrides/evaluar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku: BUFFER_EDIT.sku, tor, toy, tog }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const ev = await resp.json();
    BUFFER_EDIT.evaluacion = ev;
    const va = ev.valores_actuales, vp = ev.valores_propuestos;
    const emoji = ev.semaforo === 'verde' ? '🟢' : ev.semaforo === 'rojo' ? '🔴' : '🟡';
    const diff = `
      <div style="font-size:12.5px;margin-top:10px;color:var(--gray-600);">
        <b>Diff:</b> TOR ${fmtTM(va.tor)} → ${fmtTM(vp.tor)} ·
        TOY ${fmtTM(va.toy)} → ${fmtTM(vp.toy)} ·
        TOG ${fmtTM(va.tog)} → ${fmtTM(vp.tog)}
      </div>`;
    const bodega = (ev.inventario_promedio_total_actual_tm != null && ev.capacidad_pt_tm)
      ? `<div style="font-size:12.5px;margin-top:4px;color:var(--gray-600);">
          <b>Bodega (inventario promedio):</b> ${fmtTM(ev.inventario_promedio_total_actual_tm)} → ${fmtTM(ev.inventario_promedio_total_propuesto_tm)} TM de ${fmtTM(ev.capacidad_pt_tm)} TM
        </div>` : '';
    if (evalBox) evalBox.innerHTML = `<div class="modal-eval-box ${ev.semaforo}">${emoji} ${ev.mensaje}</div>${diff}${bodega}`;
    if (btnConfirmar) btnConfirmar.disabled = false;
  } catch (err) {
    console.error('No se pudo evaluar el cambio de buffer', err);
    if (evalBox) evalBox.innerHTML = '<p style="font-size:12.5px;color:var(--red-700);margin-top:8px;">No se pudo evaluar el cambio — revisa la conexión con el backend.</p>';
  }
}
async function confirmarBufferModal() {
  if (!BUFFER_EDIT.sku || !BUFFER_EDIT.evaluacion) return;
  const btnConfirmar = document.getElementById('modalBufferConfirmar');
  const tor = parseFloat(document.getElementById('modalBufferTor').value) || 0;
  const toy = parseFloat(document.getElementById('modalBufferToy').value) || 0;
  const tog = parseFloat(document.getElementById('modalBufferTog').value) || 0;
  if (btnConfirmar) { btnConfirmar.disabled = true; btnConfirmar.textContent = 'Guardando…'; }
  try {
    const resp = await fetch(API_BASE + '/api/v1/ddmrp/overrides/guardar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku: BUFFER_EDIT.sku, tor, toy, tog, actualizado_por: 'planificador' }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const daf = parseFloat(document.getElementById('modalBufferDaf').value) || 1.0;
    const zaf = parseFloat(document.getElementById('modalBufferZaf').value) || 1.0;
    const ltaf = parseFloat(document.getElementById('modalBufferLtaf').value) || 1.0;
    await guardarFactoresManuales(BUFFER_EDIT.sku, daf, zaf, ltaf);

    DDMRP.cargado = false;
    POS.cargado = false;
    await Promise.all([cargarDDMRP(), cargarPosicionamiento()]);
    cerrarModalEditarBuffer();
    renderDDMRP();
    flashGuardado('Buffer actualizado ✓ (el optimizador usará este valor en la próxima corrida)');
  } catch (err) {
    console.error('No se pudo guardar el cambio de buffer', err);
    alert('No se pudo guardar el cambio. Revisa la conexión con el backend e intenta de nuevo.');
    if (btnConfirmar) { btnConfirmar.disabled = false; btnConfirmar.textContent = 'Confirmar cambio'; }
  }
}
function initModalEditarBuffer() {
  const overlay = document.getElementById('modalEditarBuffer');
  if (!overlay || overlay.dataset.wired) return;
  overlay.dataset.wired = '1';
  document.getElementById('modalEditarBufferCerrar').addEventListener('click', cerrarModalEditarBuffer);
  document.getElementById('modalBufferCancelar').addEventListener('click', cerrarModalEditarBuffer);
  document.getElementById('modalBufferConfirmar').addEventListener('click', confirmarBufferModal);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) cerrarModalEditarBuffer(); });
  let temporizadorEval;
  ['modalBufferTor', 'modalBufferToy', 'modalBufferTog'].forEach(id => {
    document.getElementById(id).addEventListener('input', () => {
      clearTimeout(temporizadorEval);
      temporizadorEval = setTimeout(evaluarBufferModal, 450);
    });
  });
  ['Daf', 'Zaf', 'Ltaf'].forEach(nombre => {
    const btn = document.getElementById('modalBuffer' + nombre + 'Reset');
    if (btn) btn.addEventListener('click', () => { document.getElementById('modalBuffer' + nombre).value = 1.0; });
  });
  document.getElementById('modalBufferUsarRecomendacion').addEventListener('click', () => {
    const rec = BUFFER_EDIT.evaluacion && BUFFER_EDIT.evaluacion.recomendacion_sistema;
    if (!rec) return;
    document.getElementById('modalBufferTor').value = rec.tor;
    document.getElementById('modalBufferToy').value = rec.toy;
    document.getElementById('modalBufferTog').value = rec.tog;
    evaluarBufferModal();
  });
}
let ALERTAS_MTO_CACHE = null;
async function cargarAlertasMtoDetalle() {
  if (ALERTAS_MTO_CACHE) return ALERTAS_MTO_CACHE;
  try {
    const r = await fetchJSON('/api/v1/ddmrp/alertas-mto');
    ALERTAS_MTO_CACHE = new Map((Array.isArray(r) ? r : []).map(a => [String(a.sku).replace('.0', '').trim(), a]));
  } catch (err) { ALERTAS_MTO_CACHE = new Map(); }
  return ALERTAS_MTO_CACHE;
}
function pillUrgenciaMto(urgencia) {
  const map = {
    CUBIERTO: ['pill-green', 'Cubierto con stock'],
    A_TIEMPO: ['pill-blue', 'En plan, llega a tiempo'],
    HOY: ['pill-amber', 'Lanzar hoy'],
    TARDE: ['pill-red', 'Lanzamiento vencido'],
  };
  const par = map[urgencia];
  return par ? `<span class="pill ${par[0]}">${par[1]}</span>` : '<span style="color:var(--gray-600);">—</span>';
}
async function renderMtoTabla() {
  const tbody = document.getElementById('mtoTabla');
  const badge = document.getElementById('mtoConteoBadge');
  if (!tbody) return;
  const filas = (POS.datos || []).filter(d => !d.es_mts);
  if (badge) badge.textContent = `${filas.length} SKU`;
  if (!filas.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:var(--gray-400);">Sin SKU MTO registrados. Correr clasificar_mts_mto.py si falta el dato.</td></tr>`;
    return;
  }
  const hoy0 = new Date(); hoy0.setHours(0, 0, 0, 0);
  tbody.innerHTML = filas.map(d => {
    // Fecha de planificación = la fecha real en el plan de producción para
    // este SKU (primera corrida futura, excluye YA PRODUCIDO). Si el SKU no
    // está en el plan, se declara explícitamente "Sin planificación".
    const plan = primeraCorridaFuturaPorSku(d.sku);
    let fechaCell;
    if (plan && plan.fecha) {
      const f = parseFechaLocal(plan.fecha);
      const vencida = f < hoy0;
      const esHoy = f.getTime() === hoy0.getTime();
      const color = vencida ? 'var(--red-700)' : esHoy ? 'var(--amber-700)' : 'var(--gray-900)';
      const texto = esHoy ? `HOY · ${fechaCorta(f)}` : fechaCorta(f);
      fechaCell = `<span style="color:${color};font-weight:700;">${texto}</span>`;
    } else {
      fechaCell = '<span style="color:var(--gray-400);">Sin planificación</span>';
    }
    return `
    <tr class="clickable-row" data-sku="${d.sku}">
      <td><b>${d.sku}</b></td>
      <td>${(descripcionSku(d.sku) || '—').toString().slice(0, 34)}</td>
      <td class="num-right">${d.adu != null ? fmtNum(d.adu, 2) : '—'}</td>
      <td class="num-right">${fmtTM(d.stock_tm)}</td>
      <td class="num-right">${fmtTM(d.pedidos_tm)}</td>
      <td class="num-right">${fechaCell}</td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('[data-sku]').forEach(tr => tr.addEventListener('click', () => irASkuEnBuffer(tr.dataset.sku)));
}
function semaforoPct(pct) {
  const p = (pct == null ? 0 : pct) * 100;
  if (p >= 90) return { color: 'var(--emerald-700)', emoji: '🟢', estado: 'Dentro de objetivo' };
  if (p >= 70) return { color: 'var(--amber-700)', emoji: '🟡', estado: 'Cerca del objetivo' };
  return { color: 'var(--red-700)', emoji: '🔴', estado: 'Por debajo del objetivo' };
}
function ringOtif(pct, label) {
  const size = 132, r = 50, stroke = 14;
  const C = 2 * Math.PI * r;
  const p = pct == null ? 0 : Math.max(0, Math.min(1, pct));
  const s = semaforoPct(pct);
  const colorArco = pct == null ? 'var(--gray-200)' : s.color;
  return `
    <div style="text-align:center;">
      <div style="position:relative;width:${size}px;height:${size}px;margin:0 auto;">
        <svg width="${size}" height="${size}" viewBox="0 0 120 120" style="transform:rotate(-90deg);">
          <circle cx="60" cy="60" r="${r}" fill="none" stroke="var(--gray-200)" stroke-width="${stroke}"/>
          <circle cx="60" cy="60" r="${r}" fill="none" stroke="${colorArco}" stroke-width="${stroke}"
                  stroke-linecap="round" stroke-dasharray="${p * C} ${C}"/>
        </svg>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
                    font-size:26px;font-weight:800;font-family:'Consolas','SF Mono',monospace;
                    color:var(--ink);letter-spacing:-.5px;">${pct == null ? '—' : fmtPct(pct)}</div>
      </div>
      <div style="margin-top:10px;font-size:11.5px;font-weight:700;color:var(--gray-600);
                  text-transform:uppercase;letter-spacing:.6px;">${label}</div>
    </div>`;
}
function otifAnillo(pct, label) {
  const s = semaforoPct(pct);
  return `
    <div style="flex:1;min-width:0;">
      ${ringOtif(pct, label)}
      <div style="margin-top:8px;text-align:center;font-size:12px;font-weight:700;
                  color:${pct == null ? 'var(--gray-400)' : s.color};">${pct == null ? '—' : s.emoji + ' ' + s.estado}</div>
    </div>`;
}
async function calcularOtifDeSkus(skus) {
  let tmTotal = 0, tmDescubiertoActual = 0, tmDescubiertoPotencial = 0, conPedidos = 0;
  let tmVencidoSiempre = 0;
  await Promise.all(skus.map(async sku => {
    const n = nfpDeSku(sku);
    const disponible = n ? ((n.disponible_tm || 0) + (n.transito_tm || 0)) : 0;
    const reponer = n ? (n.cantidad_a_ordenar_tm || 0) : 0;

    const pedidos = await pedidosDeSku(sku);
    const hoy0 = new Date(); hoy0.setHours(0, 0, 0, 0);
    const vencidosTm = (pedidos || [])
      .filter(p => { const f = parseFechaLocal(p.fecha_entrega); return f && f < hoy0 && (p.cantidad_tm || 0) > 0; })
      .reduce((a, p) => a + p.cantidad_tm, 0);

    const [evActual, evPotencial] = await Promise.all([
      evaluarCoberturaReal(sku, disponible),
      evaluarCoberturaReal(sku, disponible + reponer),
    ]);

    if (evActual.tmTotal > 0) conPedidos++;
    tmTotal += evActual.tmTotal;
    tmDescubiertoActual += Math.max(evActual.tmDescubierto, vencidosTm);
    tmDescubiertoPotencial += Math.max(evPotencial.tmDescubierto, vencidosTm);
    tmVencidoSiempre += vencidosTm;
  }));
  if (tmTotal <= 0) return { otifActual: null, otifPotencial: null, tmTotal: 0, tmDescubiertoActual: 0, tmDescubiertoPotencial: 0, tmVencidoSiempre: 0, conPedidos: 0 };
  return {
    otifActual: Math.max(0, 1 - tmDescubiertoActual / tmTotal),
    otifPotencial: Math.max(0, 1 - tmDescubiertoPotencial / tmTotal),
    tmTotal, tmDescubiertoActual, tmDescubiertoPotencial, tmVencidoSiempre, conPedidos,
  };
}
async function renderOtif(filas) {
  const wrap = document.getElementById('otifGaugeWrap');
  const elNota = document.getElementById('otifNota');
  if (!wrap) return;
  const skus = filas.slice(0, 200).map(b => b.sku);
  if (!skus.length) {
    wrap.innerHTML = `<div style="display:flex;gap:26px;">${otifAnillo(null, 'OTIF actual')}${otifAnillo(null, 'OTIF potencial')}</div>`;
    if (elNota) elNota.textContent = 'Sin SKU en el filtro actual para estimar el OTIF.';
    return;
  }
  const { otifActual, otifPotencial, tmTotal, tmDescubiertoActual, tmDescubiertoPotencial, tmVencidoSiempre, conPedidos } = await calcularOtifDeSkus(skus);
  if (tmTotal <= 0) {
    wrap.innerHTML = `<div style="display:flex;gap:26px;">${otifAnillo(null, 'OTIF actual')}${otifAnillo(null, 'OTIF potencial')}</div>`;
    if (elNota) elNota.textContent = 'Sin pedidos vigentes en el filtro actual para estimar el OTIF.';
    return;
  }
  const delta = otifPotencial - otifActual;
  const deltaTxt = delta > 0.0005 ? `+${fmtNum(delta * 100)} pp` : (delta < -0.0005 ? `${fmtNum(delta * 100)} pp` : 'sin cambio');
  const deltaFlecha = delta > 0.0005 ? '▲' : (delta < -0.0005 ? '▼' : '—');
  const deltaColor = delta > 0.0005 ? 'var(--emerald-700)' : (delta < -0.0005 ? 'var(--red-700)' : 'var(--ink-faint)');
  wrap.innerHTML = `
    <div style="display:flex;gap:26px;">
      ${otifAnillo(otifActual, 'OTIF actual')}
      ${otifAnillo(otifPotencial, 'OTIF potencial')}
    </div>
    <div style="margin-top:14px;font-size:13px;font-weight:700;color:${deltaColor};">${deltaFlecha} Impacto de reposición: ${deltaTxt}</div>`;
  if (elNota) {
    const tmEliminadas = Math.max(0, tmDescubiertoActual - tmDescubiertoPotencial);
    const sInsight = semaforoPct(otifPotencial);
    const techoTexto = tmVencidoSiempre > 0
      ? ` El máximo alcanzable hoy es ${fmtPct(otifPotencial)}, no 100%: ${fmtTM(tmVencidoSiempre)} ya están fuera de plazo y no pueden contar como "a tiempo" aunque se cubran con stock.`
      : '';
    let mensaje;
    if (delta > 0.0005) {
      mensaje = `Reposición sugerida elimina ${fmtTM(tmEliminadas)} sin cobertura y aumenta el OTIF en ${fmtNum(delta * 100)} pp.${techoTexto}`;
    } else if (tmDescubiertoActual <= 0) {
      mensaje = 'Sin problemas de cobertura en el filtro actual — el OTIF ya está en su máximo posible.';
    } else {
      mensaje = `La reposición sugerida no mejora el OTIF: la falta de cobertura no depende del inventario disponible.${techoTexto}`;
    }
    elNota.innerHTML = `
      <div style="background:${sInsight.color === 'var(--emerald-700)' ? 'var(--emerald-100)' : sInsight.color === 'var(--amber-700)' ? 'var(--amber-100)' : 'var(--red-100)'};
                  border-left:3px solid ${sInsight.color};border-radius:0 8px 8px 0;padding:10px 14px;
                  font-size:13px;font-weight:600;color:${sInsight.color};">${sInsight.emoji} ${mensaje}</div>
      <p style="margin:8px 0 0;font-size:11.5px;color:var(--gray-500);font-weight:400;">
        Estimado sobre ${conPedidos} SKU con pedidos vigentes (de ${skus.length} en el filtro) · TM en pedidos ${fmtTM(tmTotal)}.</p>`;
  }
}
function miniBarraPosicion(b, nfpRow) {
  const tog = b.tog || 1;
  const pctRoja = Math.min((b.tor / tog) * 100, 100);
  const pctAmarilla = Math.min((b.toy / tog) * 100, 100);
  const nfpVal = (nfpRow && nfpRow.nfp_tm != null) ? nfpRow.nfp_tm
    : (b.disponible_tm != null ? b.disponible_tm : (b.tor + (b.toy - b.tor) / 2));
  const pctNfp = Math.min(Math.max((nfpVal / tog) * 100, 0), 100);
  return `
    <div style="position:relative;width:104px;height:14px;border-radius:4px;display:flex;
                overflow:visible;border:1px solid var(--gray-200);" title="NFP: ${Math.round(nfpVal)} TM">
      <div style="width:${pctRoja}%;background:var(--red-100);border-radius:4px 0 0 4px;"></div>
      <div style="width:${pctAmarilla - pctRoja}%;background:var(--amber-100);"></div>
      <div style="width:${100 - pctAmarilla}%;background:var(--emerald-100);border-radius:0 4px 4px 0;"></div>
      <div style="position:absolute;left:${pctNfp}%;top:-3px;bottom:-3px;width:2px;background:var(--gray-900);border-radius:1px;"></div>
    </div>`;
}
function pillZonaNfp(zona) {
  const z = (zona || '').toUpperCase();
  const estilos = {
    ROJO: ['var(--red-100)', 'var(--red-700)'],
    AMARILLO: ['var(--amber-100)', '#8A5A0E'],
    VERDE: ['var(--emerald-100)', 'var(--emerald-700)'],
    SOBRE_TOG: ['var(--emerald-100)', 'var(--emerald-700)'],
  };
  const [bg, fg] = estilos[z] || ['var(--gray-100)', 'var(--gray-600)'];
  return `<span style="background:${bg};color:${fg};font-weight:700;font-size:11px;padding:2px 9px;border-radius:20px;white-space:nowrap;">${z || '—'}</span>`;
}
function renderMPChart(m, unidadDlt) {
  const canvas = document.getElementById('mpChart');
  if (!canvas) return;
  if (CHARTS.mp) { CHARTS.mp.destroy(); CHARTS.mp = null; }
  if (!m || !chartDisponible(canvas)) return;
  // Misma fórmula que la tarjeta "Inventario máximo" (mpTogSub en
  // renderMPKpis) y que el chart de PT (renderDDMRPChart): TOG÷ADU. Antes,
  // con un SKU seleccionado, esta etiqueta usaba cobertura_fisica_objetivo_dias
  // del backend (TOR + Verde/2) -- un número distinto que nunca iba a
  // coincidir con la tarjeta, que siempre usa TOG÷ADU. Son dos preguntas
  // distintas (cuánto dura el máximo vs. cuánto ocupa la bodega en
  // promedio); para que la tarjeta y el gráfico cuadren, los dos deben
  // responder la MISMA pregunta.
  const cobTog = diasCobertura(m.tog, m.adu);
  CHARTS.mp = new Chart(canvas, {
    type: 'bar',
    data: { labels: [`Hoy · lead time ${unidadDlt}`], datasets: [
      { label: 'Zona roja (mínimo)', data: [m.tor || 0], backgroundColor: '#B54B4B', stack: 'buffer',
        barPercentage: 0.55, categoryPercentage: 0.7, datalabels: { display: false } },
      { label: 'Zona amarilla', data: [m.yellow || 0], backgroundColor: '#D9A441', stack: 'buffer',
        barPercentage: 0.55, categoryPercentage: 0.7, datalabels: { display: false } },
      { label: 'Zona verde', data: [m.green || 0], backgroundColor: '#1F6E52', stack: 'buffer',
        barPercentage: 0.55, categoryPercentage: 0.7, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: false,
        datalabels: { display: true, anchor: 'end', align: 'top', offset: 2, color: '#0B2E24', font: { size: 11, weight: 'bold' },
          formatter: () => cobTog != null ? `${Math.round(m.tog)} TM\n${cobTog.toFixed(0)} d` : `${Math.round(m.tog)} TM` } },
    ]},
    options: { responsive: true, maintainAspectRatio: false, layout: { padding: { top: 30, bottom: 6 } },
      plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: {
          // Días de ESA zona sola (TM de la zona ÷ ADU), no acumulados --
          // la cobertura del TOG total ya está arriba, junto a las toneladas.
          label: c => { const cob = diasCobertura(c.parsed.y, m.adu);
            return cob != null ? `${c.dataset.label}: ${Math.round(c.parsed.y)} TM · ${fmtNum(cob)} d` : `${c.dataset.label}: ${Math.round(c.parsed.y)} TM`; },
          footer: () => '' } } },
      scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, ticks: { callback: v => v + ' TM' }, grid: { color: 'rgba(20,30,25,0.08)' } } } },
  });
}
function renderMensajeSinFormula(mensaje) {
  return `
    <div style="display:flex;align-items:center;gap:14px;padding:20px 22px;
                background:linear-gradient(135deg,var(--gray-100),var(--white));
                border:1px solid var(--gray-200);border-radius:10px;">
      <div style="width:40px;height:40px;border-radius:10px;background:var(--emerald-100);
                  display:flex;align-items:center;justify-content:center;flex-shrink:0;
                  font-size:18px;font-weight:800;color:var(--emerald-700);">i</div>
      <div>
        <p style="margin:0;font-size:13.5px;font-weight:bold;color:var(--gray-900);">Sin fórmula registrada esta semana</p>
        <p style="margin:2px 0 0;font-size:12.5px;color:var(--gray-600);">${mensaje || 'Formulaciones aún no ha compartido el BOM de este PT para el plan vigente.'}</p>
      </div>
    </div>`;
}
function mostrarBufferVacio(mostrar) {
  const canvas = document.getElementById('ddmrpChart');
  const vacio = document.getElementById('ddmrpChartVacio');
  if (canvas) canvas.style.display = mostrar ? 'none' : '';
  if (vacio) vacio.style.display = mostrar ? 'flex' : 'none';
}
function renderDDMRPDetalle() {
  const sku = DDMRP.skuSeleccionado;
  const titulo = document.getElementById('ddmrpSkuSeleccionado');
  const b = filasPtFuenteFinal().find(x =>
    String(x.sku || '').replace('.0', '').trim() === String(sku || '').replace('.0', '').trim());
  if (!b) {
    const mtoRow = skuMtoSeleccionado();
    if (mtoRow) { renderDetalleMto(mtoRow); return; }
    if (titulo) titulo.textContent = 'Selecciona un SKU en la tabla';
    mostrarBufferVacio(false);
    renderDDMRPChart(null);
    renderMpVinculadas(null);
    const cardPedidos = document.getElementById('detallePedidosCliente');
    if (cardPedidos) cardPedidos.style.display = 'none';
    return;
  }
  mostrarBufferVacio(false);
  // Sin filtro de búsqueda (FILTROS_DDMRP.sku vacío) el buffer muestra el
  // AGREGADO de todos los SKU, igual que ya hacía la tarjeta de pedidos
  // comprometidos (renderDetallePedidosClienteAgregado) -- antes el buffer
  // siempre mostraba un solo SKU (el primero de la lista) aunque no hubiera
  // nada filtrado.
  if (FILTROS_DDMRP.sku) {
    const desc = descripcionSku(b.sku);
    if (titulo) titulo.innerHTML = `<b>${b.sku}</b>${desc ? ' — ' + desc : ''}`;
    renderDDMRPChart(b.sku);
    renderMpVinculadas(b.sku);
    const nfp = nfpDeSku(b.sku);
    const disponibleFisico = nfp ? ((nfp.disponible_tm || 0) + (nfp.transito_tm || 0)) : (b.disponible_tm || 0);
    renderDetallePedidosCliente(b.sku, disponibleFisico);
  } else {
    if (titulo) titulo.innerHTML = `<b>Todos los SKU</b> <span style="color:var(--gray-600);font-weight:normal;">(${filasDDMRP().length} con buffer)</span>`;
    renderDDMRPChart(null);
    renderMpVinculadas(null);
    renderDetallePedidosClienteAgregado(filasDDMRP());
  }
}
/* Vista de detalle para un SKU MTO: sin buffer que graficar (el espacio
   del chart muestra un estado elegante en su lugar, vía mostrarBufferVacio),
   pero la fórmula (MP) y los pedidos comprometidos sí aplican igual que a
   un MTS -- un MTO también consume materia prima y tiene clientes. */
function renderDetalleMto(posRow) {
  const titulo = document.getElementById('ddmrpSkuSeleccionado');
  const desc = descripcionSku(posRow.sku);
  if (titulo) {
    titulo.innerHTML = `<b>${posRow.sku}</b>${desc ? ' — ' + desc : ''} ` +
      `<span class="badge-top" style="background:var(--gray-100);color:var(--gray-600);">MTO · sin buffer</span>`;
  }
  mostrarBufferVacio(true);
  const otifWrap = document.getElementById('otifGaugeWrap');
  const otifNota = document.getElementById('otifNota');
  if (otifWrap) {
    otifWrap.innerHTML = `<div style="text-align:center;color:var(--gray-400);font-size:13px;padding:30px 10px;">
      No aplica OTIF de buffer a un SKU MTO — se fabrica contra pedido, sin inventario objetivo.</div>`;
  }
  if (otifNota) otifNota.innerHTML = '';
  renderMpVinculadas(posRow.sku);
  const nfp = nfpDeSku(posRow.sku);
  const disponibleFisico = nfp ? ((nfp.disponible_tm || 0) + (nfp.transito_tm || 0)) : (posRow.stock_tm || 0);
  renderDetallePedidosCliente(posRow.sku, disponibleFisico);
}
async function renderDetallePedidosClienteAgregado(filas) {
  const card = document.getElementById('detallePedidosCliente');
  const titulo = document.getElementById('tituloPedidosCliente');
  const tbody = document.getElementById('tablaPedidosCliente');
  const badge = document.getElementById('badgeMargenRiesgo');
  const impacto = document.getElementById('impactoConsecuencia');
  const insight = document.getElementById('insightPedidosSku');
  if (!card || !tbody) return;
  const margenes = await cargarMargenSku();
  const skus = filas.slice(0, 200).map(x => x.sku);
  const todas = [];
  await Promise.all(skus.map(async s => {
    const nfp = nfpDeSku(s);
    const disponible = nfp ? ((nfp.disponible_tm || 0) + (nfp.transito_tm || 0)) : 0;
    const filasSku = await evaluarCoberturaConPlan(s, disponible);
    filasSku.forEach(f => todas.push({ ...f, sku: s }));
  }));
  if (!todas.length) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  todas.sort((a, c) => String(a.fecha_entrega).localeCompare(String(c.fecha_entrega)));
  if (titulo) titulo.textContent = `Pedidos Comprometidos — todos los SKU en el filtro (${skus.length})`;
  let acum = 0, tmNoATiempo = 0;
  const filasEval = todas.map(f => {
    const q = f.cantidad_tm || 0;
    acum += q;
    const margenUnit = margenes.get(String(f.sku).trim()) ?? null;
    const noATiempo = f.cubiertoTarde + f.sinCobertura;
    const vencido = parseFechaLocal(f.fecha_entrega) < hoy;
    if (noATiempo > 0.01) tmNoATiempo += noATiempo;
    return { ...f, _acum: acum, _valor: margenUnit != null ? margenUnit * q : null, _vencido: vencido };
  });
  const margenRiesgoTotal = filasEval.reduce((a, f) =>
    a + (((f.cubiertoTarde + f.sinCobertura) > 0.01 && f._valor != null) ? f._valor : 0), 0);
  if (badge) {
    badge.innerHTML = margenRiesgoTotal ? `<span class="pill pill-red">${fmtUSD(margenRiesgoTotal)} en riesgo de no llegar a tiempo</span>`
      : (tmNoATiempo > 0 ? `<span class="pill pill-amber">${fmtTM(tmNoATiempo)} en riesgo de no llegar a tiempo</span>` : `<span class="pill pill-green">Todo cubierto a tiempo</span>`);
  }
  tbody.innerHTML = filasEval.map(f => {
    const q = f.cantidad_tm || 0;
    const claseVencido = f._vencido ? 'style="background:rgba(181,75,75,.06);"' : '';
    let clase = 'fila-cubierta', pill = '<span class="pill pill-green">Cubierto (stock)</span>';
    if (f.sinCobertura > 0.01) { clase = 'fila-riesgo'; pill = '<span class="pill pill-red">Sin cobertura</span>'; }
    else if (f.cubiertoTarde > 0.01) { clase = 'fila-tarde'; pill = '<span class="pill pill-amber">Se produce, tarde</span>'; }
    else if (f.cubiertoPlan > 0.01) { clase = 'fila-plan'; pill = '<span class="pill pill-blue">Cubre con plan</span>'; }
    return `<tr class="${clase}" ${claseVencido}>
      <td>${f.fecha_entrega}${f._vencido ? ' ⚠️' : ''}</td>
      <td><b>${f.sku}</b> · ${(f.cliente_nombre || f.cliente || '—').toString().slice(0, 20)}</td>
      <td class="num-right">${fmtTM(q)}</td><td class="num-right" style="color:var(--gray-600);">${fmtTM(f._acum)}</td>
      <td class="num-right">${f._valor != null ? fmtUSD(f._valor) : '—'}</td>
      <td>${pill}</td>
    </tr>`;
  }).join('');
  if (impacto) impacto.style.display = 'none';
  if (insight) {
    const porCliente = new Map();
    filasEval.filter(f => (f.cubiertoTarde + f.sinCobertura) > 0.01).forEach(f => {
      const c = f.cliente_nombre || f.cliente;
      const a = porCliente.get(c) || { tm: 0, fecha: f.fecha_entrega };
      a.tm += (f.cubiertoTarde + f.sinCobertura);
      porCliente.set(c, a);
    });
    const top = [...porCliente.entries()].sort((a, c) => c[1].tm - a[1].tm)[0];
    insight.innerHTML = top
      ? `<b>Cliente más expuesto (todos los SKU):</b> ${top[0]} — ${fmtTM(top[1].tm)} en riesgo de no llegar a tiempo, primera entrega el ${top[1].fecha}.`
      : `Todos los pedidos vigentes en el filtro actual están cubiertos a tiempo (stock o plan de producción).`;
  }
}
function renderDDMRPChart(sku) {
  const canvas = document.getElementById('ddmrpChart');
  if (!canvas) return;
  if (CHARTS.ddmrp) { CHARTS.ddmrp.destroy(); CHARTS.ddmrp = null; }
  if (!chartDisponible(canvas)) return;
  const actualFinal = filasPtFuenteFinal();
  const futuros = DDMRP.bufferPt.filter(b => b.estado === 'ok' && b.mes_offset > 0);
  let meses;
  if (sku) {
    const key = String(sku).replace('.0', '').trim();
    const cero = actualFinal.find(b => String(b.sku).replace('.0', '').trim() === key);
    const resto = futuros.filter(b => String(b.sku).replace('.0', '').trim() === key)
      .sort((a, b) => a.mes_offset - b.mes_offset);
    meses = cero ? [cero, ...resto] : resto;
  } else {
    const cero = { mes_offset: 0, tor: 0, toy: 0, tog: 0, green: 0, yellow: 0, adu: 0 };
    actualFinal.forEach(b => {
      cero.tor += b.tor || 0; cero.toy += b.toy || 0; cero.tog += b.tog || 0;
      cero.green += b.green || 0; cero.yellow += b.yellow || 0; cero.adu += b.adu || 0;
    });
    const porMes = new Map();
    futuros.forEach(b => {
      const acc = porMes.get(b.mes_offset) || { mes_offset: b.mes_offset, tor: 0, toy: 0, tog: 0, green: 0, yellow: 0, adu: 0 };
      acc.tor += b.tor || 0; acc.toy += b.toy || 0; acc.tog += b.tog || 0;
      acc.green += b.green || 0; acc.yellow += b.yellow || 0; acc.adu += b.adu || 0;
      porMes.set(b.mes_offset, acc);
    });
    meses = [cero, ...[...porMes.values()].sort((a, b) => a.mes_offset - b.mes_offset)];
  }
  if (!meses.length) return;
  const ancla = getAncla();
  const labels = meses.map(b => { const d = offsetAFecha(b.mes_offset, ancla); return `${NOMBRES_MES[d.getMonth()]} '${String(d.getFullYear()).slice(2)}`; });
  CHARTS.ddmrp = new Chart(canvas, {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Zona roja (mínimo)', data: meses.map(b => b.tor || 0), backgroundColor: '#B54B4B', stack: 'buffer',
        barPercentage: 0.55, categoryPercentage: 0.7, datalabels: { display: false } },
      { label: 'Zona amarilla', data: meses.map(b => b.yellow || 0), backgroundColor: '#D9A441', stack: 'buffer',
        barPercentage: 0.55, categoryPercentage: 0.7, datalabels: { display: false } },
      { label: 'Zona verde', data: meses.map(b => b.green || 0), backgroundColor: '#1F6E52', stack: 'buffer',
        barPercentage: 0.55, categoryPercentage: 0.7, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: false,
        datalabels: { display: true, anchor: 'end', align: 'top', offset: 2, color: '#0B2E24', font: { size: 11, weight: 'bold' },
          formatter: (v, ctx) => { const i = ctx.dataIndex; const tog = meses[i].tog || 0; const cob = diasCobertura(tog, meses[i].adu);
            return cob != null ? `${Math.round(tog)} TM\n${cob.toFixed(0)} d` : `${Math.round(tog)} TM`; } } },
    ]},
    options: { responsive: true, maintainAspectRatio: false, layout: { padding: { top: 30, bottom: 6 } },
      plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: {
          // Días de ESA zona sola (TM de la zona ÷ ADU del mes), no
          // acumulados -- la cobertura del TOG total ya está arriba, junto
          // a las toneladas (y se repite en el footer para el mes del punto).
          label: c => { const i = c.dataIndex; const cob = diasCobertura(c.parsed.y, meses[i].adu);
            return cob != null ? `${c.dataset.label}: ${Math.round(c.parsed.y)} TM · ${fmtNum(cob)} d` : `${c.dataset.label}: ${Math.round(c.parsed.y)} TM`; },
          footer: items => { const i = items[0].dataIndex; const cob = diasCobertura(meses[i].tog, meses[i].adu); return cob != null ? `` : ''; } } } },
      scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, ticks: { callback: v => v + ' TM' }, grid: { color: 'rgba(20,30,25,0.08)' } } } },
  });
}
async function renderMpVinculadas(skuPt) {
  const cont = document.getElementById('mpVinculadasPt');
  if (!cont) return;
  if (!skuPt) { cont.innerHTML = '<p style="font-size:13px;color:var(--gray-400);">Selecciona un PT arriba.</p>'; return; }
  cont.innerHTML = '<p style="font-size:13px;color:var(--gray-400);">Cargando fórmula…</p>';
  try {
    const [r] = await Promise.all([
      fetchJSON('/api/v1/mp/por-pt/' + encodeURIComponent(skuPt)),
      cargarComprasMp(),
    ]);
    if (!r.mp || !r.mp.length) { cont.innerHTML = renderMensajeSinFormula(r.mensaje); return; }
    const porMp = new Map();
    r.mp.forEach(m => { const prev = porMp.get(m.sku_mp); if (!prev || (m.pct || 0) > (prev.pct || 0)) porMp.set(m.sku_mp, m); });
    const lista = [...porMp.values()].sort((a, b) => (b.pct || 0) - (a.pct || 0));
    cont.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;">
        ${lista.map(m => {
          // La señal (y su cantidad/fecha) SIEMPRE sale del plan de compras
          // (/api/v1/compras-mp/plan) -- nunca de un "TOG - stock" calculado
          // aparte (el /por-pt/ del backend trae cantidad_a_reponer_tm con
          // esa cuenta vieja; ya no se usa). Así esta tarjeta y el detalle
          // de la MP en Materia Prima nunca pueden decir cosas distintas.
          const c = compraMpDeSku(m.sku_mp);
          const color = c ? colorSenalCompra(c.senal) : 'var(--gray-500)';
          let detalle;
          if (!c) {
            detalle = '<span style="font-size:10.5px;background:var(--gray-100);color:var(--gray-600);padding:3px 9px;border-radius:5px;font-weight:bold;">Sin buffer</span>';
          } else {
            const fechaTxt = c.fecha_referencia ? fechaCorta(parseFechaLocal(c.fecha_referencia)) : null;
            let linea;
            if (c.senal === 'COMPRAR_HOY' || c.senal === 'COMPRAR_ANTES_DE') {
              linea = `Comprar ${fmtTM(c.cantidad_sugerida_tm)}${fechaTxt ? ' · ' + fechaTxt : ''}`;
            } else if (c.senal === 'NO_COMPRAR_HASTA') {
              linea = fechaTxt ? `No comprar hasta ${fechaTxt}` : 'No comprar en el horizonte';
            } else if (c.senal === 'CUBIERTO') {
              linea = 'Cubierto';
            } else {
              linea = SENAL_COMPRA_ETIQUETA[c.senal] || c.senal;
            }
            detalle = `${pillSenalCompra(c.senal)}<p style="font-size:11px;color:var(--gray-600);margin:8px 0 0;">${linea}</p>`;
          }
          return `
            <div class="mp-vinculada" data-mp="${m.sku_mp}" style="background:var(--white);border:1px solid var(--gray-200);border-radius:var(--radius);
                        box-shadow:0 1px 3px rgba(11,46,36,0.06);
                        padding:12px 14px;transition:box-shadow .15s,border-color .15s;cursor:pointer;"
                 onmouseover="this.style.boxShadow='0 4px 12px rgba(11,46,36,.10)';this.style.borderColor='${color}'"
                 onmouseout="this.style.boxShadow='0 1px 3px rgba(11,46,36,0.06)';this.style.borderColor='var(--gray-200)'">
              <p style="font-size:10.5px;color:var(--gray-400);margin:0 0 3px;letter-spacing:.2px;">${m.sku_mp} · ${m.pct}% de fórmula</p>
              <p style="font-size:12px;font-weight:bold;margin:0 0 8px;color:var(--gray-900);">${(m.mp_descripcion || '').slice(0, 26)}</p>
              <p style="font-size:17px;font-weight:800;margin:0 0 8px;color:${color};">${fmtTM(m.disponible_tm)}</p>
              ${detalle}
            </div>`;
        }).join('')}
      </div>`;
    cont.querySelectorAll('[data-mp]').forEach(el => el.addEventListener('click', () => irAMpEnBuffer(el.dataset.mp)));
  } catch (err) {
    cont.innerHTML = '<p style="font-size:13px;color:var(--gray-400);">Sin datos disponibles.</p>';
  }
}
function calcularOcupacionBodegaPt(skus) {
  const set = skus ? new Set(skus.map(s => String(s).replace('.0', '').trim())) : null;
  const invTotal = (NFP.datos || [])
    .filter(r => !set || set.has(String(r.sku).replace('.0', '').trim()))
    .reduce((a, r) => a + (r.disponible_tm || 0) + (r.transito_tm || 0), 0);
  const capPt = CFG.capacidadPtTm || (POS.resumen && POS.resumen.capacidad_bodega_tm) || null;
  return { invTotal, capPt, pct: capPt ? (invTotal / capPt) * 100 : null };
}
// Igual criterio que calcularOcupacionBodegaPt, pero para MP: inventario
// REAL en planta (inventarioMp -> misma fuente que Hoy), no el promedio de
// la política. El promedio de política (TOR + Verde/2) se muestra aparte.
function calcularOcupacionBodegaMp(skus) {
  const inv = inventarioMp(skus);
  const capMp = CFG.capacidadMpTm || null;
  return { invTotal: inv.totalTm, capMp, pct: capMp ? (inv.totalTm / capMp) * 100 : null };
}
function renderPosicionamiento() {
  const set = (id, t) => { const e = document.getElementById(id); if (e) e.textContent = t; };
  const r = POS.resumen;
  if (!r) return;
  const sinFiltro = !FILTROS_DDMRP.sku && FILTROS_DDMRP.rotacion === 'todos';
  const filasVisibles = filasDDMRP().filter(b => rotacionDeSku(b.sku) !== 'mto');
  const nSkuVisible = filasVisibles.length;
  const { invTotal, capPt, pct: pctPt } = calcularOcupacionBodegaPt(sinFiltro ? null : filasVisibles.map(b => b.sku));
  set('posOcupPt', pctPt != null ? `${pctPt.toFixed(0)}%` : '—');
  set('posOcupPtSub', capPt
    ? `${fmtTM(invTotal)} de ${fmtTM(capPt)} · inventario real (disponible + tránsito)` +
      (sinFiltro ? '' : ` · ${nSkuVisible} SKU en el filtro actual`)
    : 'define la capacidad de bodega PT en Configuración');
}
async function renderDetallePedidosCliente(sku, disponibleFisicoTM) {
  const card = document.getElementById('detallePedidosCliente');
  const titulo = document.getElementById('tituloPedidosCliente');
  const tbody = document.getElementById('tablaPedidosCliente');
  const badge = document.getElementById('badgeMargenRiesgo');
  const impacto = document.getElementById('impactoConsecuencia');
  const insight = document.getElementById('insightPedidosSku');
  if (!card || !tbody) return;
  const margenes = await cargarMargenSku();
  const margenUnit = margenes.get(String(sku).trim()) ?? null;
  const filas0 = await evaluarCoberturaConPlan(sku, disponibleFisicoTM);
  if (!filas0.length) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  const desc = descripcionSku(sku);
  if (titulo) {
    titulo.innerHTML = `Pedidos Comprometidos — <b>${sku}</b>` + (desc ? ` · ${desc}` : '') +
      ` <span style="color:var(--gray-600);font-weight:normal;">· disponible + tránsito ${fmtTM(disponibleFisicoTM)}` +
      (margenUnit != null ? ` · margen ${fmtUSD(margenUnit)}/TM</span>` : ' · sin margen registrado</span>');
  }
  let acum = 0, tmNoATiempo = 0, tmSinCobertura = 0, quiebre = null;
  const clientesAfectados = new Set();
  const filas = filas0.map((f, idx) => {
    const q = f.cantidad_tm || 0;
    acum += q;
    const noATiempo = f.cubiertoTarde + f.sinCobertura;
    const vencido = parseFechaLocal(f.fecha_entrega) < hoy;
    if (noATiempo > 0.01) {
      if (quiebre === null) quiebre = idx;
      tmNoATiempo += noATiempo;
      tmSinCobertura += f.sinCobertura;
      clientesAfectados.add(f.cliente_nombre || f.cliente);
    }
    return { ...f, _acum: acum, _valor: margenUnit != null ? margenUnit * q : null, _vencido: vencido };
  });
  const margenRiesgo = margenUnit != null ? margenUnit * tmNoATiempo : null;
  if (badge) {
    badge.innerHTML = margenRiesgo ? `<span class="pill pill-red">${fmtUSD(margenRiesgo)} en riesgo de no llegar a tiempo</span>`
      : (tmNoATiempo > 0 ? `<span class="pill pill-amber">${fmtTM(tmNoATiempo)} en riesgo de no llegar a tiempo</span>` : `<span class="pill pill-green">Todo cubierto a tiempo</span>`);
  }
  tbody.innerHTML = filas.map(f => {
    const q = f.cantidad_tm || 0;
    const claseVencido = f._vencido ? 'style="background:rgba(181,75,75,.06);"' : '';
    let clase = 'fila-cubierta', pill = '<span class="pill pill-green">Cubierto (stock)</span>';
    if (f.sinCobertura > 0.01) { clase = 'fila-riesgo'; pill = '<span class="pill pill-red">Sin cobertura</span>'; }
    else if (f.cubiertoTarde > 0.01) { clase = 'fila-tarde'; pill = '<span class="pill pill-amber">Se produce, tarde</span>'; }
    else if (f.cubiertoPlan > 0.01) { clase = 'fila-plan'; pill = '<span class="pill pill-blue">Cubre con plan</span>'; }
    return `<tr class="${clase}" ${claseVencido}>
      <td>${f.fecha_entrega}${f._vencido ? ' ⚠️' : ''}</td><td>${(f.cliente_nombre || f.cliente || '—').toString().slice(0, 30)}</td>
      <td class="num-right">${fmtTM(q)}</td><td class="num-right" style="color:var(--gray-600);">${fmtTM(f._acum)}</td>
      <td class="num-right">${f._valor != null ? fmtUSD(f._valor) : '—'}</td>
      <td>${pill}</td>
    </tr>`;
  }).join('');
  if (impacto) {
    if (tmNoATiempo > 0) {
      const f = filas[quiebre];
      impacto.style.display = 'block';
      const soloTarde = tmSinCobertura <= 0.01;
      impacto.innerHTML = `<b>El stock alcanza hasta el pedido del ${f.fecha_entrega}</b> (${f.cliente_nombre || f.cliente}).<br>
        Desde ahí, <b>${fmtTM(tmNoATiempo)}</b> no llegan a tiempo` +
        (soloTarde ? ` — el plan de producción sí las cubre, pero con el lead time de ${LT_PRODUCCION_HOY} días quedan disponibles después de la fecha de entrega` : `, de las cuales <b>${fmtTM(tmSinCobertura)}</b> no tienen respaldo ni en stock ni en el plan de producción vigente`) +
        `, afectando a <b>${clientesAfectados.size} cliente(s)</b>` +
        (margenRiesgo ? ` y representan <b>${fmtUSD(margenRiesgo)}</b> de margen` : '') + `.`;
    } else { impacto.style.display = 'none'; }
  }
  if (insight) {
    const porCliente = new Map();
    filas.filter(f => (f.cubiertoTarde + f.sinCobertura) > 0.01).forEach(f => {
      const c = f.cliente_nombre || f.cliente;
      const a = porCliente.get(c) || { tm: 0, fecha: f.fecha_entrega };
      a.tm += (f.cubiertoTarde + f.sinCobertura);
      porCliente.set(c, a);
    });
    const top = [...porCliente.entries()].sort((a, b) => b[1].tm - a[1].tm)[0];
    insight.innerHTML = top
      ? `<b>Cliente más expuesto:</b> ${top[0]} — ${fmtTM(top[1].tm)} en riesgo de no llegar a tiempo, primera entrega comprometida el ${top[1].fecha}. ` +
        `<span style="color:var(--gray-600);">Concentra el mayor volumen expuesto de este SKU; la decisión de a quién priorizar es comercial.</span>`
      : `Todos los pedidos vigentes de este SKU están cubiertos a tiempo (stock o plan de producción).`;
  }
}
// Única fuente de descripción de MP: mp_descripcion en ddmrp_buffer_mp --
// la misma que ve el planificador en Inventario Estratégico -> Materia
// Prima. Sin fallback a otro catálogo: mezclar fuentes mostraba producto
// terminado o material de empaque disfrazado de materia prima.
function descripcionMp(m) { return (m && m.mp_descripcion) || ''; }
// Solo MP identificables (con descripción) -- una sin mp_descripcion no es
// una materia prima reconocible en este catálogo y se excluye del buscador
// en vez de aparecer con el código pelado.
function opcionesMp() {
  return DDMRP.bufferMp
    .filter(m => descripcionMp(m))
    .map(m => ({ value: m.sku, label: `${m.sku} — ${descripcionMp(m)}` }));
}
function leerFiltrosMP() {
  const elMp = document.getElementById('mpBuscar_ddmrp');
  const elLt = document.getElementById('ltSel_mp');
  return { mp: elMp ? elMp.value.trim() : '', lt: elLt ? elLt.value : 'todos' };
}
function filasMP() {
  const f = FILTROS_MP;
  return DDMRP.bufferMp.filter(m => {
    if (m.estado !== 'ok') return false;
    if (!f.mp && !MP_DEL_PLAN_SET.has(m.sku)) return false;
    if (f.lt !== 'todos') {
      const d = m.dlt_dias;
      if (d == null) return false;
      if (f.lt === 'corto' && d > 20) return false;
      if (f.lt === 'medio' && (d <= 20 || d > 75)) return false;
      if (f.lt === 'largo' && d <= 75) return false;
    }
    if (f.mp) {
      const cod = f.mp.split(' — ')[0].trim().toLowerCase();
      if (!`${m.sku} ${descripcionMp(m)}`.toLowerCase().includes(cod)) return false;
    }
    return true;
  }).sort((a, b) => (b.tog || 0) - (a.tog || 0));
}
async function renderNodoMP() {
  FILTROS_MP = leerFiltrosMP();
  await Promise.all([mpConsumidasPorElPlan(), cargarFlujoNetoMp(), cargarProduccion(), cargarFlujoNetoMpTodas(), cargarComprasMp()]);
  const filas = filasMP();
  const mpBusqueda = FILTROS_MP.mp ? FILTROS_MP.mp.split(' — ')[0].trim().replace('.0', '') : '';
  if (mpBusqueda) {
    DDMRP.mpSeleccionada = mpBusqueda;
  } else {
    const sigueVisible = filas.some(m => m.sku === DDMRP.mpSeleccionada);
    if (!sigueVisible) DDMRP.mpSeleccionada = filas.length ? filas[0].sku : null;
  }
  const mpSinBuffer = mpSinBufferSeleccionada();
  if (mpSinBuffer) { await renderKpisMpSinBuffer(mpSinBuffer); } else { renderMPKpis(filas); }
  renderMPTabla(filas);
  renderMPDetalle();
  renderMpLocalTabla();
}

/* Igual que un SKU MTO del lado PT: una MP sin buffer (proveedor local,
   sin lead time, o sin historia de consumo) nunca aparece en filasMP()
   (exige estado === 'ok'), así que buscarla dejaba todo en '—'. Estas
   funciones le dan el mismo tratamiento que a un MTO: inventario actual,
   consumo diario y % de bodega, sin pretender mostrar un buffer que esta
   MP no tiene. */
function mpSinBufferSeleccionada() {
  const sku = DDMRP.mpSeleccionada;
  if (!sku) return null;
  const key = String(sku).replace('.0', '').trim();
  const row = (DDMRP.bufferMp || []).find(m => String(m.sku).replace('.0', '').trim() === key);
  return (row && row.estado !== 'ok') ? row : null;
}
async function renderKpisMpSinBuffer(row) {
  const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  const inv = inventarioMp([row.sku]);
  const { invTotal, capMp, pct } = calcularOcupacionBodegaMp([row.sku]);
  set('mpTog', '—');
  set('mpTogSub', 'MP sin buffer calculable (proveedor local o sin historia)');
  set('mpTor', '—');
  set('mpTorSub', 'MP sin buffer: sin inventario mínimo');
  set('mpInvActual', fmtTM(inv.totalTm));
  set('mpInvActualSub', 'disponible + bloqueado + inspección + tránsito, hoy');
  set('mpAOrdenar', row.adu != null ? fmtNum(row.adu, 2) + ' TM/d' : '—');
  set('mpAOrdenarSub', 'consumo diario estimado (sin buffer que ordenar)');
  set('posOcupMp', pct != null ? `${fmtNum(pct, 1)}%` : '—');
  set('posOcupMpSub', capMp
    ? `${fmtTM(invTotal)} de ${fmtTM(capMp)} · ocupación de esta MP en bodega`
    : 'define la capacidad de bodega MP en Configuración');
  set('posOcupMpPolitica', '');
}
function renderMpLocalTabla() {
  const tbody = document.getElementById('mpLocalTabla');
  const badge = document.getElementById('mpLocalConteoBadge');
  if (!tbody) return;
  // Sin descripción no es identificable como materia prima real -- se
  // excluye en vez de mostrarse con el código pelado (mismo criterio que
  // el resto del catálogo de MP).
  const filas = (DDMRP.bufferMp || []).filter(m => m.estado !== 'ok' && descripcionMp(m));
  if (badge) badge.textContent = `${filas.length} MP`;
  if (!filas.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:var(--gray-400);">Todas las MP identificables tienen buffer calculable.</td></tr>`;
    return;
  }
  tbody.innerHTML = filas.map(m => {
    const key = String(m.sku).replace('.0', '').trim();
    const stockReal = NFP_MP_TODAS.mapa.get(key);
    const stock = stockReal ? (stockReal.total_tm || 0) : 0;
    const desc = descripcionMp(m);
    // El motivo lo calcula el backend (motivo_sin_buffer) -- no se recalcula
    // aquí para no marcar "Sin historia" a MP que en realidad tienen lead
    // time 0 u otra causa real.
    if (m.motivo_sin_buffer === undefined) avisarCampoFaltante('motivo_sin_buffer');
    const motivo = m.motivo_sin_buffer || '—';
    return `<tr class="clickable-row" data-mp="${m.sku}">
      <td><b>${m.sku}</b></td>
      <td>${desc}</td>
      <td class="num-right">${m.adu != null ? fmtNum(m.adu, 2) : '—'}</td>
      <td class="num-right">${fmtTM(stock)}</td>
      <td style="font-size:12px;color:var(--gray-600);">${motivo}</td>
      <td style="text-align:center;"><button class="btn btn-secondary btn-small" data-definir-buffer-mp="${m.sku}">Definir buffer</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr[data-mp]').forEach(tr => tr.addEventListener('click', (e) => {
    if (e.target.closest('[data-definir-buffer-mp]')) return;
    irAMpEnBuffer(tr.dataset.mp);
  }));
  tbody.querySelectorAll('[data-definir-buffer-mp]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      abrirModalEditarBufferMp(btn.dataset.definirBufferMp, { vacio: true });
    });
  });
}
function renderMPKpis(filas) {
  const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  const tog = filas.reduce((a, m) => a + (m.tog || 0), 0);
  const tor = filas.reduce((a, m) => a + (m.tor || 0), 0);
  const aduF = filas.reduce((a, m) => a + (m.adu || 0), 0);
  set('mpTog', fmtTM(tog)); set('mpTor', fmtTM(tor));
  const cobTog = diasCobertura(tog, aduF);
  const cobTor = diasCobertura(tor, aduF);
  set('mpTogSub', cobTog != null ? `${filas.length} MP · ${fmtNum(cobTog)} días de consumo` : `${filas.length} MP · suma del TOG`);
  set('mpTorSub', cobTor != null ? `${fmtNum(cobTor)} días de protección contra quiebre` : 'protección contra falta de insumo (TOR)');

  // Inventario actual: MISMA función/fuente que Hoy (inventarioMp). Sin
  // filtro de búsqueda, todas las MP (también sin buffer, igual que Hoy);
  // con filtro, restringido a las MP visibles en la tabla.
  const sinFiltro = !FILTROS_MP.mp && FILTROS_MP.lt === 'todos';
  const skusFiltro = sinFiltro ? null : filas.map(m => m.sku);
  const inv = inventarioMp(skusFiltro);
  set('mpInvActual', fmtTM(inv.totalTm));
  set('mpInvActualSub', inv.dias != null ? `${fmtNum(inv.dias)} días de cobertura hoy` : 'disponible + bloqueado + inspección + tránsito, hoy');

  const skusSet = new Set(filas.map(m => String(m.sku).replace('.0', '').trim()));
  const filasOrden = (NFP_MP.datos || []).filter(r =>
    skusSet.has(String(r.sku).replace('.0', '').trim()) && (r.cantidad_a_ordenar_tm || 0) > 0);
  const aOrdenar = filasOrden.reduce((a, r) => a + (r.cantidad_a_ordenar_tm || 0), 0);
  set('mpAOrdenar', aOrdenar > 0 ? fmtTM(aOrdenar) : '0 TM');
  set('mpAOrdenarSub', `${filasOrden.length} MP requieren orden · según flujo neto`);

  // % Ocupación MP (bodega): inventario REAL en planta, igual criterio que
  // PT (calcularOcupacionBodegaPt). El promedio de la política (TOR +
  // Verde/2) se muestra aparte para no confundir "lo que hay" con "lo que
  // pide el buffer".
  const { invTotal, capMp, pct: pctMp } = calcularOcupacionBodegaMp(skusFiltro);
  const baseMp = (DDMRP.bufferMp || []).filter(m => m.estado === 'ok'
    && (sinFiltro || skusSet.has(String(m.sku).replace('.0', '').trim())));
  const promMp = baseMp.reduce((a, m) => a + (m.tor || 0) + Math.max((m.tog || 0) - (m.toy || 0), 0) / 2, 0);
  const pctPolitica = capMp ? (promMp / capMp) * 100 : null;
  const nRacionadas = baseMp.filter(m => m.tog_racionado).length;
  set('posOcupMp', pctMp != null ? `${pctMp.toFixed(0)}%` : '—');
  set('posOcupMpSub', capMp
    ? `${fmtTM(invTotal)} de ${fmtTM(capMp)} · inventario real (en planta)`
    : 'define la capacidad de bodega MP en Configuración');
  set('posOcupMpPolitica', (capMp && pctPolitica != null)
    ? `política: ${pctPolitica.toFixed(0)}% (TOR + Verde/2)` + (nRacionadas ? ` · ${nRacionadas} MP con Zona Verde recortada por bodega` : '')
    : '');
}
function renderMPTabla(filas) {
  const tbody = document.getElementById('mpTabla');
  if (!tbody) return;
  if (!filas.length) { tbody.innerHTML = `<tr><td colspan="13" style="color:var(--gray-400);">Sin materias primas con buffer calculable para este filtro</td></tr>`; return; }
  tbody.innerHTML = filas.map(m => {
    const sel = m.sku === DDMRP.mpSeleccionada ? ' style="background:var(--emerald-50);"' : '';
    const lt = m.dlt_dias;
    const colorLt = lt == null ? 'var(--gray-400)' : lt <= 20 ? 'var(--emerald-700)' : lt <= 75 ? '#8A5A0E' : 'var(--red-700)';
    const nfpRow = nfpMpDeSku(m.sku);
    const key = String(m.sku).replace('.0', '').trim();
    const stockReal = NFP_MP_TODAS.mapa.get(key);
    const esCritico = nfpRow && nfpRow.alerta_onhand === 'CRITICO';
    const manual = m.ajuste_manual
      ? '<span class="badge-top" style="background:var(--gray-100);color:var(--gray-600);margin-left:4px;">Manual</span>' : '';
    const descCompleta = (descripcionMp(m) || '—').toString();
    const descCorta = descCompleta.length > 34 ? descCompleta.slice(0, 34) + '…' : descCompleta;
    if (nfpRow && nfpRow.en_mano_tm === undefined) avisarCampoFaltante('en_mano_tm');
    if (nfpRow && nfpRow.oc_en_camino_tm === undefined) avisarCampoFaltante('oc_en_camino_tm');
    const disponibleTxt = nfpRow && nfpRow.en_mano_tm != null ? fmtTM(nfpRow.en_mano_tm) : '—';
    const bloqueadoTxt = stockReal && stockReal.bloqueado_tm != null ? fmtTM(stockReal.bloqueado_tm) : '0 TM';
    const inspeccionTxt = stockReal && stockReal.inspeccion_tm != null ? fmtTM(stockReal.inspeccion_tm) : '0 TM';
    const transitoSapTxt = nfpRow && nfpRow.transito_sap_tm != null ? fmtTM(nfpRow.transito_sap_tm) : '0 TM';
    const tipDisponible = `Incluye ${transitoSapTxt} de tránsito SAP (ya en planta). No incluye bloqueado ${bloqueadoTxt} ni inspección ${inspeccionTxt}`
      .replace(/"/g, '&quot;');
    return `<tr class="clickable-row" data-mp="${m.sku}"${sel}>
      <td><b>${m.sku}</b>${manual}</td>
      <td title="${descCompleta.replace(/"/g, '&quot;')}">${descCorta}</td>
      <td class="num-right" style="white-space:nowrap;">${m.adu != null ? fmtNum(m.adu, 2) : '—'}</td>
      <td class="num-right" style="color:${colorLt};white-space:nowrap;">${lt != null ? Math.round(lt) + ' d' : '—'}</td>
      <td>${miniBarraPosicion(m, nfpRow)}</td>
      <td class="num-right" style="white-space:nowrap;">${nfpRow && nfpRow.nfp_tm != null ? fmtTM(nfpRow.nfp_tm) : '—'}</td>
      <td class="num-right" style="white-space:nowrap;" title="${tipDisponible}">${disponibleTxt}</td>
      <td class="num-right" style="white-space:nowrap;">${nfpRow && nfpRow.oc_en_camino_tm != null ? fmtTM(nfpRow.oc_en_camino_tm) : '—'}</td>
      <td class="num-right" style="white-space:nowrap;">${nfpRow && nfpRow.demanda_calificada_tm != null ? fmtTM(nfpRow.demanda_calificada_tm) : '—'}</td>
      <td class="num-right" style="white-space:nowrap;">${nfpRow && nfpRow.cantidad_a_ordenar_tm ? fmtTM(nfpRow.cantidad_a_ordenar_tm) : '—'}</td>
      <td>${esCritico ? '<span style="color:var(--red-700);font-weight:700;">Alerta</span>' : '<span style="color:var(--gray-400);">OK</span>'}</td>
      <td>${(() => { const c = compraMpDeSku(m.sku); return c ? pillSenalCompra(c.senal, c.fecha_referencia ? fechaCorta(parseFechaLocal(c.fecha_referencia)) : null) : '<span style="color:var(--gray-400);">—</span>'; })()}</td>
      <td style="text-align:center;"><button class="btn btn-secondary btn-small" data-editar-buffer-mp="${m.sku}">Editar</button></td>
    </tr>`;
  }).join('');
  // Igual que PT (renderDDMRPTabla): clic en la fila llena el buscador y
  // vuelve a renderizar todo el nodo, así las tarjetas KPI también se
  // filtran a esta MP (antes solo se actualizaban el buffer y la tabla).
  tbody.querySelectorAll('tr[data-mp]').forEach(tr => {
    tr.addEventListener('click', () => {
      const sku = tr.dataset.mp;
      DDMRP.mpSeleccionada = sku;
      const inp = document.getElementById('mpBuscar_ddmrp');
      if (inp) {
        const fila = (DDMRP.bufferMp || []).find(x => x.sku === sku);
        const d = fila ? descripcionMp(fila) : '';
        inp.value = d ? `${sku} — ${d}` : String(sku);
      }
      renderNodoMP();
    });
  });
  tbody.querySelectorAll('[data-editar-buffer-mp]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      abrirModalEditarBufferMp(btn.dataset.editarBufferMp);
    });
  });
}
/* Modal único "Editar buffer — MP": TOR/TOY/TOG + ZAF/LTAF + DAF heredado,
   con evaluación en vivo contra /overrides-mp/evaluar y revertir a DDMRP.
   Reemplaza el modal anterior de solo-factores. opts.vacio=true lo abre con
   TOR/TOY/TOG en blanco (flujo "Definir buffer" desde MP sin buffer). */
// "DAF heredado" NO es "qué PT consume esta MP en su fórmula" (eso se ve en
// la tabla "Consumo proyectado de MP por producto", a la derecha del
// buffer, y puede tener muchísimos PT). Esta caja solo avisa si algún PT
// tiene un DAF MANUAL activo (≠ 1.0) que además ajustaría la demanda de
// esta MP -- por eso puede decir "ninguno" aunque la MP esté en decenas de
// fórmulas: hoy ningún PT tiene un DAF manual puesto.
const MENSAJE_SIN_DAF_HEREDADO = 'Ningún PT tiene hoy un DAF manual (≠ 1.0) activo que ajuste la demanda de esta MP. '
  + 'Esto no significa que ningún PT la consuma en su fórmula — eso se ve en "Consumo proyectado de MP por producto".';
const MP_BUFFER_EDIT = { sku: null, actuales: null, factoresActuales: null, evaluacion: null, leadTimeActual: null, leadTimeSistema: null };
async function abrirModalEditarBufferMp(sku, opts) {
  opts = opts || {};
  const clave = String(sku).replace('.0', '').trim();
  const row = (DDMRP.bufferMp || []).find(m => String(m.sku).replace('.0', '').trim() === clave);
  MP_BUFFER_EDIT.sku = sku;
  MP_BUFFER_EDIT.actuales = (row && row.estado === 'ok' && !opts.vacio) ? { tor: row.tor, toy: row.toy, tog: row.tog } : null;
  MP_BUFFER_EDIT.evaluacion = null;
  const titulo = document.getElementById('modalEditarBufferMpTitulo');
  const desc = descripcionMp(row || {}) || descripcionSku(sku) || '';
  if (titulo) titulo.textContent = `Editar buffer — MP ${sku}${desc ? ' · ' + desc : ''}`;
  document.getElementById('modalBufferMpTor').value = MP_BUFFER_EDIT.actuales ? (MP_BUFFER_EDIT.actuales.tor ?? '') : '';
  document.getElementById('modalBufferMpToy').value = MP_BUFFER_EDIT.actuales ? (MP_BUFFER_EDIT.actuales.toy ?? '') : '';
  document.getElementById('modalBufferMpTog').value = MP_BUFFER_EDIT.actuales ? (MP_BUFFER_EDIT.actuales.tog ?? '') : '';
  const evalBox = document.getElementById('modalBufferMpEval');
  if (evalBox) evalBox.innerHTML = '';
  const err = document.getElementById('modalMpFactoresError');
  if (err) err.style.display = 'none';
  const btnConfirmar = document.getElementById('modalBufferMpConfirmar');
  if (btnConfirmar) { btnConfirmar.disabled = true; btnConfirmar.textContent = 'Confirmar cambio'; }
  const btnRevertir = document.getElementById('modalBufferMpRevertir');
  if (btnRevertir) btnRevertir.style.display = (row && row.ajuste_manual) ? '' : 'none';
  const heredado = document.getElementById('modalBufferMpDafHeredado');
  if (heredado) heredado.textContent = 'Cargando…';
  document.getElementById('modalBufferMpZaf').value = 1.0;
  document.getElementById('modalBufferMpLtaf').value = 1.0;
  MP_BUFFER_EDIT.factoresActuales = { zaf: 1.0, ltaf: 1.0 };
  document.getElementById('modalEditarBufferMp').classList.add('open');
  try {
    const r = await fetchJSON('/api/v1/ddmrp/factores-manuales-mp/' + encodeURIComponent(sku));
    document.getElementById('modalBufferMpZaf').value = r.zaf ?? 1.0;
    document.getElementById('modalBufferMpLtaf').value = r.ltaf ?? 1.0;
    MP_BUFFER_EDIT.factoresActuales = { zaf: r.zaf ?? 1.0, ltaf: r.ltaf ?? 1.0 };
    if (heredado) {
      heredado.textContent = (r.delta_adu_daf_pt && Math.abs(r.delta_adu_daf_pt) > 0.001)
        ? `ADU ${r.delta_adu_daf_pt > 0 ? '+' : ''}${fmtNum(r.delta_adu_daf_pt, 2)} TM/día por: ${r.pt_con_daf || '—'}`
        : MENSAJE_SIN_DAF_HEREDADO;
    }
  } catch (err2) {
    if (heredado) heredado.textContent = MENSAJE_SIN_DAF_HEREDADO;
  }
  // Lead time real (corrección del dato, no una política) -- endpoint propio
  // /ddmrp/lead-time-manual-mp, independiente del override de TOR/TOY/TOG.
  // Si el endpoint todavía no existe en el backend, el catch deja el campo
  // vacío y muestra el lead time del buffer actual como "del sistema".
  const sistemaLt = document.getElementById('modalBufferMpLeadTimeSistema');
  const manualLt = document.getElementById('modalBufferMpLeadTimeManual');
  try {
    const lt = await fetchJSON('/api/v1/ddmrp/lead-time-manual-mp/' + encodeURIComponent(sku));
    const sistema = lt.lead_time_sistema_dias != null ? lt.lead_time_sistema_dias
      : (row && row.dlt_dias != null ? row.dlt_dias : null);
    // El campo nunca queda vacío: si no hay manual, se precarga con el del
    // sistema (editable igual) -- vacío con solo un placeholder confundía,
    // parecía "no hay dato" cuando sí lo había.
    if (manualLt) manualLt.value = lt.lead_time_manual_dias ?? (sistema != null ? Math.round(sistema) : '');
    if (sistemaLt) sistemaLt.textContent = sistema != null ? Math.round(sistema) : '—';
    MP_BUFFER_EDIT.leadTimeActual = lt.lead_time_manual_dias ?? null;
    MP_BUFFER_EDIT.leadTimeSistema = sistema;
  } catch (err3) {
    const sistema = row && row.dlt_dias != null ? row.dlt_dias : null;
    if (manualLt) manualLt.value = sistema != null ? Math.round(sistema) : '';
    if (sistemaLt) sistemaLt.textContent = sistema != null ? Math.round(sistema) : '—';
    MP_BUFFER_EDIT.leadTimeActual = null;
    MP_BUFFER_EDIT.leadTimeSistema = sistema;
  }
  if (!opts.vacio) {
    await evaluarBufferModalMp();
  } else if (evalBox) {
    evalBox.innerHTML = '<p style="font-size:12.5px;color:var(--gray-400);margin-top:8px;">Completa TOR/TOY/TOG para evaluar el impacto en bodega.</p>';
  }
}
function cerrarModalEditarBufferMp() {
  const overlay = document.getElementById('modalEditarBufferMp');
  if (overlay) overlay.classList.remove('open');
  MP_BUFFER_EDIT.sku = null; MP_BUFFER_EDIT.actuales = null; MP_BUFFER_EDIT.evaluacion = null;
  MP_BUFFER_EDIT.factoresActuales = null; MP_BUFFER_EDIT.leadTimeActual = null; MP_BUFFER_EDIT.leadTimeSistema = null;
}
async function evaluarBufferModalMp() {
  if (!MP_BUFFER_EDIT.sku) return;
  const tor = parseFloat(document.getElementById('modalBufferMpTor').value) || 0;
  const toy = parseFloat(document.getElementById('modalBufferMpToy').value) || 0;
  const tog = parseFloat(document.getElementById('modalBufferMpTog').value) || 0;
  const evalBox = document.getElementById('modalBufferMpEval');
  const btnConfirmar = document.getElementById('modalBufferMpConfirmar');
  if (evalBox) evalBox.innerHTML = '<p style="font-size:12.5px;color:var(--gray-400);margin-top:8px;">Evaluando…</p>';
  if (btnConfirmar) btnConfirmar.disabled = true;
  try {
    const resp = await fetch(API_BASE + '/api/v1/ddmrp/overrides-mp/evaluar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku: MP_BUFFER_EDIT.sku, tor, toy, tog }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const ev = await resp.json();
    MP_BUFFER_EDIT.evaluacion = ev;
    const va = ev.valores_actuales, vp = ev.valores_propuestos;
    const emoji = ev.semaforo === 'verde' ? '🟢' : ev.semaforo === 'rojo' ? '🔴' : '🟡';
    const diff = `
      <div style="font-size:12.5px;margin-top:10px;color:var(--gray-600);">
        <b>Diff:</b> TOR ${fmtTM(va.tor)} → ${fmtTM(vp.tor)} ·
        TOY ${fmtTM(va.toy)} → ${fmtTM(vp.toy)} ·
        TOG ${fmtTM(va.tog)} → ${fmtTM(vp.tog)}
      </div>`;
    const bodega = (ev.inventario_promedio_total_actual_tm != null && ev.capacidad_mp_tm)
      ? `<div style="font-size:12.5px;margin-top:4px;color:var(--gray-600);">
          <b>Bodega MP (inventario promedio):</b> ${fmtTM(ev.inventario_promedio_total_actual_tm)} → ${fmtTM(ev.inventario_promedio_total_propuesto_tm)} TM de ${fmtTM(ev.capacidad_mp_tm)} TM
        </div>` : '';
    if (evalBox) evalBox.innerHTML = `<div class="modal-eval-box ${ev.semaforo}">${emoji} ${ev.mensaje}</div>${diff}${bodega}`;
    const valido = tor >= 0 && tor <= toy && toy <= tog;
    if (btnConfirmar) btnConfirmar.disabled = !valido;
  } catch (err) {
    console.error('No se pudo evaluar el cambio de buffer MP', err);
    if (evalBox) evalBox.innerHTML = '<p style="font-size:12.5px;color:var(--red-700);margin-top:8px;">No se pudo evaluar el cambio — revisa la conexión con el backend.</p>';
  }
}
async function confirmarBufferModalMp() {
  if (!MP_BUFFER_EDIT.sku || !MP_BUFFER_EDIT.evaluacion) return;
  const btn = document.getElementById('modalBufferMpConfirmar');
  const err = document.getElementById('modalMpFactoresError');
  const tor = parseFloat(document.getElementById('modalBufferMpTor').value) || 0;
  const toy = parseFloat(document.getElementById('modalBufferMpToy').value) || 0;
  const tog = parseFloat(document.getElementById('modalBufferMpTog').value) || 0;
  const zaf = parseFloat(document.getElementById('modalBufferMpZaf').value);
  const ltaf = parseFloat(document.getElementById('modalBufferMpLtaf').value);
  const act = MP_BUFFER_EDIT.actuales;
  const cambioOverride = !act || tor !== act.tor || toy !== act.toy || tog !== act.tog;
  const facAct = MP_BUFFER_EDIT.factoresActuales || { zaf: 1, ltaf: 1 };
  const fueraDeRango = v => isNaN(v) || v < 0.5 || v > 3.0;
  const cambioFactores = !fueraDeRango(zaf) && !fueraDeRango(ltaf)
    && (Math.abs(zaf - (facAct.zaf ?? 1)) > 0.001 || Math.abs(ltaf - (facAct.ltaf ?? 1)) > 0.001);
  if (err) err.style.display = 'none';
  if (btn) { btn.disabled = true; btn.textContent = 'Guardando…'; }
  try {
    let guardoOverride = false;
    if (cambioOverride) {
      const resp = await fetch(API_BASE + '/api/v1/ddmrp/overrides-mp/guardar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku: MP_BUFFER_EDIT.sku, tor, toy, tog, actualizado_por: 'planificador' }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      guardoOverride = true;
    }
    if (cambioFactores) {
      const clave = String(MP_BUFFER_EDIT.sku).replace('.0', '').trim();
      await fetch(API_BASE + '/api/v1/ddmrp/factores-manuales-mp', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku: MP_BUFFER_EDIT.sku, zaf, ltaf, actualizado_por: 'planificador' }),
      });
      DDMRP.factoresMp.set(clave, { sku: clave, zaf, ltaf });
    }
    DDMRP.cargado = false;
    await cargarDDMRP();
    cerrarModalEditarBufferMp();
    renderMPTabla(filasMP());
    renderMPDetalle();
    renderMPKpis(filasMP());
    renderMpLocalTabla();
    const aviso = guardoOverride
      ? ' — con un ajuste manual vigente, ZAF y LTAF no cambian este buffer hasta que lo reviertas.'
      : '';
    flashGuardado(`Buffer MP actualizado ✓ — el flujo neto se recalcula en la próxima corrida${aviso}`);
  } catch (e) {
    if (err) { err.style.display = 'block'; err.textContent = e.message || 'No se pudo guardar.'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Confirmar cambio'; }
  }
}
async function revertirBufferModalMp() {
  if (!MP_BUFFER_EDIT.sku) return;
  if (!confirm('Se quitará tu ajuste manual y la MP volverá a la política DDMRP.')) return;
  try {
    const resp = await fetch(API_BASE + '/api/v1/ddmrp/overrides-mp/revertir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku: MP_BUFFER_EDIT.sku, actualizado_por: 'planificador' }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    DDMRP.cargado = false;
    await cargarDDMRP();
    cerrarModalEditarBufferMp();
    renderMPTabla(filasMP());
    renderMPDetalle();
    renderMPKpis(filasMP());
    renderMpLocalTabla();
    flashGuardado('Ajuste manual revertido ✓ — vuelve a la política DDMRP');
  } catch (e) {
    alert('No se pudo revertir el ajuste. Revisa la conexión con el backend e intenta de nuevo.');
  }
}
function initModalEditarBufferMp() {
  const overlay = document.getElementById('modalEditarBufferMp');
  if (!overlay || overlay.dataset.wired) return;
  overlay.dataset.wired = '1';
  document.getElementById('modalEditarBufferMpCerrar').addEventListener('click', cerrarModalEditarBufferMp);
  document.getElementById('modalBufferMpCancelar').addEventListener('click', cerrarModalEditarBufferMp);
  document.getElementById('modalBufferMpConfirmar').addEventListener('click', confirmarBufferModalMp);
  document.getElementById('modalBufferMpRevertir').addEventListener('click', revertirBufferModalMp);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) cerrarModalEditarBufferMp(); });
  let temporizadorEval;
  ['modalBufferMpTor', 'modalBufferMpToy', 'modalBufferMpTog'].forEach(id => {
    document.getElementById(id).addEventListener('input', () => {
      clearTimeout(temporizadorEval);
      temporizadorEval = setTimeout(evaluarBufferModalMp, 400);
    });
  });
  ['Zaf', 'Ltaf'].forEach(nombre => {
    const btn = document.getElementById('modalBufferMp' + nombre + 'Reset');
    if (btn) btn.addEventListener('click', () => { document.getElementById('modalBufferMp' + nombre).value = 1.0; });
  });
  document.getElementById('modalBufferMpUsarRecomendacion').addEventListener('click', () => {
    const rec = MP_BUFFER_EDIT.evaluacion && MP_BUFFER_EDIT.evaluacion.recomendacion_sistema;
    if (!rec) return;
    document.getElementById('modalBufferMpTor').value = rec.tor;
    document.getElementById('modalBufferMpToy').value = rec.toy;
    document.getElementById('modalBufferMpTog').value = rec.tog;
    evaluarBufferModalMp();
  });
  // Lead time real: se guarda solo (al salir del campo) y es independiente
  // del botón "Confirmar cambio" de TOR/TOY/TOG -- así también sirve para
  // una MP sin buffer (Definir buffer), donde todavía no hay TOR/TOY/TOG
  // válidos que confirmar pero sí puede hacer falta corregir el lead time.
  const inputLt = document.getElementById('modalBufferMpLeadTimeManual');
  if (inputLt) {
    inputLt.addEventListener('change', async () => {
      if (!MP_BUFFER_EDIT.sku) return;
      const val = inputLt.value !== '' ? parseFloat(inputLt.value) : null;
      if (val === MP_BUFFER_EDIT.leadTimeActual) return;
      try {
        const resp = await fetch(API_BASE + '/api/v1/ddmrp/lead-time-manual-mp', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sku: MP_BUFFER_EDIT.sku, lead_time_dias: val, actualizado_por: 'planificador' }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        MP_BUFFER_EDIT.leadTimeActual = val;
        flashGuardado('Lead time guardado ✓ — se aplica en la próxima corrida de ajustar_buffer_mp.py');
      } catch (e) {
        alert('No se pudo guardar el lead time. Revisa la conexión con el backend (requiere el endpoint /api/v1/ddmrp/lead-time-manual-mp).');
      }
    });
  }
  const btnLtRevertir = document.getElementById('modalBufferMpLeadTimeRevertir');
  if (btnLtRevertir) {
    btnLtRevertir.addEventListener('click', async () => {
      if (!MP_BUFFER_EDIT.sku) return;
      try {
        const resp = await fetch(API_BASE + '/api/v1/ddmrp/lead-time-manual-mp', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sku: MP_BUFFER_EDIT.sku, lead_time_dias: null, actualizado_por: 'planificador' }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const sistema = MP_BUFFER_EDIT.leadTimeSistema;
        if (inputLt) inputLt.value = sistema != null ? Math.round(sistema) : '';
        MP_BUFFER_EDIT.leadTimeActual = null;
        flashGuardado('Lead time revertido al del sistema ✓ — se aplica en la próxima corrida de ajustar_buffer_mp.py');
      } catch (e) {
        alert('No se pudo revertir el lead time. Revisa la conexión con el backend (requiere el endpoint /api/v1/ddmrp/lead-time-manual-mp).');
      }
    });
  }
}
function mostrarBufferMpVacio(mostrar) {
  const canvas = document.getElementById('mpChart');
  const vacio = document.getElementById('mpChartVacio');
  if (canvas) canvas.style.display = mostrar ? 'none' : '';
  if (vacio) vacio.style.display = mostrar ? 'flex' : 'none';
}
function renderMPDetalle() {
  const titulo = document.getElementById('mpSeleccionado');
  const m = DDMRP.bufferMp.find(x => x.sku === DDMRP.mpSeleccionada && x.estado === 'ok');
  if (!m) {
    const sinBuffer = mpSinBufferSeleccionada();
    if (sinBuffer) {
      mostrarBufferMpVacio(true);
      const desc = descripcionMp(sinBuffer);
      if (titulo) {
        titulo.innerHTML = `<b>${sinBuffer.sku}</b>${desc ? ' — ' + desc : ''} ` +
          `<span class="badge-top" style="background:var(--gray-100);color:var(--gray-600);">sin buffer</span>`;
      }
      renderPtQueConsumenMp(sinBuffer.sku);
      return;
    }
    mostrarBufferMpVacio(false);
    renderMPChart(null);
    if (titulo) titulo.textContent = 'Selecciona una MP en la lista';
    renderPtQueConsumenMp(null);
    return;
  }
  mostrarBufferMpVacio(false);
  // Sin filtro de búsqueda (FILTROS_MP.mp vacío) el buffer muestra el
  // AGREGADO de todas las MP con buffer, igual que PT -- antes siempre
  // mostraba una sola MP (la primera de la lista) aunque no hubiera nada
  // filtrado. Usa filasMP(), el MISMO universo que las tarjetas KPI
  // (Inventario máximo/mínimo), para que el TOG y los días del gráfico
  // cuadren con lo que dicen las tarjetas -- antes sumaba TODAS las MP con
  // buffer (73) mientras las tarjetas solo sumaban las del plan (51).
  if (!FILTROS_MP.mp) {
    const todas = filasMP();
    const agregado = { sku: null, tor: 0, toy: 0, tog: 0, green: 0, yellow: 0, adu: 0 };
    todas.forEach(x => {
      agregado.tor += x.tor || 0; agregado.toy += x.toy || 0; agregado.tog += x.tog || 0;
      agregado.green += x.green || 0; agregado.yellow += x.yellow || 0; agregado.adu += x.adu || 0;
    });
    if (titulo) titulo.innerHTML = `<b>Todas las MP</b> <span style="color:var(--gray-600);font-weight:normal;">(${todas.length} con buffer)</span>`;
    renderMPChart(agregado, 'promedio');
    renderPtQueConsumenMp(null);
    return;
  }
  const desc = descripcionMp(m);
  if (titulo) titulo.innerHTML = `<b>${m.sku}</b>${desc ? ' — ' + desc : ''}`;
  renderMPChart(m, `${Math.round(m.dlt_dias)} días`);
  renderPtQueConsumenMp(m.sku);
}
async function renderPtQueConsumenMp(skuMp) {
  const cont = document.getElementById('mpPtConsumen');
  if (!cont) return;
  if (!skuMp) { cont.innerHTML = '<p style="font-size:13px;color:var(--gray-400);">Selecciona una MP arriba.</p>'; return; }
  cont.innerHTML = '<p style="color:var(--gray-400);font-size:12px;">Calculando relaciones PT ↔ MP (una sola vez por sesión)…</p>';
  try {
    const consumen = await ptQueConsumen(skuMp);
    const nfpMp = nfpMpDeSku(skuMp);
    const stockActual = nfpMp ? nfpMp.disponible_tm : null;
    const consumoTotalSemana = consumen.reduce((a, p) => a + (p.consumoTm || 0), 0);
    const stockResultante = stockActual != null ? stockActual - consumoTotalSemana : null;

    const kpisHtml = `
      <div class="kpi-row" style="grid-template-columns:repeat(3,1fr);margin-bottom:12px;">
        <div class="card"><p class="kpi-label">Stock actual</p><p class="kpi-value neutral">${fmtTM(stockActual)}</p></div>
        <div class="card"><p class="kpi-label">Consumo total semana</p><p class="kpi-value neutral">${fmtTM(consumoTotalSemana)}</p></div>
        <div class="card"><p class="kpi-label">Stock resultante</p><p class="kpi-value ${stockResultante != null && stockResultante < 0 ? 'danger' : 'neutral'}">${fmtTM(stockResultante)}</p></div>
      </div>`;

    if (!consumen.length) {
      cont.innerHTML = kpisHtml + renderMensajeSinFormula('Ningún PT del plan de producción vigente consume esta MP en su fórmula de esta semana.');
      return;
    }
    // Una MP popular (ej. torta de soya) puede estar en decenas de fórmulas
    // de PT -- sin tope de altura, esta tarjeta se alarga y estira a la
    // tarjeta del Buffer (misma fila del grid) dejando un hueco en blanco
    // debajo del gráfico. Con scroll propio, las dos tarjetas quedan del
    // mismo tamaño, igual que Producto Terminado.
    cont.innerHTML = kpisHtml + `
      <div style="max-height:240px;overflow-y:auto;">
      <table><thead><tr><th>PT</th><th>Producto</th><th class="num-right">% fórmula</th>
        <th class="num-right">TM a producir</th><th class="num-right">Consumo de esta MP</th></tr></thead>
      <tbody>${consumen.map(p => `<tr class="clickable-row" data-pt="${p.sku_pt}">
          <td><b>${p.sku_pt}</b></td><td style="font-size:12px;">${(descripcionSku(p.sku_pt) || '—').slice(0, 30)}</td>
          <td class="num-right">${p.pct}%</td>
          <td class="num-right">${fmtTM(p.tmSemana)}</td>
          <td class="num-right">${fmtTM(p.consumoTm)}</td></tr>`).join('')}</tbody></table>
      </div>`;
    cont.querySelectorAll('[data-pt]').forEach(tr => tr.addEventListener('click', () => irASkuEnBuffer(tr.dataset.pt)));
  } catch (err) {
    cont.innerHTML = '<p style="color:var(--gray-400);font-size:12px;">Sin datos disponibles.</p>';
  }
}
function sincronizarRotacionConSku() {
  const elSku = document.getElementById('skuBuscar_ddmrp');
  const elRot = document.getElementById('rotSel_ddmrp');
  if (!elSku || !elRot) return;
  const cod = elSku.value.trim().split(' — ')[0].trim().toLowerCase();
  if (!cod) {
    if (elRot.dataset.autoSync === '1') { elRot.value = 'todos'; elRot.dataset.autoSync = '0'; }
    return;
  }
  const fuente = (POS.datos && POS.datos.length) ? POS.datos.map(p => p.sku) : DDMRP.bufferPt.map(b => b.sku);
  const coincidencias = fuente.filter(sku => String(sku || '').toLowerCase().includes(cod));
  if (!coincidencias.length) return;
  const niveles = new Set(coincidencias.map(sku => rotacionDeSku(sku) || 'mto'));
  if (niveles.size === 1 && elRot.value !== [...niveles][0]) { elRot.value = [...niveles][0]; elRot.dataset.autoSync = '1'; }
}
function initFiltrosDDMRP() {
  const elRot = document.getElementById('rotSel_ddmrp');
  if (elRot && !elRot.dataset.wired) { elRot.dataset.wired = '1'; elRot.addEventListener('change', () => { elRot.dataset.autoSync = '0'; renderDDMRP(); }); }
  const elSku = document.getElementById('skuBuscar_ddmrp');
  if (elSku && !elSku.dataset.wiredInput) {
    elSku.dataset.wiredInput = '1';
    elSku.addEventListener('input', () => { sincronizarRotacionConSku(); renderDDMRP(); });
    elSku.addEventListener('focus', () => elSku.select());
  }
  initCombobox('skuBuscar_ddmrp', opcionesSkuDDMRP, () => { sincronizarRotacionConSku(); renderDDMRP(); });
  const elLt = document.getElementById('ltSel_mp');
  if (elLt && !elLt.dataset.wired) { elLt.dataset.wired = '1'; elLt.addEventListener('change', () => renderDDMRP()); }
  const elMp = document.getElementById('mpBuscar_ddmrp');
  if (elMp && !elMp.dataset.wiredInput) {
    elMp.dataset.wiredInput = '1';
    elMp.addEventListener('input', () => renderDDMRP());
    elMp.addEventListener('focus', () => elMp.select());
  }
  initCombobox('mpBuscar_ddmrp', opcionesMp, () => renderDDMRP());
}
/* Flete: etiqueta/color/tooltip de motivo_planta (calcular_flete_pedidos.py +
   optimizar_producción.py). pill-gray reutiliza pill-buffer (mismo gris);
   pill-purple es nueva (ver CSS). */
const MOTIVO_PLANTA_INFO = {
  FLETE: { label: 'Planta con menor flete', clase: 'pill-green',
    tip: 'Se produce donde sale más barato el camión al cliente.' },
  UNICA_PLANTA: { label: 'Única planta posible', clase: 'pill-buffer',
    tip: 'Este SKU solo tiene línea en esta planta.' },
  POR_NEGOCIO: { label: 'Por negocio', clase: 'pill-purple',
    tip: 'Excluido de la planta más barata en Configuración → Exclusiones.' },
  CAPACIDAD: { label: 'Planta barata llena', clase: 'pill-amber',
    tip: 'La planta con menor flete no tenía capacidad; se produjo aquí para no atrasar el pedido.' },
  SIN_LINEA_EN_PLANTA_BARATA: { label: 'Sin línea en planta barata', clase: 'pill-amber',
    tip: 'La planta con menor flete no tiene línea configurada para este calibre.' },
  SIN_DATO_FLETE: { label: 'Sin dato de flete', clase: 'pill-buffer',
    tip: 'El pedido no tiene región o tarifa (ver avisos de flete).' },
  BUFFER_SIN_CLIENTE: { label: 'Buffer', clase: 'pill-buffer',
    tip: 'Reposición de buffer: todavía no tiene cliente, el flete no aplica.' },
};
function pillMotivoPlanta(p) {
  if (p.motivo_planta === undefined) { avisarCampoFaltante('motivo_planta'); return '—'; }
  if (!p.motivo_planta) return '—';
  const info = MOTIVO_PLANTA_INFO[p.motivo_planta] || { label: p.motivo_planta, clase: 'pill-buffer', tip: '' };
  return `<span class="pill ${info.clase}" style="cursor:pointer;" title="${info.tip}" data-flete-sku="${p.sku}">${info.label}</span>`;
}
let PLAN_PEDIDOS = new Map();
async function cargarPedidosDelPlan() {
  const skus = [...new Set(PROD.plan.map(p => String(p.sku).trim()))];
  const faltan = skus.filter(s => !PLAN_PEDIDOS.has(s));
  if (!faltan.length) return;
  await Promise.all(faltan.map(async s => {
    try { PLAN_PEDIDOS.set(s, await pedidosDeSku(s)); } catch (err) { PLAN_PEDIDOS.set(s, []); }
  }));
}
function clientesDeCorrida(sku, fecha, dias = 7) {
  const ped = PLAN_PEDIDOS.get(String(sku).trim()) || [];
  const f0 = parseFechaLocal(fecha);
  if (!f0 || !ped.length) return [];
  const f1 = new Date(f0); f1.setDate(f1.getDate() + dias);
  const acc = new Map();
  ped.forEach(p => {
    const f = parseFechaLocal(p.fecha_entrega);
    if (!f || f < f0 || f > f1 || !(p.cantidad_tm > 0)) return;
    const c = p.cliente_nombre || p.cliente || '—';
    acc.set(c, (acc.get(c) || 0) + p.cantidad_tm);
  });
  return [...acc.entries()].sort((a, b) => b[1] - a[1]);
}
function justificacionBuffer(sku) {
  const rot = rotacionDeSku(sku);
  const adu = aduSkuHoy(sku);
  const partes = [];
  if (adu != null) partes.push(`${fmtNum(adu)} TM/día`);
  if (rot) partes.push(`rotación ${rot}`);
  return partes.length ? partes.join(' · ') : '—';
}
function kpisPlanDesdeDatos() {
  const plan = PROD.plan || [];
  if (!plan.length) return null;
  const fechas = [...new Set(plan.map(p => p.fecha))].sort();
  const tmTotal = plan.reduce((a, p) => a + (p.tm || 0), 0);
  const tmPedido = plan.filter(p => p.motivo === 'PEDIDO').reduce((a, p) => a + (p.tm || 0), 0);
  const tmBuffer = plan.filter(p => p.motivo === 'BUFFER').reduce((a, p) => a + (p.tm || 0), 0);
  const nSku = new Set(plan.map(p => p.sku)).size;
  const f0 = parseFechaLocal(fechas[0]), f1 = parseFechaLocal(fechas[fechas.length - 1]);
  return { tmTotal, tmPedido, tmBuffer, nSku, nDias: fechas.length, f0, f1 };
}
function mejorRateLineaCFG(recurso) {
  const [planta, linea] = recurso.split('|');
  let mejor = 0;
  (CFG.lineas || []).forEach(l => {
    const p = String(l.planta || '').toUpperCase().replace('Á', 'A');
    let ln = String(l.linea || '').toUpperCase();
    if (ln && !ln.startsWith('L')) ln = 'L' + ln;
    if (p === planta && ln === linea) mejor = Math.max(mejor, Number(l.rate) || 0);
  });
  return mejor;
}

/* Punto 6 del ajuste "una sola fuente de riesgo": horas_dia en
   paradas_programadas significa HORAS DE PARADA por día (24 = parada
   total), no horas restantes. Estas tres funciones ya reflejan esa
   semántica: horasDisponiblesRecursoDia, renderImpactoParadasPlan y
   franjaParadasProgramadas. */
function recursoDePlanRow(p) {
  return normalizarRecursoPlantaLinea(p.planta || (p.recurso || '').split('|')[0], p.linea);
}
function recursoDeParadaObj(p) {
  return normalizarRecursoPlantaLinea(p.planta, p.linea);
}
function normalizarRecursoPlantaLinea(planta, linea) {
  const pl = String(planta || '').trim().toUpperCase().replace(/Á/g, 'A');
  let ln = String(linea || '').trim().toUpperCase();
  if (ln && !ln.startsWith('L')) ln = 'L' + ln;
  return `${pl}|${ln}`;
}
function mapaCapacidadPorRecurso() {
  const mapa = new Map();
  (CFG.lineas || []).forEach(l => {
    const recurso = normalizarRecursoPlantaLinea(l.planta, l.linea);
    const horas = Number(l.horas) || 0;
    if (!horas) return;
    mapa.set(recurso, Math.max(mapa.get(recurso) || 0, horas));
  });
  return mapa;
}
function paradaVigenteParaRecurso(recurso, fecha) {
  const f = parseFechaLocal(fecha);
  if (!f) return null;
  return (PARADAS_PROGRAMADAS || []).find(p => {
    const fi = parseFechaLocal(p.fecha_inicio), ff = parseFechaLocal(p.fecha_fin);
    return recursoDeParadaObj(p) === recurso && fi && ff && f >= fi && f <= ff;
  }) || null;
}
function horasUsadasRecursoDia(recurso, fecha) {
  return (PROD.plan || []).filter(p => recursoDePlanRow(p) === recurso && p.fecha === fecha && p.motivo !== 'YA PRODUCIDO')
    .reduce((a, p) => a + (p.horas || 0), 0);
}
function horasDisponiblesRecursoDia(recurso, fecha, capMap) {
  const capBase = capMap.get(recurso) ?? 20;
  const parada = paradaVigenteParaRecurso(recurso, fecha);
  const capEfectiva = parada ? Math.max(0, capBase - (Number(parada.horas_dia) || 0)) : capBase;
  return Math.max(0, capEfectiva - horasUsadasRecursoDia(recurso, fecha));
}
function buscarDiaConEspacio(sku, recurso, horasNecesarias, noAntesDe, noDespuesDe, capMap) {
  const [planta, linea] = recurso.split('|');
  if (!lineaCompatible(sku, linea)) return null;
  const dias = [...new Set((PROD.plan || []).map(p => p.fecha))].sort();
  for (const f of dias) {
    if (noAntesDe && f < noAntesDe) continue;
    if (noDespuesDe && f > noDespuesDe) continue;
    if (horasDisponiblesRecursoDia(recurso, f, capMap) >= horasNecesarias) return f;
  }
  return null;
}

/* Única versión de renderImpactoParadasPlan(): calcula la capacidad
   REALMENTE perdida (horas perdidas/día x días x mejor rate de la
   línea), independiente de cómo el optimizador reacomodó el resto de
   la semana. horas_dia de la parada = horas DE PARADA por día, así que
   horasPerdidasDia = min(horasNormales, horas_dia) -- nunca más de lo
   que la línea produce normalmente. */
async function renderImpactoParadasPlan() {
  const cont = document.getElementById('prodImpactoParadas');
  if (!cont) return;
  const hoy = hoyStr();
  const vigentes = (PARADAS_PROGRAMADAS || []).filter(p => String(p.fecha_fin || '').slice(0, 10) >= hoy);
  if (!vigentes.length) { cont.innerHTML = ''; return; }
  const mejorRatePorLinea = new Map();
  (PROD.plan || []).forEach(p => {
    if (!p.tm || !p.horas) return;
    const rate = p.tm / p.horas;
    const ln = p.recurso || `${p.planta}|${p.linea}`;
    if (!mejorRatePorLinea.has(ln) || rate > mejorRatePorLinea.get(ln)) mejorRatePorLinea.set(ln, rate);
  });
  let tmPerdidaTotal = 0;
  vigentes.forEach(p => {
    const linea = String(p.linea || '').toUpperCase();
    const ln = linea.startsWith('L') ? linea : 'L' + linea;
    const recurso = `${String(p.planta || '').toUpperCase()}|${ln}`;
    const horasNormales = horasLineaConfigurada(recurso) ?? 20;
    const horasPerdidasDia = Math.min(horasNormales, Number(p.horas_dia) || 0);
    if (horasPerdidasDia <= 0) return;
    const inicio = parseFechaLocal(p.fecha_inicio);
    const fin = parseFechaLocal(p.fecha_fin);
    if (!inicio || !fin) return;
    const dias = Math.max(1, Math.round((fin - inicio) / 86400000) + 1);
    const rate = mejorRatePorLinea.get(recurso) || mejorRateLineaCFG(recurso);
    tmPerdidaTotal += horasPerdidasDia * dias * rate;
  });
  if (tmPerdidaTotal <= 0.5) { cont.innerHTML = ''; return; }
  cont.innerHTML = `
    <div class="aviso-box warn" style="margin-bottom:14px;">
      <b>⏸ Impacto estimado de las paradas programadas:</b>
      ~${fmtTM(tmPerdidaTotal)} de capacidad de producción perdida en el horizonte.
      <a href="#" data-ir-simulador="1" style="color:var(--emerald-800);font-weight:700;text-decoration:underline;">Ver qué hacer en el Simulador →</a>
    </div>`;
  const link = cont.querySelector('[data-ir-simulador]');
  if (link) link.addEventListener('click', async (e) => {
    e.preventDefault();
    document.querySelectorAll('.nav-item').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.querySelector('[data-screen="simulador"]').classList.add('active');
    document.getElementById('simulador').classList.add('active');
    await renderSimuladorScreen();
    irAModuloSimulador('reprogramacion');
  });
}
/* Tarjetas de flete arriba del plan -- todo sale de /api/v1/flete/resumen,
   nada se calcula aquí. */
function renderFleteKpis() {
  const set = (id, t) => { const e = document.getElementById(id); if (e) e.textContent = t; };
  const r = FLETE.resumen;
  if (!r || r.flete_estimado_usd == null) {
    set('fleteEstimado', '—'); set('fleteSobrecosto', '—'); set('fleteAhorro', '—');
    set('fleteSobrecostoSub', 'sin datos de flete');
    set('fleteFecha', 'Flete: correr calcular_flete_pedidos.py');
    return;
  }
  set('fleteEstimado', fmtUSD(r.flete_estimado_usd));
  set('fleteSobrecosto', fmtUSD(r.sobrecosto_usd));
  set('fleteAhorro', fmtUSD(r.valor_max_elegir_bien_usd));
  const porMotivo = Array.isArray(r.por_motivo_planta) ? r.por_motivo_planta : [];
  const tmEvitable = porMotivo.filter(x => x.motivo_planta === 'CAPACIDAD' || x.motivo_planta === 'POR_NEGOCIO')
    .reduce((a, x) => a + (x.tm || 0), 0);
  set('fleteSobrecostoSub', tmEvitable > 0.5 ? `${fmtTM(tmEvitable)} por capacidad llena o exclusión de negocio` : 'ninguno evitable esta semana');
  set('fleteFecha', r.fecha_calculo ? `Flete calculado el ${r.fecha_calculo}` : 'Sin fecha de cálculo de flete');
}
async function renderProduccion() {
  const cargas = [cargarProduccion(), cargarPosicionamiento(), cargarPedidosDelPlan(), cargarFleteResumen()];
  if (!PARADAS_CARGADAS) cargas.push(cargarParadasProgramadas());
  await Promise.all(cargas);
  franjaParadasProgramadas('prodParadasStrip');
  await renderImpactoParadasPlan();
  renderFleteKpis();
  const set = (id, t) => { const e = document.getElementById(id); if (e) e.textContent = t; };
  const avC = document.getElementById('prodAvisoCambios');
  if (avC) {
    try {
      const cambios = await fetchJSON('/api/v1/produccion/cambios');
      const lista = Array.isArray(cambios) ? cambios : (cambios.cambios || []);
      if (lista.length) {
        avC.style.display = 'block';
        const soloUno = lista.length === 1 && (lista[0].tipo === 'sin_cambios' || lista[0].tipo === 'primera_corrida');
        avC.innerHTML = soloUno ? `<b>Plan dinámico —</b> ${lista[0].mensaje}` : `
          <b>Plan dinámico — esto cambió desde la última corrida:</b>
          <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px;">
            ${lista.slice(0, 8).map(c => `
              <div style="display:flex;gap:10px;align-items:flex-start;background:var(--white);
                          border:1px solid var(--gray-200);border-radius:6px;padding:8px 12px;">
                <span style="color:var(--emerald-700);font-weight:bold;">●</span>
                <span style="font-size:13px;">${c.mensaje}</span>
              </div>`).join('')}
          </div>`;
      } else { avC.style.display = 'none'; }
    } catch (err) { avC.style.display = 'none'; }
  }
  const k = kpisPlanDesdeDatos();
  if (!k) { set('prodTm', '—'); set('prodSub', 'Sin plan. Correr optimizar_produccion.py.'); return; }
  set('prodFecha', `calculado ${PROD.resumen && PROD.resumen.fecha_calculo ? PROD.resumen.fecha_calculo : '—'}`);
  set('prodTm', fmtTM(k.tmTotal));
  set('prodSub', `${k.nSku} SKU · horizonte ${fechaCorta(k.f0)}–${fechaCorta(k.f1)} (${k.nDias} días)`);
  set('prodPedido', fmtTM(k.tmPedido));
  set('prodBuffer', fmtTM(k.tmBuffer));
  const r = PROD.resumen || {};
  const g = r.riesgo || {};
  set('prodRiesgo', g.total_tm ? fmtTM(g.total_tm) : '0 TM');
  set('prodRiesgoSub', g.total_tm ? `ver detalle abajo · horizonte ${fechaCorta(k.f0)}–${fechaCorta(k.f1)}` : 'toda la demanda se cumple en este horizonte');
  const av = document.getElementById('prodAvisoRiesgo');
  if (av) {
    if (g.total_tm > 0) {
      av.style.display = 'block';
      let h = '';
      if (g.ya_no_llegan_tm > 0) h += `⚠ <b>${fmtTM(g.ya_no_llegan_tm)} ya no llegan.</b> Entrega dentro del lead time de producción: producir hoy no alcanza. No es una falla del plan — el pedido se conoció tarde. Hay que negociar fecha o cubrir desde otra bodega.`;
      if (g.falta_capacidad_tm > 0) h += `${h ? '<br><br>' : ''}<b>${fmtTM(g.falta_capacidad_tm)} por falta de capacidad.</b> Hay tiempo de producir pero no hay línea disponible. Acá sí se puede actuar: turno extra, maquila o repriorizar.`;
      const det = (g.detalle || []).slice(0, 6).map(x => `${x.sku}${descripcionSku(x.sku) ? ' ' + descripcionSku(x.sku).slice(0, 24) : ''} (${Math.round(x.tm_no_servida)} TM)`).join(' · ');
      if (det) h += `<br><br><b>SKU afectados:</b> ${det}`;
      av.innerHTML = h;
    } else { av.style.display = 'none'; }
  }
  /* Punto 3 del ajuste "una sola fuente de riesgo": SKU cuyos pedidos el
     modelo ni siquiera puede planificar (sin línea/rate configurado o
     sin posicionamiento) -- distinto de "falta capacidad", requiere
     acción en Configuración, no reprogramación. */
  const avSinLinea = document.getElementById('prodAvisoSinLinea');
  if (avSinLinea) {
    try {
      const riesgoDetalle = await fetchJSON('/api/v1/produccion/riesgo-detalle');
      const sinModelo = (Array.isArray(riesgoDetalle) ? riesgoDetalle : [])
        .filter(r => r.causa === 'SIN_LINEA_CONFIGURADA' || r.causa === 'SIN_POSICIONAMIENTO');
      if (sinModelo.length) {
        const porSku = new Map();
        sinModelo.forEach(r => { porSku.set(r.sku, (porSku.get(r.sku) || 0) + (r.tm_no_servida || 0)); });
        const total = [...porSku.values()].reduce((a, v) => a + v, 0);
        avSinLinea.style.display = 'block';
        avSinLinea.innerHTML = `<b>⚠ ${fmtTM(total)} de pedidos de SKU que el modelo no puede planificar:</b> falta configurar su línea/calibre en Configuración.<br>` +
          [...porSku.entries()].slice(0, 6).map(([sku, tm]) => `${sku}${descripcionSku(sku) ? ' ' + descripcionSku(sku).slice(0, 24) : ''} (${Math.round(tm)} TM)`).join(' · ');
      } else { avSinLinea.style.display = 'none'; }
    } catch (err) { avSinLinea.style.display = 'none'; }
  }
  const avStock = document.getElementById('prodAvisoStockout');
  if (avStock) {
    try {
      const stockout = await fetchJSON('/api/v1/produccion/alertas-stockout');
      if (Array.isArray(stockout) && stockout.length) {
        avStock.style.display = 'block';
        avStock.innerHTML = `<b>⚠ ${stockout.length} SKU donde el plan NO arranca a tiempo según el buffer:</b><br>` +
          stockout.slice(0, 6).map(s => `• ${s.sku}${s.descripcion ? ' ' + s.descripcion.slice(0, 24) : ''} — debía arrancar el ${s.fecha_limite_inicio}, ${s.primer_dia_plan ? `programado ${s.primer_dia_plan}` : 'NO está en el plan'}`).join('<br>');
      } else { avStock.style.display = 'none'; }
    } catch (err) { avStock.style.display = 'none'; }
  }
  const avSobre = document.getElementById('prodAvisoSobreproduccion');
  if (avSobre) {
    try {
      const sobre = await fetchJSON('/api/v1/produccion/alertas-sobreproduccion');
      if (Array.isArray(sobre) && sobre.length) {
        avSobre.style.display = 'block';
        avSobre.innerHTML = `<b>${sobre.length} SKU con producción esta semana sin que el buffer lo pida:</b><br>` +
          sobre.slice(0, 6).map(s => `• ${s.sku}${s.descripcion ? ' ' + s.descripcion.slice(0, 24) : ''} — TOG ${fmtTM(s.tog)}, plan ${fmtTM(s.tm_en_plan_semana)}`).join('<br>');
      } else { avSobre.style.display = 'none'; }
    } catch (err) { avSobre.style.display = 'none'; }
  }
  const cl = document.getElementById('prodLineas');
  if (cl) {
    const horasApi = (r.horas_dia_linea && typeof r.horas_dia_linea === 'object') ? r.horas_dia_linea : {};
    const porPlanta = new Map();
    (r.lineas || []).forEach(l => {
      const rec = String(l.recurso || l.linea || '');
      const [pl, ln] = rec.includes('|') ? rec.split('|') : ['—', rec];
      if (!porPlanta.has(pl)) porPlanta.set(pl, []);
      porPlanta.get(pl).push({ ...l, _pl: pl, _ln: ln, _rec: rec });
    });
    const plantas = [...porPlanta.keys()].sort();
    if (!plantas.includes(PROD.filtroRecurso) && PROD.filtroRecurso !== 'TODOS') PROD.filtroRecurso = plantas[0] || 'TODOS';
    if (PROD.filtroRecurso === 'TODOS' && plantas.length) PROD.filtroRecurso = plantas[0];

    const tabs = plantas.map(p => {
      const tm = porPlanta.get(p).reduce((a, l) => a + (l.tm_total || 0), 0);
      return `<div class="tab ${PROD.filtroRecurso === p ? 'active' : ''}" data-plfiltro="${p}">${p} · ${fmtTM(tm)}</div>`;
    }).join('');

    const lns = (porPlanta.get(PROD.filtroRecurso) || []).sort((a, b) => String(a._ln).localeCompare(String(b._ln)));
    const barras = lns.map(l => {
      const horasLinea = horasLineaConfigurada(l._rec) ?? horasApi[l._rec] ?? 20;
      const u = Math.min((l.h_dia_prom || 0) / horasLinea, 1);
      const pct = (u * 100).toFixed(0);
      const color = u >= 0.95 ? '#B54B4B' : u >= 0.80 ? '#D9A441' : 'var(--emerald-600)';
      return `
        <div style="margin-bottom:13px;">
          <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px;">
            <b>${l._ln}</b>
            <span>${fmtTM(l.tm_total)} · <b style="color:${color};">${pct}%</b>
              <span style="color:var(--gray-400);">(${fmtNum(l.h_dia_prom || 0)} de ${horasLinea} h/día${horasLineaConfigurada(l._rec) != null ? ' · config' : ''})</span></span>
          </div>
          <div class="bar-track" style="height:9px;"><div class="bar-fill" style="width:${pct}%;background:${color};"></div></div>
        </div>`;
    }).join('');
    cl.innerHTML = `<div class="tabs">${tabs}</div>${barras}`;
    cl.querySelectorAll('[data-plfiltro]').forEach(el => el.addEventListener('click', () => { PROD.filtroRecurso = el.dataset.plfiltro; renderProduccion(); }));
  }
  renderProdChart();
  renderProdDias();
  renderProdGridSemanal();
  initBotonesDias();
}
function renderProdChart() {
  const c = document.getElementById('prodChart');
  if (!c) return;
  if (CHARTS.prod) { CHARTS.prod.destroy(); CHARTS.prod = null; }
  const plan = (PROD.plan || []).filter(p => String(p.planta || (p.recurso || '').split('|')[0]) === PROD.filtroRecurso);
  if (!plan.length) return;
  if (!chartDisponible(c)) return;
  const dias = [...new Set(plan.map(p => p.fecha))].sort();
  const lineas = [...new Set(plan.map(p => String(p.linea)))].sort();
  const PALETA = ['rgba(31,110,82,.85)', 'rgba(91,127,166,.85)', 'rgba(217,164,65,.85)', 'rgba(181,75,75,.8)', 'rgba(46,139,107,.7)', 'rgba(120,120,115,.7)'];
  const datasets = lineas.map((ln, k) => ({
    label: ln,
    data: dias.map(d => plan.filter(p => p.fecha === d && String(p.linea) === ln).reduce((a, p) => a + (p.tm || 0), 0)),
    backgroundColor: PALETA[k % PALETA.length], barPercentage: 0.55, categoryPercentage: 0.7, datalabels: { display: false },
  }));
  const totales = dias.map(d => plan.filter(p => p.fecha === d).reduce((a, p) => a + (p.tm || 0), 0));
  const ultimo = datasets[datasets.length - 1];
  ultimo.borderRadius = { topLeft: 4, topRight: 4 };
  ultimo.borderSkipped = false;
  ultimo.datalabels = { display: true, anchor: 'end', align: 'top', offset: 2, color: '#1C1C1A', font: { size: 10, weight: 'bold' }, formatter: (v, ctx) => Math.round(totales[ctx.dataIndex]) };
  CHARTS.prod = new Chart(c, {
    type: 'bar',
    data: { labels: dias.map(d => { const f = parseFechaLocal(d); if (!f) return d; const dd = ['do','lu','ma','mi','ju','vi','sa'][f.getDay()]; return `${dd} ${f.getDate()}/${f.getMonth() + 1}`; }), datasets },
    options: { responsive: true, maintainAspectRatio: false, layout: { padding: { top: 18 } },
      plugins: { legend: { display: lineas.length > 1, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: x => `${x.dataset.label}: ${Math.round(x.parsed.y)} TM` } } },
      scales: { x: { stacked: true, grid: { display: false }, ticks: { font: { size: 10 } } }, y: { stacked: true, ticks: { callback: v => v + ' TM' }, grid: { color: 'rgba(20,30,25,0.08)' } } } },
  });
}
function numeroSemanaISO(fecha) {
  const d = new Date(Date.UTC(fecha.getFullYear(), fecha.getMonth(), fecha.getDate()));
  const dia = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dia);
  const inicioAnio = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil((((d - inicioAnio) / 86400000) + 1) / 7);
}
function rangoSemana(fecha) {
  const d = new Date(fecha), dia = d.getDay() || 7;
  const lunes = new Date(d); lunes.setDate(d.getDate() - dia + 1);
  const domingo = new Date(lunes); domingo.setDate(lunes.getDate() + 6);
  return `${fechaCorta(lunes)} — ${fechaCorta(domingo)}`;
}
function detectarCambioPlan(fechaCalculo) {
  if (!fechaCalculo) return null;
  const anterior = localStorage.getItem('prod_ultima_fecha_calculo');
  localStorage.setItem('prod_ultima_fecha_calculo', fechaCalculo);
  if (anterior && anterior !== fechaCalculo) return `Plan recalculado — el anterior era del ${anterior}, este es del ${fechaCalculo}. Puede haber cambiado por pedidos nuevos o modificados desde entonces.`;
  return null;
}
function renderProdDias() {
  const cont = document.getElementById('prodDias');
  if (!cont) return;
  if (!PROD.plan.length) { cont.innerHTML = `<p style="color:var(--gray-400);font-size:13px;">Sin plan.</p>`; return; }
  const hoyF = hoyStr();
  const planF = PROD.plan.filter(p => String(p.planta || (p.recurso || '').split('|')[0]) === PROD.filtroRecurso && p.fecha >= hoyF);
  const dias = [...new Set(planF.map(p => p.fecha))].sort();
  const nombresDia = ['dom','lun','mar','mié','jue','vie','sáb'];
  if (!dias.length) { cont.innerHTML = `<p style="color:var(--gray-400);font-size:13px;">Sin producción programada desde hoy en ${PROD.filtroRecurso}.</p>`; return; }
  const aviso = detectarCambioPlan(PROD.resumen ? PROD.resumen.fecha_calculo : null);
  const avisoHtml = aviso ? `<div class="aviso-box warn" style="margin-bottom:14px;">${aviso}</div>` : '';
  const porSemana = new Map();
  dias.forEach(f => {
    const d = parseFechaLocal(f);
    if (!d) return;
    const sem = numeroSemanaISO(d);
    if (!porSemana.has(sem)) porSemana.set(sem, { dias: [], rango: rangoSemana(d) });
    porSemana.get(sem).dias.push(f);
  });
  const semanasOrdenadas = [...porSemana.keys()].sort((a, b) => a - b);
  cont.innerHTML = avisoHtml + semanasOrdenadas.map((sem, si) => {
    const info = porSemana.get(sem);
    const tmSemana = planF.filter(p => info.dias.includes(p.fecha)).reduce((a, p) => a + (p.tm || 0), 0);
    const diasHtml = info.dias.map((f, i) => {
      const filas = planF.filter(p => p.fecha === f);
      const tm = filas.reduce((a, p) => a + (p.tm || 0), 0);
      const ped = filas.filter(p => p.motivo === 'PEDIDO').reduce((a, p) => a + (p.tm || 0), 0);
      const d = parseFechaLocal(f);
      const etiqueta = d ? `${nombresDia[d.getDay()]} ${d.getDate()}/${d.getMonth() + 1}` : f;
      const esHoy = f === hoyF;
      const cerrado = (si === 0 && i >= 3) || si > 0 ? ' cerrado' : '';
      return `
        <div class="dia-card${cerrado}">
          <div class="dia-head" data-dia="${f}">
            <span class="f">${etiqueta}${esHoy ? ' · HOY' : ''}</span>
            <span class="t">${fmtTM(tm)} · ${filas.length} corrida(s) · ${ped > 0 ? fmtTM(ped) + ' para pedidos' : 'solo reposición'}</span>
          </div>
          <div class="dia-body">
            <table><thead><tr><th>Planta</th><th>Línea</th><th>SKU</th><th>Producto</th>
              <th class="num-right">TM</th><th class="num-right">Horas</th><th>Motivo</th>
              <th>Motivo planta</th><th class="num-right">Flete USD/TM</th><th class="num-right">Sobrecosto USD</th>
              <th>Para quién / por qué</th></tr></thead><tbody>
            ${filas.sort((a, b) => String(a.linea).localeCompare(String(b.linea))).map(p => {
              const desc = (descripcionSku(p.sku) || '—').toString().slice(0, 30);
              let razon;
              if (p.motivo === 'PEDIDO') {
                const cl = clientesDeCorrida(p.sku, p.fecha);
                razon = cl.length ? cl.slice(0, 2).map(c => `${c[0].slice(0, 22)} <span style="color:var(--gray-400);">${Math.round(c[1])} TM</span>`).join(' · ') +
                  (cl.length > 2 ? ` <span style="color:var(--gray-400);">+${cl.length - 2}</span>` : '') : '<span style="color:var(--gray-400);">sin pedido en la ventana</span>';
              } else {
                razon = `<span style="color:var(--gray-600);">rota ${justificacionBuffer(p.sku)}</span>`;
              }
              const pl = p.planta || (p.recurso || '').split('|')[0] || '—';
              const fleteTxt = p.flete_usd_tm !== undefined
                ? (p.flete_usd_tm != null ? fmtNum(p.flete_usd_tm, 2) : '—')
                : (avisarCampoFaltante('flete_usd_tm'), '—');
              const sobrecostoTxt = p.sobrecosto_flete_usd !== undefined
                ? (p.sobrecosto_flete_usd ? fmtUSD(p.sobrecosto_flete_usd) : '—')
                : (avisarCampoFaltante('sobrecosto_flete_usd'), '—');
              return `<tr>
                <td style="font-size:12px;color:var(--gray-600);">${pl}</td><td><b>${p.linea}</b></td><td>${p.sku}</td>
                <td style="font-size:12.5px;">${desc}</td><td class="num-right">${fmtTM(p.tm)}</td><td class="num-right">${p.horas}</td>
                <td><span class="pill ${pillMotivo(p.motivo)}">${p.motivo || '—'}</span></td>
                <td>${pillMotivoPlanta(p)}</td><td class="num-right">${fleteTxt}</td><td class="num-right">${sobrecostoTxt}</td>
                <td style="font-size:12px;">${razon}</td></tr>`;
            }).join('')}
            </tbody></table>
          </div>
        </div>`;
    }).join('');
    return `<h4 style="font-size:13px;font-weight:bold;color:var(--emerald-900);margin:${si > 0 ? '20px' : '0'} 0 10px;padding-left:10px;border-left:3px solid var(--emerald-600);">
      Semana ${si + 1} · ${info.rango} · ${fmtTM(tmSemana)}</h4>${diasHtml}`;
  }).join('');
  cont.querySelectorAll('[data-dia]').forEach(el => el.addEventListener('click', () => el.closest('.dia-card').classList.toggle('cerrado')));
  cont.querySelectorAll('[data-flete-sku]').forEach(el => el.addEventListener('click', (e) => {
    e.stopPropagation();
    abrirPanelFleteSku(el.dataset.fleteSku);
  }));
}
/* Panel de detalle de flete por SKU (clic en el pill de "Motivo planta"):
   GET /flete/pedidos?sku= -- una fila por línea de pedido, con el USD/TM
   de cada planta para que el planificador vea POR QUÉ el modelo eligió
   la planta que eligió. */
async function abrirPanelFleteSku(sku) {
  const card = document.getElementById('fletePanelSku');
  const titulo = document.getElementById('fletePanelSkuTitulo');
  if (!card) return;
  card.style.display = 'block';
  if (titulo) titulo.innerHTML = `<b>${sku}</b>${descripcionSku(sku) ? ' — ' + descripcionSku(sku) : ''} · cargando…`;
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  const filas = await fletePedidosDeSku(sku);
  if (titulo) titulo.innerHTML = `<b>${sku}</b>${descripcionSku(sku) ? ' — ' + descripcionSku(sku) : ''}`;
  renderPanelFleteSkuTabla(filas);
}
function cerrarPanelFleteSku() {
  const card = document.getElementById('fletePanelSku');
  if (card) card.style.display = 'none';
}
function renderPanelFleteSkuTabla(filas) {
  const tbody = document.getElementById('fletePanelSkuTabla');
  if (!tbody) return;
  if (!filas.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="color:var(--gray-400);">Sin pedidos de flete para este SKU.</td></tr>`;
    return;
  }
  tbody.innerHTML = filas.map(p => {
    const dur = p.usd_tm_DURAN, gye = p.usd_tm_GUAYAQUIL;
    const barataDur = dur != null && (gye == null || dur <= gye);
    const barataGye = gye != null && (dur == null || gye < dur);
    const badges = [
      p.robusta === 0 ? '<span class="pill pill-amber" title="La planta más barata cambia entre sus distintos Ship To.">depende del campamento</span>' : '',
      p.temporada_invernal === 1 ? '<span class="pill pill-blue" title="El despacho cae en temporada de lluvia: se usó la RESTRICCION INVERNAL.">restricción invernal</span>' : '',
    ].filter(Boolean).join(' ');
    return `<tr>
      <td>${p.cliente || '—'}</td>
      <td style="white-space:nowrap;">${p.fecha_entrega ? fechaCorta(parseFechaLocal(p.fecha_entrega)) : '—'}</td>
      <td class="num-right">${p.tm_faltante != null ? fmtTM(p.tm_faltante) : '—'}</td>
      <td>${p.regiones || '—'}</td>
      <td class="num-right" style="${barataDur ? 'font-weight:bold;color:var(--emerald-700);' : ''}">${dur != null ? fmtNum(dur, 2) : '—'}</td>
      <td class="num-right" style="${barataGye ? 'font-weight:bold;color:var(--emerald-700);' : ''}">${gye != null ? fmtNum(gye, 2) : '—'}</td>
      <td class="num-right">${p.camiones != null ? p.camiones : '—'}</td>
      <td style="font-size:12px;">${p.estado || '—'}${badges ? ' ' + badges : ''}</td>
    </tr>`;
  }).join('');
}
function initBotonesDias() {
  const be = document.getElementById('btnExpandirDias'), bc = document.getElementById('btnColapsarDias');
  if (be && !be.dataset.wired) { be.dataset.wired = '1'; be.addEventListener('click', () => document.querySelectorAll('#prodDias .dia-card').forEach(c => c.classList.remove('cerrado'))); }
  if (bc && !bc.dataset.wired) { bc.dataset.wired = '1'; bc.addEventListener('click', () => document.querySelectorAll('#prodDias .dia-card').forEach(c => c.classList.add('cerrado'))); }
  const bx = document.getElementById('btnExportarPlanExcel');
  if (bx && !bx.dataset.wired) { bx.dataset.wired = '1'; bx.addEventListener('click', exportarPlanProduccionExcel); }
  const bf = document.getElementById('fletePanelSkuCerrar');
  if (bf && !bf.dataset.wired) { bf.dataset.wired = '1'; bf.addEventListener('click', cerrarPanelFleteSku); }
}
function renderProdGridSemanal() {
  const cont = document.getElementById('prodGridSemanal');
  if (!cont) return;
  const planF = (PROD.plan || []).filter(p => String(p.planta || (p.recurso || '').split('|')[0]) === PROD.filtroRecurso);
  if (!planF.length) { cont.innerHTML = `<p style="color:var(--gray-400);font-size:13px;">Sin plan para ${PROD.filtroRecurso}.</p>`; return; }
  const dias = [...new Set(planF.map(p => p.fecha))].sort();
  const nombresDia = ['Domingo','Lunes','Martes','Miércoles','Jueves','Viernes','Sábado'];
  const labelsDias = dias.map(f => { const d = parseFechaLocal(f); return d ? `${nombresDia[d.getDay()]}<br><span style="font-weight:normal;font-size:10px;opacity:.8;">${d.getDate()}/${d.getMonth()+1}</span>` : f; });
  const lineas = [...new Set(planF.map(p => String(p.linea)))].sort();
  cont.innerHTML = lineas.map(linea => {
    const filasLinea = planF.filter(p => String(p.linea) === linea);
    const skusOrden = []; const vistos = new Set();
    filasLinea.slice().sort((a, b) => a.fecha.localeCompare(b.fecha)).forEach(p => { if (!vistos.has(p.sku)) { vistos.add(p.sku); skusOrden.push(p.sku); } });
    const filasHtml = skusOrden.map(sku => {
      const desc = descripcionSku(sku) || '—';
      const celdas = dias.map(f => {
        const fila = filasLinea.find(p => p.fecha === f && p.sku === sku);
        if (!fila) return `<td><span class="celda-vacia">—</span></td>`;
        return `<td><span class="celda-tm ${claseCeldaMotivo(fila.motivo)}">${Math.round(fila.tm)}</span></td>`;
      }).join('');
      return `<tr><td class="sku-cell">${sku}</td><td class="desc-cell">${desc}</td>${celdas}</tr>`;
    }).join('');
    const totalesDia = dias.map(f => filasLinea.filter(p => p.fecha === f).reduce((a, p) => a + (p.tm || 0), 0));
    const totalLinea = totalesDia.reduce((a, v) => a + v, 0);
    const filaTotal = `<tr style="background:var(--emerald-50);font-weight:bold;border-top:2px solid var(--emerald-700);">
      <td class="sku-cell" colspan="2">TOTAL LÍNEA</td>${totalesDia.map(v => `<td>${v > 0 ? Math.round(v) : '—'}</td>`).join('')}</tr>`;
    return `<div class="grid-semanal"><h4>Línea ${linea} <span style="font-weight:normal;color:var(--gray-600);">· total ${fmtTM(totalLinea)}</span></h4>
      <table class="tabla-semanal"><thead><tr><th>SKU</th><th>Descripción</th>${labelsDias.map(l => `<th>${l}</th>`).join('')}</tr></thead>
      <tbody>${filasHtml}${filaTotal}</tbody></table></div>`;
  }).join('');
}
let SIM_RESULTADOS = [];
let SIM_PLANTA = null;
let SIM_TAB_ACTIVA = 'capacidad';
function tarjetaImpacto(valor, etiqueta, color) {
  return `<div class="resultado-card"><p class="v ${color || ''}">${valor}</p><p class="l">${etiqueta}</p></div>`;
}
function mesActualRango() {
  const hoy = new Date();
  const inicio = new Date(hoy.getFullYear(), hoy.getMonth(), 1);
  const fin = new Date(hoy.getFullYear(), hoy.getMonth() + 1, 0);
  return { inicio, fin };
}
async function calcularDemandaPedidosDelMes() {
  const skus = [...new Set((DDMRP.bufferPt || [])
    .filter(b => b.mes_offset === 0 && b.estado === 'ok').map(b => b.sku))];
  const { inicio, fin } = mesActualRango();
  let total = 0;
  await Promise.all(skus.map(async sku => {
    const pedidos = await pedidosDeSku(sku);
    (pedidos || []).forEach(p => {
      const f = parseFechaLocal(p.fecha_entrega);
      if (f && f >= inicio && f <= fin && (p.cantidad_tm || 0) > 0) total += p.cantidad_tm;
    });
  }));
  return total;
}

/* Punto 4 del ajuste "una sola fuente de riesgo": la "Capacidad" de la
   consola pasa de comparar demanda-vs-capacidad-teórica-del-mes (una
   cifra que no reflejaba lo que el plan real hace) a la utilización
   REAL del plan vigente (horas usadas / horas disponibles del plan). */
function calcularUtilizacionPlan() {
  const plan = PROD.plan || [];
  if (!plan.length) return null;
  const nDias = new Set(plan.map(p => p.fecha)).size;
  const recursos = [...new Set(plan.map(p => recursoDePlanRow(p)))];
  const horasUsadas = plan.reduce((a, p) => a + (p.horas || 0), 0);
  const capMap = mapaCapacidadPorRecurso();
  const horasDisponiblesTotal = recursos.reduce((a, rec) => a + (capMap.get(rec) ?? 20) * nDias, 0);
  return horasDisponiblesTotal > 0 ? (horasUsadas / horasDisponiblesTotal) * 100 : null;
}
async function calcularSituacionActual() {
  await cargarProduccion();
  const capacidadPct = calcularUtilizacionPlan();
  const { pct: bodegaPct } = calcularOcupacionBodegaPt(null);
  const riesgo = await calcularRiesgoRealSkus();
  const skusConBuffer = (DDMRP.bufferPt || []).filter(b => b.mes_offset === 0 && b.estado === 'ok').map(b => b.sku);
  const otifAgregado = await calcularOtifDeSkus(skusConBuffer);
  const otifPct = otifAgregado.otifActual != null ? otifAgregado.otifActual * 100 : null;
  return {
    capacidadPct, otifPct, bodegaPct,
    clientesEnRiesgo: riesgo.clientesMap.size,
    deficitReal: riesgo.deficitTotal,
    skusEnRiesgo: riesgo.skusAfectados.size,
  };
}
function renderConsolaKpi(id, valor, etiqueta, alerta, notaConexion) {
  const nota = notaConexion
    ? `<p class="nota-conexion-tabs" data-ir-inventario="1" style="margin:4px 0 0;font-size:10.5px;color:var(--gray-500);text-decoration:underline;cursor:pointer;">${notaConexion}</p>`
    : '';
  return `<div class="consola-kpi"><p class="v ${alerta ? 'alerta' : ''}">${valor}</p><p class="l">${etiqueta}</p>${nota}</div>`;
}
async function renderConsolaHero() {
  const cont = document.getElementById('consolaHeroGrid');
  if (!cont) return;
  const s = await calcularSituacionActual();
  cont.innerHTML =
    renderConsolaKpi('cap', s.capacidadPct != null ? fmtNum(s.capacidadPct, 0) + '%' : '—', 'Capacidad usada (plan)', s.capacidadPct > 100) +
    renderConsolaKpi('otif', s.otifPct != null ? fmtNum(s.otifPct, 1) + '%' : '—', 'OTIF', s.otifPct != null && s.otifPct < 90, 'mismo cálculo que Inventario Estratégico') +
    renderConsolaKpi('bod', s.bodegaPct != null ? fmtNum(s.bodegaPct, 0) + '%' : '—', 'Bodega', s.bodegaPct > 90) +
    renderConsolaKpi('cli', String(s.clientesEnRiesgo), 'Clientes en riesgo', s.clientesEnRiesgo > 0);
  cont.querySelectorAll('[data-ir-inventario]').forEach(el => el.addEventListener('click', () => irASkuEnBuffer('')));
  franjaParadasProgramadas('simParadasStrip');
  renderRecomendacionSistema(s);
  return s;
}
function renderRecomendacionSistema(s) {
  const yaExiste = document.getElementById('sistemaRecomiendaBox');
  if (yaExiste) yaExiste.remove();
  const problemas = [];
  if (s.bodegaPct != null && s.bodegaPct > 90) problemas.push({
    modulo: 'sobrestock', ico: '▤', severidad: s.bodegaPct > 100 ? 'critico' : 'alerta', titulo: 'Sobrestock en bodega',
    txt: `La bodega está al <b>${fmtNum(s.bodegaPct, 0)}%</b> de su capacidad. Impacto operativo ${s.bodegaPct > 100 ? 'alto' : 'moderado'}.`,
    cta: 'Ver Sobrestock →',
  });
  if (s.deficitReal > 0 || (s.otifPct != null && s.otifPct < 90)) problemas.push({
    modulo: 'capacidad', ico: '⚖', severidad: s.deficitReal > 0 ? 'critico' : 'alerta', titulo: 'Riesgo de cobertura / OTIF bajo objetivo',
    txt: s.deficitReal > 0
      ? `<b>${s.skusEnRiesgo} SKU</b> tienen pedidos sin cobertura (${fmtTM(s.deficitReal)}), OTIF real ${s.otifPct != null ? fmtNum(s.otifPct, 1) + '%' : '—'}. ${s.clientesEnRiesgo} cliente(s) podrían verse afectados.`
      : `El OTIF real está en <b>${fmtNum(s.otifPct, 1)}%</b>, por debajo del objetivo — revisa la priorización de clientes.`,
    cta: 'Ver Capacidad y Clientes →',
  });
  const hoy = hoyStr();
  const parada = (PARADAS_PROGRAMADAS || []).find(p => String(p.fecha_fin || '').slice(0, 10) >= hoy);
  if (parada) problemas.push({
    modulo: 'reprogramacion', ico: '⏸', severidad: 'alerta', titulo: 'Parada programada configurada',
    txt: `${parada.planta || '—'} · Línea ${parada.linea || '—'} parada del ${String(parada.fecha_inicio || '').slice(0, 10)} al ${String(parada.fecha_fin || '').slice(0, 10)}. Revisa su impacto en el plan.`,
    cta: 'Ver Reprogramación →',
    parada,
  });
  if (!problemas.length) return;
  const box = document.createElement('div');
  box.id = 'sistemaRecomiendaBox';
  box.innerHTML = `
    <p class="modulo-subtitulo" style="margin:0 0 8px;">Atención requerida</p>
    <div class="atencion-grid">
      ${problemas.map((p, i) => `
        <div class="atencion-card ${p.severidad}" data-ir="${p.modulo}" data-idx="${i}">
          <div class="ico">${p.ico}</div>
          <div class="txt"><p class="t">${p.titulo}</p><p class="s">${p.txt}</p></div>
          <span class="cta">${p.cta || 'Abrir análisis →'}</span>
        </div>`).join('')}
    </div>`;
  document.getElementById('simConsola').insertBefore(box, document.getElementById('consolaHeroGrid').closest('.consola-hero').nextSibling);
  box.querySelectorAll('[data-ir]').forEach(el => el.addEventListener('click', () => irAModuloSimulador(el.dataset.ir)));
}
function irAModuloSimulador(modulo) {
  document.getElementById('simConsola').style.display = 'none';
  document.getElementById('simModuloActivo').style.display = 'block';
  document.querySelectorAll('.panel-modulo').forEach(p => p.classList.toggle('activo', p.dataset.modulo === modulo));
  SIM_TAB_ACTIVA = modulo;
  document.getElementById('resultadoEjecutivoCard').style.display = 'none';
  document.getElementById('simEstado').textContent = '';
  if (modulo === 'capacidad') calcularModulo1();
  else if (modulo === 'reprogramacion') calcularModuloReprogramacion();
  else if (modulo === 'sobrestock') calcularModulo4();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function initMenuModulos() {
  const wrap = document.getElementById('menuModulos');
  if (!wrap || wrap.dataset.wired) return;
  wrap.dataset.wired = '1';
  wrap.querySelectorAll('[data-ir]').forEach(el => el.addEventListener('click', () => irAModuloSimulador(el.dataset.ir)));
  const btnVolver = document.getElementById('btnVolverConsola');
  if (btnVolver && !btnVolver.dataset.wired) {
    btnVolver.dataset.wired = '1';
    btnVolver.addEventListener('click', async () => {
      document.getElementById('simModuloActivo').style.display = 'none';
      document.getElementById('simConsola').style.display = 'block';
      await renderConsolaHero();
    });
  }
}
function calcularCapacidadTeoricaMax() {
  const porRecurso = new Map();
  (CFG.lineas || []).forEach(l => {
    const planta = (l.planta || '').toUpperCase().replace('Á', 'A').trim();
    const lineaRaw = (l.linea || '').toUpperCase().trim();
    const linea = lineaRaw.startsWith('L') ? lineaRaw : 'L' + lineaRaw;
    const recurso = `${planta}|${linea}`;
    const horas = Number(l.horas) || 0;
    const rate = Number(l.rate) || 0;
    if (!horas || !rate) return;
    const capDia = horas * rate;
    const prev = porRecurso.get(recurso) || 0;
    porRecurso.set(recurso, Math.max(prev, capDia));
  });
  let total = 0;
  porRecurso.forEach(v => { total += v; });
  return total;
}
function diasDelMesActual() {
  const hoy = new Date();
  return new Date(hoy.getFullYear(), hoy.getMonth() + 1, 0).getDate();
}
function calcularCapacidadTeoricaMaxMes() {
  return calcularCapacidadTeoricaMax() * diasDelMesActual();
}
async function calcularModulo1() {
  const capacidadDisponible = calcularCapacidadTeoricaMaxMes();
  const demandaMes = await calcularDemandaPedidosDelMes();
  const deficitCapacidad = Math.max(demandaMes - capacidadDisponible, 0);
  const riesgo = await calcularRiesgoRealSkus();
  const bloqueOk = document.getElementById('m1EstadoOk');
  const bloqueDeficit = document.getElementById('m1BloqueDeficit');
  const pctCapacidad = capacidadDisponible > 0 ? (demandaMes / capacidadDisponible) * 100 : null;
  const primeraFecha = riesgo.detalle[0]?.fecha || '—';
  document.getElementById('m1Pedidos').textContent = fmtTM(demandaMes);
  document.getElementById('m1Capacidad').textContent = fmtTM(capacidadDisponible);
  document.getElementById('m1PctCapacidad').textContent = pctCapacidad != null ? `${fmtNum(pctCapacidad, 0)}%` : '—';
  document.getElementById('m1SinCobertura').textContent = fmtTM(riesgo.deficitTotal);
  document.getElementById('m1SinCoberturaSub').textContent = riesgo.deficitTotal > 0
    ? `${riesgo.skusAfectados.size} SKU · primer quiebre el ${primeraFecha}` : 'sin quiebres reales pendientes';
  if (deficitCapacidad <= 0 && riesgo.deficitTotal <= 0) {
    bloqueDeficit.style.display = 'none';
    bloqueOk.style.display = 'block';
    bloqueOk.innerHTML = `
      <div class="estado-ok-banner">
        <div class="check">✓</div>
        <div class="txt">
          <p class="t">El plan puede ejecutarse sin restricciones</p>
          <p class="s">La capacidad disponible (${fmtTM(capacidadDisponible)}) cubre los pedidos del mes (${fmtTM(demandaMes)}), y
          el stock actual (disponible + tránsito) alcanza para todos los pedidos pendientes verificados por fecha.
          Ningún cliente ni pedido está en riesgo ahora mismo.</p>
        </div>
      </div>`;
    return;
  }
  bloqueOk.style.display = 'none';
  bloqueDeficit.style.display = 'block';
  const ordenadosClientes = [...riesgo.clientesMap.entries()].sort((a, b) => {
    const fa = a[1].fechaMin || '9999-99-99', fb = b[1].fechaMin || '9999-99-99';
    if (fa !== fb) return fa.localeCompare(fb);
    return b[1].margen - a[1].margen;
  });
  document.getElementById('m1Recomendacion').innerHTML = `
    <p class="recomendacion-titulo">Prioridad de atención por cliente — fecha límite más próxima primero, mayor margen como desempate</p>
    <div id="m1RankingClientes">
      ${ordenadosClientes.slice(0, 12).map(([cliente, v], i) => {
        const idCol = `m1Cliente${i}`;
        const filasCliente = riesgo.detalle.filter(d => d.cliente === cliente).sort((a, b) => String(a.fecha).localeCompare(String(b.fecha)));
        let acum = 0;
        const totalMargen = filasCliente.reduce((a, d) => a + (d.valor || 0), 0);
        return `
        <div class="colapsable cerrado" id="${idCol}" style="margin-top:${i === 0 ? 0 : 10}px;">
          <div class="colapsable-head ranking-cliente" data-toggle="${idCol}">
            <div class="num">${i + 1}</div>
            <div class="nombre">${cliente}<br><span style="font-weight:normal;font-size:11px;color:var(--gray-500);">fecha límite más próxima: ${v.fechaMin || '—'}</span></div>
            <div class="metricas">
              <div class="metrica"><div class="v">${fmtTM(v.tm)}</div><div class="l">Sin cobertura</div></div>
              <div class="metrica"><div class="v">${fmtUSD(v.margen)}</div><div class="l">Margen</div></div>
            </div>
            <span class="chev">▼</span>
          </div>
          <div class="colapsable-body">
            <table>
              <thead><tr><th>Fecha entrega</th><th>SKU</th><th>Producto</th>
                <th class="num-right">TM sin cobertura</th><th class="num-right">Acumulado</th><th class="num-right">Valor</th></tr></thead>
              <tbody>${filasCliente.map(d => {
                acum += d.tm;
                return `<tr><td>${d.fecha}</td><td>${d.sku}</td><td style="font-size:12.5px;">${(d.descripcion || '').slice(0, 30)}</td>
                  <td class="num-right">${fmtTM(d.tm)}</td><td class="num-right" style="color:var(--gray-600);">${fmtTM(acum)}</td>
                  <td class="num-right">${fmtUSD(d.valor)}</td></tr>`;
              }).join('')}</tbody>
              <tfoot><tr style="font-weight:bold;border-top:2px solid var(--emerald-700);">
                <td colspan="3">Total</td><td class="num-right">${fmtTM(acum)}</td><td></td><td class="num-right">${fmtUSD(totalMargen)}</td></tr></tfoot>
            </table>
          </div>
        </div>`;
      }).join('')}
    </div>`;
  initColapsables(document.getElementById('m1RankingClientes'));
  const puedeReprogramar = riesgo.deficitTotal > 0 || deficitCapacidad > 0;
  document.getElementById('m1Conclusion').innerHTML = `
    <p class="tag">Conclusión ejecutiva</p>
    <p class="pregunta">¿Qué está pasando?</p>
    <p class="respuesta">${riesgo.skusAfectados.size} SKU tienen pedidos que no llegan a cubrirse a tiempo con el stock disponible + tránsito actual — el primer quiebre real cae el ${primeraFecha}.</p>
    <p class="pregunta">¿Cuál es el impacto?</p>
    <p class="respuesta">${riesgo.clientesMap.size} cliente(s) afectados, ${fmtTM(riesgo.deficitTotal)} sin cobertura.</p>
    <p class="pregunta">¿Qué recomienda el sistema?</p>
    <p class="respuesta">Atender primero a los clientes con fecha límite más próxima (ranking arriba) — revisa primero sus SKU con Producción, puede requerir adelantar una corrida o negociar fecha con el cliente.
      ${puedeReprogramar ? ' <a href="#" data-reprogramar="1" style="color:var(--emerald-700);font-weight:700;">Ver reprogramación recomendada →</a>' : ''}</p>
    <p class="pregunta">¿Qué resultado se espera?</p>
    <p class="respuesta">Corre el escenario abajo para confirmar el margen y riesgo real que arroja el modelo completo con esta priorización aplicada.</p>`;
  const linkReprog = document.querySelector('#m1Conclusion [data-reprogramar]');
  if (linkReprog) linkReprog.addEventListener('click', (e) => { e.preventDefault(); irAModuloSimulador('reprogramacion'); });
}
/* Punto 2 del ajuste "una sola fuente de riesgo": antes, un SKU con
   pedidos en riesgo pero SIN ninguna corrida de motivo BUFFER (todas
   sus corridas ya son PEDIDO, comprometidas) caía como "sin opción" --
   como si el plan no lo produjera. En realidad el plan SÍ lo produce,
   solo que llega tarde para esa entrega puntual y sus corridas no se
   pueden mover. Se distingue con el nuevo tipo 'stockout_tarde_comprometido'. */
async function calcularSugerenciasReprogramacion() {
  const capMap = mapaCapacidadPorRecurso();
  const hoy = hoyStr();
  const sugerenciasParadas = [];
  const sugerenciasStockout = [];
  const sugerenciasSobrestock = [];
  await cargarPedidosDelPlan();
  const margenes = await cargarMargenSku();
  const paradasVigentes = (PARADAS_PROGRAMADAS || []).filter(p => String(p.fecha_fin || '').slice(0, 10) >= hoy);
  for (const parada of paradasVigentes) {
    const recurso = recursoDeParadaObj(parada);
    const [planta, linea] = recurso.split('|');
    const inicio = String(parada.fecha_inicio || '').slice(0, 10);
    const fin = String(parada.fecha_fin || '').slice(0, 10);
    const enVentana = (PROD.plan || []).filter(p => recursoDePlanRow(p) === recurso && p.fecha >= inicio && p.fecha <= fin && p.motivo !== 'YA PRODUCIDO');
    const tmPerdida = enVentana.reduce((a, p) => a + (p.tm || 0), 0);
    if (tmPerdida <= 0.01) continue;

    const skusAfectados = [...new Set(enVentana.map(p => p.sku))];
    const clientesSet = new Set();
    let pedidosAfectados = 0;
    skusAfectados.forEach(sku => {
      (PLAN_PEDIDOS.get(String(sku).trim()) || []).forEach(p => {
        const f = parseFechaLocal(p.fecha_entrega);
        if (f && f >= parseFechaLocal(inicio) && f <= parseFechaLocal(fin) && (p.cantidad_tm || 0) > 0) {
          pedidosAfectados++;
          clientesSet.add(p.cliente_nombre || p.cliente || '—');
        }
      });
    });

    const margenProm = skusAfectados.length
      ? skusAfectados.reduce((a, s) => a + (margenes.get(String(s).trim()) || 0), 0) / skusAfectados.length : 0;
    const costoParada = tmPerdida * margenProm;
    const sobrecostoEfectivo = margenProm * 0.15;
    const costoSobreproducir = tmPerdida * sobrecostoEfectivo;
    const conviene = costoSobreproducir < costoParada ? 'sobreproducir' : 'parar';
    const ahorro = Math.abs(costoParada - costoSobreproducir);

    const corridaBuffer = enVentana.filter(p => p.motivo === 'BUFFER').sort((a, b) => b.tm - a.tm)[0];
    let destino = null;
    if (corridaBuffer) {
      destino = buscarDiaConEspacio(corridaBuffer.sku, recurso, corridaBuffer.horas || 0, hoy, sumarDiaStr(inicio, -1), capMap);
    }

    sugerenciasParadas.push({
      tipo: 'parada', planta, linea, inicio, fin, tmPerdida, pedidosAfectados,
      clientes: clientesSet.size, costoParada, costoSobreproducir, conviene, ahorro,
      origen: corridaBuffer ? { sku: corridaBuffer.sku, fecha: corridaBuffer.fecha, linea: corridaBuffer.linea } : null,
      destino: destino ? { fecha: destino, linea } : null,
    });
  }
  const riesgo = await calcularRiesgoRealSkus();
  const porSku = new Map();
  riesgo.detalle.forEach(d => {
    const key = String(d.sku).replace('.0', '').trim();
    const prev = porSku.get(key);
    if (!prev) { porSku.set(key, { ...d, tm: d.tm, nPedidos: 1 }); return; }
    prev.tm += d.tm;
    prev.nPedidos += 1;
    if (String(d.fecha).localeCompare(String(prev.fecha)) < 0) { prev.fecha = d.fecha; prev.cliente = d.cliente; }
  });
  const pedidosUrgentes = [...porSku.values()].sort((a, b) => String(a.fecha).localeCompare(String(b.fecha))).slice(0, 6);
  for (const d of pedidosUrgentes) {
    const key = String(d.sku).replace('.0', '').trim();
    const todasCorridas = (PROD.plan || [])
      .filter(p => String(p.sku).replace('.0', '').trim() === key && (p.tm || 0) > 0)
      .sort((a, b) => a.fecha.localeCompare(b.fecha));
    const candidatas = todasCorridas.filter(p => p.motivo === 'BUFFER');
    if (!candidatas.length) {
      if (todasCorridas.length) {
        sugerenciasStockout.push({
          tipo: 'stockout_tarde_comprometido', sku: d.sku, descripcion: d.descripcion,
          cliente: d.cliente, fecha: d.fecha, tm: d.tm, tipoRiesgo: d.tipo,
          fechasCorridas: todasCorridas.map(p => p.fecha),
        });
      } else {
        sugerenciasStockout.push({ tipo: 'stockout_sin_opcion', sku: d.sku, descripcion: d.descripcion, cliente: d.cliente, fecha: d.fecha, tm: d.tm });
      }
      continue;
    }
    const corrida = candidatas[0];
    const recurso = recursoDePlanRow(corrida);
    const fechaLimite = sumarDiaStr(d.fecha, -LT_PRODUCCION_HOY);
    const destino = buscarDiaConEspacio(corrida.sku, recurso, corrida.horas || 0, hoy, fechaLimite, capMap);
    sugerenciasStockout.push({
      tipo: 'stockout', sku: d.sku, descripcion: d.descripcion, cliente: d.cliente, fechaPedido: d.fecha, tm: d.tm,
      origen: { sku: corrida.sku, fecha: corrida.fecha, linea: corrida.linea },
      destino: destino ? { fecha: destino, linea: corrida.linea } : null,
    });
  }
  const bufferHoy = (DDMRP.bufferPt || []).filter(b => b.mes_offset === 0 && b.estado === 'ok');
  const excesosPorSku = new Map();
  bufferHoy.forEach(b => {
    const n = nfpDeSku(b.sku);
    if (!n || !(b.tog > 0)) return;
    const disponible = (n.disponible_tm || 0) + (n.transito_tm || 0);
    if (disponible <= b.tog) return;
    const key = String(b.sku).replace('.0', '').trim();
    const exceso = disponible - b.tog;
    const prev = excesosPorSku.get(key);
    if (!prev || exceso > prev.exceso) excesosPorSku.set(key, { sku: b.sku, descripcion: descripcionSku(b.sku) || n.descripcion, exceso });
  });
  const excesos = [...excesosPorSku.values()].sort((a, b) => b.exceso - a.exceso);
  for (const e of excesos.slice(0, 5)) {
    const key = String(e.sku).replace('.0', '').trim();
    const proxima = (PROD.plan || [])
      .filter(p => String(p.sku).replace('.0', '').trim() === key && p.motivo === 'BUFFER' && p.fecha >= hoy)
      .sort((a, b) => a.fecha.localeCompare(b.fecha))[0];
    if (!proxima) continue;
    const recurso = recursoDePlanRow(proxima);
    const ultimoDiaHorizonte = [...new Set((PROD.plan || []).map(p => p.fecha))].sort().slice(-1)[0];
    const destino = buscarDiaConEspacio(proxima.sku, recurso, proxima.horas || 0, sumarDiaStr(proxima.fecha, 1), ultimoDiaHorizonte, capMap);
    sugerenciasSobrestock.push({
      tipo: 'sobrestock', sku: e.sku, descripcion: e.descripcion, exceso: e.exceso,
      origen: { sku: proxima.sku, fecha: proxima.fecha, linea: proxima.linea },
      destino: destino ? { fecha: destino, linea: proxima.linea } : null,
    });
  }
  return { paradas: sugerenciasParadas, stockout: sugerenciasStockout, sobrestock: sugerenciasSobrestock };
}
async function aplicarMovimientoCorrida(origen, destino, elBoton) {
  if (!origen || !destino) return false;
  if (elBoton) { elBoton.disabled = true; elBoton.textContent = 'Aplicando…'; }
  const snapshot = JSON.parse(JSON.stringify(PROD.plan));
  const fila = PROD.plan.find(p => p.sku === origen.sku && p.fecha === origen.fecha && String(p.linea) === String(origen.linea));
  const plantaOrigen = fila ? (fila.planta || (fila.recurso || '').split('|')[0]) : null;
  if (fila) { fila.fecha = destino.fecha; fila.linea = destino.linea; if (plantaOrigen) fila.recurso = `${plantaOrigen}|${destino.linea}`; }
  try {
    const resp = await fetch(API_BASE + '/api/v1/produccion/simular', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sku: origen.sku, fecha_origen: origen.fecha, linea_origen: String(origen.linea),
        fecha_destino: destino.fecha, linea_destino: String(destino.linea),
        motivo_usuario: `Reprogramado desde recomendación del sistema (${new Date().toLocaleString('es-EC')})`,
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const r = await resp.json();
    PROD.plan = Array.isArray(r.plan) ? r.plan : PROD.plan;
    return true;
  } catch (err) {
    PROD.plan = snapshot;
    if (elBoton) { elBoton.disabled = false; elBoton.textContent = 'Aplicar'; }
    alert(`No se pudo aplicar el movimiento: ${err.message}`);
    return false;
  }
}
function tarjetaSugerenciaParada(s, idx) {
  const puedeAplicar = s.conviene === 'sobreproducir' && s.origen && s.destino;
  return `
    <div class="card" style="margin-bottom:12px;border-left:4px solid var(--amber-700);">
      <p class="panel-title" style="margin:0 0 4px;">⏸ Parada ${s.planta} · Línea ${s.linea} — ${s.inicio} al ${s.fin}</p>
      <p class="panel-sub" style="margin:0 0 8px;">${fmtTM(s.tmPerdida)} de producción programada dentro de la ventana, afectando ${s.pedidosAfectados} pedido(s) de ${s.clientes} cliente(s).</p>
      <p style="font-size:13px;margin:0 0 8px;">
        ${s.conviene === 'sobreproducir'
          ? `<b>Recomendado: producir anticipadamente.</b> Ahorra ${fmtUSD(s.ahorro)} frente a dejar la línea parada (costo de parar ${fmtUSD(s.costoParada)} vs. adelantar ${fmtUSD(s.costoSobreproducir)}).`
          : `<b>Recomendado: aceptar la parada.</b> Adelantar producción costaría ${fmtUSD(s.costoSobreproducir)}, más que el margen que se perdería (${fmtUSD(s.costoParada)}).`}
      </p>
      ${puedeAplicar
        ? `<p style="font-size:12.5px;color:var(--gray-600);margin:0 0 8px;">Mover <b>${s.origen.sku}</b> del ${s.origen.fecha} → <b>${s.destino.fecha}</b> (misma línea, antes de la parada).</p>
           <button class="btn btn-primary btn-small" data-aplicar-parada="${idx}">Aplicar</button>`
        : s.conviene === 'sobreproducir'
          ? `<p style="font-size:12.5px;color:var(--gray-400);">No se encontró un día con espacio disponible antes de la parada para adelantar automáticamente — ajusta manualmente en la línea de tiempo abajo.</p>`
          : ''}
    </div>`;
}
function tarjetaSugerenciaStockout(s, idx) {
  const sufijoPedidos = s.nPedidos > 1 ? `(${s.nPedidos} pedidos, primero de ${s.cliente})` : `(${s.cliente})`;
  if (s.tipo === 'stockout_sin_opcion') {
    return `
      <div class="card" style="margin-bottom:12px;border-left:4px solid var(--red-700);">
        <p class="panel-title" style="margin:0 0 4px;">🔴 ${s.sku} — ${(s.descripcion || '').slice(0, 34)}</p>
        <p class="panel-sub" style="margin:0;">Entrega más próxima ${s.fecha}${sufijoPedidos} · faltan ${fmtTM(s.tm)} en total — no hay ninguna corrida de este SKU en el plan que se pueda adelantar. Requiere hora extra, maquila o negociar la fecha con el cliente.</p>
      </div>`;
  }
  if (s.tipo === 'stockout_tarde_comprometido') {
    const color = s.tipoRiesgo === 'SIN_COBERTURA' ? 'var(--red-700)' : 'var(--amber-700)';
    const icono = s.tipoRiesgo === 'SIN_COBERTURA' ? '🔴' : '🟡';
    const sufijoPedidos2 = s.nPedidos > 1 ? ` (${s.nPedidos} pedidos, primero de ${s.cliente})` : ` (${s.cliente})`;
    return `
      <div class="card" style="margin-bottom:12px;border-left:4px solid ${color};">
        <p class="panel-title" style="margin:0 0 4px;">${icono} ${s.sku} — ${(s.descripcion || '').slice(0, 34)}</p>
        <p class="panel-sub" style="margin:0;">Entrega más próxima ${s.fecha}${sufijoPedidos2} · faltan ${fmtTM(s.tm)} en total. El plan sí lo produce (fechas: ${s.fechasCorridas.join(', ')}), pero llega tarde para esta entrega. Sus corridas están comprometidas con pedidos y no se pueden mover desde aquí.</p>
      </div>`;
  }
  return `
    <div class="card" style="margin-bottom:12px;border-left:4px solid var(--red-700);">
      <p class="panel-title" style="margin:0 0 4px;">🔴 ${s.sku} — ${(s.descripcion || '').slice(0, 34)}</p>
      <p class="panel-sub" style="margin:0 0 8px;">Entrega más próxima ${s.fechaPedido}${sufijoPedidos} · faltan ${fmtTM(s.tm)} en total.</p>
      ${s.destino
        ? `<p style="font-size:12.5px;color:var(--gray-600);margin:0 0 8px;">Adelantar la corrida de ${s.origen.fecha} (Línea ${s.origen.linea}) → <b>${s.destino.fecha}</b>, a tiempo para esta entrega.</p>
           <button class="btn btn-primary btn-small" data-aplicar-stockout="${idx}">Aplicar</button>`
        : `<p style="font-size:12.5px;color:var(--gray-400);">Hay una corrida futura de este SKU (${s.origen.fecha}, Línea ${s.origen.linea}) pero no se encontró un día con espacio libre para adelantarla a tiempo — ajusta manualmente abajo.</p>`}
    </div>`;
}
function tarjetaSugerenciaSobrestock(s, idx) {
  return `
    <div class="card" style="margin-bottom:12px;border-left:4px solid var(--emerald-700);">
      <p class="panel-title" style="margin:0 0 4px;">▤ ${s.sku} — ${(s.descripcion || '').slice(0, 34)}</p>
      <p class="panel-sub" style="margin:0 0 8px;">+${fmtTM(s.exceso)} sobre el inventario máximo (TOG).</p>
      ${s.destino
        ? `<p style="font-size:12.5px;color:var(--gray-600);margin:0 0 8px;">Posponer la corrida del ${s.origen.fecha} (Línea ${s.origen.linea}) → <b>${s.destino.fecha}</b>, dando tiempo a que baje el exceso.</p>
           <button class="btn btn-secondary btn-small" data-aplicar-sobrestock="${idx}">Aplicar</button>`
        : `<p style="font-size:12.5px;color:var(--gray-400);">No se encontró un día con espacio más adelante en el horizonte para posponerla — considera no producirla esta semana.</p>`}
    </div>`;
}
async function calcularModuloReprogramacion() {
  const cont = document.getElementById('reprogSugerencias');
  if (!cont) return;
  cont.innerHTML = `<p style="font-size:13px;color:var(--gray-400);">Analizando paradas, riesgo de cobertura y sobrestock…</p>`;
  const sug = await calcularSugerenciasReprogramacion();
  const total = sug.paradas.length + sug.stockout.length + sug.sobrestock.length;
  if (!total) {
    cont.innerHTML = `
      <div class="estado-ok-banner">
        <div class="check">✓</div>
        <div class="txt">
          <p class="t">No hay movimientos que recomendar ahora mismo</p>
          <p class="s">Sin paradas con impacto en el plan, sin pedidos en riesgo real de quiebre y sin SKU sobre su inventario máximo. El plan actual puede ejecutarse tal cual.</p>
        </div>
      </div>`;
  } else {
    cont.innerHTML = `
      ${sug.paradas.length ? `<p class="recomendacion-titulo">Paradas programadas con impacto en el plan — reprograma esto</p>${sug.paradas.map(tarjetaSugerenciaParada).join('')}` : ''}
      ${sug.stockout.length ? `<p class="recomendacion-titulo">Para evitar quiebres (pedidos en riesgo real) — reprograma esto</p>${sug.stockout.map(tarjetaSugerenciaStockout).join('')}` : ''}
      ${sug.sobrestock.length ? `<p class="recomendacion-titulo">Para evitar sobrestock — reprograma esto</p>${sug.sobrestock.map(tarjetaSugerenciaSobrestock).join('')}` : ''}`;

    cont.querySelectorAll('[data-aplicar-parada]').forEach(btn => btn.addEventListener('click', async () => {
      const s = sug.paradas[Number(btn.dataset.aplicarParada)];
      if (await aplicarMovimientoCorrida(s.origen, s.destino, btn)) calcularModuloReprogramacion();
    }));
    cont.querySelectorAll('[data-aplicar-stockout]').forEach(btn => btn.addEventListener('click', async () => {
      const s = sug.stockout[Number(btn.dataset.aplicarStockout)];
      if (await aplicarMovimientoCorrida(s.origen, s.destino, btn)) calcularModuloReprogramacion();
    }));
    cont.querySelectorAll('[data-aplicar-sobrestock]').forEach(btn => btn.addEventListener('click', async () => {
      const s = sug.sobrestock[Number(btn.dataset.aplicarSobrestock)];
      if (await aplicarMovimientoCorrida(s.origen, s.destino, btn)) calcularModuloReprogramacion();
    }));
  }
  const sel = document.getElementById('simPlantaSel');
  if (sel) {
    const plantas = [...new Set((PROD.plan || []).map(p => p.planta || (p.recurso || '').split('|')[0]))].filter(Boolean).sort();
    if (!SIM_PLANTA || !plantas.includes(SIM_PLANTA)) SIM_PLANTA = plantas[0] || null;
    sel.innerHTML = plantas.map(p => `<option value="${p}" ${p === SIM_PLANTA ? 'selected' : ''}>${p}</option>`).join('');
    if (!sel.dataset.wired) {
      sel.dataset.wired = '1';
      sel.addEventListener('change', () => { SIM_PLANTA = sel.value; renderTimelineArrastrable(); });
    }
  }
  renderTimelineArrastrable();
}
function ringOcupacion(pct, label) {
  const size = 132, r = 50, stroke = 14;
  const C = 2 * Math.PI * r;
  const p = pct == null ? 0 : Math.max(0, Math.min(1, pct / 100));
  const color = pct == null ? 'var(--gray-200)' : pct > 100 ? 'var(--red-700)' : pct > 90 ? 'var(--amber-700)' : 'var(--emerald-600)';
  return `
    <div style="text-align:center;">
      <div style="position:relative;width:${size}px;height:${size}px;margin:0 auto;">
        <svg width="${size}" height="${size}" viewBox="0 0 120 120" style="transform:rotate(-90deg);">
          <circle cx="60" cy="60" r="${r}" fill="none" stroke="var(--gray-200)" stroke-width="${stroke}"/>
          <circle cx="60" cy="60" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
                  stroke-linecap="round" stroke-dasharray="${p * C} ${C}"/>
        </svg>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
                    font-size:24px;font-weight:800;font-family:'Consolas','SF Mono',monospace;
                    color:var(--ink);letter-spacing:-.5px;">${pct == null ? '—' : fmtNum(pct, 0) + '%'}</div>
      </div>
      <div style="margin-top:10px;font-size:11px;font-weight:700;color:var(--gray-600);
                  text-transform:uppercase;letter-spacing:.4px;max-width:132px;line-height:1.4;">${label}</div>
    </div>`;
}
function calcularModulo4() {
  const bufferHoy = (DDMRP.bufferPt || []).filter(b => b.mes_offset === 0 && b.estado === 'ok');
  const excesos = [];
  bufferHoy.forEach(b => {
    const n = nfpDeSku(b.sku);
    if (!n || !(b.tog > 0)) return;
    const disponible = (n.disponible_tm || 0) + (n.transito_tm || 0);
    if (disponible <= b.tog) return;
    excesos.push({ sku: b.sku, descripcion: descripcionSku(b.sku) || n.descripcion, stock: disponible, tog: b.tog, exceso: disponible - b.tog, adu: b.adu || 0 });
  });
  excesos.sort((a, b) => b.exceso - a.exceso);
  const invTotal = (NFP.datos || []).reduce((a, r) => a + (r.disponible_tm || 0) + (r.transito_tm || 0), 0);
  const capacidadMax = CFG.capacidadPtTm || null;
  const bodegaPct = capacidadMax ? (invTotal / capacidadMax) * 100 : null;
  const diferencia = capacidadMax != null ? invTotal - capacidadMax : null;
  document.getElementById('m4StockActual').textContent = fmtTM(invTotal);
  document.getElementById('m4CapacidadMax').textContent = capacidadMax != null ? fmtTM(capacidadMax) : '—';
  const elDif = document.getElementById('m4Diferencia');
  elDif.textContent = diferencia != null ? (diferencia > 0 ? '+' : '') + fmtTM(diferencia) : '—';
  elDif.className = 'kpi-value ' + (diferencia > 0 ? 'danger' : 'neutral');
  document.getElementById('m4PctOcupacion').textContent = bodegaPct != null ? fmtNum(bodegaPct, 0) + '%' : '—';
  document.getElementById('m4PctOcupacionSub').textContent = bodegaPct == null ? '—' : bodegaPct > 100 ? 'sobre el máximo' : bodegaPct >= 90 ? 'riesgo alto' : 'dentro de rango';
  const contTop = document.getElementById('m4TopExcesos');
  const resultado = document.getElementById('m4ResultadoRow');
  const conclusion = document.getElementById('m4Conclusion');
  if (!excesos.length) {
    contTop.innerHTML = `<p style="font-size:13px;color:var(--gray-400);">Ningún SKU está por encima de su inventario máximo ahora mismo.</p>`;
    resultado.innerHTML = ''; conclusion.innerHTML = '';
    return;
  }
  const META_OCUPACION_PCT = 80;
  const tmNecesarioLiberar = capacidadMax ? Math.max(0, invTotal - capacidadMax * (META_OCUPACION_PCT / 100)) : 0;
  const seleccion = new Set();
  let acumulado = 0;
  if (tmNecesarioLiberar > 0) {
    for (const e of excesos) {
      seleccion.add(e.sku);
      acumulado += e.exceso;
      if (acumulado >= tmNecesarioLiberar) break;
    }
  }
  const nuevaOcupacion = capacidadMax ? ((invTotal - acumulado) / capacidadMax) * 100 : null;
  contTop.innerHTML = `
    <table>
      <thead><tr>
        <th>SKU</th><th>Producto</th>
        <th class="num-right">TM excedidas</th>
        <th class="num-right">ADU</th>
        <th>Reanuda producción</th>
        <th>Estado</th>
      </tr></thead>
      <tbody>
        ${excesos.slice(0, 8).map((e, i) => {
          const dias = e.adu > 0 ? Math.ceil(e.exceso / e.adu) : null;
          const fechaReanuda = dias != null ? sumarDiaStr(hoyStr(), dias) : null;
          const detener = seleccion.has(e.sku);
          return `<tr>
            <td><b>${e.sku}</b></td>
            <td style="font-size:12.5px;">${(e.descripcion || '').slice(0, 40)}</td>
            <td class="num-right" style="color:var(--red-700);">${fmtTM(e.exceso)}</td>
            <td class="num-right">${e.adu > 0 ? fmtNum(e.adu, 1) : '—'}</td>
            <td>${fechaReanuda || '—'}</td>
            <td><span class="badge-top" style="${detener ? 'background:var(--red-100);color:var(--red-700);' : 'background:var(--gray-100);color:var(--gray-600);'}">${detener ? 'Detener producción' : 'Sin acción por ahora'}</span></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
  resultado.innerHTML = `
    ${tarjetaImpacto(fmtTM(acumulado), 'TM que se liberarían')}
    ${tarjetaImpacto(bodegaPct != null ? fmtNum(bodegaPct, 0) + '%' : '—', 'Ocupación actual')}
    ${tarjetaImpacto(nuevaOcupacion != null ? fmtNum(nuevaOcupacion, 0) + '%' : '—', `Ocupación si detienes ${seleccion.size} SKU`)}`;
  conclusion.innerHTML = `
    <p class="tag">Conclusión ejecutiva</p>
    <p class="pregunta">¿Qué está pasando?</p>
    <p class="respuesta">${excesos.length} SKU están por encima de su inventario máximo. La bodega está al ${bodegaPct != null ? fmtNum(bodegaPct, 0) + '%' : '—'} de su capacidad.</p>
    <p class="pregunta">¿Cuál es el impacto?</p>
    <p class="respuesta">Capital inmovilizado y espacio de bodega comprometido para recibir nueva producción o materia prima.</p>
    <p class="pregunta">¿Qué recomienda el sistema?</p>
    <p class="respuesta">${seleccion.size > 0
      ? `Dejar de fabricar temporalmente los ${seleccion.size} SKU marcados "Detener producción" arriba — es lo mínimo necesario para que la bodega no baje de ${META_OCUPACION_PCT}% de ocupación. El resto está sobre su máximo individual pero no hace falta tocarlo todavía.`
      : `La bodega ya está dentro del rango mínimo de ${META_OCUPACION_PCT}% de ocupación — no hace falta detener producción de ningún SKU todavía, aunque algunos sigan sobre su inventario máximo individual.`}</p>
    <p class="pregunta">¿Qué resultado se espera?</p>
    <p class="respuesta">Corre el escenario abajo para confirmar que el plan no produce de más estos SKU y ver el margen resultante.</p>`;
}
let SIM_BASELINE = null;
async function capturarBaseline() {
  const k = kpisPlanDesdeDatos();
  const s = await calcularSituacionActual();
  SIM_BASELINE = {
    margen: (k ? k.tmTotal : 0) * 0,
    otif: s.otifPct,
    riesgoTm: 0,
    utilizacion: s.capacidadPct,
  };
}
function armarOverridesSegunModulo() {
  const overrides = {};
  if (SIM_TAB_ACTIVA === 'capacidad') {
    overrides.prioridad_cliente = {};
    PLAN_PEDIDOS.forEach((pedidos, sku) => {
      (pedidos || []).slice(0, 3).forEach(p => {
        const cli = p.cliente_nombre || p.cliente;
        if (!cli) return;
        if (!overrides.prioridad_cliente[sku]) overrides.prioridad_cliente[sku] = {};
        overrides.prioridad_cliente[sku][cli] = 2.0;
      });
    });
  }
  return overrides;
}
function nombreEscenarioSegunModulo() {
  const nombres = {
    capacidad: 'Capacidad y priorización de clientes',
    reprogramacion: 'Reprogramación de corridas',
    sobrestock: 'Verificación de sobrestock',
  };
  return nombres[SIM_TAB_ACTIVA] || 'Escenario';
}
function celdaAntesDespues(valorAntes, valorDespues, etiqueta, formatoFn, mejorSiSube) {
  const antesNum = typeof valorAntes === 'number' ? valorAntes : null;
  const despuesNum = typeof valorDespues === 'number' ? valorDespues : null;
  let deltaHtml = '';
  if (antesNum != null && despuesNum != null) {
    const delta = despuesNum - antesNum;
    const mejora = mejorSiSube ? delta > 0 : delta < 0;
    if (Math.abs(delta) > 0.01) {
      deltaHtml = `<p class="ad-delta ${mejora ? 'mejora' : 'empeora'}">${delta > 0 ? '▲' : '▼'} ${formatoFn(Math.abs(delta))}</p>`;
    }
  }
  return {
    antes: `<div class="ad-metrica"><p class="v">${formatoFn(valorAntes)}</p><p class="l">${etiqueta}</p></div>`,
    despues: `<div class="ad-metrica despues"><p class="v">${formatoFn(valorDespues)}</p><p class="l">${etiqueta}</p>${deltaHtml}</div>`,
  };
}
function renderResultadoEjecutivo() {
  const card = document.getElementById('resultadoEjecutivoCard');
  const grid = document.getElementById('antesDespuesGrid');
  const hist = document.getElementById('resultadoEjecutivoHistorial');
  if (!card || !SIM_RESULTADOS.length) return;
  card.style.display = 'block';
  const r = SIM_RESULTADOS[SIM_RESULTADOS.length - 1];
  const margenDespues = r.margen_bruto_usd ?? r.z_usd;
  const totalDemanda = (r.tm_producidas || 0) + (r.tm_riesgo || 0);
  const otifDespues = totalDemanda > 0 ? Math.max(0, 1 - (r.tm_riesgo || 0) / totalDemanda) * 100 : null;
  const antes = SIM_BASELINE || {};
  const cMargen = celdaAntesDespues(0, margenDespues, 'Margen', v => fmtUSD(v), true);
  const cOtif = celdaAntesDespues(antes.otif, otifDespues, 'OTIF', v => v != null ? fmtNum(v, 1) + '%' : '—', true);
  const cRiesgo = celdaAntesDespues(0, r.tm_riesgo, 'TM en riesgo', v => fmtTM(v), false);
  const cEstado = { antes: `<div class="ad-metrica"><p class="v">—</p><p class="l">Estado</p></div>`,
    despues: `<div class="ad-metrica despues"><p class="v">${r.estado === 'Optimal' ? 'ÓPTIMO' : r.estado.toUpperCase()}</p><p class="l">Estado</p></div>` };
  grid.innerHTML = `
    <div class="ad-col"><p class="ad-col-label">Antes</p>${cMargen.antes}${cOtif.antes}${cRiesgo.antes}${cEstado.antes}</div>
    <div class="ad-flecha">→</div>
    <div class="ad-col"><p class="ad-col-label">Después</p>${cMargen.despues}${cOtif.despues}${cRiesgo.despues}${cEstado.despues}</div>`;
  if (SIM_RESULTADOS.length > 1) {
    hist.innerHTML = `<p class="panel-sub" style="margin:0 0 6px;">Escenarios corridos en esta sesión:</p>
      <table><thead><tr><th>Escenario</th><th class="num-right">Margen</th><th class="num-right">TM riesgo</th><th>Estado</th></tr></thead>
      <tbody>${SIM_RESULTADOS.map(x => `<tr><td>${x.escenario}</td><td class="num-right">${fmtUSD(x.margen_bruto_usd ?? x.z_usd)}</td>
        <td class="num-right">${fmtTM(x.tm_riesgo)}</td><td>${x.estado}</td></tr>`).join('')}</tbody></table>`;
  } else { hist.innerHTML = ''; }
}
function initBotonCorrerEscenario() {
  const btn = document.getElementById('btnCorrerEscenario');
  if (!btn || btn.dataset.wired) return;
  btn.dataset.wired = '1';
  btn.addEventListener('click', async () => {
    const nombre = nombreEscenarioSegunModulo();
    const overrides = armarOverridesSegunModulo();
    const estado = document.getElementById('simEstado');
    estado.textContent = 'Resolviendo el modelo completo… puede tardar un momento.';
    btn.disabled = true;
    await capturarBaseline();
    try {
      const resp = await fetch(API_BASE + '/api/v1/produccion/escenario', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nombre, overrides }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const r = await resp.json();
      SIM_RESULTADOS.push(r);
      renderResultadoEjecutivo();
      estado.textContent = `Listo — ${nombre} (${r.estado})`;
    } catch (err) {
      estado.textContent = `Error corriendo el escenario: ${err.message}`;
    } finally { btn.disabled = false; }
  });
}
function initModulosSimulador() {
  initMenuModulos();
  initBotonCorrerEscenario();
}
async function renderSimuladorScreen() {
  const cargas = [cargarProduccion(), cargarPedidosDelPlan(), cargarDDMRP(), cargarNFP()];
  if (!PARADAS_CARGADAS) cargas.push(cargarParadasProgramadas());
  await Promise.all(cargas);
  initModulosSimulador();
  document.getElementById('simConsola').style.display = 'block';
  document.getElementById('simModuloActivo').style.display = 'none';
  await renderConsolaHero();
  const sel = document.getElementById('simPlantaSel');
  if (sel) {
    const plantas = [...new Set((PROD.plan || []).map(p => p.planta || (p.recurso || '').split('|')[0]))].filter(Boolean).sort();
    if (!SIM_PLANTA || !plantas.includes(SIM_PLANTA)) SIM_PLANTA = plantas[0] || null;
    sel.innerHTML = plantas.map(p => `<option value="${p}" ${p === SIM_PLANTA ? 'selected' : ''}>${p}</option>`).join('');
    if (!sel.dataset.wired) {
      sel.dataset.wired = '1';
      sel.addEventListener('change', () => { SIM_PLANTA = sel.value; renderTimelineArrastrable(); });
    }
  }
}
async function irASimularParada(parada) {
  await renderSimuladorScreen();
  SIM_PLANTA = parada.planta || SIM_PLANTA;
  irAModuloSimulador('reprogramacion');
}
let TL_DRAG = null;
const TL_EN_VUELO = new Set();
function claveCorridaTL(p) { return `${p.sku}|${p.fecha}|${p.linea}`; }
function lineaCompatible(sku, linea) {
  const key = String(sku).replace('.0', '').trim();
  const fila = CFG.compatibilidad.find(c => String(c.sku).replace('.0', '').trim() === key);
  if (!fila || !fila.lineas) return true;
  const permitidas = String(fila.lineas).split(',').map(s => s.trim()).filter(Boolean);
  return !permitidas.length || permitidas.includes(String(linea));
}
function renderTimelineArrastrable() {
  const cont = document.getElementById('timelineArrastrable');
  if (!cont || !SIM_PLANTA) { if (cont) cont.innerHTML = '<p style="color:var(--gray-400);font-size:13px;">Sin plan cargado.</p>'; return; }
  const planF = (PROD.plan || []).filter(p => String(p.planta || (p.recurso || '').split('|')[0]) === SIM_PLANTA && p.motivo !== 'YA PRODUCIDO' && p.fecha >= hoyStr());
  const dias = [...new Set(planF.map(p => p.fecha))].sort();
  const lineas = [...new Set(planF.map(p => String(p.linea)))].sort();
  if (!dias.length) { cont.innerHTML = `<p style="color:var(--gray-400);font-size:13px;">Sin plan futuro para ${SIM_PLANTA}.</p>`; return; }
  const nombresDia = ['do', 'lu', 'ma', 'mi', 'ju', 'vi', 'sa'];
  cont.innerHTML = lineas.map(linea => {
    const celdas = dias.map(dia => {
      const corridas = planF.filter(p => p.fecha === dia && String(p.linea) === linea);
      const f = parseFechaLocal(dia);
      const label = f ? `${nombresDia[f.getDay()]} ${f.getDate()}/${f.getMonth() + 1}` : dia;
      return `
        <div class="tl-celda" data-linea="${linea}" data-fecha="${dia}">
          <div class="tl-celda-fecha">${label}</div>
          ${corridas.map(c => {
            const bloqueada = c.motivo === 'PEDIDO';
            const key = claveCorridaTL({ sku: c.sku, fecha: c.fecha, linea: String(c.linea) });
            const cargando = TL_EN_VUELO.has(key);
            return `<div class="tl-bloque ${bloqueada ? 'bloqueada' : ''} ${cargando ? 'cargando' : ''}"
                       ${!bloqueada && !cargando ? 'draggable="true"' : ''}
                       data-sku="${c.sku}" data-fecha="${c.fecha}" data-linea="${c.linea}"
                       title="${descripcionSku(c.sku) || c.sku}">
                  <div class="tl-bloque-top"><span>${c.sku}</span>${bloqueada ? '🔒' : ''}</div>
                  <div class="tl-bloque-tm">${Math.round(c.tm)} TM</div>
                </div>`;
          }).join('')}
        </div>`;
    }).join('');
    return `<div class="tl-linea-label">Línea ${linea}</div><div class="tl-fila">${celdas}</div>`;
  }).join('');
  cont.querySelectorAll('.tl-bloque[draggable="true"]').forEach(el => {
    el.addEventListener('dragstart', () => { TL_DRAG = { sku: el.dataset.sku, fecha: el.dataset.fecha, linea: el.dataset.linea }; });
    el.addEventListener('dragend', () => { TL_DRAG = null; });
  });
  cont.querySelectorAll('.tl-bloque.bloqueada').forEach(el => el.addEventListener('click', () => irASkuEnBuffer(el.dataset.sku)));
  cont.querySelectorAll('.tl-celda').forEach(celda => {
    celda.addEventListener('dragover', e => { e.preventDefault(); celda.classList.add('sobrevuelo'); });
    celda.addEventListener('dragleave', () => celda.classList.remove('sobrevuelo'));
    celda.addEventListener('drop', e => { e.preventDefault(); celda.classList.remove('sobrevuelo'); soltarCorridaTL({ linea: celda.dataset.linea, fecha: celda.dataset.fecha }); });
  });
}
async function soltarCorridaTL(target) {
  if (!TL_DRAG) return;
  const origen = TL_DRAG; TL_DRAG = null;
  if (origen.linea === target.linea && origen.fecha === target.fecha) return;
  const aviso = document.getElementById('timelineArrastrableAviso');
  if (!lineaCompatible(origen.sku, target.linea)) {
    if (aviso) {
      aviso.style.display = 'block'; aviso.className = 'aviso-box warn';
      aviso.innerHTML = `⚠ Según la tabla de compatibilidad (Configuración), la línea ${target.linea} no está habilitada para ${origen.sku}. Se intentará igual, pero revisa el resultado.`;
    }
  } else if (aviso) { aviso.style.display = 'none'; }
  const key = claveCorridaTL(origen);
  TL_EN_VUELO.add(key);
  const snapshot = JSON.parse(JSON.stringify(PROD.plan));
  const fila = PROD.plan.find(p => p.sku === origen.sku && p.fecha === origen.fecha && String(p.linea) === origen.linea);
  if (fila) { fila.fecha = target.fecha; fila.linea = target.linea; fila.recurso = `${SIM_PLANTA}|${target.linea}`; }
  renderTimelineArrastrable();
  try {
    const resp = await fetch(API_BASE + '/api/v1/produccion/simular', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sku: origen.sku, fecha_origen: origen.fecha, linea_origen: origen.linea,
        fecha_destino: target.fecha, linea_destino: target.linea,
        motivo_usuario: `Reprogramado desde el Simulador (${new Date().toLocaleString('es-EC')})`,
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const r = await resp.json();
    PROD.plan = Array.isArray(r.plan) ? r.plan : PROD.plan;
    if (aviso && aviso.style.display !== 'block') { aviso.style.display = 'block'; aviso.className = 'aviso-box'; aviso.innerHTML = `Plan reoptimizado con la restricción aplicada. Revisa "Plan de Producción" para ver el resultado completo.`; }
  } catch (err) {
    PROD.plan = snapshot;
    if (aviso) { aviso.style.display = 'block'; aviso.className = 'aviso-box warn'; aviso.innerHTML = `No se pudo reprogramar ${origen.sku}: se revirtió el cambio.`; }
  } finally {
    TL_EN_VUELO.delete(key);
    renderTimelineArrastrable();
  }
}
const XLS_COLOR = {
  brandDark: 'FF0A2E22', brandMid: 'FF0F5F44', brandLight: 'FF188256',
  brandTint: 'FFD2ECDE', gray: 'FFCBD6CF', grayFill: 'FFF3F6F4', white: 'FFFFFFFF',
  red: 'FFB54B4B', amber: 'FFD9A441',
};
function xlsPlantasDelPlan() {
  return [...new Set((PROD.plan || []).map(p => p.planta || (p.recurso || '').split('|')[0]))]
    .filter(Boolean).sort();
}
function xlsLineasDePlanta(planta) {
  return [...new Set((PROD.plan || [])
    .filter(p => (p.planta || (p.recurso || '').split('|')[0]) === planta)
    .map(p => String(p.linea)))].sort();
}
function xlsRecursoDe(p) {
  return p.recurso || `${p.planta || ''}|${p.linea || ''}`;
}
function xlsCapacidadHorasLinea(recurso) {
  return horasLineaConfigurada(recurso) ?? 20;
}
function xlsTituloHoja(ws, texto, ultimaCol) {
  const fila = ws.addRow([texto]);
  ws.mergeCells(fila.number, 1, fila.number, ultimaCol);
  fila.height = 26;
  fila.getCell(1).font = { bold: true, size: 15, color: { argb: XLS_COLOR.white } };
  fila.getCell(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: XLS_COLOR.brandDark } };
  fila.getCell(1).alignment = { vertical: 'middle', horizontal: 'left', indent: 1 };
  return fila;
}
function xlsSubtitulo(ws, texto, ultimaCol) {
  const fila = ws.addRow([texto]);
  ws.mergeCells(fila.number, 1, fila.number, ultimaCol);
  fila.getCell(1).font = { italic: true, size: 10.5, color: { argb: 'FF4A4A45' } };
  fila.getCell(1).alignment = { vertical: 'middle', horizontal: 'left', indent: 1 };
  return fila;
}
function xlsEncabezadoBloque(ws, texto, ultimaCol, colorFondo) {
  const fila = ws.addRow([texto]);
  ws.mergeCells(fila.number, 1, fila.number, ultimaCol);
  fila.height = 20;
  fila.getCell(1).font = { bold: true, size: 12, color: { argb: XLS_COLOR.white } };
  fila.getCell(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: colorFondo || XLS_COLOR.brandMid } };
  fila.getCell(1).alignment = { vertical: 'middle', horizontal: 'left', indent: 1 };
  return fila;
}
function xlsBordeFino(celda) {
  celda.border = { top: { style: 'thin', color: { argb: XLS_COLOR.gray } }, left: { style: 'thin', color: { argb: XLS_COLOR.gray } },
    bottom: { style: 'thin', color: { argb: XLS_COLOR.gray } }, right: { style: 'thin', color: { argb: XLS_COLOR.gray } } };
}
function xlsEncabezadoTabla(ws, encabezados) {
  const fila = ws.addRow(encabezados);
  fila.eachCell(c => {
    c.font = { bold: true, size: 10.5, color: { argb: XLS_COLOR.white } };
    c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: XLS_COLOR.brandLight } };
    c.alignment = { vertical: 'middle', horizontal: 'center', wrapText: true };
    xlsBordeFino(c);
  });
  return fila;
}
function xlsFilaTotal(ws, valores, colorFondo) {
  const fila = ws.addRow(valores);
  fila.eachCell(c => {
    c.font = { bold: true };
    c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: colorFondo || XLS_COLOR.brandTint } };
    xlsBordeFino(c);
  });
  return fila;
}
function xlsFilaTotalMerge2(ws, etiqueta, valores, colorFondo) {
  const fila = ws.addRow([etiqueta, '', ...valores]);
  ws.mergeCells(fila.number, 1, fila.number, 2);
  fila.eachCell(c => {
    c.font = { bold: true };
    c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: colorFondo || XLS_COLOR.brandTint } };
    xlsBordeFino(c);
  });
  return fila;
}
function xlsColorOcupacion(pct) {
  if (pct >= 95) return XLS_COLOR.red;
  if (pct >= 80) return XLS_COLOR.amber;
  return XLS_COLOR.brandLight;
}
function xlsConstruirHojaPlan(wb) {
  const ws = wb.addWorksheet('Plan Semanal', { views: [{ state: 'frozen', ySplit: 1 }] });
  const k = kpisPlanDesdeDatos();
  const nombresDia = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
  const anchoMax = 12;
  xlsTituloHoja(ws, 'Plan de Producción — Cargill Ecuador Aquanutrition', anchoMax);
  if (k) {
    xlsSubtitulo(ws, `Calculado: ${PROD.resumen && PROD.resumen.fecha_calculo ? PROD.resumen.fecha_calculo : '—'}  ·  ` +
      `Horizonte: ${fechaCorta(k.f0)}–${fechaCorta(k.f1)} (${k.nDias} días)  ·  ` +
      `Total del plan: ${Math.round(k.tmTotal).toLocaleString()} TM  ·  ` +
      `Pedidos: ${Math.round(k.tmPedido).toLocaleString()} TM  ·  Buffer: ${Math.round(k.tmBuffer).toLocaleString()} TM`, anchoMax);
  }
  ws.addRow([]);
  const plantas = xlsPlantasDelPlan();
  const totalPorDiaGeneral = new Map();
  plantas.forEach(planta => {
    const planPlanta = (PROD.plan || []).filter(p => (p.planta || (p.recurso || '').split('|')[0]) === planta);
    const dias = [...new Set(planPlanta.map(p => p.fecha))].sort();
    const ultimaCol = 2 + dias.length;
    const labelsDias = dias.map(f => { const d = parseFechaLocal(f); return d ? `${nombresDia[d.getDay()].slice(0, 3)} ${d.getDate()}/${d.getMonth() + 1}` : f; });

    xlsEncabezadoBloque(ws, `PLANTA: ${planta}`, ultimaCol, XLS_COLOR.brandDark);

    const lineas = xlsLineasDePlanta(planta);
    const totalPorDiaPlanta = dias.map(() => 0);

    lineas.forEach(linea => {
      const filasLinea = planPlanta.filter(p => String(p.linea) === linea);
      xlsEncabezadoTabla(ws, ['SKU', 'Descripción', ...labelsDias]);

      const skusOrden = []; const vistos = new Set();
      filasLinea.slice().sort((a, b) => a.fecha.localeCompare(b.fecha)).forEach(p => { if (!vistos.has(p.sku)) { vistos.add(p.sku); skusOrden.push(p.sku); } });

      skusOrden.forEach(sku => {
        const desc = descripcionSku(sku) || '—';
        const valores = dias.map(f => {
          const fila = filasLinea.find(p => p.fecha === f && p.sku === sku);
          return fila ? Math.round(fila.tm) : null;
        });
        const fila = ws.addRow([sku, desc, ...valores]);
        fila.eachCell((c, colNum) => { xlsBordeFino(c); if (colNum > 2) c.alignment = { horizontal: 'right' }; });
      });

      const totalesDiaLinea = dias.map((f, i) => {
        const t = filasLinea.filter(p => p.fecha === f).reduce((a, p) => a + (p.tm || 0), 0);
        totalPorDiaPlanta[i] += t;
        return t;
      });
      xlsFilaTotalMerge2(ws, `TOTAL LÍNEA ${linea}`, totalesDiaLinea.map(v => v > 0 ? Math.round(v) : '—'));
      ws.addRow([]);
    });

    dias.forEach((f, i) => { totalPorDiaGeneral.set(f, (totalPorDiaGeneral.get(f) || 0) + totalPorDiaPlanta[i]); });
    const filaTotalPlanta = xlsFilaTotalMerge2(ws, `TOTAL PLANTA ${planta}`, totalPorDiaPlanta.map(v => v > 0 ? Math.round(v) : '—'), XLS_COLOR.brandMid);
    filaTotalPlanta.eachCell(c => { c.font = { bold: true, color: { argb: XLS_COLOR.white } }; });
    ws.addRow([]); ws.addRow([]);
  });
  const diasGeneral = [...totalPorDiaGeneral.keys()].sort();
  if (diasGeneral.length) {
    const labelsGeneral = diasGeneral.map(f => { const d = parseFechaLocal(f); return d ? `${nombresDia[d.getDay()].slice(0, 3)} ${d.getDate()}/${d.getMonth() + 1}` : f; });
    xlsEncabezadoBloque(ws, 'TOTAL GENERAL — TODAS LAS PLANTAS', 1 + diasGeneral.length, XLS_COLOR.brandDark);
    xlsEncabezadoTabla(ws, ['Concepto', ...labelsGeneral]);
    const totalGeneral = diasGeneral.map(f => Math.round(totalPorDiaGeneral.get(f)));
    const filaGen = xlsFilaTotal(ws, ['TM totales del día', ...totalGeneral], XLS_COLOR.brandTint);
    filaGen.getCell(1).font = { bold: true };
  }
  ws.getColumn(1).width = 14;
  ws.getColumn(2).width = 28;
  for (let c = 3; c <= 40; c++) ws.getColumn(c).width = 10;
  return ws;
}
function xlsConstruirHojaAuditoria(wb) {
  const ws = wb.addWorksheet('Auditoría', { views: [{ state: 'frozen', ySplit: 1 }] });
  const nombresDia = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
  xlsTituloHoja(ws, 'Auditoría de Producción — TM y % de Ocupación por Día, Línea y Calibre', 12);
  xlsSubtitulo(ws, `"Calibre" se muestra al nivel de SKU. % de ocupación = horas programadas ÷ capacidad configurada de la línea (Configuración → Líneas de producción).`, 12);
  ws.addRow([]);
  const plantas = xlsPlantasDelPlan();
  plantas.forEach(planta => {
    const planPlanta = (PROD.plan || []).filter(p => (p.planta || (p.recurso || '').split('|')[0]) === planta);
    const dias = [...new Set(planPlanta.map(p => p.fecha))].sort();
    const ultimaCol = 2 + dias.length;
    const labelsDias = dias.map(f => { const d = parseFechaLocal(f); return d ? `${nombresDia[d.getDay()].slice(0, 3)} ${d.getDate()}/${d.getMonth() + 1}` : f; });

    xlsEncabezadoBloque(ws, `PLANTA: ${planta}`, ultimaCol, XLS_COLOR.brandDark);

    xlsLineasDePlanta(planta).forEach(linea => {
      const filasLinea = planPlanta.filter(p => String(p.linea) === linea);
      const recurso = xlsRecursoDe(filasLinea[0] || { planta, linea });
      const capHoras = xlsCapacidadHorasLinea(recurso);

      xlsEncabezadoBloque(ws, `Línea ${linea} · TM producidas por SKU/calibre`, ultimaCol, XLS_COLOR.brandMid);
      xlsEncabezadoTabla(ws, ['SKU / Calibre', 'Descripción', ...labelsDias]);
      const skusOrden = []; const vistos = new Set();
      filasLinea.slice().sort((a, b) => a.fecha.localeCompare(b.fecha)).forEach(p => { if (!vistos.has(p.sku)) { vistos.add(p.sku); skusOrden.push(p.sku); } });
      skusOrden.forEach(sku => {
        const valores = dias.map(f => {
          const fila = filasLinea.find(p => p.fecha === f && p.sku === sku);
          return fila ? Math.round(fila.tm) : null;
        });
        const fila = ws.addRow([sku, descripcionSku(sku) || '—', ...valores]);
        fila.eachCell((c, colNum) => { xlsBordeFino(c); if (colNum > 2) c.alignment = { horizontal: 'right' }; });
      });
      const totalesDiaTm = dias.map(f => filasLinea.filter(p => p.fecha === f).reduce((a, p) => a + (p.tm || 0), 0));
      xlsFilaTotalMerge2(ws, 'TOTAL LÍNEA (TM)', totalesDiaTm.map(v => v > 0 ? Math.round(v) : '—'));
      ws.addRow([]);

      xlsEncabezadoBloque(ws, `Línea ${linea} · % de ocupación por SKU/calibre (capacidad: ${capHoras} h/día)`, ultimaCol, XLS_COLOR.brandMid);
      xlsEncabezadoTabla(ws, ['SKU / Calibre', 'Descripción', ...labelsDias]);
      skusOrden.forEach(sku => {
        const valoresPct = dias.map(f => {
          const fila = filasLinea.find(p => p.fecha === f && p.sku === sku);
          if (!fila || !capHoras) return null;
          return Math.round(((fila.horas || 0) / capHoras) * 1000) / 10;
        });
        const fila = ws.addRow([sku, descripcionSku(sku) || '—', ...valoresPct.map(v => v != null ? v / 100 : null)]);
        fila.eachCell((c, colNum) => {
          xlsBordeFino(c);
          if (colNum > 2) {
            c.alignment = { horizontal: 'right' };
            if (c.value != null) {
              c.numFmt = '0.0%';
              c.font = { color: { argb: xlsColorOcupacion(c.value * 100) } };
            }
          }
        });
      });
      const totalesDiaPct = dias.map(f => {
        const horasDia = filasLinea.filter(p => p.fecha === f).reduce((a, p) => a + (p.horas || 0), 0);
        return capHoras ? horasDia / capHoras : 0;
      });
      const filaTotalPct = xlsFilaTotalMerge2(ws, 'TOTAL LÍNEA (%)', totalesDiaPct, XLS_COLOR.brandTint);
      totalesDiaPct.forEach((v, i) => {
        const c = filaTotalPct.getCell(3 + i);
        c.numFmt = '0.0%'; c.font = { bold: true, color: { argb: xlsColorOcupacion(v * 100) } };
      });
      ws.addRow([]); ws.addRow([]);
    });
  });
  ws.getColumn(1).width = 14;
  ws.getColumn(2).width = 28;
  for (let c = 3; c <= 40; c++) ws.getColumn(c).width = 10;
  return ws;
}
async function exportarPlanProduccionExcel() {
  const btn = document.getElementById('btnExportarPlanExcel');
  if (typeof ExcelJS === 'undefined') {
    alert('No se pudo cargar el generador de Excel (sin conexión a internet). Intenta de nuevo con conexión.');
    return;
  }
  if (!PROD.plan || !PROD.plan.length) {
    alert('No hay plan de producción cargado para exportar.');
    return;
  }
  const textoOriginal = btn ? btn.textContent : '';
  if (btn) { btn.textContent = 'Generando…'; btn.disabled = true; }
  try {
    const wb = new ExcelJS.Workbook();
    wb.creator = 'Cargill Ecuador — Simulador Inteligente de Abastecimiento';
    wb.created = new Date();
    xlsConstruirHojaPlan(wb);
    xlsConstruirHojaAuditoria(wb);

    const buffer = await wb.xlsx.writeBuffer();
    const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = URL.createObjectURL(blob);
    const fecha = (PROD.resumen && PROD.resumen.fecha_calculo) ? PROD.resumen.fecha_calculo : hoyStr();
    const a = document.createElement('a');
    a.href = url;
    a.download = `Plan_Produccion_Cargill_${fecha}.xlsx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error('Error exportando Excel del plan:', err);
    alert('No se pudo generar el Excel. Revisa la consola para más detalle.');
  } finally {
    if (btn) { btn.textContent = textoOriginal; btn.disabled = false; }
  }
}
function renderCfgLineasTabla() {
  const tbody = document.getElementById('cfgLineasTabla');
  if (!tbody) return;
  tbody.innerHTML = CFG.lineas.map((l, i) => {
    const tmDia = (Number(l.horas) || 0) * (Number(l.rate) || 0);
    return `<tr data-idx="${i}">
      <td><input type="text" value="${l.bu || ''}" data-f="bu"></td>
      <td><input type="text" value="${l.tipo || ''}" data-f="tipo"></td>
      <td><input type="text" value="${l.planta || ''}" data-f="planta"></td>
      <td><input type="text" value="${l.linea || ''}" data-f="linea"></td>
      <td><input type="number" step="0.01" value="${l.tamano ?? ''}" data-f="tamano"></td>
      <td><input type="number" step="0.5" value="${l.horas ?? ''}" data-f="horas"></td>
      <td><input type="number" step="0.1" value="${l.rate ?? ''}" data-f="rate"></td>
      <td class="num-right" data-tmdia>${tmDia.toFixed(0)}</td>
      <td><button class="btn btn-danger btn-small" data-del>✕</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    const idx = Number(tr.dataset.idx);
    tr.querySelectorAll('input').forEach(inp => {
      inp.addEventListener('input', () => {
        const f = inp.dataset.f;
        CFG.lineas[idx][f] = (f === 'tamano' || f === 'horas' || f === 'rate') ? parseFloat(inp.value) || 0 : inp.value;
        const tmDia = (Number(CFG.lineas[idx].horas) || 0) * (Number(CFG.lineas[idx].rate) || 0);
        tr.querySelector('[data-tmdia]').textContent = tmDia.toFixed(0);
      });
    });
    tr.querySelector('[data-del]').addEventListener('click', () => { CFG.lineas.splice(idx, 1); renderCfgLineasTabla(); });
  });
}
let PARADAS_PROGRAMADAS = [];
let PARADAS_CARGADAS = false;
async function cargarParadasProgramadas() {
  try {
    const r = await fetchJSON('/api/v1/config/paradas-programadas');
    PARADAS_PROGRAMADAS = Array.isArray(r.paradas) ? r.paradas : [];
  } catch (err) {
    PARADAS_PROGRAMADAS = [];
  }
  PARADAS_CARGADAS = true;
}
/* Punto 5 del ajuste "una sola fuente de riesgo": horas_dia en
   paradas_programadas = HORAS DE PARADA por día (24 = todo el día), no
   horas restantes de operación. La franja resumen refleja esa misma
   semántica: "parada total" solo si horas_dia cubre (o supera) las
   horas normales de la línea. */
function franjaParadasProgramadas(contenedorId) {
  const cont = document.getElementById(contenedorId);
  if (!cont) return;
  const hoy = hoyStr();
  const vigentes = (PARADAS_PROGRAMADAS || [])
    .filter(p => String(p.fecha_fin || '').slice(0, 10) >= hoy)
    .sort((a, b) => String(a.fecha_inicio || '').localeCompare(String(b.fecha_inicio || '')));
  if (!vigentes.length) { cont.innerHTML = ''; return; }
  const items = vigentes.slice(0, 6).map(p => {
    const activa = String(p.fecha_inicio || '').slice(0, 10) <= hoy;
    const linea = String(p.linea || '').toUpperCase();
    const lbl = linea.startsWith('L') ? linea : `L${linea}`;
    const recurso = `${String(p.planta || '').toUpperCase()}|${lbl}`;
    const horasNormales = horasLineaConfigurada(recurso) ?? 24;
    const horasParada = Number(p.horas_dia) || 0;
    const detalle = horasParada >= Math.min(horasNormales, 24) ? 'parada total' : `${horasParada} h parada/día`;
    return `<span class="pill ${activa ? 'pill-red' : 'pill-amber'}" style="margin:0 8px 4px 0;">
      ${p.planta || '—'} · ${lbl} — ${activa ? 'PARADA ACTIVA' : 'próxima'}:
      ${String(p.fecha_inicio || '').slice(0, 10)} → ${String(p.fecha_fin || '').slice(0, 10)} (${detalle})
    </span>`;
  }).join('');
  cont.innerHTML = `<div class="aviso-box" style="margin-bottom:14px;">
      <b>⏸ Paradas / mantenimiento programado en Configuración</b>
      <div style="margin-top:8px;">${items}</div>
    </div>`;
}
function renderCfgParadasTabla() {
  const tbody = document.getElementById('cfgParadasTabla');
  if (!tbody) return;
  if (!PARADAS_PROGRAMADAS.length) {
    tbody.innerHTML = `<tr><td colspan="9" style="color:var(--gray-400);">Sin paradas registradas — todas las líneas operan con su horario normal.</td></tr>`;
    return;
  }
  tbody.innerHTML = PARADAS_PROGRAMADAS.map((p, i) => `<tr data-idx="${i}">
      <td><input type="text" value="${p.planta || ''}" data-f="planta"></td>
      <td><input type="text" value="${p.linea || ''}" data-f="linea"></td>
      <td><input type="date" value="${(p.fecha_inicio || '').slice(0, 10)}" data-f="fecha_inicio"></td>
      <td><input type="date" value="${(p.fecha_fin || '').slice(0, 10)}" data-f="fecha_fin"></td>
      <td><input type="number" min="0" max="24" step="0.5" value="${p.horas_dia ?? 24}" data-f="horas_dia" style="max-width:90px;"></td>
      <td><select data-f="tipo" style="max-width:130px;">
            <option value="Programada" ${(!p.tipo || p.tipo === 'Programada') ? 'selected' : ''}>Programada</option>
            <option value="No planeada" ${p.tipo === 'No planeada' ? 'selected' : ''}>No planeada</option>
          </select></td>
      <td><input type="text" value="${p.motivo || ''}" data-f="motivo" placeholder="Opcional" style="min-width:120px;"></td>
      <td><button class="btn btn-secondary btn-small" data-simular="${i}">Ver en Simulador</button></td>
      <td><button class="btn btn-danger btn-small" data-del>✕</button></td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    const idx = Number(tr.dataset.idx);
    tr.querySelectorAll('input,select').forEach(inp => {
      const actualizar = () => {
        const f = inp.dataset.f;
        PARADAS_PROGRAMADAS[idx][f] = f === 'horas_dia' ? parseFloat(inp.value) || 0 : inp.value;
      };
      inp.addEventListener('input', actualizar);
      inp.addEventListener('change', actualizar);
    });
    tr.querySelector('[data-del]').addEventListener('click', () => {
      PARADAS_PROGRAMADAS.splice(idx, 1);
      renderCfgParadasTabla();
    });
  });
  tbody.querySelectorAll('[data-simular]').forEach(btn => {
    btn.addEventListener('click', () => irASimularParada(PARADAS_PROGRAMADAS[Number(btn.dataset.simular)]));
  });
}
function validarParadas() {
  const aviso = document.getElementById('cfgParadasAviso');
  const errores = [];
  PARADAS_PROGRAMADAS.forEach((p, i) => {
    if (!p.planta || !p.linea || !p.fecha_inicio || !p.fecha_fin) {
      errores.push(`Fila ${i + 1}: faltan datos (planta, línea o fechas).`);
    } else if (p.fecha_fin < p.fecha_inicio) {
      errores.push(`Fila ${i + 1} (${p.planta}|${p.linea}): la fecha fin es anterior al inicio.`);
    }
  });
  if (aviso) {
    if (errores.length) {
      aviso.style.display = 'block'; aviso.className = 'aviso-box warn';
      aviso.innerHTML = `⚠ Revisa antes de guardar:<br>${errores.join('<br>')}`;
    } else {
      aviso.style.display = 'none';
    }
  }
  return errores.length === 0;
}
async function guardarParadasEnBackend() {
  if (!validarParadas()) return false;
  try {
    const resp = await fetch(API_BASE + '/api/v1/config/paradas-programadas', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paradas: PARADAS_PROGRAMADAS.map(p => ({
        planta: p.planta, linea: p.linea, fecha_inicio: p.fecha_inicio,
        fecha_fin: p.fecha_fin, horas_dia: Number(p.horas_dia) || 0,
        tipo: p.tipo || 'Programada', motivo: p.motivo || '',
      })) }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (err) {
    console.error('No se pudieron guardar las paradas programadas', err);
    return false;
  }
}
async function consultarImpactoParada(p) {
  try {
    const resp = await fetch(API_BASE + '/api/v1/config/paradas-programadas/impacto', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        planta: p.planta, linea: p.linea,
        fecha_inicio: p.fecha_inicio, fecha_fin: p.fecha_fin,
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (err) {
    console.error('No se pudo calcular el impacto de la parada', err);
    return { filas: [], tm_total: 0 };
  }
}
async function reasignarCorridaDesdeImpacto(fila, btn) {
  await cargarProduccion();
  const capMap = mapaCapacidadPorRecurso();
  const linea = String(fila.linea || '').toUpperCase();
  const ln = linea.startsWith('L') ? linea : 'L' + linea;
  const recurso = fila.recurso || `${String(fila.planta || '').toUpperCase()}|${ln}`;
  const hoy = hoyStr();
  const noAntesDe = fila.fecha > hoy ? fila.fecha : hoy;
  const ultimoDia = [...new Set((PROD.plan || []).map(p => p.fecha))].sort().slice(-1)[0];
  const destinoFecha = buscarDiaConEspacio(fila.sku, recurso, fila.horas || 0, sumarDiaStr(noAntesDe, 1), ultimoDia, capMap);
  if (!destinoFecha) {
    alert('No se encontró un día con espacio disponible en el horizonte para reasignar esta corrida automáticamente. Ajusta manualmente en Simulador → Reprogramación.');
    return;
  }
  const origen = { sku: fila.sku, fecha: fila.fecha, linea: fila.linea };
  const destino = { fecha: destinoFecha, linea: fila.linea };
  const ok = await aplicarMovimientoCorrida(origen, destino, btn);
  if (ok) {
    if (btn) { btn.textContent = `Reasignado a ${destinoFecha} ✓`; btn.disabled = true; }
  }
}
async function renderImpactoParadasNoPlaneadas() {
  const cont = document.getElementById('cfgParadaImpacto');
  if (!cont) return;
  const noPlaneadas = (PARADAS_PROGRAMADAS || [])
    .filter(p => p.tipo === 'No planeada' && p.planta && p.linea && p.fecha_inicio && p.fecha_fin);
  if (!noPlaneadas.length) { cont.innerHTML = ''; return; }
  await cargarProduccion();
  cont.innerHTML = '<p style="font-size:13px;color:var(--gray-400);">Calculando impacto de la(s) parada(s) no planeada(s)…</p>';
  const resultados = await Promise.all(noPlaneadas.map(p => consultarImpactoParada(p)));
  cont.innerHTML = noPlaneadas.map((p, idxParada) => {
    const r = resultados[idxParada];
    const filas = r.filas || [];
    const encabezado = `${p.planta || '—'} · Línea ${p.linea || '—'} (No planeada) — ${p.fecha_inicio} a ${p.fecha_fin}` +
      (p.motivo ? ` · ${p.motivo}` : '');

    if (!filas.length) {
      return `<div class="aviso-box" style="margin-bottom:10px;"><b>⏸ ${encabezado}</b><br>
        Sin producción programada en esa ventana — sin impacto en el plan.</div>`;
    }

    const filasHtml = filas.map((f, i) => {
      const puedeReasignar = f.motivo === 'BUFFER';
      return `<tr>
        <td>${f.fecha}</td><td><b>${f.sku}</b></td>
        <td style="font-size:12px;">${(descripcionSku(f.sku) || '').slice(0, 30)}</td>
        <td class="num-right">${fmtTM(f.tm)}</td>
        <td><span class="pill ${pillMotivo(f.motivo)}">${f.motivo || '—'}</span></td>
        <td>${puedeReasignar
          ? `<button class="btn btn-primary btn-small" data-reasignar-parada="${idxParada}" data-reasignar-fila="${i}">Reasignar</button>`
          : `<span style="font-size:11.5px;color:var(--gray-400);">comprometido con cliente</span>`}</td>
      </tr>`;
    }).join('');

    return `
      <div class="aviso-box warn" style="margin-bottom:10px;"><b>⏸ ${encabezado}</b><br>
        ${fmtTM(r.tm_total)} en ${filas.length} corrida(s) del plan vigente caen dentro de esta ventana.</div>
      <table style="margin:0 0 16px;">
        <thead><tr><th>Fecha</th><th>SKU</th><th>Producto</th><th class="num-right">TM</th><th>Motivo</th><th></th></tr></thead>
        <tbody>${filasHtml}</tbody>
      </table>`;
  }).join('');
  cont._impactoResultados = resultados;
  cont.querySelectorAll('[data-reasignar-parada]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idxP = Number(btn.dataset.reasignarParada);
      const idxF = Number(btn.dataset.reasignarFila);
      const fila = cont._impactoResultados[idxP].filas[idxF];
      reasignarCorridaDesdeImpacto(fila, btn);
    });
  });
}
function renderCfgCompatTabla() {
  const tbody = document.getElementById('cfgCompatTabla');
  if (!tbody) return;
  tbody.innerHTML = CFG.compatibilidad.map((c, i) => `<tr data-idx="${i}">
      <td><input type="text" value="${c.sku || ''}" data-f="sku"></td>
      <td><input type="text" value="${c.planta || ''}" data-f="planta"></td>
      <td><input type="text" value="${c.lineas || ''}" data-f="lineas" placeholder="Ej. 1,2/3,4"></td>
      <td><button class="btn btn-danger btn-small" data-del>✕</button></td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    const idx = Number(tr.dataset.idx);
    tr.querySelectorAll('input').forEach(inp => inp.addEventListener('input', () => { CFG.compatibilidad[idx][inp.dataset.f] = inp.value; }));
    tr.querySelector('[data-del]').addEventListener('click', () => { CFG.compatibilidad.splice(idx, 1); renderCfgCompatTabla(); });
  });
}
function flashGuardado(texto) {
  const el = document.getElementById('cfgFlash');
  if (!el) return;
  el.textContent = texto || 'Guardado ✓';
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2400);
}
function descargarJSON(objeto, nombreArchivo) {
  const blob = new Blob([JSON.stringify(objeto, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = nombreArchivo;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
let PEDIDOS_MANUALES = [];
let PEDIDOS_MANUALES_CARGADOS = false;
async function cargarPedidosManuales() {
  try {
    const r = await fetchJSON('/api/v1/config/pedidos-manuales');
    PEDIDOS_MANUALES = Array.isArray(r.pedidos) ? r.pedidos : [];
  } catch (err) {
    PEDIDOS_MANUALES = [];
  }
  PEDIDOS_MANUALES_CARGADOS = true;
}
function renderCfgPedidosManualesTabla() {
  const tbody = document.getElementById('cfgPedidosManualesTabla');
  if (!tbody) return;
  if (!PEDIDOS_MANUALES.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--gray-400);">Sin pedidos adicionales registrados.</td></tr>`;
    return;
  }
  tbody.innerHTML = PEDIDOS_MANUALES.map((p, i) => `<tr data-idx="${i}">
      <td><input type="text" value="${p.sku || ''}" data-f="sku"></td>
      <td><input type="text" value="${p.cliente_nombre || ''}" data-f="cliente_nombre"></td>
      <td><input type="number" min="0" step="0.1" value="${p.cantidad_tm ?? 0}" data-f="cantidad_tm" style="max-width:100px;"></td>
      <td><input type="date" value="${(p.fecha_entrega || '').slice(0, 10)}" data-f="fecha_entrega"></td>
      <td><button class="btn btn-danger btn-small" data-del>✕</button></td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    const idx = Number(tr.dataset.idx);
    tr.querySelectorAll('input').forEach(inp => {
      inp.addEventListener('input', () => {
        const f = inp.dataset.f;
        PEDIDOS_MANUALES[idx][f] = f === 'cantidad_tm' ? parseFloat(inp.value) || 0 : inp.value;
      });
    });
    tr.querySelector('[data-del]').addEventListener('click', () => {
      PEDIDOS_MANUALES.splice(idx, 1);
      renderCfgPedidosManualesTabla();
    });
  });
}
function validarPedidosManuales() {
  const aviso = document.getElementById('cfgPedidosManualesAviso');
  const errores = [];
  PEDIDOS_MANUALES.forEach((p, i) => {
    if (!p.sku || !p.cliente_nombre || !p.fecha_entrega || !(p.cantidad_tm > 0)) {
      errores.push(`Fila ${i + 1}: faltan datos (SKU, cliente, fecha o TM debe ser mayor a 0).`);
    }
  });
  if (aviso) {
    if (errores.length) {
      aviso.style.display = 'block'; aviso.className = 'aviso-box warn';
      aviso.innerHTML = `⚠ Revisa antes de guardar:<br>${errores.join('<br>')}`;
    } else {
      aviso.style.display = 'none';
    }
  }
  return errores.length === 0;
}
async function guardarPedidosManualesEnBackend() {
  if (!validarPedidosManuales()) return false;
  try {
    const resp = await fetch(API_BASE + '/api/v1/config/pedidos-manuales', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pedidos: PEDIDOS_MANUALES.map(p => ({
        sku: p.sku, cliente_nombre: p.cliente_nombre,
        cantidad_tm: Number(p.cantidad_tm) || 0, fecha_entrega: p.fecha_entrega,
      })) }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (err) {
    console.error('No se pudieron guardar los pedidos manuales', err);
    return false;
  }
}
let EXCLUSIONES_PLANTA = [];
let EXCLUSIONES_CARGADAS = false;
async function cargarExclusionesPlanta() {
  try {
    const r = await fetchJSON('/api/v1/config/exclusiones-planta');
    EXCLUSIONES_PLANTA = Array.isArray(r.exclusiones) ? r.exclusiones : [];
  } catch (err) {
    EXCLUSIONES_PLANTA = [];
  }
  EXCLUSIONES_CARGADAS = true;
}
// Líneas reales configuradas para una planta (tab "Capacidad y Líneas de
// Producción", CFG.lineas) -- para que "Línea excluida" sea un select con
// las líneas que de verdad existen ahí, no texto libre.
function lineasDePlanta(planta) {
  const p = String(planta || '').trim().toUpperCase();
  if (!p) return [];
  const vistas = new Set();
  (CFG.lineas || []).forEach(l => {
    if (String(l.planta || '').trim().toUpperCase() === p && l.linea) vistas.add(String(l.linea).trim());
  });
  return [...vistas].sort();
}
// Combobox de SKU por fila (igual interacción que initCombobox: escribir
// código o descripción y elegir de la lista) -- antes era un <select> con
// TODO el catálogo para elegir uno por uno, impráctico con muchos SKU.
function wireComboSkuExclusion(tr, idx) {
  const input = tr.querySelector('input[data-f="sku"]');
  const panel = tr.querySelector('.combo-panel');
  if (!input || !panel) return;
  function render(texto) {
    const q = (texto || '').toLowerCase().trim();
    const opts = [...DATA.catalogoSkus.entries()]
      .filter(([sku, desc]) => !q || String(sku).toLowerCase().includes(q) || (desc || '').toLowerCase().includes(q))
      .slice(0, 50);
    panel.innerHTML = opts.length
      ? opts.map(([sku, desc]) => `<div class="combo-item" data-sku="${sku}">${sku}${desc ? ' — ' + desc : ''}</div>`).join('')
      : '<div class="combo-empty">Sin resultados</div>';
    panel.classList.add('open');
  }
  input.addEventListener('focus', () => render(input.value));
  input.addEventListener('input', () => { EXCLUSIONES_PLANTA[idx].sku = ''; render(input.value); });
  panel.addEventListener('mousedown', (e) => {
    const item = e.target.closest('.combo-item');
    if (!item) return;
    const sku = item.dataset.sku;
    const desc = DATA.catalogoSkus.get(sku) || '';
    input.value = desc ? `${sku} — ${desc}` : sku;
    EXCLUSIONES_PLANTA[idx].sku = sku;
    panel.classList.remove('open');
  });
  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !panel.contains(e.target)) panel.classList.remove('open');
  });
}
function renderCfgExclusionesTabla() {
  const tbody = document.getElementById('cfgExclusionesTabla');
  if (!tbody) return;
  if (!EXCLUSIONES_PLANTA.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--gray-400);">Sin exclusiones registradas.</td></tr>`;
    return;
  }
  tbody.innerHTML = EXCLUSIONES_PLANTA.map((e, i) => {
    const desc = e.sku ? (descripcionSku(e.sku) || '') : '';
    const skuTexto = e.sku ? (desc ? `${e.sku} — ${desc}` : e.sku) : '';
    const lineas = lineasDePlanta(e.planta_excluida);
    return `<tr data-idx="${i}">
      <td style="position:relative;">
        <input type="text" value="${skuTexto.replace(/"/g, '&quot;')}" data-f="sku" placeholder="Código o descripción…" autocomplete="off">
        <div class="combo-panel"></div>
      </td>
      <td><input type="text" value="${e.planta_excluida || ''}" data-f="planta_excluida" placeholder="Ej. GUAYAQUIL"></td>
      <td><select data-f="linea_excluida">
          <option value="">Todas las líneas</option>
          ${lineas.map(l => `<option value="${l}" ${e.linea_excluida === l ? 'selected' : ''}>Línea ${l}</option>`).join('')}
        </select></td>
      <td><input type="text" value="${e.motivo || ''}" data-f="motivo"></td>
      <td><button class="btn btn-danger btn-small" data-del>✕</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    const idx = Number(tr.dataset.idx);
    wireComboSkuExclusion(tr, idx);
    tr.querySelectorAll('select,input[data-f]:not([data-f="sku"])').forEach(inp => {
      const actualizar = () => { EXCLUSIONES_PLANTA[idx][inp.dataset.f] = inp.value; };
      inp.addEventListener('input', actualizar);
      inp.addEventListener('change', actualizar);
      // La planta excluida determina las líneas disponibles -- al salir del
      // campo, se refresca el <select> de línea con las líneas reales de esa
      // planta (tab "Capacidad y Líneas de Producción").
      if (inp.dataset.f === 'planta_excluida') {
        inp.addEventListener('change', () => renderCfgExclusionesTabla());
      }
    });
    tr.querySelector('[data-del]').addEventListener('click', () => {
      EXCLUSIONES_PLANTA.splice(idx, 1);
      renderCfgExclusionesTabla();
    });
  });
}
function validarExclusionesPlanta() {
  const aviso = document.getElementById('cfgExclusionesAviso');
  const errores = [];
  EXCLUSIONES_PLANTA.forEach((e, i) => {
    if (!e.sku || !e.planta_excluida) errores.push(`Fila ${i + 1}: faltan datos (SKU o planta excluida).`);
  });
  if (aviso) {
    if (errores.length) {
      aviso.style.display = 'block'; aviso.className = 'aviso-box warn';
      aviso.innerHTML = `⚠ Revisa antes de guardar:<br>${errores.join('<br>')}`;
    } else { aviso.style.display = 'none'; }
  }
  return errores.length === 0;
}
async function guardarExclusionesPlantaEnBackend() {
  if (!validarExclusionesPlanta()) return false;
  try {
    const resp = await fetch(API_BASE + '/api/v1/config/exclusiones-planta', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exclusiones: EXCLUSIONES_PLANTA.map(e => ({
        sku: e.sku, planta_excluida: e.planta_excluida,
        linea_excluida: e.linea_excluida || null, motivo: e.motivo || '',
      })) }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (err) {
    console.error('No se pudieron guardar las exclusiones SKU-planta', err);
    return false;
  }
}
let VIDA_UTIL_MP = new Map();
let VIDA_UTIL_MP_CARGADA = false;
// MP agregadas a mano con el buscador (siempre dentro del universo de
// opcionesCatalogoMp(): DDMRP.bufferMp con estado 'ok' y mp_descripcion).
let VIDA_UTIL_EXTRA_SKUS = [];
async function cargarVidaUtilMp() {
  try {
    const r = await fetchJSON('/api/v1/config/vida-util-mp');
    VIDA_UTIL_MP = new Map((Array.isArray(r) ? r : []).map(v => [String(v.sku).replace('.0', '').trim(), v.vida_util_dias]));
  } catch (err) {
    VIDA_UTIL_MP = new Map();
  }
  VIDA_UTIL_MP_CARGADA = true;
}
// Universo de MP para el buscador "Agregar MP": EXACTAMENTE las mismas que
// salen en Inventario Estratégico -> Materia Prima -- DDMRP.bufferMp con
// estado 'ok' y su mp_descripcion propia, nada más. Antes mezclaba otras
// tablas y el catálogo de PT por error -- por eso salían descripciones de
// producto terminado / material de empaque, y alguna MP sin descripción.
// Una MP sin mp_descripcion no se muestra (no hay de dónde sacar su nombre).
function opcionesCatalogoMp() {
  return (DDMRP.bufferMp || [])
    .filter(m => m.estado === 'ok' && m.mp_descripcion)
    .map(m => ({ value: String(m.sku).replace('.0', '').trim(), label: `${String(m.sku).replace('.0', '').trim()} — ${m.mp_descripcion}` }));
}
function initVidaUtilAgregar() {
  initCombobox('cfgVidaUtilBuscar', opcionesCatalogoMp, () => {});
  const btn = document.getElementById('cfgVidaUtilAgregar');
  if (btn && !btn.dataset.wired) {
    btn.dataset.wired = '1';
    btn.addEventListener('click', () => {
      const input = document.getElementById('cfgVidaUtilBuscar');
      if (!input || !input.value.trim()) return;
      const sku = input.value.trim().split(' — ')[0].trim().replace('.0', '');
      if (!sku) return;
      if (!VIDA_UTIL_EXTRA_SKUS.includes(sku)) VIDA_UTIL_EXTRA_SKUS.push(sku);
      input.value = '';
      renderCfgVidaUtilTabla();
    });
  }
}
function renderCfgVidaUtilTabla() {
  const tbody = document.getElementById('cfgVidaUtilTabla');
  if (!tbody) return;
  // Mismo universo que opcionesCatalogoMp(): las MP de Inventario
  // Estratégico -> Materia Prima (DDMRP.bufferMp, estado 'ok', con
  // mp_descripcion). Una MP sin descripción no se muestra -- no es
  // identificable como materia prima real sin ese dato, aunque tenga
  // vida útil guardada de antes (VIDA_UTIL_EXTRA_SKUS/VIDA_UTIL_MP solo
  // sirven para decidir qué fila de ese universo ya tiene valor).
  const vistos = new Map((DDMRP.bufferMp || [])
    .filter(m => m.estado === 'ok' && m.mp_descripcion)
    .map(m => [String(m.sku).replace('.0', '').trim(), m.mp_descripcion]));
  const filas = [...vistos.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  if (!filas.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:var(--gray-400);">Sin materias primas cargadas. Agrégalas con el buscador de arriba.</td></tr>`;
    return;
  }
  tbody.innerHTML = filas.map(([sku, desc]) => {
    const v = VIDA_UTIL_MP.get(sku);
    return `<tr data-sku="${sku}">
      <td><b>${sku}</b></td>
      <td>${desc || '—'}</td>
      <td><input type="number" min="0" step="1" value="${v != null ? v : ''}" data-f="vida_util" style="width:120px;" placeholder="Vacío = no aplica"></td>
      <td style="text-align:center;"><button class="btn btn-danger btn-small" data-quitar>✕</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr[data-sku]').forEach(tr => {
    const sku = tr.dataset.sku;
    const inp = tr.querySelector('input[data-f="vida_util"]');
    inp.addEventListener('change', async () => {
      const val = inp.value !== '' ? parseFloat(inp.value) : null;
      try {
        const resp = await fetch(API_BASE + '/api/v1/config/vida-util-mp', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sku, vida_util_dias: val, actualizado_por: 'planificador' }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        if (val != null) VIDA_UTIL_MP.set(sku, val); else VIDA_UTIL_MP.delete(sku);
        flashGuardado('Vida útil guardada ✓ — se aplica en la próxima corrida de plan_compras_mp.py');
      } catch (err) {
        alert('No se pudo guardar la vida útil. Revisa la conexión con el backend e intenta de nuevo.');
      }
    });
    tr.querySelector('[data-quitar]').addEventListener('click', async () => {
      try {
        await fetch(API_BASE + '/api/v1/config/vida-util-mp', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sku, vida_util_dias: null, actualizado_por: 'planificador' }),
        });
      } catch (err) { /* se sigue quitando de la vista aunque falle el borrado remoto */ }
      VIDA_UTIL_MP.delete(sku);
      VIDA_UTIL_EXTRA_SKUS = VIDA_UTIL_EXTRA_SKUS.filter(s => s !== sku);
      renderCfgVidaUtilTabla();
    });
  });
}
/* ------------------------------------------------------------------
 * Configuración -> Regiones de flete (calcular_flete_pedidos.py).
 * Corrección manual de región por Ship To -- autoguarda al cambiar el
 * select (mismo patrón que Vida útil MP), no va en el botón Guardar
 * general.
 * ------------------------------------------------------------------ */
const METODO_FLETE_LABEL = {
  '0_MANUAL': 'Manual', '1_EXACTO': 'Dirección exacta', '2_TEXTO_DIR': 'Texto en dirección',
  '3_TEXTO_NOMBRE': 'Nombre del Ship To', '4_VECINO_GPS': 'GPS (campamento vecino)',
  '5_TEXTO_VIA': 'Nombre de carretera (revisar)',
};
let FLETE_REGIONES = { ship_to: [], destinos: [], cargado: false };
let FLETE_REGIONES_FILTRO = '';
async function cargarFleteRegionesConfig() {
  try {
    const r = await fetchJSON('/api/v1/config/flete-regiones');
    FLETE_REGIONES.ship_to = Array.isArray(r.ship_to) ? r.ship_to : [];
    FLETE_REGIONES.destinos = Array.isArray(r.destinos) ? r.destinos : [];
  } catch (err) {
    FLETE_REGIONES = { ship_to: [], destinos: [] };
  }
  FLETE_REGIONES.cargado = true;
}
function renderCfgFleteAvisos() {
  const cont = document.getElementById('cfgFleteAvisos');
  if (!cont) return;
  const relevantes = (FLETE_AVISOS.datos || []).filter(a => a.tipo === 'SHIP_TO_SIN_REGION' || a.tipo === 'DESTINO_SIN_TARIFA');
  if (!relevantes.length) { cont.innerHTML = ''; return; }
  const porTipo = { SHIP_TO_SIN_REGION: [], DESTINO_SIN_TARIFA: [] };
  relevantes.forEach(a => { (porTipo[a.tipo] || (porTipo[a.tipo] = [])).push(a.detalle); });
  cont.innerHTML = Object.entries(porTipo).filter(([, v]) => v.length).map(([tipo, det]) => `
    <div class="aviso-box warn" style="margin-bottom:10px;">
      <b>${tipo === 'SHIP_TO_SIN_REGION' ? 'Ship To sin región' : 'Destino sin tarifa en el tabulador'}</b>
      (${det.length}): ${det.slice(0, 8).join(' · ')}${det.length > 8 ? ' …' : ''}
    </div>`).join('');
}
function renderCfgFleteRegionesChips() {
  const cont = document.getElementById('cfgFleteRegionesChips');
  if (!cont) return;
  const st = FLETE_REGIONES.ship_to || [];
  const chips = [
    { k: '', label: 'Todos', n: st.length },
    { k: 'sin_region', label: 'Sin región', n: st.filter(s => !s.region).length },
    { k: 'revisar', label: 'Revisar', n: st.filter(s => s.metodo === '4_VECINO_GPS' || s.metodo === '5_TEXTO_VIA').length },
    { k: 'con_correccion', label: 'Con corrección manual', n: st.filter(s => s.destino_manual).length },
  ];
  cont.innerHTML = chips.map(c => `<span class="pill ${FLETE_REGIONES_FILTRO === c.k ? 'pill-green' : 'pill-buffer'}"
    style="cursor:pointer;" data-filtro="${c.k}">${c.label} <span style="opacity:.7;">${c.n}</span></span>`).join(' ');
  cont.querySelectorAll('[data-filtro]').forEach(el => el.addEventListener('click', () => {
    FLETE_REGIONES_FILTRO = el.dataset.filtro;
    renderCfgFleteRegionesChips();
    renderCfgFleteRegionesTabla();
  }));
}
function filasFleteRegionesFiltradas() {
  const st = FLETE_REGIONES.ship_to || [];
  if (FLETE_REGIONES_FILTRO === 'sin_region') return st.filter(s => !s.region);
  if (FLETE_REGIONES_FILTRO === 'revisar') return st.filter(s => s.metodo === '4_VECINO_GPS' || s.metodo === '5_TEXTO_VIA');
  if (FLETE_REGIONES_FILTRO === 'con_correccion') return st.filter(s => s.destino_manual);
  return st;
}
function renderCfgFleteRegionesTabla() {
  const tbody = document.getElementById('cfgFleteRegionesTabla');
  if (!tbody) return;
  const filas = filasFleteRegionesFiltradas();
  if (!filas.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="color:var(--gray-400);">Sin Ship To para este filtro.</td></tr>`;
    return;
  }
  const destinos = FLETE_REGIONES.destinos || [];
  tbody.innerHTML = filas.map(s => {
    const metodoTxt = s.metodo ? (METODO_FLETE_LABEL[s.metodo] || s.metodo) : 'Sin región';
    return `<tr data-ship-to="${s.ship_to}">
      <td>${s.ship_to}</td>
      <td>${s.ship_to_desc || '—'}</td>
      <td>${s.sold_to || '—'}</td>
      <td>${s.region || '<span style="color:var(--red-700);">Sin región</span>'}</td>
      <td style="font-size:12px;">${metodoTxt}</td>
      <td class="num-right">${s.restriccion != null ? fmtNum(s.restriccion, 1) : '—'}</td>
      <td class="num-right">${s.restriccion_invernal != null ? fmtNum(s.restriccion_invernal, 1) : '—'}</td>
      <td><select data-f="destino_manual" style="min-width:160px;">
        <option value="">— sin corrección —</option>
        ${destinos.map(d => `<option value="${d}" ${s.destino_manual === d ? 'selected' : ''}>${d}</option>`).join('')}
      </select></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('select[data-f="destino_manual"]').forEach(sel => {
    sel.addEventListener('change', async () => {
      const shipTo = sel.closest('tr').dataset.shipTo;
      sel.disabled = true;
      try {
        const resp = await fetch(API_BASE + '/api/v1/config/flete-regiones', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ship_to: shipTo, destino: sel.value || null, actualizado_por: 'planificador' }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
        const fila = FLETE_REGIONES.ship_to.find(s => s.ship_to === shipTo);
        if (fila) fila.destino_manual = sel.value || null;
        flashGuardado('Región guardada ✓ — se aplica en la próxima corrida');
        renderCfgFleteRegionesChips();
      } catch (err) {
        alert(err.message || 'No se pudo guardar la región.');
      } finally {
        sel.disabled = false;
      }
    });
  });
}

/* ------------------------------------------------------------------
 * Configuración -> Temporada de lluvia (config_temporada_invernal).
 * Lista de rangos de fecha; se guarda completa con el botón general
 * "Guardar" (mismo patrón que Paradas/Exclusiones/Pedidos manuales).
 * ------------------------------------------------------------------ */
let TEMPORADA_INVERNAL = [];
let TEMPORADA_INVERNAL_CARGADA = false;
async function cargarTemporadaInvernalConfig() {
  try {
    const r = await fetchJSON('/api/v1/config/temporada-invernal');
    TEMPORADA_INVERNAL = Array.isArray(r.temporadas) ? r.temporadas : [];
  } catch (err) {
    TEMPORADA_INVERNAL = [];
  }
  TEMPORADA_INVERNAL_CARGADA = true;
}
function renderCfgTemporadaTabla() {
  const tbody = document.getElementById('cfgTemporadaTabla');
  if (!tbody) return;
  if (!TEMPORADA_INVERNAL.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:var(--gray-400);">Sin temporadas de lluvia registradas — se usa siempre la RESTRICCION normal.</td></tr>`;
    return;
  }
  tbody.innerHTML = TEMPORADA_INVERNAL.map((t, i) => `<tr data-idx="${i}">
      <td><input type="date" value="${(t.fecha_inicio || '').slice(0, 10)}" data-f="fecha_inicio"></td>
      <td><input type="date" value="${(t.fecha_fin || '').slice(0, 10)}" data-f="fecha_fin"></td>
      <td style="text-align:center;"><input type="checkbox" data-f="activo" ${t.activo ? 'checked' : ''}></td>
      <td><button class="btn btn-danger btn-small" data-del>✕</button></td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    const idx = Number(tr.dataset.idx);
    tr.querySelectorAll('input').forEach(inp => {
      const actualizar = () => {
        const f = inp.dataset.f;
        TEMPORADA_INVERNAL[idx][f] = f === 'activo' ? (inp.checked ? 1 : 0) : inp.value;
      };
      inp.addEventListener('input', actualizar);
      inp.addEventListener('change', actualizar);
    });
    tr.querySelector('[data-del]').addEventListener('click', () => {
      TEMPORADA_INVERNAL.splice(idx, 1);
      renderCfgTemporadaTabla();
    });
  });
}
function validarTemporadaInvernal() {
  const aviso = document.getElementById('cfgTemporadaAviso');
  const errores = [];
  TEMPORADA_INVERNAL.forEach((t, i) => {
    if (!t.fecha_inicio || !t.fecha_fin) errores.push(`Rango ${i + 1}: faltan fechas.`);
    else if (t.fecha_fin < t.fecha_inicio) errores.push(`Rango ${i + 1}: la fecha fin es anterior a la de inicio.`);
  });
  if (aviso) {
    if (errores.length) { aviso.style.display = 'block'; aviso.className = 'aviso-box warn'; aviso.innerHTML = `⚠ Revisa antes de guardar:<br>${errores.join('<br>')}`; }
    else { aviso.style.display = 'none'; }
  }
  return errores.length === 0;
}
async function guardarTemporadaInvernalEnBackend() {
  if (!validarTemporadaInvernal()) return false;
  try {
    const resp = await fetch(API_BASE + '/api/v1/config/temporada-invernal', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ temporadas: TEMPORADA_INVERNAL.map(t => ({
        fecha_inicio: t.fecha_inicio, fecha_fin: t.fecha_fin, activo: t.activo ? 1 : 0,
      })) }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    return true;
  } catch (err) {
    console.error('No se pudieron guardar las temporadas de lluvia', err);
    const aviso = document.getElementById('cfgTemporadaAviso');
    if (aviso) { aviso.style.display = 'block'; aviso.className = 'aviso-box warn'; aviso.textContent = `⚠ ${err.message}`; }
    return false;
  }
}

function initCfgTabs() {
  const wrap = document.getElementById('cfgTabsNav');
  if (!wrap || wrap.dataset.wired) return;
  wrap.dataset.wired = '1';
  wrap.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      wrap.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      document.querySelectorAll('#config [data-cfgpanel]').forEach(p => {
        p.classList.toggle('active', p.dataset.cfgpanel === tab.dataset.cfgtab);
      });
    });
  });
}
async function renderConfigScreen() {
  initCfgTabs();
  const elPt = document.getElementById('cfgCapPt'), elMp = document.getElementById('cfgCapMp');
  if (elPt) { elPt.value = CFG.capacidadPtTm ?? ''; if (!elPt.dataset.wired) { elPt.dataset.wired = '1'; elPt.addEventListener('input', () => { CFG.capacidadPtTm = elPt.value ? parseFloat(elPt.value) : null; }); } }
  if (elMp) { elMp.value = CFG.capacidadMpTm ?? ''; if (!elMp.dataset.wired) { elMp.dataset.wired = '1'; elMp.addEventListener('input', () => { CFG.capacidadMpTm = elMp.value ? parseFloat(elMp.value) : null; }); } }
  if (!CFG_BACKEND.intentado) await sincronizarCfgLineasConBackend();
  actualizarCfgNote();
  renderCfgLineasTabla();
  renderCfgCompatTabla();
  if (!PARADAS_CARGADAS) await cargarParadasProgramadas();
  renderCfgParadasTabla();
  renderImpactoParadasNoPlaneadas();
  if (!PEDIDOS_MANUALES_CARGADOS) await cargarPedidosManuales();
  renderCfgPedidosManualesTabla();
  if (!EXCLUSIONES_CARGADAS) await cargarExclusionesPlanta();
  renderCfgExclusionesTabla();
  await cargarDDMRP();
  if (!VIDA_UTIL_MP_CARGADA) await cargarVidaUtilMp();
  initVidaUtilAgregar();
  renderCfgVidaUtilTabla();
  if (!FLETE_REGIONES.cargado) await cargarFleteRegionesConfig();
  await cargarFleteAvisos();
  renderCfgFleteAvisos();
  renderCfgFleteRegionesChips();
  renderCfgFleteRegionesTabla();
  if (!TEMPORADA_INVERNAL_CARGADA) await cargarTemporadaInvernalConfig();
  renderCfgTemporadaTabla();
  const addExclusion = document.getElementById('cfgAddExclusion');
  if (addExclusion && !addExclusion.dataset.wired) {
    addExclusion.dataset.wired = '1';
    addExclusion.addEventListener('click', () => {
      EXCLUSIONES_PLANTA.push({ sku: '', planta_excluida: '', linea_excluida: '', motivo: '' });
      renderCfgExclusionesTabla();
    });
  }
  const addPedidoManual = document.getElementById('cfgAddPedidoManual');
  if (addPedidoManual && !addPedidoManual.dataset.wired) {
    addPedidoManual.dataset.wired = '1';
    addPedidoManual.addEventListener('click', () => {
      PEDIDOS_MANUALES.push({ sku: '', cliente_nombre: '', cantidad_tm: 0, fecha_entrega: '' });
      renderCfgPedidosManualesTabla();
    });
  }
  const addLinea = document.getElementById('cfgAddLinea');
  if (addLinea && !addLinea.dataset.wired) {
    addLinea.dataset.wired = '1';
    addLinea.addEventListener('click', () => { CFG.lineas.push({ bu: '', tipo: '', planta: '', linea: '', tamano: 0, horas: 20, rate: 0 }); renderCfgLineasTabla(); });
  }
  const addParada = document.getElementById('cfgAddParada');
  if (addParada && !addParada.dataset.wired) {
    addParada.dataset.wired = '1';
    addParada.addEventListener('click', () => {
      PARADAS_PROGRAMADAS.push({ planta: '', linea: '', fecha_inicio: '', fecha_fin: '', horas_dia: 24, tipo: 'Programada', motivo: '' });
      renderCfgParadasTabla();
    });
  }
  const addCompat = document.getElementById('cfgAddCompat');
  if (addCompat && !addCompat.dataset.wired) {
    addCompat.dataset.wired = '1';
    addCompat.addEventListener('click', () => { CFG.compatibilidad.push({ sku: '', planta: '', lineas: '' }); renderCfgCompatTabla(); });
  }
  const addTemporada = document.getElementById('cfgAddTemporada');
  if (addTemporada && !addTemporada.dataset.wired) {
    addTemporada.dataset.wired = '1';
    addTemporada.addEventListener('click', () => {
      TEMPORADA_INVERNAL.push({ fecha_inicio: '', fecha_fin: '', activo: 1 });
      renderCfgTemporadaTabla();
    });
  }
  const btnGuardar = document.getElementById('cfgGuardar');
  if (btnGuardar && !btnGuardar.dataset.wired) {
    btnGuardar.dataset.wired = '1';
    btnGuardar.addEventListener('click', async () => {
      guardarCfg();
      const resultados = [];
      if (CFG_BACKEND.conectado && !CFG_BACKEND.aviso) {
        resultados.push(await guardarCfgLineasEnBackend());
      }
      resultados.push(await guardarCapacidadBodegaEnBackend());
      const okParadas = await guardarParadasEnBackend();
      resultados.push(okParadas);
      if (okParadas) {
        await cargarParadasProgramadas();
        renderCfgParadasTabla();
        await renderImpactoParadasNoPlaneadas();
      }

      const okPedidosManuales = await guardarPedidosManualesEnBackend();
      resultados.push(okPedidosManuales);
      if (okPedidosManuales) {
        PEDIDOS_CACHE.clear();
        await cargarPedidosManuales();
        renderCfgPedidosManualesTabla();
      }

      resultados.push(await guardarExclusionesPlantaEnBackend());

      const okTemporada = await guardarTemporadaInvernalEnBackend();
      resultados.push(okTemporada);
      if (okTemporada) {
        await cargarTemporadaInvernalConfig();
        renderCfgTemporadaTabla();
      }

      const todoOk = resultados.every(Boolean);
      flashGuardado(todoOk
        ? 'Guardado ✓ (afecta la próxima corrida del plan de producción)'
        : 'Guardado parcial — revisa la consola, algo falló al enviar al backend');
    });
  }
  const btnExportar = document.getElementById('cfgExportar');
  if (btnExportar && !btnExportar.dataset.wired) { btnExportar.dataset.wired = '1'; btnExportar.addEventListener('click', () => descargarJSON(CFG, `cargill_config_${hoyStr()}.json`)); }
  const btnImportar = document.getElementById('cfgImportar');
  const fileImportar = document.getElementById('cfgImportarFile');
  if (btnImportar && fileImportar && !btnImportar.dataset.wired) {
    btnImportar.dataset.wired = '1';
    btnImportar.addEventListener('click', () => fileImportar.click());
    fileImportar.addEventListener('change', () => {
      const file = fileImportar.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try { CFG = Object.assign(cfgPorDefecto(), JSON.parse(reader.result)); guardarCfg(); renderConfigScreen(); flashGuardado(); }
        catch (err) { alert('El archivo no es una configuración válida.'); }
      };
      reader.readAsText(file);
      fileImportar.value = '';
    });
  }
  const btnReset = document.getElementById('cfgReset');
  if (btnReset && !btnReset.dataset.wired) {
    btnReset.dataset.wired = '1';
    btnReset.addEventListener('click', () => {
      if (!confirm('¿Restablecer todos los parámetros de Configuración a sus valores por defecto?')) return;
      CFG = cfgPorDefecto(); guardarCfg(); renderConfigScreen(); flashGuardado();
    });
  }
}
async function cargarConfiguracionTipos() {
  const resp = await fetch(API_BASE + '/api/v1/config/tipos-producto');
  const tipos = resp.ok ? await resp.json() : [];
  const tbody = document.getElementById('tablaTiposProductoBody');
  if (!tbody) return;
  tbody.innerHTML = tipos.map(t => `
    <tr>
      <td>${t.sku}</td>
      <td>${(descripcionSku(t.sku) || '—').slice(0, 34)}</td>
      <td>${t.tipo}</td>
      <td><button class="btn btn-secondary btn-small" onclick="eliminarTipoProductoSku('${t.sku}')">Quitar</button></td>
    </tr>`).join('') || '<tr><td colspan="4">Sin SKUs configurados. Se tratan como tipo único.</td></tr>';
}
async function eliminarTipoProductoSku(sku) {
  await fetch(API_BASE + `/api/v1/config/tipos-producto/${encodeURIComponent(sku)}`, { method: 'DELETE' });
  cargarConfiguracionTipos();
}
async function guardarTipoProductoSku() {
  const sku = CONFIG_TIPO_SKU_SELECCIONADO;
  const tipo = document.getElementById('configTipoValor').value.trim();
  if (!sku || !tipo) return;
  await fetch(API_BASE + '/api/v1/config/tipos-producto', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sku, tipo })
  });
  document.getElementById('configTipoValor').value = '';
  document.getElementById('configTipoSkuBuscar').value = '';
  CONFIG_TIPO_SKU_SELECCIONADO = null;
  cargarConfiguracionTipos();
}
let CONFIG_TIPO_SKU_SELECCIONADO = null;
function initConfiguracionTipos() {
  const input = document.getElementById('configTipoSkuBuscar');
  const panel = document.getElementById('configTipoSkuBuscar_panel');
  if (!input || input.dataset.wired) return;
  input.dataset.wired = '1';
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    CONFIG_TIPO_SKU_SELECCIONADO = null;
    if (!q) { panel.classList.remove('open'); panel.innerHTML = ''; return; }
    const coincidencias = filasPtFuenteFinal().filter(f =>
      String(f.sku).toLowerCase().includes(q) || (descripcionSku(f.sku) || '').toLowerCase().includes(q)
    ).slice(0, 30);
    panel.innerHTML = coincidencias.map(f =>
      `<div class="combo-item" data-sku="${f.sku}">${f.sku} — ${(descripcionSku(f.sku) || '').slice(0, 40)}</div>`
    ).join('') || '<div class="combo-empty">Sin resultados</div>';
    panel.classList.add('open');
    panel.querySelectorAll('.combo-item').forEach(item => {
      item.addEventListener('click', () => {
        CONFIG_TIPO_SKU_SELECCIONADO = item.dataset.sku;
        input.value = item.textContent;
        panel.classList.remove('open');
      });
    });
  });
  document.addEventListener('click', (e) => {
    if (e.target !== input && !panel.contains(e.target)) panel.classList.remove('open');
  });
}
async function renderMatrizSetup() {
  const cont = document.getElementById('matrizSetupContenedor');
  if (!cont) return;
  const resp = await fetch(API_BASE + '/api/v1/config/setup-tiempos');
  const filas = resp.ok ? await resp.json() : [];
  let html = '<table class="cfg-table"><thead><tr><th>Etapa origen</th><th>Etapa destino</th><th>Minutos</th><th></th></tr></thead><tbody>';
  filas.forEach((f, i) => {
    html += `<tr>
      <td><input type="text" value="${f.tipo_origen}" id="setupOrigen_${i}" style="width:120px;"></td>
      <td><input type="text" value="${f.tipo_destino}" id="setupDestino_${i}" style="width:120px;"></td>
      <td><input type="number" min="0" step="1" value="${f.minutos}" id="setupMin_${i}" style="width:80px;"></td>
      <td>
        <button class="btn btn-secondary btn-small" onclick="guardarFilaSetup(${i})">Guardar</button>
        <button class="btn btn-danger btn-small" onclick="eliminarFilaSetup('${f.tipo_origen}','${f.tipo_destino}')">Eliminar</button>
      </td>
    </tr>`;
  });
  html += `<tr>
    <td><input type="text" id="setupOrigenNuevo" placeholder="STARTER" style="width:120px;"></td>
    <td><input type="text" id="setupDestinoNuevo" placeholder="GROWER" style="width:120px;"></td>
    <td><input type="number" min="0" step="1" id="setupMinNuevo" placeholder="min" style="width:80px;"></td>
    <td><button class="btn btn-primary btn-small" onclick="agregarFilaSetup()">+ Agregar</button></td>
  </tr>`;
  html += '</tbody></table>';
  cont.innerHTML = html;
}
async function guardarFilaSetup(i) {
  const origen = document.getElementById(`setupOrigen_${i}`).value.trim().toUpperCase();
  const destino = document.getElementById(`setupDestino_${i}`).value.trim().toUpperCase();
  const minutos = parseFloat(document.getElementById(`setupMin_${i}`).value);
  if (!origen || !destino || isNaN(minutos)) return;
  await fetch(API_BASE + '/api/v1/config/setup-tiempos', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tipo_origen: origen, tipo_destino: destino, minutos })
  });
  renderMatrizSetup();
}
async function agregarFilaSetup() {
  const origen = document.getElementById('setupOrigenNuevo').value.trim().toUpperCase();
  const destino = document.getElementById('setupDestinoNuevo').value.trim().toUpperCase();
  const minutos = parseFloat(document.getElementById('setupMinNuevo').value);
  if (!origen || !destino || isNaN(minutos)) return;
  await fetch(API_BASE + '/api/v1/config/setup-tiempos', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tipo_origen: origen, tipo_destino: destino, minutos })
  });
  renderMatrizSetup();
}
async function eliminarFilaSetup(origen, destino) {
  await fetch(API_BASE + `/api/v1/config/setup-tiempos/${encodeURIComponent(origen)}/${encodeURIComponent(destino)}`, { method: 'DELETE' });
  renderMatrizSetup();
}
async function cargarLoteMaximo() {
  const resp = await fetch(API_BASE + '/api/v1/config/lote-maximo');
  const data = resp.ok ? await resp.json() : { lote_maximo_tm: null };
  const el = document.getElementById('configLoteMaximo');
  if (el) el.value = data.lote_maximo_tm ?? '';
}
async function guardarLoteMaximo() {
  const el = document.getElementById('configLoteMaximo');
  if (!el) return;
  const val = el.value;
  await fetch(API_BASE + '/api/v1/config/lote-maximo', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lote_maximo_tm: val === '' ? null : parseFloat(val) })
  });
}

document.addEventListener('DOMContentLoaded', () => {
  if (typeof Chart !== 'undefined') {
    Chart.defaults.font.family = "Calibri, 'Segoe UI', sans-serif";
    if (typeof ChartDataLabels !== 'undefined') Chart.register(ChartDataLabels);
    Chart.defaults.plugins.datalabels.display = false;
  } else {
    console.warn('Chart.js no se pudo cargar (CDN bloqueado o sin internet) — los gráficos no se mostrarán, el resto de la app funciona igual.');
  }
  initConfiguracionTipos();
  const tabSecuenciacion = document.querySelector('[data-cfgtab="secuenciacion"]');
  if (tabSecuenciacion) {
    tabSecuenciacion.addEventListener('click', () => {
      cargarConfiguracionTipos();
      cargarLoteMaximo();
      renderMatrizSetup();
    });
  }
  cargarCfg();
  initNav();
  initTabsDDMRP();
  initFiltrosDDMRP();
  initColapsables(document);
  initModalEditarBuffer();
  initModalEditarBufferMp();
  cargarInicial().then(() => renderHoy());
});
/* =====================================================================
 * Plan de Compras MP (plan_compras_mp.py / /api/v1/compras-mp/*)
 * ÚNICA fuente de señales de compra de MP -- nada se calcula aquí.
 * ===================================================================== */
let COMPRAS_FILTRO_SENAL = null;
let COMPRAS_SKU_SELECCIONADO = null;
// fmtTM redondea al TM entero -- para MP de consumo casi nulo eso convierte
// una fracción real de tonelada (ej. 0.3 TM) en "0 TM" y hace parecer
// absurda la cobertura en días (que sí usa el valor exacto). Más decimales
// aquí para que esa fracción se vea en vez de desaparecer.
function fmtTmPreciso(v) {
  if (v == null || isNaN(v)) return '—';
  const dec = Math.abs(v) > 0 && Math.abs(v) < 1 ? 2 : 1;
  return fmtNum(v, dec) + ' TM';
}

async function renderComprasMp() {
  await cargarComprasMp();
  initComprasWiring();
  const fechaEl = document.getElementById('comprasFecha');
  if (fechaEl) {
    fechaEl.textContent = COMPRAS_MP.fecha_calculo
      ? `Calculado el ${COMPRAS_MP.fecha_calculo}` : 'Sin datos. Correr plan_compras_mp.py.';
  }
  renderComprasChips();
  renderComprasAvisos();
  renderComprasTabla();
  if (COMPRAS_SKU_SELECCIONADO) await abrirComprasDetalle(COMPRAS_SKU_SELECCIONADO);
}

function initComprasWiring() {
  const btn = document.getElementById('comprasExportar');
  if (btn && !btn.dataset.wired) {
    btn.dataset.wired = '1';
    btn.addEventListener('click', () => { window.open(API_BASE + '/api/v1/compras-mp/exportar', '_blank'); });
  }
}

// Estética "segmented filter" (fondo blanco, un punto de color por señal) en
// vez de chips rellenos de color -- antes era muy saturado para un ERP.
// Sin "Riesgo de vencimiento": a pedido, se quitó de esta barra (la vida
// útil es opcional y casi siempre está en 0).
function renderComprasChips() {
  const cont = document.getElementById('comprasChips');
  if (!cont) return;
  const conteo = COMPRAS_MP.conteo || {};
  const ordenSenales = ['COMPRAR_HOY', 'COMPRAR_ANTES_DE', 'NO_COMPRAR_HASTA', 'CUBIERTO'];
  const total = COMPRAS_MP.plan.length;
  const base = 'display:inline-flex;align-items:center;gap:7px;padding:7px 16px;border-radius:8px;' +
    'font-size:12.5px;font-weight:600;cursor:pointer;border:1px solid var(--gray-200);background:var(--white);' +
    'color:var(--gray-700);transition:border-color .12s,background .12s;';
  const chipTodas = `<span data-senal="" style="${base}${!COMPRAS_FILTRO_SENAL ? 'background:var(--gray-100);border-color:var(--gray-400);' : ''}">` +
    `Todas <span style="color:var(--gray-400);">${total}</span></span>`;
  const chips = ordenSenales.map(s => {
    const n = conteo[s] || 0;
    const dot = colorSenalCompra(s);
    const activo = COMPRAS_FILTRO_SENAL === s;
    return `<span data-senal="${s}" style="${base}${activo ? `background:${bgSenalCompra(s)};border-color:${dot};` : ''}">` +
      `<span style="width:7px;height:7px;border-radius:50%;background:${dot};display:inline-block;flex-shrink:0;"></span>` +
      `${SENAL_COMPRA_ETIQUETA[s]} <span style="color:var(--gray-400);">${n}</span></span>`;
  }).join('');
  cont.innerHTML = chipTodas + chips;
  cont.querySelectorAll('[data-senal]').forEach(el => {
    el.addEventListener('click', () => {
      COMPRAS_FILTRO_SENAL = el.dataset.senal || null;
      renderComprasChips();
      renderComprasTabla();
    });
  });
}

function renderComprasAvisos() {
  const cont = document.getElementById('comprasAvisos');
  if (!cont) return;
  const avisos = COMPRAS_MP.avisos || [];
  if (!avisos.length) { cont.innerHTML = ''; return; }
  cont.innerHTML = avisos.map(a => {
    const rojo = a.tipo === 'BODEGA_LLENA';
    const estilo = rojo ? 'background:var(--red-100);border-left-color:var(--red-700);color:var(--red-700);' : '';
    return `<div class="aviso-box ${rojo ? '' : 'warn'}" style="margin-bottom:8px;${estilo}">` +
      `${a.sku ? `<b>${a.sku}</b> — ` : ''}${a.mensaje}</div>`;
  }).join('');
}

// Orden por urgencia real (Comprar hoy > Comprar a más tardar > No comprar
// hasta > Cubierto, luego por fecha) -- el backend ya inserta así, pero un
// SELECT * sin ORDER BY no garantiza ese orden; se fuerza aquí.
const ORDEN_URGENCIA_SENAL = { COMPRAR_HOY: 0, COMPRAR_ANTES_DE: 1, RIESGO_VENCIMIENTO: 2, NO_COMPRAR_HASTA: 3, CUBIERTO: 4 };
function filasComprasFiltradas() {
  const plan = COMPRAS_MP.plan || [];
  const filas = COMPRAS_FILTRO_SENAL ? plan.filter(p => p.senal === COMPRAS_FILTRO_SENAL) : plan.slice();
  return filas.sort((a, b) => {
    const oa = ORDEN_URGENCIA_SENAL[a.senal] ?? 9, ob = ORDEN_URGENCIA_SENAL[b.senal] ?? 9;
    if (oa !== ob) return oa - ob;
    const fa = a.fecha_referencia || '9999', fb = b.fecha_referencia || '9999';
    if (fa !== fb) return fa < fb ? -1 : 1;
    return String(a.sku).localeCompare(String(b.sku));
  });
}

function renderComprasTabla() {
  const tbody = document.getElementById('comprasTabla');
  const sub = document.getElementById('comprasTablaSub');
  if (!tbody) return;
  const filas = filasComprasFiltradas();
  if (sub) sub.textContent = `${filas.length} MP`;
  if (!filas.length) {
    tbody.innerHTML = `<tr><td colspan="10" style="color:var(--gray-400);">Sin materias primas para este filtro.</td></tr>`;
    return;
  }
  tbody.innerHTML = filas.map(p => {
    const sel = p.sku === COMPRAS_SKU_SELECCIONADO ? ' style="background:var(--emerald-50);"' : '';
    const fechaTxt = p.fecha_referencia ? fechaCorta(parseFechaLocal(p.fecha_referencia)) : '—';
    const llegadaTxt = p.fecha_llegada_sugerida ? fechaCorta(parseFechaLocal(p.fecha_llegada_sugerida)) : '—';
    const desc = (p.descripcion || descripcionSku(p.sku) || '—').toString();
    const descCorta = desc.length > 34 ? desc.slice(0, 34) + '…' : desc;
    return `<tr class="clickable-row" data-sku="${p.sku}"${sel}>
      <td><b>${p.sku}</b></td>
      <td title="${desc.replace(/"/g, '&quot;')}">${descCorta}</td>
      <td>${pillSenalCompra(p.senal)}</td>
      <td style="white-space:nowrap;">${fechaTxt}</td>
      <td class="num-right" style="white-space:nowrap;">${p.cantidad_sugerida_tm ? fmtTM(p.cantidad_sugerida_tm) : '—'}</td>
      <td style="white-space:nowrap;">${llegadaTxt}</td>
      <td class="num-right" style="white-space:nowrap;">${fmtTmPreciso(p.en_planta_tm)}</td>
      <td class="num-right" style="white-space:nowrap;">${fmtTmPreciso(p.en_camino_tm)}</td>
      <td class="num-right" style="white-space:nowrap;">${p.cobertura_dias != null ? fmtNum(p.cobertura_dias) + ' d' : '—'}</td>
      <td class="num-right" style="white-space:nowrap;">${p.lead_time_dias != null ? p.lead_time_dias + ' d' : '—'}</td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr[data-sku]').forEach(tr => {
    tr.addEventListener('click', () => abrirComprasDetalle(tr.dataset.sku));
  });
}

async function abrirComprasDetalle(sku) {
  COMPRAS_SKU_SELECCIONADO = sku;
  renderComprasTabla();
  const card = document.getElementById('comprasDetalleCard');
  const titulo = document.getElementById('comprasDetalleTitulo');
  if (!card) return;
  card.style.display = 'block';
  if (titulo) titulo.textContent = `Cargando ${sku}…`;
  try {
    const r = await fetchJSON('/api/v1/compras-mp/proyeccion/' + encodeURIComponent(sku));
    const resumen = r.resumen;
    const desc = (resumen && resumen.descripcion) || descripcionSku(sku) || '';
    if (titulo) titulo.innerHTML = `<b>${sku}</b>${desc ? ' — ' + desc : ''}`;
    renderComprasOcTabla(r.oc_en_camino || []);
    renderComprasSugeridasTabla(r.compras_sugeridas || []);
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    if (titulo) titulo.textContent = `Sin datos de proyección para ${sku}`;
    console.error('No se pudo cargar la proyección de compras MP', err);
  }
}

// OC en camino: fecha fija (ya zarpó, es ETA real o estimada). Mismo tamaño
// que "Compras sugeridas" (scroll propio) para que las dos cajas queden
// parejas aunque una tenga más filas que la otra.
function renderComprasOcTabla(oc) {
  const tbody = document.getElementById('comprasOcTabla');
  if (!tbody) return;
  if (!oc.length) { tbody.innerHTML = `<tr><td colspan="4" style="color:var(--gray-400);">Sin OC en camino.</td></tr>`; return; }
  tbody.innerHTML = oc.map(o => `<tr>
    <td>${o.nave || '—'}</td>
    <td>${o.booking || '—'}</td>
    <td class="num-right">${fmtTM(o.cantidad_tm)}</td>
    <td>${o.fecha_llegada ? fechaCorta(parseFechaLocal(o.fecha_llegada)) : '—'}${o.fuente_fecha === 'ETA_ESTIMADO' ? ' (estimada)' : ''}</td>
  </tr>`).join('');
}

// Compras sugeridas: solo fecha de LLEGADA (depende del lead time, es un
// dato firme) y cantidad -- se quitó "Lanzar" (cuándo se emite la OC
// depende del proveedor, no aporta). Se agrega el total del horizonte
// arriba de la tabla, para ver de un vistazo cuánto hay que reabastecer en
// total sin sumar fila por fila.
function renderComprasSugeridasTabla(sug) {
  const tbody = document.getElementById('comprasSugeridasTabla');
  const totalEl = document.getElementById('comprasSugeridasTotal');
  if (!tbody) return;
  const total = sug.reduce((a, s) => a + (s.cantidad_tm || 0), 0);
  if (totalEl) totalEl.textContent = sug.length ? `Total a reabastecer en el horizonte: ${fmtTM(total)}` : '';
  if (!sug.length) { tbody.innerHTML = `<tr><td colspan="2" style="color:var(--gray-400);">Sin compras sugeridas en el horizonte.</td></tr>`; return; }
  const ordenadas = sug.slice().sort((a, b) => String(a.fecha_llegada || '').localeCompare(String(b.fecha_llegada || '')));
  tbody.innerHTML = ordenadas.map(s => `<tr>
    <td>${s.fecha_llegada ? fechaCorta(parseFechaLocal(s.fecha_llegada)) : '—'}</td>
    <td class="num-right">${fmtTM(s.cantidad_tm)}</td>
  </tr>`).join('');
}