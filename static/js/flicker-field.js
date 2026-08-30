/* UWITZ — FLICKER FIELD (interactive variant, v2) */
(() => {
  class UwitzFlickerField extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.style.position = this.style.position || 'absolute';
      this.style.inset = this.style.inset || '0';
      this.style.display = 'block';
      this.style.overflow = 'hidden';
      this.style.zIndex = this.style.zIndex || '0';
      this._cursorEnabled = this.getAttribute('cursor') !== '0';
      this.style.pointerEvents = this._cursorEnabled ? 'auto' : 'none';
      const canvas = document.createElement('canvas');
      canvas.style.width = '100%'; canvas.style.height = '100%'; canvas.style.display = 'block';
      this.appendChild(canvas);
      this._canvas = canvas;
      this._ctx = canvas.getContext('2d');
      this._ro = new ResizeObserver(() => this._resize());
      this._ro.observe(this);
      this._resize();
      this._reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      this._pointer = null; this._pointerCell = null;
      if (this._cursorEnabled) {
        this._onMove = (e) => { const r = this.getBoundingClientRect(); this._pointer = { x: e.clientX - r.left, y: e.clientY - r.top }; };
        this._onLeave = () => { this._pointer = null; };
        this.addEventListener('pointermove', this._onMove);
        this.addEventListener('pointerleave', this._onLeave);
      }
      this._last = 0;
      this._frame = this._frame.bind(this);
      this._raf = requestAnimationFrame(this._frame);
    }
    disconnectedCallback() {
      if (this._raf) cancelAnimationFrame(this._raf);
      if (this._ro) this._ro.disconnect();
      if (this._onMove) this.removeEventListener('pointermove', this._onMove);
      if (this._onLeave) this.removeEventListener('pointerleave', this._onLeave);
    }
    _resolveColor() {
      const attr = this.getAttribute('color');
      if (attr) return attr;
      const cs = getComputedStyle(this);
      const v = cs.getPropertyValue('--accent') || cs.getPropertyValue('--text-accent');
      return (v && v.trim()) || '#DA2A1C';
    }
    _num(attr, fallback) { const v = parseFloat(this.getAttribute(attr)); return isNaN(v) ? fallback : v; }
    _pickTarget(block, pull) {
      const cols = this._cols, rows = this._rows;
      const maxC = Math.max(0, cols - block), maxR = Math.max(0, rows - block);
      if (this._pointerCell && pull > 0 && Math.random() < pull) {
        const spread = 4 + Math.random() * 6;
        const c = this._pointerCell.c - (block - 1) / 2 + (Math.random() - 0.5) * spread * 2;
        const r = this._pointerCell.r - (block - 1) / 2 + (Math.random() - 0.5) * spread * 2;
        return { c: Math.max(0, Math.min(maxC, Math.round(c))), r: Math.max(0, Math.min(maxR, Math.round(r))) };
      }
      return { c: Math.floor(Math.random() * (maxC + 1)), r: Math.floor(Math.random() * (maxR + 1)) };
    }
    _makeWalker(now, baseBlock) {
      const b = Math.max(1, baseBlock + Math.round((Math.random() - 0.5) * 2));
      const shapes = ['square', 'square', 'wide', 'tall'];
      const shape = shapes[Math.floor(Math.random() * shapes.length)];
      let bw = b, bh = b;
      if (shape === 'wide') { bw = b + Math.round(b * 0.7) + 1; bh = Math.max(2, Math.round(b * 0.66)); }
      else if (shape === 'tall') { bh = b + Math.round(b * 0.7) + 1; bw = Math.max(2, Math.round(b * 0.66)); }
      const start = this._pickTarget(Math.max(bw, bh), 0);
      return { bw, bh, shape, speedJitter: 0.7 + Math.random() * 0.8, brightJitter: 0.6 + Math.random() * 0.55, cx: start.c, cy: start.r, fromC: start.c, fromR: start.r, toC: start.c, toR: start.r, travelStart: now, travelDur: 0, dwellStart: now, dwellDur: 500 + Math.random() * 1500, phase: 'dwell' };
    }
    _resize() {
      const rect = this.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.round(rect.width)), h = Math.max(1, Math.round(rect.height));
      this._w = w; this._h = h;
      this._canvas.width = w * dpr; this._canvas.height = h * dpr;
      this._ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const gap = this._num('gap', 11);
      this._gap = gap;
      const baseBlock = Math.max(1, Math.round(this._num('block', 3)));
      this._cols = Math.ceil(w / gap) + 1; this._rows = Math.ceil(h / gap) + 1;
      this._alpha = new Float32Array(this._cols * this._rows);
      const count = Math.max(1, Math.round(this._num('count', 30)));
      const now = performance.now();
      this._walkers = Array.from({ length: count }, () => { const wk = this._makeWalker(now, baseBlock); wk.dwellStart = now - Math.random() * 1600; return wk; });
      this._cursorWalker = { block: Math.max(2, baseBlock), cx: -99, cy: -99, active: false };
    }
    _advance(wk, now, speedMul, pull) {
      const sm = speedMul * (wk.speedJitter || 1);
      if (wk.phase === 'dwell') {
        if (now - wk.dwellStart >= wk.dwellDur) {
          const t = this._pickTarget(Math.max(wk.bw, wk.bh), pull);
          wk.fromC = wk.toC; wk.fromR = wk.toR;
          if (wk.axis === undefined) wk.axis = Math.random() < 0.5 ? 'h' : 'v';
          else wk.axis = wk.axis === 'h' ? 'v' : 'h';
          if (wk.axis === 'h') t.r = wk.fromR; else t.c = wk.fromC;
          wk.toC = t.c; wk.toR = t.r;
          const dist = Math.hypot(wk.toC - wk.fromC, wk.toR - wk.fromR) || 1;
          wk.travelStart = now; wk.travelDur = (300 + dist * (55 + Math.random() * 55)) / sm;
          wk.phase = 'travel';
        }
      } else {
        const p = Math.min(1, (now - wk.travelStart) / wk.travelDur);
        const eased = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
        wk.cx = wk.fromC + (wk.toC - wk.fromC) * eased;
        wk.cy = wk.fromR + (wk.toR - wk.fromR) * eased;
        if (p >= 1) { wk.cx = wk.toC; wk.cy = wk.toR; wk.dwellStart = now; wk.dwellDur = ((pull > 0 ? 250 : 550) + Math.random() * (pull > 0 ? 700 : 1500)) / sm; wk.phase = 'dwell'; }
      }
      if (wk.phase === 'dwell') { wk.cx = wk.toC; wk.cy = wk.toR; }
    }
    _advanceCursor(now) {
      const cw = this._cursorWalker;
      if (this._pointerCell) {
        cw.active = true; cw.releaseAt = 0;
        const half = (cw.block - 1) / 2;
        const targetC = this._pointerCell.c - half, targetR = this._pointerCell.r - half;
        if (cw.cx == null || cw.cx < -50) { cw.cx = targetC; cw.cy = targetR; }
        cw.cx += (targetC - cw.cx) * 0.28; cw.cy += (targetR - cw.cy) * 0.28;
      } else if (cw.active) {
        if (!cw.releaseAt) cw.releaseAt = now;
        if (now - cw.releaseAt > 300) cw.active = false;
      }
    }
    _envelope(wk, now) {
      if (wk.phase === 'dwell') { const p = Math.min(1, (now - wk.dwellStart) / wk.dwellDur); return Math.sin(p * Math.PI); }
      const p = Math.min(1, (now - wk.travelStart) / wk.travelDur);
      return 0.4 + 0.3 * Math.sin(p * Math.PI);
    }
    _stamp(centerC, centerR, halfW, halfH, peakVal, shape) {
      const cols = this._cols, rows = this._rows, A = this._alpha;
      const c0 = Math.max(0, Math.floor(centerC - halfW - 0.5)), c1 = Math.min(cols - 1, Math.ceil(centerC + halfW + 0.5));
      const r0 = Math.max(0, Math.floor(centerR - halfH - 0.5)), r1 = Math.min(rows - 1, Math.ceil(centerR + halfH + 0.5));
      const armW = Math.max(0.6, halfW * 0.34), armH = Math.max(0.6, halfH * 0.34);
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          const dc = Math.abs(c - centerC), dr = Math.abs(r - centerR);
          if (dc > halfW + 0.5 || dr > halfH + 0.5) continue;
          if (shape === 'plus' && dc > armW && dr > armH) continue;
          const edgeFade = (dc > halfW - 0.4 || dr > halfH - 0.4) ? 0.55 : 1;
          const v = peakVal * edgeFade, idx = r * cols + c;
          if (v > A[idx]) A[idx] = v;
        }
      }
    }
    _frame(t) {
      this._raf = requestAnimationFrame(this._frame);
      if (t - this._last < 33) return;
      this._last = t;
      const ctx = this._ctx, w = this._w, h = this._h;
      if (!w || !h || !this._walkers) return;
      const color = this._resolveColor(), peak = this._num('opacity', 0.8);
      const speedMul = this._num('speed', 1), pull = this._cursorEnabled ? this._num('pull', 0.6) : 0;
      const now = this._reduced ? 0 : t, gap = this._gap;
      const cols = this._cols, rows = this._rows, baseOp = 0.09, A = this._alpha;
      if (this._pointer) { this._pointerCell = { c: this._pointer.x / gap, r: this._pointer.y / gap }; } else { this._pointerCell = null; }
      A.fill(baseOp);
      if (!this._reduced) {
        for (const wk of this._walkers) {
          this._advance(wk, now, speedMul, pull);
          const halfW = (wk.bw - 1) / 2, halfH = (wk.bh - 1) / 2;
          this._stamp(wk.cx + halfW, wk.cy + halfH, halfW, halfH, this._envelope(wk, now) * peak * (wk.brightJitter || 1), wk.shape);
        }
        this._advanceCursor(now);
      }
      const cw = this._cursorWalker;
      if (cw && cw.active) { const cwHalf = (cw.block - 1) / 2; this._stamp(cw.cx + cwHalf, cw.cy + cwHalf, cwHalf, cwHalf, Math.min(1, peak * 1.2), 'square'); }
      if (this._pointerCell) {
        const pc = this._pointerCell.c, pr = this._pointerCell.r, R = 6.5;
        const c0 = Math.max(0, Math.floor(pc - R)), c1 = Math.min(cols - 1, Math.ceil(pc + R));
        const r0 = Math.max(0, Math.floor(pr - R)), r1 = Math.min(rows - 1, Math.ceil(pr + R));
        for (let r = r0; r <= r1; r++) { for (let c = c0; c <= c1; c++) { const dist = Math.hypot(c - pc, r - pr); if (dist <= R) { const v = Math.min(1, peak * 1.05) * (1 - dist / R) * (1 - dist / R); const idx = r * cols + c; if (v > A[idx]) A[idx] = v; } } }
      }
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = color; ctx.lineCap = 'round'; ctx.lineWidth = 1.4;
      const arm = 2.8;
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const op = A[r * cols + c];
          if (op <= 0.012) continue;
          ctx.globalAlpha = Math.min(1, op);
          const x = c * gap, y = r * gap;
          ctx.beginPath(); ctx.moveTo(x - arm, y); ctx.lineTo(x + arm, y); ctx.moveTo(x, y - arm); ctx.lineTo(x, y + arm); ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    }
  }
  if (!customElements.get('uwitz-flicker-field')) customElements.define('uwitz-flicker-field', UwitzFlickerField);
})();
