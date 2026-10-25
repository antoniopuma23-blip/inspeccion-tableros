/* ==========================================================================
   reporte.js — Selección de inspección, vista previa y generación del PDF.
   ========================================================================== */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  let inspecciones = [];
  let seleccion = Number(new URLSearchParams(location.search).get("id")) || null;
  const esMovil = () => window.matchMedia("(max-width: 860px)").matches;

  async function cargarLista() {
    inspecciones = await Robot.api("/api/inspecciones");
    if (!seleccion && inspecciones.length) seleccion = inspecciones[0].id;
    pintarLista();
    if (seleccion) seleccionar(seleccion);
  }

  function pintarLista() {
    const q = $("buscar").value.trim().toLowerCase();
    const filtradas = inspecciones.filter((i) =>
      !q || i.tablero.toLowerCase().includes(q) || `ins-${String(i.id).padStart(5, "0")}`.includes(q));
    $("lista").innerHTML = filtradas.length ? filtradas.map((i) => `
      <div class="item-insp ${i.id === seleccion ? "sel" : ""}" data-id="${i.id}">
        ${i.img_mini_termica ? `<img src="/capturas/${i.img_mini_termica}" alt="" loading="lazy">` : ""}
        <div class="txt"><b>${escaparHTML(i.tablero)}</b><small>INS-${String(i.id).padStart(5, "0")} · ${fmtFecha(i.fecha)}</small></div>
        ${insigniaClasif(i.nivel, `${i.delta_t.toFixed(1)} °C`)}
      </div>`).join("") : '<p class="vacio">No hay inspecciones registradas.</p>';
  }

  async function seleccionar(id) {
    seleccion = Number(id);
    history.replaceState(null, "", `/reporte?id=${seleccion}`);
    pintarLista();
    let i;
    try { i = await Robot.api(`/api/inspecciones/${seleccion}`); }
    catch (e) { $("vista-previa").innerHTML = `<p class="vacio">${escaparHTML(e.message)}</p>`; return; }
    const folio = `INS-${String(i.id).padStart(5, "0")}`;
    const c = CLASIFICACIONES[i.nivel];
    $("acciones-reporte").hidden = false;
    $("btn-descargar").href = `/api/inspecciones/${i.id}/pdf?descargar=1`;
    $("vista-previa").innerHTML = `
      <div class="tarjeta-dt" style="--c:${c.color}">
        <div class="dt-fila"><div><span class="dt-etq">${folio} · ${fmtFecha(i.fecha)}</span><h2 style="margin-top:4px">${escaparHTML(i.tablero)}</h2></div>${insigniaClasif(i.nivel, i.clasificacion)}</div>
        <div class="dt-detalle">
          <div>T máx<b>${fmtTemp(i.t_max)}</b></div>
          <div>T referencia<b>${fmtTemp(i.t_referencia)}</b></div>
          <div>ΔT<b>${fmtTemp(i.delta_t)}</b></div>
        </div>
      </div>
      <div class="rejilla rejilla-2">
        <div>${i.img_visible ? `<img src="/capturas/${i.img_visible}" alt="Visible" style="width:100%;border-radius:8px">` : '<p class="vacio">Sin imagen visible</p>'}</div>
        <div><img src="/capturas/${i.img_termica}" alt="Termograma" style="width:100%;border-radius:8px"></div>
      </div>
      <p style="margin:14px 0 4px"><b>Recomendación:</b> <span id="recomendacion"></span></p>
      <p style="color:var(--tenue);white-space:pre-wrap;margin:0 0 12px"><b>Notas:</b> ${escaparHTML(i.notas || "Sin observaciones.")}</p>
      <div id="contenedor-pdf"></div>`;
    try {
      const cfg = await Robot.api("/api/config");
      $("recomendacion").textContent = cfg.clasificaciones[i.nivel].recomendacion;
    } catch (_) { /* opcional */ }
  }

  $("lista").addEventListener("click", (e) => {
    const item = e.target.closest(".item-insp");
    if (item) seleccionar(item.dataset.id);
  });
  $("buscar").addEventListener("input", pintarLista);

  $("btn-generar").addEventListener("click", () => {
    if (!seleccion) return;
    const url = `/api/inspecciones/${seleccion}/pdf?t=${Date.now()}`;
    if (esMovil()) { window.open(url, "_blank"); return; }
    $("contenedor-pdf").innerHTML = `<iframe class="visor-pdf" src="${url}" title="Reporte PDF"></iframe>`;
    Robot.aviso("Reporte generado.", "ok");
  });

  // ------------------------------------------------------------ Encabezado
  async function cargarEncabezado() {
    const d = await Robot.cargarConfig();
    $("r-empresa").value = d.valores["reporte.empresa"] || "";
    $("r-titulo").value = d.valores["reporte.titulo"] || "";
    $("r-responsable").value = d.valores["reporte.responsable"] || "";
  }
  $("form-encabezado").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await Robot.api("/api/config", { json: {
        "reporte.empresa": $("r-empresa").value, "reporte.titulo": $("r-titulo").value,
        "reporte.responsable": $("r-responsable").value,
      } });
      Robot.aviso("Encabezado guardado.", "ok");
    } catch (err) { Robot.aviso(err.message, "error"); }
  });
  $("logo").addEventListener("change", async () => {
    const archivo = $("logo").files[0];
    if (!archivo) return;
    const datos = new FormData();
    datos.append("logo", archivo);
    try {
      await Robot.api("/api/logo", { method: "POST", body: datos });
      $("logo-vista").src = "/api/logo?t=" + Date.now();
      Robot.aviso("Logo actualizado.", "ok");
    } catch (err) { Robot.aviso(err.message, "error"); }
  });

  cargarEncabezado().catch(() => {});
  cargarLista().catch((e) => Robot.aviso(e.message, "error"));
})();
