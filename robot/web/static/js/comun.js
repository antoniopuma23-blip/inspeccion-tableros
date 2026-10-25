/* ==========================================================================
   comun.js — Conexión Socket.IO, barra de estado, paro de emergencia,
   avisos y utilidades compartidas por todas las páginas.
   ========================================================================== */
"use strict";

const Robot = (() => {
  const bus = new EventTarget();
  const socket = io({ transports: ["websocket", "polling"], reconnectionDelay: 500, reconnectionDelayMax: 2000 });

  const estadoApp = {
    conectado: false,
    config: {},
    paletas: {},
    estado: {},
    simulacion: window.SIMULACION,
  };

  function emitirLocal(nombre, detalle) {
    bus.dispatchEvent(new CustomEvent(nombre, { detail: detalle }));
  }
  function escuchar(nombre, fn) {
    bus.addEventListener(nombre, (e) => fn(e.detail));
  }

  // ------------------------------------------------------------ Socket.IO
  socket.on("connect", () => { estadoApp.conectado = true; pintarConexion(); emitirLocal("conexion", true); });
  socket.on("disconnect", () => {
    estadoApp.conectado = false; pintarConexion(); emitirLocal("conexion", false);
    aviso("Conexión perdida con el robot. Los motores se detienen automáticamente.", "error");
  });
  socket.on("bienvenida", (d) => {
    estadoApp.config = d.config; estadoApp.paletas = d.paletas; estadoApp.simulacion = d.simulacion;
    emitirLocal("config", d.config);
    emitirLocal("paletas", d.paletas);
    actualizarEstado(d.estado);
    emitirLocal("listo", d);
  });
  socket.on("config", (cfg) => { estadoApp.config = cfg; emitirLocal("config", cfg); });
  socket.on("estado", actualizarEstado);
  socket.on("termico", (t) => emitirLocal("termico", t));
  socket.on("seguridad", (s) => {
    const tipo = s.tipo === "rearme" ? "ok" : "error";
    aviso(s.mensaje, tipo);
    emitirLocal("seguridad", s);
  });
  socket.on("nueva_inspeccion", (d) => emitirLocal("nueva_inspeccion", d));

  // ------------------------------------------------------------ Barra de estado
  const $ = (sel) => document.querySelector(sel);
  function chip(id, texto, nivel) {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelector("b").textContent = texto;
    if (nivel) el.dataset.nivel = nivel; else delete el.dataset.nivel;
  }
  function pintarConexion() {
    const n = estadoApp.estado.clientes;
    chip("est-conexion", estadoApp.conectado ? `En línea${n > 1 ? " · " + n : ""}` : "Sin conexión",
      estadoApp.conectado ? "ok" : "mal");
  }
  function actualizarEstado(e) {
    if (!e) return;
    estadoApp.estado = e;
    pintarConexion();
    if (e.bateria_v == null) {
      chip("est-bateria", "sin dato", null);
    } else {
      const nivel = { ok: "ok", baja: "aviso", critica: "mal" }[e.bateria_alerta] || null;
      chip("est-bateria", `${e.bateria_v.toFixed(2)} V · ${e.bateria_pct}%`, nivel);
    }
    chip("est-oruga-izq", `${fmtNum(e.v_izq, 2)} m/s`, null);
    chip("est-oruga-der", `${fmtNum(e.v_der, 2)} m/s`, null);
    chip("est-cpu", e.cpu == null ? "—" : `${Math.round(e.cpu)} %`, e.cpu > 85 ? "aviso" : null);
    chip("est-temp", e.temp_soc == null ? "sin dato" : `${e.temp_soc.toFixed(1)} °C`,
      e.temp_soc > 75 ? "mal" : e.temp_soc > 65 ? "aviso" : null);
    chip("est-hora", e.hora || "", null);
    $("#banda-paro").hidden = !e.paro_emergencia;
    document.body.classList.toggle("en-paro", !!e.paro_emergencia);
    emitirLocal("estado", e);
  }

  // ------------------------------------------------------------ Paro de emergencia
  function paro() {
    socket.emit("paro");
    // Respaldo por HTTP por si el socket está reconectando
    fetch("/api/paro", { method: "POST" }).catch(() => {});
    emitirLocal("paro_local", true);
    const b = $("#btn-paro");
    b.classList.add("pulsado");
    setTimeout(() => b.classList.remove("pulsado"), 180);
  }
  function rearmar() {
    if (!confirm("¿Confirmas que el área está despejada y deseas rearmar el robot?")) return;
    socket.emit("rearmar");
  }
  document.addEventListener("DOMContentLoaded", () => {
    const b = $("#btn-paro");
    b.addEventListener("pointerdown", (ev) => { ev.preventDefault(); paro(); });
    b.addEventListener("click", (ev) => ev.preventDefault());
    $("#btn-rearmar").addEventListener("click", rearmar);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.code !== "Space") return;
    const t = ev.target;
    if (t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && !["range", "checkbox", "radio"].includes(t.type)) || t.isContentEditable)) return;
    ev.preventDefault();
    paro();
  });

  // ------------------------------------------------------------ Utilidades
  function aviso(texto, tipo = "info", ms = 4200) {
    const cont = document.getElementById("avisos");
    if (!cont) return;
    const el = document.createElement("div");
    el.className = "aviso " + (tipo === "aviso" ? "aviso-amarillo" : tipo);
    el.textContent = texto;
    cont.appendChild(el);
    while (cont.children.length > 4) cont.firstChild.remove();
    setTimeout(() => el.remove(), ms);
  }

  async function api(url, opciones = {}) {
    const op = { ...opciones };
    if (op.json !== undefined) {
      op.method = op.method || "POST";
      op.headers = { "Content-Type": "application/json", ...(op.headers || {}) };
      op.body = JSON.stringify(op.json);
      delete op.json;
    }
    const r = await fetch(url, op);
    let datos = null;
    try { datos = await r.json(); } catch (_) { /* respuesta sin JSON */ }
    if (!r.ok || (datos && datos.ok === false)) {
      throw new Error((datos && datos.error) || `Error ${r.status}`);
    }
    return datos;
  }

  async function cargarPaletas() {
    if (Object.keys(estadoApp.paletas).length) return estadoApp.paletas;
    estadoApp.paletas = await api("/api/paletas");
    return estadoApp.paletas;
  }

  async function cargarConfig() {
    const d = await api("/api/config");
    estadoApp.config = d.valores;
    return d;
  }

  return { socket, estado: estadoApp, escuchar, aviso, api, paro, cargarPaletas, cargarConfig };
})();

