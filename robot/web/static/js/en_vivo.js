/* ==========================================================================
   en_vivo.js — Página de inspección en vivo.
   ========================================================================== */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  let paletaActual = "ironbow";

  // ------------------------------------------------------------ Vistas térmicas
  const vista = new VistaTermica($("cv-termico"), {
    alCursor: (p) => { $("t-cursor").textContent = p ? fmtTemp(p.t) : "—"; },
  });
  const vistaSup = new VistaTermica($("cv-superpuesto"), {
    alCursor: (p) => { $("t-cursor").textContent = p ? fmtTemp(p.t) : "—"; },
  });

  function aplicarPaleta(nombre) {
    paletaActual = nombre;
    vista.paleta = nombre;
    vistaSup.paleta = nombre;
    $("sel-paleta").value = nombre;
  }

  Robot.escuchar("paletas", (paletas) => {
    const sel = $("sel-paleta");
    sel.innerHTML = Object.entries(paletas).map(([k, p]) => `<option value="${k}">${p.nombre}</option>`).join("");
    vista.fijarPaletas(paletas);
    vistaSup.fijarPaletas(paletas);
    aplicarPaleta(Robot.estado.config["interfaz.paleta"] || "ironbow");
  });
  Robot.escuchar("config", (cfg) => {
    posicionarSuperpuesto($("cv-superpuesto"), cfg);
    pintarTablaCriterio($("tabla-criterio"), cfg, null);
  });
  $("sel-paleta").addEventListener("change", (e) => aplicarPaleta(e.target.value));

  // ------------------------------------------------------------ Superposición
  const chkSup = $("chk-superponer"), rngOp = $("rng-opacidad");
  function actualizarSuperposicion() {
    $("cv-superpuesto").hidden = !chkSup.checked;
    $("cv-superpuesto").style.opacity = rngOp.value / 100;
    $("ctl-opacidad").style.opacity = chkSup.checked ? 1 : 0.45;
    try { localStorage.setItem("superponer", JSON.stringify({ on: chkSup.checked, op: rngOp.value })); } catch (_) {}
  }
  try {
    const g = JSON.parse(localStorage.getItem("superponer") || "null");
    if (g) { chkSup.checked = g.on; rngOp.value = g.op; }
  } catch (_) {}
  chkSup.addEventListener("change", actualizarSuperposicion);
  rngOp.addEventListener("input", actualizarSuperposicion);
  actualizarSuperposicion();

  // ------------------------------------------------------------ Rango fijo
  const chkRango = $("chk-rango");
  function actualizarRango() {
    $("fila-rango").hidden = !chkRango.checked;
    if (chkRango.checked) {
      const min = Number($("rango-min").value), max = Number($("rango-max").value);
      vista.rangoManual = vistaSup.rangoManual = max > min ? { min, max } : null;
    } else {
      vista.rangoManual = vistaSup.rangoManual = null;
    }
  }
  chkRango.addEventListener("change", actualizarRango);
  $("rango-min").addEventListener("input", actualizarRango);
  $("rango-max").addEventListener("input", actualizarRango);

  // ------------------------------------------------------------ Datos térmicos
  let ultimoTermico = 0;
  Robot.escuchar("termico", (t) => {
    ultimoTermico = Date.now();
    vista.actualizar(t);
    if (chkSup.checked) vistaSup.actualizar(t);
    $("sin-termico").hidden = true;
    $("t-max").textContent = fmtTemp(t.t_max);
    $("t-min").textContent = fmtTemp(t.t_min);
    $("t-med").textContent = fmtTemp(t.t_mediana);
    $("fps-termico").textContent = `${fmtNum(t.fps, 1)} Hz`;
    dibujarEscala($("cv-escala"), Robot.estado.paletas, paletaActual, vista.rango.min, vista.rango.max);

    const dt = t.t_max - t.t_mediana;
    const c = clasificarDeltaT(dt, Robot.estado.config);
    $("tarjeta-dt").style.setProperty("--c", c.color);
    $("dt-valor").textContent = `${dt.toFixed(1)} °C`;
    $("dt-clasif").innerHTML = insigniaClasif(c.nivel);
    pintarTablaCriterio($("tabla-criterio"), Robot.estado.config, c.nivel);
  });
  setInterval(() => {
    if (Date.now() - ultimoTermico > 3000) $("sin-termico").hidden = false;
  }, 1000);

  // ------------------------------------------------------------ Video (reconexión)
  const video = $("video");
  video.addEventListener("error", () => setTimeout(() => { video.src = "/video.mjpg?t=" + Date.now(); }, 1500));
  Robot.escuchar("conexion", (ok) => { if (ok) video.src = "/video.mjpg?t=" + Date.now(); });

  // ------------------------------------------------------------ Control y telemetría
  crearPanelJoystick($("panel-joystick"));

  function barra(id, v, vmax) {
    const el = $(id);
    const f = Math.max(-1, Math.min(1, v / (vmax || 0.3)));
    el.style.width = `${Math.abs(f) * 50}%`;
    el.style.left = f >= 0 ? "50%" : `${50 - Math.abs(f) * 50}%`;
    el.classList.toggle("neg", f < 0);
  }
  Robot.escuchar("estado", (e) => {
    const vmax = e.velocidad_maxima;
    barra("bar-izq", e.v_izq, vmax); barra("bar-der", e.v_der, vmax);
    barra("bar-ref-izq", e.ref_izq, vmax); barra("bar-ref-der", e.ref_der, vmax);
    $("val-izq").textContent = `${fmtNum(e.v_izq, 2)} m/s`;
    $("val-der").textContent = `${fmtNum(e.v_der, 2)} m/s`;
    $("val-ref-izq").textContent = `${fmtNum(e.ref_izq, 2)} m/s`;
    $("val-ref-der").textContent = `${fmtNum(e.ref_der, 2)} m/s`;
    $("d-vmax").textContent = `${fmtNum(vmax, 2)} m/s`;
    $("d-lazo").textContent = e.lazo_cerrado ? "Lazo cerrado (PI + encoders)" : "Lazo abierto";
    $("d-duty").textContent = `${Math.round(e.duty_izq * 100)} % / ${Math.round(e.duty_der * 100)} %`;
    $("d-paro").textContent = e.ultimo_paro || "—";
    $("estado-motores").innerHTML = !e.motores_ok
      ? '<span class="estado-comp" style="--c:var(--peligro)">Motores no disponibles</span>'
      : e.paro_emergencia
        ? '<span class="estado-comp" style="--c:var(--peligro)">Bloqueado por paro</span>'
        : '<span class="estado-comp" style="--c:var(--ok)">Listo</span>';
  });
})();
