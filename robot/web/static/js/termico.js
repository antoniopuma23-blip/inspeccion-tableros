/* ==========================================================================
   termico.js — Representación del mapa térmico en <canvas>.
   El servidor envía la matriz cruda (32×24 °C) y el navegador la colorea,
   la escala con interpolación y dibuja el punto caliente, la referencia y
   la temperatura bajo el cursor. Así la Raspberry Pi no gasta CPU en esto.
   ========================================================================== */
"use strict";

class VistaTermica {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {object} op  { paleta, mostrarCaja, alClic(fila, col, t), alCursor(info|null), superpuesto }
   */
  constructor(canvas, op = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.op = Object.assign({ paleta: "ironbow", mostrarCaja: true, superpuesto: false }, op);
    this.cuadro = null;
    this.tabla = null;
    this.rango = { min: 20, max: 40 };
    this.rangoManual = null;
    this.cursor = null;          // {fila, col}
    this.referencia = null;      // {fila, col}
    this.offscreen = document.createElement("canvas");
    this.offCtx = this.offscreen.getContext("2d");
    this._ajustarTamano();
    new ResizeObserver(() => { this._ajustarTamano(); this.dibujar(); }).observe(canvas);

    const mover = (ev) => {
      const p = this._celdaDesdeEvento(ev);
      this.cursor = p;
      if (this.op.alCursor) this.op.alCursor(p ? { ...p, t: this.temperatura(p.fila, p.col) } : null);
      this.dibujar();
    };
    canvas.addEventListener("pointermove", mover);
    canvas.addEventListener("pointerdown", mover);
    canvas.addEventListener("pointerleave", () => {
      this.cursor = null;
      if (this.op.alCursor) this.op.alCursor(null);
      this.dibujar();
    });
    canvas.addEventListener("click", (ev) => {
      const p = this._celdaDesdeEvento(ev);
      if (p && this.op.alClic) this.op.alClic(p.fila, p.col, this.temperatura(p.fila, p.col));
    });
  }

  set paleta(nombre) {
    this.op.paleta = nombre;
    this.tabla = null;
    this.dibujar();
  }

  fijarPaletas(paletas) {
    this.paletas = paletas;
    this.tabla = null;
    this.dibujar();
  }

  _tabla() {
    if (!this.tabla && this.paletas) {
      const p = this.paletas[this.op.paleta] || this.paletas.ironbow;
      this.tabla = p ? p.tabla : null;
    }
    return this.tabla;
  }

