/* ==========================================================================
   gamepad.js — Control Xbox conectado a la PC o celular que muestra la
   interfaz (Gamepad API del navegador). Se carga en todas las páginas.

   Asignación (igual que robot/logica/gamepad.py, control conectado a la Pi):
     Stick izquierdo ........ arcade: avanzar/retroceder y girar
     Ambos sticks (vertical)  tanque: cada stick mueve su oruga
     B ...................... PARO DE EMERGENCIA
     Menú (≡) mantener 1.5 s  Rearmar
     Vista (⧉) .............. Cambiar modo arcade ↔ tanque
     LB / RB ................ Limitador −25 % / +25 %
     A ...................... Captura rápida (último tablero)

   Seguridad: mientras hay movimiento se envía un comando cada 100 ms (el
   watchdog del robot lo exige); al soltar se envía "detener"; si el control
   se desconecta o la pestaña se oculta, el robot se detiene.
   ========================================================================== */
"use strict";

const ControlXbox = (() => {
  // Índices del "standard mapping" de la Gamepad API
  const B = { A: 0, B: 1, X: 2, Y: 3, LB: 4, RB: 5, LT: 6, RT: 7, VISTA: 8, MENU: 9 };
  const TIEMPO_REARME_MS = 1500;
  let indice = null;
  let limitador = 1.0;
  let enMovimiento = false;
  let previos = [];
  let tMenu = null;
  let ultimoEnvio = 0;

  const cfg = () => Robot.estado.config || {};
  const modo = () => cfg()["control_xbox.modo"] || "arcade";

  function zonaMuerta(v, z) {
    v = Math.max(-1, Math.min(1, v));
    return Math.abs(v) <= z ? 0 : Math.sign(v) * (Math.abs(v) - z) / (1 - z);
  }
  function acondicionar(v) {
    const z = cfg()["control_xbox.zona_muerta"] ?? 0.15;
    const e = cfg()["control_xbox.expo"] ?? 0.35;
    const x = zonaMuerta(v, z);
    return (1 - e) * x + e * x * x * x;
  }

  function pintarChip() {
    const pi = (Robot.estado.estado || {}).xbox_pi;
    let texto = "sin control", nivel = null;
    if (indice !== null) {
      texto = `navegador · ${modo()} · ${Math.round(limitador * 100)} %`;
      nivel = "ok";
    } else if (pi && pi.conectado) {
      texto = `en la Pi · ${pi.modo} · ${Math.round(pi.limitador * 100)} %`;
      nivel = "ok";
    }
    Robot.chip("est-mando", texto, nivel);
  }

  function vibrar(fuerte = 1.0, ms = 350) {
    const gp = indice !== null ? navigator.getGamepads()[indice] : null;
    if (gp && gp.vibrationActuator && gp.vibrationActuator.playEffect) {
      gp.vibrationActuator.playEffect("dual-rumble", { duration: ms, strongMagnitude: fuerte, weakMagnitude: fuerte })
        .catch(() => {});
    }
  }

  function soltar() {
    if (enMovimiento) {
      Robot.socket.emit("detener");
      enMovimiento = false;
    }
  }

  function presionado(gp, b) {
    return !!(gp.buttons[b] && gp.buttons[b].pressed);
  }
  function flanco(gp, b) {
    return presionado(gp, b) && !previos[b];
  }

  function ciclo() {
    if (indice === null) return;
    const gp = navigator.getGamepads()[indice];
    if (!gp || !gp.connected) return;
    if (document.hidden) { soltar(); return; }

    // --- Botones ---------------------------------------------------------
    if (flanco(gp, B.B)) {
      Robot.paro();
      enMovimiento = false;
      vibrar(1.0, 500);
    }
    if (flanco(gp, B.A)) {
      Robot.socket.emit("captura_rapida", null, (r) => {
        if (r && r.ok) { Robot.aviso(`Captura rápida INS-${String(r.id).padStart(5, "0")} (${r.tablero})`, "ok"); vibrar(0.4, 150); }
        else if (r) Robot.aviso(r.error, "aviso");
      });
    }
    if (flanco(gp, B.LB)) limitador = Math.max(0.25, +(limitador - 0.25).toFixed(2));
    if (flanco(gp, B.RB)) limitador = Math.min(1.0, +(limitador + 0.25).toFixed(2));
    if (flanco(gp, B.LB) || flanco(gp, B.RB)) Robot.aviso(`Limitador del control: ${Math.round(limitador * 100)} %`, "info", 1500);
    if (flanco(gp, B.VISTA)) {
      const nuevo = modo() === "arcade" ? "tanque" : "arcade";
      Robot.api("/api/config", { json: { "control_xbox.modo": nuevo } })
        .then(() => Robot.aviso(`Modo del control: ${nuevo === "tanque" ? "tanque (un stick por oruga)" : "arcade (stick izquierdo)"}`, "info"))
        .catch((e) => Robot.aviso(e.message, "error"));
    }
    if (presionado(gp, B.MENU)) {
      tMenu = tMenu ?? performance.now();
      if (performance.now() - tMenu >= TIEMPO_REARME_MS) {
        Robot.socket.emit("rearmar");
        tMenu = Infinity;     // no repetir hasta soltar
        vibrar(0.3, 200);
      }
    } else {
      tMenu = null;
    }
    previos = gp.buttons.map((b) => b.pressed);

    // --- Ejes (Gamepad API: arriba es negativo) -------------------------
    const lx = acondicionar(gp.axes[0] || 0) * limitador;
    const ly = -acondicionar(gp.axes[1] || 0) * limitador;
    const ry = -acondicionar(gp.axes[3] || 0) * limitador;
    const tanque = modo() === "tanque";
    const a = tanque ? ly : lx, b = tanque ? ry : ly;
    const activo = Math.abs(a) > 0.001 || Math.abs(b) > 0.001;

    if (activo && Robot.estado.conectado) {
      const ahora = performance.now();
      if (ahora - ultimoEnvio >= 100) {
        ultimoEnvio = ahora;
        if (tanque) Robot.socket.emit("orugas", { izq: +a.toFixed(3), der: +b.toFixed(3) });
        else Robot.socket.emit("mover", { x: +a.toFixed(3), y: +b.toFixed(3) });
        enMovimiento = true;
      }
    } else {
      soltar();
    }
  }

  function adoptar(gp, avisar) {
    indice = gp.index;
    previos = gp.buttons.map((b) => b.pressed);
    if (avisar) Robot.aviso(`Control conectado: ${gp.id.split("(")[0].trim()}. B = paro, Menú (mantener) = rearmar.`, "ok");
    pintarChip();
  }
  window.addEventListener("gamepadconnected", (e) => { if (indice === null) adoptar(e.gamepad, true); });
  // Al cambiar de página el navegador no repite "gamepadconnected": se busca
  // un control que ya esté conectado
  function buscarConectado() {
    if (indice !== null || !navigator.getGamepads) return;
    const gp = Array.from(navigator.getGamepads()).find((g) => g && g.connected);
    if (gp) adoptar(gp, false);
  }
  window.addEventListener("gamepaddisconnected", (e) => {
    if (e.gamepad.index !== indice) return;
    indice = null;
    soltar();
    Robot.socket.emit("detener");
    Robot.aviso("Control desconectado: robot detenido.", "aviso");
    pintarChip();
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) soltar(); });
  window.addEventListener("blur", soltar);
  Robot.escuchar("estado", pintarChip);
  Robot.escuchar("config", pintarChip);
  Robot.escuchar("ambiente_evento", (ev) => { if (ev.nivel === "alarma") vibrar(1.0, 800); });
  Robot.escuchar("seguridad", (s) => { if (s.tipo !== "rearme") vibrar(1.0, 400); });

  setInterval(() => { buscarConectado(); ciclo(); }, 50);
  return { estado: () => ({ conectado: indice !== null, modo: modo(), limitador }) };
})();
