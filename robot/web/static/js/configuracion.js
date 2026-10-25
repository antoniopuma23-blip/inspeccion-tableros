/* ==========================================================================
   configuracion.js — Edición de parámetros y diagnóstico.
   ========================================================================== */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("form-config");
  const campos = () => form.querySelectorAll("[data-clave]");
  const NOMBRES_COMP = {
    motores: "Motores (L298N)", encoders: "Encoders", camara_termica: "Cámara térmica MLX90640",
    camara_visible: "Cámara visible (webcam USB)", sensores_ambiente: "Sensores TechRoach", control_xbox: "Control Xbox (Pi)",
  };
  let paletas = {};

  function rellenar(valores) {
    campos().forEach((el) => {
      const v = valores[el.dataset.clave];
      if (v === undefined) return;
      if (el.type === "checkbox") el.checked = !!v;
      else if (el.dataset.escala) el.value = +(v * Number(el.dataset.escala)).toFixed(2);
      else el.value = v;
    });
    actualizarAyudas();
  }

  function leer() {
    const cambios = {};
    campos().forEach((el) => {
      const clave = el.dataset.clave;
      if (el.type === "checkbox") cambios[clave] = el.checked;
      else if (el.type === "number" || el.type === "range") {
        const v = parseFloat(el.value);
        if (Number.isFinite(v)) cambios[clave] = el.dataset.escala ? v / Number(el.dataset.escala) : v;
      } else cambios[clave] = el.value;
    });
    return cambios;
  }

  function actualizarAyudas() {
    const v = parseFloat($("vmax").value);
    $("vmax-txt").textContent = `${v.toFixed(2)} m/s`;
    $("alerta-vmax").hidden = !(v > 0.3);
    $("alerta-emis").hidden = !(parseFloat($("emis").value) < 0.6);
    const p = paletas[$("paleta").value];
    if (p) {
      const paradas = p.tabla.filter((_, i) => i % 32 === 0 || i === 255).map((c) => `rgb(${c.join(",")})`);
      $("muestra-paleta").style.background = `linear-gradient(90deg, ${paradas.join(",")})`;
    }
  }
  form.addEventListener("input", actualizarAyudas);
  $("material").addEventListener("change", (e) => { if (e.target.value) { $("emis").value = e.target.value; actualizarAyudas(); } });

  async function cargar() {
    paletas = await Robot.cargarPaletas();
    $("paleta").innerHTML = Object.entries(paletas).map(([k, p]) => `<option value="${k}">${p.nombre}</option>`).join("");
    const d = await Robot.cargarConfig();
    rellenar(d.valores);
    pintarPines(d.pines);
    await diagnostico();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("btn-guardar");
    btn.disabled = true;
    try {
      const r = await Robot.api("/api/config", { json: leer() });
      rellenar(r.valores);
      Robot.aviso("Configuración guardada y aplicada.", "ok");
      diagnostico();
    } catch (err) {
      Robot.aviso(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  $("btn-restablecer").addEventListener("click", async () => {
    if (!confirm("¿Restablecer todos los parámetros a los valores de config.yaml?")) return;
    try {
      const r = await Robot.api("/api/config/restablecer", { method: "POST" });
      rellenar(r.valores);
      Robot.aviso("Valores de fábrica restablecidos.", "ok");
      diagnostico();
    } catch (err) { Robot.aviso(err.message, "error"); }
  });

  async function diagnostico() {
    const d = await Robot.api("/api/diagnostico");
    $("tabla-diag").innerHTML = Object.entries(d.componentes).map(([k, c]) => `
      <tr><td><b>${NOMBRES_COMP[k] || k}</b></td>
      <td><span class="estado-comp" style="--c:${c.disponible ? "var(--ok)" : "var(--peligro)"}">${c.disponible ? "Disponible" : "No disponible"}</span></td>
      <td>${escaparHTML(c.nombre)}</td><td style="color:var(--tenue)">${escaparHTML(c.detalle || "")}${c.errores_i2c ? ` · errores I2C: ${c.errores_i2c}` : ""}</td></tr>`).join("");
    const p = d.parametros_control;
    const vlibre = p.rpm_nominales / 60 * Math.PI * p.diametro_rueda;
    $("d-watchdog").textContent = `${d.watchdog_ms} ms (paros por watchdog: ${d.expiraciones_watchdog})`;
    $("d-vlibre").textContent = `${vlibre.toFixed(2)} m/s a ${p.rpm_nominales} rpm`;
    $("d-cpr").textContent = d.cuentas_por_vuelta;
    const e = d.esp32 || {};
    $("estado-esp32").innerHTML = e.habilitado === false || !e.puerto
      ? '<span class="estado-comp">Deshabilitado</span>'
      : `<span class="estado-comp" style="--c:${e.conectado ? "var(--ok)" : "var(--aviso)"}">${e.conectado ? "Conectado" : "Sin respuesta"}${e.error ? " · " + escaparHTML(e.error) : ""}</span>`;
  }
  $("btn-diag").addEventListener("click", diagnostico);

  function pintarPines(pines) {
    const nombres = {
      motor_izquierdo: "Motor izquierdo", motor_derecho: "Motor derecho",
      encoder_izquierdo: "Encoder izquierdo", encoder_derecho: "Encoder derecho",
      i2c: "I2C-1 (MLX90640 + ADS1115)", uart_esp32: "ESP32 (UART0)",
      dht22: "DHT22", pir: "PIR", vibracion: "Piezoeléctrico", iluminacion: "Iluminación (LDR)",
      alarma: "Alarma", mq2_digital: "MQ-2 (DO, respaldo)", mq7_digital: "MQ-7 (DO, respaldo)",
    };
    const senales = { pwm: "PWM (EN)", in1: "Dirección 1", in2: "Dirección 2", a: "Canal A", b: "Canal B", sda: "SDA", scl: "SCL", tx: "TX", rx: "RX",
      datos: "Datos", salida: "Salida digital", mosfet: "MOSFET del LED", buzzer: "Zumbador", led: "LED rojo" };
    const filas = [];
    for (const [grupo, sen] of Object.entries(pines)) {
      for (const [s, gpio] of Object.entries(sen)) {
        filas.push(`<tr><td>${nombres[grupo] || grupo}</td><td>${senales[s] || s}</td><td style="font-family:var(--mono)">GPIO${gpio}</td></tr>`);
      }
    }
    $("tabla-pines").innerHTML = filas.join("");
  }

  cargar().catch((e) => Robot.aviso(e.message, "error"));
})();