  _ajustarTamano() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    this.dpr = dpr;
  }

  _celdaDesdeEvento(ev) {
    if (!this.cuadro) return null;
    const r = this.canvas.getBoundingClientRect();
    const x = (ev.clientX - r.left) / r.width;
    const y = (ev.clientY - r.top) / r.height;
    if (x < 0 || x >= 1 || y < 0 || y >= 1) return null;
    return { fila: Math.floor(y * this.cuadro.filas), col: Math.floor(x * this.cuadro.columnas) };
  }

  temperatura(fila, col) {
    if (!this.cuadro) return null;
    return this.cuadro.datos[fila * this.cuadro.columnas + col];
  }

  /** Promedio 3×3 (igual que el servidor) para la referencia por punto. */
  promedioVecindad(fila, col) {
    if (!this.cuadro) return null;
    const { filas, columnas, datos } = this.cuadro;
    let s = 0, n = 0;
    for (let f = Math.max(0, fila - 1); f <= Math.min(filas - 1, fila + 1); f++) {
      for (let c = Math.max(0, col - 1); c <= Math.min(columnas - 1, col + 1); c++) {
        s += datos[f * columnas + c]; n++;
      }
    }
    return s / n;
  }

  actualizar(cuadro) {
    this.cuadro = cuadro;
    if (this.rangoManual) {
      this.rango = { ...this.rangoManual };
    } else {
      // Rango automático suavizado para evitar parpadeo de colores
      const min = cuadro.t_min, max = Math.max(cuadro.t_max, cuadro.t_min + 2);
      const a = this._iniciado ? 0.3 : 1;
      this.rango.min += (min - this.rango.min) * a;
      this.rango.max += (max - this.rango.max) * a;
      this._iniciado = true;
    }
    this.dibujar();
  }

  dibujar() {
    const ctx = this.ctx, cv = this.canvas;
    const tabla = this._tabla();
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!this.cuadro || !tabla) return;
    const { filas, columnas, datos } = this.cuadro;

    // 1) Imagen de 32×24 píxeles coloreada con la paleta
    if (this.offscreen.width !== columnas) { this.offscreen.width = columnas; this.offscreen.height = filas; }
    const img = this.offCtx.createImageData(columnas, filas);
    const { min, max } = this.rango;
    const k = 255 / Math.max(0.1, max - min);
    for (let i = 0; i < datos.length; i++) {
      let idx = Math.round((datos[i] - min) * k);
      idx = idx < 0 ? 0 : idx > 255 ? 255 : idx;
      const c = tabla[idx];
      img.data[i * 4] = c[0]; img.data[i * 4 + 1] = c[1]; img.data[i * 4 + 2] = c[2]; img.data[i * 4 + 3] = 255;
    }
    this.offCtx.putImageData(img, 0, 0);

    // 2) Escalado con interpolación bilineal del navegador
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(this.offscreen, 0, 0, cv.width, cv.height);

    const cw = cv.width / columnas, ch = cv.height / filas;
    const fuente = Math.max(11, Math.round(12 * this.dpr));
    ctx.font = `600 ${fuente}px ui-monospace, Menlo, Consolas, monospace`;
    ctx.textBaseline = "bottom";

    // 3) Recuadro del punto más caliente
    if (this.op.mostrarCaja && this.cuadro.pos_max) {
      const [f, c] = this.cuadro.pos_max;
      const x = Math.max(0, (c - 1) * cw), y = Math.max(0, (f - 1) * ch);
      const w = Math.min(cv.width, (c + 2) * cw) - x, h = Math.min(cv.height, (f + 2) * ch) - y;
      ctx.lineWidth = 4 * this.dpr; ctx.strokeStyle = "rgba(0,0,0,.7)"; ctx.strokeRect(x, y, w, h);
      ctx.lineWidth = 2 * this.dpr; ctx.strokeStyle = "#ffffff"; ctx.strokeRect(x, y, w, h);
      this._etiqueta(`▲ ${this.cuadro.t_max.toFixed(1)} °C`, x, y > fuente + 6 ? y - 3 : y + h + fuente + 6, "#fff");
    }

    // 4) Punto de referencia
    if (this.referencia) {
      const x = (this.referencia.col + 0.5) * cw, y = (this.referencia.fila + 0.5) * ch;
      this._cruz(x, y, 10 * this.dpr, "#5dff7a");
      this._etiqueta("REF", x + 8 * this.dpr, y - 6 * this.dpr, "#5dff7a");
    }

    // 5) Cursor
    if (this.cursor) {
      const x = (this.cursor.col + 0.5) * cw, y = (this.cursor.fila + 0.5) * ch;
      this._cruz(x, y, 8 * this.dpr, "#ffd166");
      const t = this.temperatura(this.cursor.fila, this.cursor.col);
      const texto = `${t.toFixed(1)} °C`;
      const ancho = ctx.measureText(texto).width;
      const tx = x + 12 * this.dpr + ancho > cv.width ? x - ancho - 12 * this.dpr : x + 12 * this.dpr;
      this._etiqueta(texto, tx, y + fuente / 2, "#ffd166");
    }
  }

  _cruz(x, y, r, color) {
    const ctx = this.ctx;
    for (const [col, w] of [["rgba(0,0,0,.75)", 4], [color, 2]]) {
      ctx.strokeStyle = col; ctx.lineWidth = w * this.dpr;
      ctx.beginPath();
      ctx.moveTo(x - r, y); ctx.lineTo(x + r, y);
      ctx.moveTo(x, y - r); ctx.lineTo(x, y + r);
      ctx.stroke();
    }
  }

  _etiqueta(texto, x, y, color) {
    const ctx = this.ctx;
    ctx.lineWidth = 4 * this.dpr; ctx.strokeStyle = "rgba(0,0,0,.85)";
    ctx.strokeText(texto, x, y);
    ctx.fillStyle = color;
    ctx.fillText(texto, x, y);
  }
}

/** Dibuja la escala vertical de color con marcas de temperatura. */
function dibujarEscala(canvas, paletas, nombre, min, max) {
  if (!paletas) return;
  const p = paletas[nombre] || paletas.ironbow;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (!w || !h) return;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#05070a"; ctx.fillRect(0, 0, w, h);
  const m = 12 * dpr, x0 = 8 * dpr, bw = 14 * dpr, alto = h - 2 * m;
  for (let y = 0; y < alto; y++) {
    const c = p.tabla[Math.round(255 * (1 - y / (alto - 1)))];
    ctx.fillStyle = `rgb(${c[0]},${c[1]},${c[2]})`;
    ctx.fillRect(x0, m + y, bw, 1);
  }
  ctx.strokeStyle = "#34445b"; ctx.lineWidth = dpr; ctx.strokeRect(x0, m, bw, alto);
  ctx.fillStyle = "#cfd8e3";
  ctx.font = `600 ${Math.round(10.5 * dpr)}px ui-monospace, Menlo, Consolas, monospace`;
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const y = m + (i / 4) * alto;
    const t = max - (i / 4) * (max - min);
    ctx.fillRect(x0 + bw, y - dpr / 2, 4 * dpr, dpr);
    ctx.fillText(t.toFixed(1) + "°", x0 + bw + 6 * dpr, y);
  }
}

/** Coloca el canvas superpuesto sobre el video según la alineación configurada. */
function posicionarSuperpuesto(canvas, cfg) {
  const ex = cfg["superposicion.escala_x"] ?? 0.88, ey = cfg["superposicion.escala_y"] ?? 0.72;
  const dx = cfg["superposicion.desplazamiento_x"] ?? 0, dy = cfg["superposicion.desplazamiento_y"] ?? 0;
  canvas.style.width = `${ex * 100}%`;
  canvas.style.height = `${ey * 100}%`;
  canvas.style.left = `${(0.5 + dx - ex / 2) * 100}%`;
  canvas.style.top = `${(0.5 + dy - ey / 2) * 100}%`;
}
