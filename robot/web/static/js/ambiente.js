/* ==========================================================================
   ambiente.js — Monitoreo ambiental: lecturas, gráficas, eventos,
   calibración de los MQ y eventos simulados.
   ========================================================================== */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_PUNTOS = 600;
  let graficas = {};
  let ultimoPunto = 0;

  const ejeTiempo = {
    type: "linear",
    ticks: { color: "#8b98a9", maxTicksLimit: 6, maxRotation: 0,
      callback: (v) => new Date(v).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) },
    grid: { color: "rgba(255,255,255,.05)" },
  };
  const eje = (texto, color, pos = "left") => ({
    position: pos, title: { display: true, text: texto, color }, ticks: { color },
    grid: { color: pos === "left" ? "rgba(255,255,255,.06)" : "transparent" },
  });
  const serie = (label, color, yAxisID, dash) => ({
    label, data: [], yAxisID, borderColor: color, backgroundColor: color + "22",
    borderWidth: 2, pointRadius: 0, tension: 0.25, borderDash: dash || [],
  });

  function crearGraficas() {
    if (!window.Chart) return;
    const base = (datasets, scales) => ({
      type: "line", data: { datasets },
      options: { responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
        interaction: { mode: "nearest", intersect: false },
        scales: { x: ejeTiempo, ...scales },
        plugins: { legend: { labels: { color: "#cfd8e3", boxWidth: 12 } } } },
    });
    graficas.th = new Chart($("graf-th"), base(
      [serie("Temperatura (°C)", "#ff8a5b", "y"), serie("Humedad (%)", "#3fa7ff", "y2")],
      { y: eje("°C", "#ff8a5b"), y2: eje("% HR", "#3fa7ff", "right") }));
    graficas.gas = new Chart($("graf-gas"), base(
      [serie("MQ-2 (ppm)", "#fca311", "y"), serie("MQ-7 CO (ppm)", "#e74c3c", "y2"),
        serie("Alarma MQ-2", "#fca311", "y", [6, 4]), serie("Alarma MQ-7", "#e74c3c", "y2", [6, 4])],
      { y: eje("MQ-2 ppm", "#fca311"), y2: eje("CO ppm", "#e74c3c", "right") }));
  }

  function agregarPunto(p) {
    if (!graficas.th) return;
    const t = p.t * 1000;
    const push = (ds, y) => { ds.data.push({ x: t, y }); if (ds.data.length > MAX_PUNTOS) ds.data.shift(); };
    const [temp, hum] = graficas.th.data.datasets;
    push(temp, p.temperatura); push(hum, p.humedad);
    const [mq2, mq7, al2, al7] = graficas.gas.data.datasets;
    const cfg = Robot.estado.config;
    push(mq2, p.mq2_ppm); push(mq7, p.mq7_ppm);
    push(al2, cfg["sensores.mq2.alarma_ppm"] ?? null); push(al7, cfg["sensores.mq7.alarma_ppm"] ?? null);
  }

  function refrescarGraficas() {
    Object.values(graficas).forEach((g) => g.update("none"));
  }

  async function cargarHistorial() {
    const d = await Robot.api("/api/ambiente");
    d.historial.forEach(agregarPunto);
    refrescarGraficas();
    if (d.historial.length) ultimoPunto = d.historial[d.historial.length - 1].t * 1000;
    pintarSensores($("sensores"), d.actual);
    if (d.simulacion) {
      $("panel-sim").hidden = false;
      $("botones-sim").innerHTML = Object.entries(d.eventos_simulables)
        .map(([k, txt]) => `<button type="button" class="btn" data-evento="${k}">${escaparHTML(txt)}</button>`).join("");
    }
  }

  async function cargarEventos() {
    const eventos = await Robot.api("/api/eventos?limite=150");
    $("tabla-eventos").innerHTML = eventos.length ? eventos.map((e) => `
      <tr><td class="num" style="text-align:left">${fmtFecha(e.fecha)}</td><td>${escaparHTML(e.tipo)}</td>
      <td><span class="nivel-evento ${escaparHTML(e.nivel)}">${escaparHTML(e.nivel)}</span></td>
      <td>${escaparHTML(e.mensaje)}</td></tr>`).join("")
      : '<tr><td colspan="4" class="vacio">Sin eventos registrados.</td></tr>';
  }

  Robot.escuchar("estado", (e) => {
    const a = e.ambiente;
    if (!a) return;
    pintarSensores($("sensores"), a);
    const ahora = Date.now();
    if (ahora - ultimoPunto >= 1000) {
      ultimoPunto = ahora;
      agregarPunto({ t: ahora / 1000, ...a });
      refrescarGraficas();
    }
    $("estado-respuestas").textContent = a.alarma ? "ALARMA DE GAS ACTIVA"
      : a.factor_velocidad < 1 ? `Velocidad limitada al ${Math.round(a.factor_velocidad * 100)} %` : "Sin restricciones";
    const pi = e.xbox_pi;
    const nav = ControlXbox.estado();
    $("estado-mando").innerHTML = nav.conectado
      ? '<span class="estado-comp" style="--c:var(--ok)">Conectado al navegador</span>'
      : pi && pi.conectado ? `<span class="estado-comp" style="--c:var(--ok)">${escaparHTML(pi.nombre)} (en la Pi)</span>`
        : '<span class="estado-comp">Sin control</span>';
  });

  Robot.escuchar("config", (cfg) => {
    $("r-hum").textContent = cfg["respuestas.humedad_alta_pct"];
    $("r-fh").textContent = Math.round(cfg["respuestas.factor_humedad"] * 100);
    $("r-fp").textContent = Math.round(cfg["respuestas.factor_presencia"] * 100);
    $("r-dp").textContent = cfg["respuestas.duracion_presencia_s"];
  });
  Robot.escuchar("ambiente_evento", () => cargarEventos());
  Robot.escuchar("seguridad", () => cargarEventos());

  $("botones-sim").addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-evento]");
    if (!b) return;
    try {
      const r = await Robot.api("/api/sim/evento", { json: { evento: b.dataset.evento } });
      Robot.aviso(`Simulando: ${r.descripcion}`, "info");
      setTimeout(cargarEventos, 300);
    } catch (e) { Robot.aviso(e.message, "error"); }
  });

  $("btn-calibrar").addEventListener("click", async () => {
    if (!confirm("¿El robot está en aire limpio, lejos de fuentes de gas, y ya pasó el calentamiento de los sensores?")) return;
    try {
      const r = await Robot.api("/api/sensores/calibrar", { method: "POST" });
      Robot.aviso("Calibración guardada: " + Object.entries(r.valores).map(([k, v]) => `${k.split(".")[1].toUpperCase()} R0 = ${v} kΩ`).join(", "), "ok", 6000);
      cargarEventos();
    } catch (e) { Robot.aviso(e.message, "error"); }
  });
  $("btn-eventos").addEventListener("click", cargarEventos);

  crearGraficas();
  cargarHistorial().catch((e) => Robot.aviso(e.message, "error"));
  cargarEventos().catch(() => {});
})();
