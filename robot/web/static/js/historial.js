/* ==========================================================================
   historial.js — Tabla de inspecciones con filtros y gráfica de tendencia.
   ========================================================================== */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const COLORES_SERIES = ["#fca311", "#3fa7ff", "#2ecc71", "#a879ff", "#ff6b9a", "#5ce1e6", "#f5d76e", "#ff8a5b"];
  let grafica = null;
  let inspecciones = [];

  // Filtro inicial desde la URL (?tablero=...)
  const params = new URLSearchParams(location.search);

  function filtros() {
    return {
      tablero: $("f-tablero").value,
      nivel: $("f-nivel").value,
      desde: $("f-desde").value,
      hasta: $("f-hasta").value,
    };
  }

  async function cargarTableros() {
    const tableros = await Robot.api("/api/tableros");
    const sel = $("f-tablero");
    const actual = sel.value || params.get("tablero") || "";
    sel.innerHTML = '<option value="">Todos</option>' +
      tableros.map((t) => `<option value="${escaparHTML(t.codigo)}">${escaparHTML(t.codigo)} (${t.inspecciones})</option>`).join("");
    sel.value = actual;
    $("k-tableros").textContent = tableros.length;
  }

  async function cargar() {
    const f = filtros();
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== "")).toString();
    try {
      inspecciones = await Robot.api("/api/inspecciones" + (qs ? "?" + qs : ""));
    } catch (e) {
      Robot.aviso(e.message, "error");
      return;
    }
    pintarTabla();
    pintarResumen();
    await pintarGrafica(f.tablero);
  }

  function pintarResumen() {
    $("k-total").textContent = inspecciones.length;
    $("k-criticos").textContent = inspecciones.filter((i) => i.nivel === 3).length;
    const max = inspecciones.reduce((a, i) => (!a || i.t_max > a.t_max ? i : a), null);
    $("k-tmax").textContent = max ? fmtTemp(max.t_max) : "—";
    $("k-tmax-tablero").textContent = max ? `${max.tablero} · ${fmtFecha(max.fecha)}` : "—";
  }

  function pintarTabla() {
    const cuerpo = $("cuerpo-tabla");
    if (!inspecciones.length) {
      cuerpo.innerHTML = '<tr><td colspan="9" class="vacio">No hay inspecciones con estos filtros. Captura un hallazgo en la página <a href="/captura">Captura</a>.</td></tr>';
      return;
    }
    cuerpo.innerHTML = inspecciones.map((i) => {
      const folio = `INS-${String(i.id).padStart(5, "0")}`;
      const mini = i.img_miniatura ? `<img src="/capturas/${i.img_miniatura}" alt="Visible" data-ver="${i.id}" loading="lazy">` : "";
      const miniT = i.img_mini_termica ? `<img src="/capturas/${i.img_mini_termica}" alt="Térmica" data-ver="${i.id}" loading="lazy">` : "";
      return `<tr>
        <td><div class="miniaturas">${mini}${miniT}</div></td>
        <td class="num">${folio}${i.simulacion ? ' <small title="Modo simulación" style="color:var(--acento)">SIM</small>' : ""}</td>
        <td>${fmtFecha(i.fecha)}</td>
        <td><b>${escaparHTML(i.tablero)}</b>${i.ubicacion ? `<br><small style="color:var(--tenue)">${escaparHTML(i.ubicacion)}</small>` : ""}</td>
        <td class="num">${fmtTemp(i.t_max)}</td>
        <td class="num ocultar-movil">${fmtTemp(i.t_referencia)}</td>
        <td class="num"><b>${fmtTemp(i.delta_t)}</b></td>
        <td>${insigniaClasif(i.nivel, i.clasificacion)}</td>
        <td><div class="acciones">
          <button class="btn btn-chico" data-ver="${i.id}">Ver</button>
          <a class="btn btn-chico" href="/api/inspecciones/${i.id}/pdf" target="_blank" rel="noopener">PDF</a>
          <button class="btn btn-chico btn-peligro" data-borrar="${i.id}" title="Eliminar">✕</button>
        </div></td>
      </tr>`;
    }).join("");
  }

  async function pintarGrafica(tablero) {
    if (!window.Chart) return;
    const series = await Robot.api("/api/tendencia" + (tablero ? "?tablero=" + encodeURIComponent(tablero) : ""));
    const nombres = Object.keys(series);
    const datasets = nombres.slice(0, 8).map((n, k) => ({
      label: n,
      data: series[n].map((p) => ({ x: new Date(p.fecha).getTime(), y: p.t_max, nivel: p.nivel, dt: p.delta_t, id: p.id })),
      borderColor: COLORES_SERIES[k % COLORES_SERIES.length],
      backgroundColor: COLORES_SERIES[k % COLORES_SERIES.length] + "33",
      pointBackgroundColor: series[n].map((p) => CLASIFICACIONES[p.nivel].color),
      pointBorderColor: "#0b0f14",
      pointRadius: 5, pointHoverRadius: 7, borderWidth: 2, tension: 0.25,
    }));
    if (tablero && series[tablero]) {
      datasets.push({
        label: "ΔT", yAxisID: "y2",
        data: series[tablero].map((p) => ({ x: new Date(p.fecha).getTime(), y: p.delta_t })),
        borderColor: "#e74c3c", borderDash: [6, 4], pointRadius: 3, borderWidth: 1.5, tension: 0.25,
      });
    }
    $("leyenda-grafica").textContent = nombres.length > 8 ? "Mostrando 8 tableros; filtra para ver uno en detalle" : "Color del punto = clasificación";

    // Si todas las inspecciones caben en ~2 días se muestran horas; si no, fechas
    const tiempos = datasets.flatMap((d) => d.data.map((p) => p.x));
    const lapso = tiempos.length ? Math.max(...tiempos) - Math.min(...tiempos) : 0;
    const formatoEje = lapso < 2 * 86400000
      ? (v) => new Date(v).toLocaleString("es-MX", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
      : (v) => new Date(v).toLocaleDateString("es-MX", { day: "2-digit", month: "short", year: "2-digit" });
    const ejeX = {
      type: "linear",
      ticks: { color: "#8b98a9", maxRotation: 0, maxTicksLimit: 7, callback: formatoEje },
      grid: { color: "rgba(255,255,255,.05)" },
    };
    const config = {
      type: "line",
      data: { datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", intersect: false },
        scales: {
          x: ejeX,
          y: { title: { display: true, text: "T máx (°C)", color: "#8b98a9" }, ticks: { color: "#8b98a9" }, grid: { color: "rgba(255,255,255,.06)" } },
          y2: { display: !!tablero, position: "right", title: { display: true, text: "ΔT (°C)", color: "#e74c3c" }, ticks: { color: "#e74c3c" }, grid: { display: false } },
        },
        plugins: {
          legend: {
            labels: {
              color: "#cfd8e3", usePointStyle: true,
              // La leyenda usa el color de la serie, no el del primer punto (que indica la clasificación)
              generateLabels: (chart) => Chart.defaults.plugins.legend.labels.generateLabels(chart)
                .map((l) => ({ ...l, fillStyle: chart.data.datasets[l.datasetIndex].borderColor, strokeStyle: chart.data.datasets[l.datasetIndex].borderColor })),
            },
          },
          tooltip: {
            callbacks: {
              title: (items) => fmtFecha(new Date(items[0].parsed.x).toISOString()),
              label: (ctx) => {
                const r = ctx.raw;
                if (ctx.dataset.label === "ΔT") return `ΔT: ${r.y.toFixed(1)} °C`;
                return `${ctx.dataset.label}: ${r.y.toFixed(1)} °C · ΔT ${r.dt.toFixed(1)} °C · ${CLASIFICACIONES[r.nivel].etiqueta}`;
              },
            },
          },
        },
      },
    };
    if (grafica) grafica.destroy();
    grafica = new Chart($("grafica"), config);
  }

  // ------------------------------------------------------------ Detalle
  async function verDetalle(id) {
    const i = await Robot.api(`/api/inspecciones/${id}`);
    const folio = `INS-${String(i.id).padStart(5, "0")}`;
    $("modal-titulo").textContent = `${folio} · ${i.tablero}`;
    $("modal-cuerpo").innerHTML = `
      <div class="rejilla rejilla-2">
        <div>${i.img_visible ? `<img src="/capturas/${i.img_visible}" alt="Visible">` : '<p class="vacio">Sin imagen visible</p>'}</div>
        <div><img src="/capturas/${i.img_termica}" alt="Termograma"></div>
      </div>
      ${i.img_fusion ? `<div style="margin-top:12px"><div class="etiqueta">Superposición térmico/visible</div><img src="/capturas/${i.img_fusion}" alt="Fusión" style="margin-top:6px"></div>` : ""}
      <div class="rejilla rejilla-2" style="margin-top:14px">
        <dl class="datos-lista">
          <dt>Fecha</dt><dd>${fmtFecha(i.fecha)}</dd>
          <dt>Ubicación</dt><dd>${escaparHTML(i.ubicacion || "—")}</dd>
          <dt>T máx / mín</dt><dd>${fmtTemp(i.t_max)} / ${fmtTemp(i.t_min)}</dd>
          <dt>T media</dt><dd>${fmtTemp(i.t_media)}</dd>
          <dt>Referencia</dt><dd>${fmtTemp(i.t_referencia)} (${i.modo_referencia})</dd>
          <dt>ΔT</dt><dd><b>${fmtTemp(i.delta_t)}</b></dd>
          <dt>Emisividad</dt><dd>${i.emisividad.toFixed(2)}</dd>
        </dl>
        <div>
          ${insigniaClasif(i.nivel, i.clasificacion)}
          <p style="white-space:pre-wrap;color:var(--tenue)">${escaparHTML(i.notas || "Sin notas.")}</p>
          <div class="acciones">
            <a class="btn btn-primario btn-chico" href="/api/inspecciones/${i.id}/pdf" target="_blank" rel="noopener">Reporte PDF</a>
            <a class="btn btn-chico" href="/reporte?id=${i.id}">Abrir en Reporte</a>
            <a class="btn btn-chico" href="/api/inspecciones/${i.id}/matriz.csv">Matriz CSV</a>
          </div>
        </div>
      </div>`;
    $("modal").hidden = false;
  }
  $("modal-cerrar").addEventListener("click", () => { $("modal").hidden = true; });
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("modal").hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("modal").hidden = true; });

  $("cuerpo-tabla").addEventListener("click", async (e) => {
    const ver = e.target.closest("[data-ver]");
    const borrar = e.target.closest("[data-borrar]");
    if (ver) verDetalle(ver.dataset.ver);
    if (borrar) {
      const id = borrar.dataset.borrar;
      if (!confirm(`¿Eliminar la inspección INS-${String(id).padStart(5, "0")}? Esta acción no se puede deshacer.`)) return;
      try {
        await Robot.api(`/api/inspecciones/${id}`, { method: "DELETE" });
        Robot.aviso("Inspección eliminada.", "ok");
        await cargarTableros();
        cargar();
      } catch (err) { Robot.aviso(err.message, "error"); }
    }
  });

  ["f-tablero", "f-nivel", "f-desde", "f-hasta"].forEach((id) => $(id).addEventListener("change", cargar));
  $("btn-limpiar").addEventListener("click", () => {
    ["f-tablero", "f-nivel", "f-desde", "f-hasta"].forEach((id) => { $(id).value = ""; });
    history.replaceState(null, "", "/historial");
    cargar();
  });
  Robot.escuchar("nueva_inspeccion", async () => { await cargarTableros(); cargar(); });

  cargarTableros().then(cargar).catch((e) => Robot.aviso(e.message, "error"));
})();
