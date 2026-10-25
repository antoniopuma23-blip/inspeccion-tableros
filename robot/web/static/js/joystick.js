/* ==========================================================================
   joystick.js — Joystick táctil + control por teclado (WASD / flechas).

   Protocolo de seguridad:
   * Mientras hay movimiento se envía "mover" cada 100 ms (alimenta el
     watchdog de 500 ms del robot).
   * Al soltar se envía "detener" una vez y se deja de enviar.
   * Si la pestaña pierde el foco o se oculta, se suelta todo y se detiene.
   ========================================================================== */
"use strict";

class Joystick {
  constructor(elemento, alCambiar) {
    this.el = elemento;
    this.perilla = elemento.querySelector(".perilla");
    this.alCambiar = alCambiar;
    this.x = 0; this.y = 0;
    this.idPuntero = null;
    elemento.addEventListener("pointerdown", (e) => this._inicio(e));
    elemento.addEventListener("pointermove", (e) => this._mover(e));
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) {
      elemento.addEventListener(ev, (e) => this._fin(e));
    }
  }

  _inicio(e) {
    if (this.el.classList.contains("bloqueado")) return;
    e.preventDefault();
    this.idPuntero = e.pointerId;
    this.el.setPointerCapture(e.pointerId);
    this.el.classList.add("activo");
    this._mover(e);
  }

  _mover(e) {
    if (e.pointerId !== this.idPuntero) return;
    const r = this.el.getBoundingClientRect();
    const radio = r.width / 2 - 30;
    let dx = e.clientX - (r.left + r.width / 2);
    let dy = e.clientY - (r.top + r.height / 2);
    const dist = Math.hypot(dx, dy);
    if (dist > radio) { dx *= radio / dist; dy *= radio / dist; }
    this.perilla.style.transform = `translate(${dx}px, ${dy}px)`;
    let x = dx / radio, y = -dy / radio;
    // Zona muerta del 12 %
    const m = Math.hypot(x, y);
    if (m < 0.12) { x = 0; y = 0; }
    this.x = x; this.y = y;
    this.alCambiar(x, y);
  }

  _fin(e) {
    if (e.pointerId !== this.idPuntero) return;
    this.soltar();
  }

  soltar() {
    this.idPuntero = null;
    this.el.classList.remove("activo");
    this.perilla.style.transform = "translate(0, 0)";
    this.x = 0; this.y = 0;
    this.alCambiar(0, 0);
  }
}

class ControlManual {
  /**
   * @param {object} op { joystick: HTMLElement, teclas: HTMLElement, limitador: HTMLInputElement }
   */
  constructor(op) {
    this.socket = Robot.socket;
    this.joyX = 0; this.joyY = 0;
    this.teclasActivas = new Set();
    this.enMovimiento = false;
    this.limitador = op.limitador || null;
    this.indicadorTeclas = op.teclas || null;
    this.joystick = op.joystick ? new Joystick(op.joystick, (x, y) => { this.joyX = x; this.joyY = y; this._tick(); }) : null;

    this.mapa = {
      KeyW: "arriba", ArrowUp: "arriba", KeyS: "abajo", ArrowDown: "abajo",
      KeyA: "izquierda", ArrowLeft: "izquierda", KeyD: "derecha", ArrowRight: "derecha",
    };
    document.addEventListener("keydown", (e) => this._tecla(e, true));
    document.addEventListener("keyup", (e) => this._tecla(e, false));
    window.addEventListener("blur", () => this.soltarTodo());
    document.addEventListener("visibilitychange", () => { if (document.hidden) this.soltarTodo(); });
    Robot.escuchar("paro_local", () => this.soltarTodo());
    Robot.escuchar("conexion", (ok) => { if (!ok) this.soltarTodo(); });
    Robot.escuchar("estado", (e) => {
      if (this.joystick) this.joystick.el.classList.toggle("bloqueado", !!e.paro_emergencia);
    });

    setInterval(() => this._tick(), 100);
  }

  _tecla(e, presionada) {
    const dir = this.mapa[e.code];
    if (!dir) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" && t.type !== "range" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
    e.preventDefault();
    if (presionada) this.teclasActivas.add(dir); else this.teclasActivas.delete(dir);
    this._pintarTeclas();
    this._tick();
  }

  _pintarTeclas() {
    if (!this.indicadorTeclas) return;
    this.indicadorTeclas.querySelectorAll("[data-dir]").forEach((el) => {
      el.classList.toggle("on", this.teclasActivas.has(el.dataset.dir));
    });
  }

  comando() {
    let x = this.joyX, y = this.joyY;
    if (this.teclasActivas.size) {
      const a = this.teclasActivas;
      y = (a.has("arriba") ? 1 : 0) - (a.has("abajo") ? 1 : 0);
      x = (a.has("derecha") ? 1 : 0) - (a.has("izquierda") ? 1 : 0);
    }
    const k = this.limitador ? Number(this.limitador.value) / 100 : 1;
    return { x: x * k, y: y * k };
  }

  _tick() {
    const { x, y } = this.comando();
    const activo = Math.abs(x) > 0.001 || Math.abs(y) > 0.001;
    if (activo) {
      if (!Robot.estado.conectado) return;
      this.socket.emit("mover", { x: +x.toFixed(3), y: +y.toFixed(3) });
      this.enMovimiento = true;
    } else if (this.enMovimiento) {
      this.socket.emit("detener");
      this.enMovimiento = false;
    }
  }

  soltarTodo() {
    this.teclasActivas.clear();
    this._pintarTeclas();
    if (this.joystick && this.joystick.idPuntero !== null) this.joystick.soltar();
    this.joyX = 0; this.joyY = 0;
    if (this.enMovimiento) { this.socket.emit("detener"); this.enMovimiento = false; }
  }
}

/** Inserta el marcado del joystick y el indicador de teclas en un contenedor. */
function crearPanelJoystick(contenedor) {
  contenedor.innerHTML = `
    <div class="zona-joystick">
      <div class="joystick" aria-label="Joystick de movimiento">
        <span class="flecha n">▲</span><span class="flecha s">▼</span>
        <span class="flecha o">◀</span><span class="flecha e">▶</span>
        <div class="perilla"></div>
      </div>
      <div class="teclas" aria-hidden="true">
        <span class="tecla vacia"></span><span class="tecla" data-dir="arriba">W</span><span class="tecla vacia"></span>
        <span class="tecla" data-dir="izquierda">A</span><span class="tecla" data-dir="abajo">S</span><span class="tecla" data-dir="derecha">D</span>
      </div>
      <div class="ayuda-teclado">Teclado: WASD o flechas · <b>Espacio</b> = paro</div>
      <label class="control-compacto" style="width:100%;justify-content:center">
        Limitador <input type="range" class="limitador" min="20" max="100" step="5" value="100">
        <b class="valor-limitador" style="font-family:var(--mono);min-width:44px">100 %</b>
      </label>
    </div>`;
  const limitador = contenedor.querySelector(".limitador");
  const valor = contenedor.querySelector(".valor-limitador");
  limitador.addEventListener("input", () => { valor.textContent = `${limitador.value} %`; });
  return new ControlManual({
    joystick: contenedor.querySelector(".joystick"),
    teclas: contenedor.querySelector(".teclas"),
    limitador,
  });
}