/* ---------------------------------------------------------------- Formato */
function fmtNum(v, dec = 1) {
  return v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(dec);
}
function fmtTemp(v, dec = 1) {
  return v == null || Number.isNaN(v) ? "— °C" : `${Number(v).toFixed(dec)} °C`;
}
function fmtFecha(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("es-MX", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
function escaparHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------------------------- Clasificación ANSI/NETA */
const CLASIFICACIONES = [
  { nivel: 0, etiqueta: "Sin anomalía", color: "#2ecc71" },
  { nivel: 1, etiqueta: "Posible deficiencia", color: "#f1c40f" },
  { nivel: 2, etiqueta: "Reparar en próxima oportunidad", color: "#e67e22" },
  { nivel: 3, etiqueta: "Reparación inmediata", color: "#e74c3c" },
];

/** Misma regla que robot/logica/analisis_termico.py::clasificar_delta_t */
function clasificarDeltaT(dt, cfg) {
  const p = cfg["analisis.umbral_posible_c"] ?? 1;
  const r = cfg["analisis.umbral_reparar_c"] ?? 4;
  const i = cfg["analisis.umbral_inmediata_c"] ?? 15;
  if (dt > i) return CLASIFICACIONES[3];
  if (dt >= r) return CLASIFICACIONES[2];
  if (dt >= p) return CLASIFICACIONES[1];
  return CLASIFICACIONES[0];
}

/** Tabla de criterio ΔT con la fila del nivel actual resaltada. */
function pintarTablaCriterio(tabla, cfg, nivelActual) {
  if (!tabla) return;
  const p = cfg["analisis.umbral_posible_c"] ?? 1;
  const r = cfg["analisis.umbral_reparar_c"] ?? 4;
  const i = cfg["analisis.umbral_inmediata_c"] ?? 15;
  const rangos = [`< ${p} °C`, `${p} – <${r} °C`, `${r} – ${i} °C`, `> ${i} °C`];
  const html = CLASIFICACIONES.map((c, k) =>
    `<tr class="${k === nivelActual ? "actual" : ""}"><td><span class="punto" style="--c:${c.color}"></span>${rangos[k]}</td><td>${c.etiqueta}</td></tr>`).join("");
  if (tabla.dataset.html !== html) { tabla.innerHTML = html; tabla.dataset.html = html; }
}

function insigniaClasif(nivel, texto) {
  const c = CLASIFICACIONES[nivel] || CLASIFICACIONES[0];
  return `<span class="clasif" style="--c:${c.color}">${escaparHTML(texto || c.etiqueta)}</span>`;
}
