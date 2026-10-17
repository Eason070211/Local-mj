/* =====================================================================
 * tiles.js — Mahjong tile renderer.
 *
 * Design rule: EVERY visual part of a tile (bevel/edge, face frame,
 * face fill, symbols) is derived from the SAME base rect passed into
 * drawTile().  The frame and its fill can never drift apart because
 * they are computed from one geometry — this is the "一体" (one-piece)
 * requirement.
 * ===================================================================== */
'use strict';

const Tiles = (function () {
  const T = {};
  T.TILE_TYPES = 34;
  T.HONOR = 27;
  T.SUIT_NAMES = ['万', '筒', '条'];
  T.HONOR_NAMES = ['东', '南', '西', '北', '中', '发', '白'];
  T.NUM = ['一', '二', '三', '四', '五', '六', '七', '八', '九'];
  T.NUM_CN = ['壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖'];

  T.suit = (t) => t < 9 ? 0 : (t < 18 ? 1 : (t < 27 ? 2 : 3));
  T.value = (t) => t < 27 ? t % 9 + 1 : -1;

  // ---------------- geometry ----------------
  // One tile occupies w x h.  Aspect: h = w * 1.42
  const W = 0;  // placeholders — real sizes set per call

  // pin-dot layouts (fractions of face w/h), classic patterns
  const DOT_LAYOUTS = {
    1: [[0.50, 0.50]],
    2: [[0.50, 0.30], [0.50, 0.70]],
    3: [[0.50, 0.25], [0.50, 0.50], [0.50, 0.75]],
    4: [[0.32, 0.26], [0.68, 0.26], [0.32, 0.74], [0.68, 0.74]],
    5: [[0.32, 0.26], [0.68, 0.26], [0.32, 0.74], [0.68, 0.74], [0.50, 0.50]],
    6: [[0.30, 0.26], [0.70, 0.26], [0.30, 0.50], [0.70, 0.50], [0.30, 0.74], [0.70, 0.74]],
    7: [[0.30, 0.26], [0.70, 0.26], [0.30, 0.50], [0.70, 0.50], [0.30, 0.74], [0.70, 0.74], [0.50, 0.60]],
    8: [[0.30, 0.26], [0.70, 0.26], [0.30, 0.46], [0.70, 0.46], [0.30, 0.66], [0.70, 0.66], [0.30, 0.86], [0.70, 0.86]],
    9: [[0.32, 0.26], [0.68, 0.26], [0.32, 0.50], [0.68, 0.50], [0.32, 0.74], [0.68, 0.74],
        [0.50, 0.26], [0.50, 0.50], [0.50, 0.74]],
  };

  // bamboo layouts: exact (fx, fy) positions per rank (1 = bird)
  const BAM_LAYOUTS = {
    2: [[0.5, 0.30], [0.5, 0.72]],
    3: [[0.36, 0.74], [0.5, 0.52], [0.64, 0.30]],
    4: [[0.36, 0.32], [0.64, 0.32], [0.36, 0.72], [0.64, 0.72]],
    5: [[0.34, 0.28], [0.66, 0.28], [0.5, 0.5], [0.34, 0.72], [0.66, 0.72]],
    6: [[0.36, 0.24], [0.64, 0.24], [0.36, 0.5], [0.64, 0.5],
        [0.36, 0.76], [0.64, 0.76]],
    7: [[0.5, 0.18], [0.34, 0.46], [0.66, 0.46], [0.34, 0.66], [0.66, 0.66],
        [0.34, 0.86], [0.66, 0.86]],
    8: [[0.33, 0.20], [0.5, 0.20], [0.67, 0.20], [0.33, 0.5], [0.67, 0.5],
        [0.33, 0.80], [0.5, 0.80], [0.67, 0.80]],
    9: [[0.33, 0.18], [0.5, 0.18], [0.67, 0.18],
        [0.33, 0.5], [0.5, 0.5], [0.67, 0.5],
        [0.33, 0.82], [0.5, 0.82], [0.67, 0.82]],
  };

  function rr(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function dot(ctx, cx, cy, r, color) {
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.25)';
    ctx.lineWidth = Math.max(0.5, r * 0.12);
    ctx.stroke();
  }

  function bamboo(ctx, cx, cy, r, color) {
    // straight vertical bamboo: equal-width bar, dark-green outline + caps,
    // one red node band
    const w = r * 1.1, h = r * 3.4;
    ctx.fillStyle = color;
    ctx.fillRect(cx - w, cy - h / 2, w * 2, h);
    ctx.strokeStyle = '#0e4d1c';
    ctx.lineWidth = Math.max(0.6, r * 0.18);
    ctx.strokeRect(cx - w, cy - h / 2, w * 2, h);
    // soft highlight
    ctx.fillStyle = 'rgba(255,255,255,0.30)';
    ctx.fillRect(cx - w * 0.75, cy - h * 0.35, w * 0.35, h * 0.7);
    // red node
    ctx.fillStyle = '#c12b1d';
    ctx.fillRect(cx - w, cy - h * 0.055, w * 2, h * 0.11);
    // dark green caps top & bottom
    ctx.fillStyle = '#0e4d1c';
    ctx.fillRect(cx - w, cy - h / 2, w * 2, h * 0.07);
    ctx.fillRect(cx - w, cy + h / 2 - h * 0.07, w * 2, h * 0.07);
  }

  function bird(ctx, cx, cy, r) {
    // stylized 一索 bird: body + head + wing
    ctx.save();
    ctx.translate(cx, cy);
    ctx.fillStyle = '#2f7d32';
    ctx.beginPath();
    ctx.ellipse(0, 2.5, r * 1.15, r * 0.85, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(r * 0.9, -r * 0.7, r * 0.62, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#d9b64a'; // beak
    ctx.beginPath();
    ctx.moveTo(r * 1.45, -r * 0.8);
    ctx.lineTo(r * 1.85, -r * 0.6);
    ctx.lineTo(r * 1.4, -r * 0.5);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#1c5a1f'; // wing
    ctx.beginPath();
    ctx.ellipse(-r * 0.15, 1.5, r * 0.85, r * 0.5, 0.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function drawFaceSymbols(ctx, t, fw, fh) {
    const suit = T.suit(t);
    const v = T.value(t);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    if (suit === 3) { // honors
      const name = T.HONOR_NAMES[t - T.HONOR];
      let color = '#1d2430', glow = false;
      if (name === '中') { color = '#c12b1d'; glow = true; }
      if (name === '发') { color = '#1e7a34'; glow = true; }
      if (name === '白') {
        ctx.strokeStyle = '#2a5b8f';
        ctx.lineWidth = fw * 0.05;
        rr(ctx, fw * 0.22, fh * 0.20, fw * 0.56, fh * 0.60, fw * 0.08);
        ctx.stroke();
        return;
      }
      ctx.font = `900 ${fh * 0.52}px "PingFang SC","Microsoft YaHei",sans-serif`;
      if (glow) {
        ctx.shadowColor = color; ctx.shadowBlur = fh * 0.12;
      }
      ctx.fillStyle = color;
      ctx.fillText(name, fw * 0.5, fh * 0.52);
      ctx.shadowBlur = 0;
      return;
    }

    if (suit === 0) { // man — big number + 萬 mark
      const big = v === 5 ? '#c12b1d' : '#1d2430';
      ctx.font = `900 ${fh * 0.42}px "PingFang SC","Microsoft YaHei",serif`;
      ctx.fillStyle = big;
      ctx.shadowColor = 'rgba(0,0,0,0.18)'; ctx.shadowBlur = fh * 0.04;
      ctx.fillText(T.NUM[v - 1], fw * 0.5, fh * 0.40);
      ctx.shadowBlur = 0;
      ctx.font = `700 ${fh * 0.20}px "PingFang SC","Microsoft YaHei",serif`;
      ctx.fillStyle = '#b9301f';
      ctx.fillText('萬', fw * 0.5, fh * 0.74);
      return;
    }
    if (suit === 1) { // pin
      const color = v === 5 ? '#c12b1d' : '#1d6f9c';
      const pts = DOT_LAYOUTS[v];
      for (const [fx, fy] of pts) {
        dot(ctx, fw * fx, fh * fy, fw * 0.075, color);
      }
      return;
    }
    // sou
    if (v === 1) { bird(ctx, fw * 0.5, fh * 0.52, fw * 0.16); return; }
    for (const [fx, fy] of BAM_LAYOUTS[v]) {
      bamboo(ctx, fw * fx, fh * fy, fw * 0.068, '#1e7a34');
    }
  }

  // ------------------------------------------------------------------
  // drawTile — the one-piece tile.
  // ctx, tile(id), x, y (center), w, h, opts {rot, scale, faceUp,
  //   hover(0..1), dim(0..1), sel(0..1), glow(0..1)}
  // ------------------------------------------------------------------
  T.drawTile = function (ctx, tile, x, y, w, h, opts) {
    opts = opts || {};
    const rot = opts.rot || 0;
    const scale = opts.scale || 1;
    const faceUp = opts.faceUp !== false;
    const hover = opts.hover || 0;
    const dim = opts.dim || 0;
    const sel = opts.sel || 0;
    const glow = opts.glow || 0;

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rot);
    ctx.scale(scale, scale);
    ctx.translate(-w / 2, -h / 2);

    // drop shadow
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.55)';
    ctx.shadowBlur = 8 + hover * 14;
    ctx.shadowOffsetY = 3 + hover * 5;
    rr(ctx, 1, 2, w - 2, h - 2, w * 0.09);
    ctx.fillStyle = 'rgba(0,0,0,0.9)';
    ctx.fill();
    ctx.restore();

    // halo glow (last discard / winning tile)
    if (glow > 0) {
      const g = ctx.createRadialGradient(w / 2, h / 2, h * 0.1, w / 2, h / 2, h * 0.85);
      g.addColorStop(0, `rgba(255,214,120,${0.34 * glow})`);
      g.addColorStop(1, 'rgba(255,214,120,0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(w / 2, h / 2, h * 0.9, 0, Math.PI * 2);
      ctx.fill();
    }

    if (!faceUp) {
      // ---- tile back ----
      const bg = ctx.createLinearGradient(0, 0, 0, h);
      bg.addColorStop(0, '#1b4a5a');
      bg.addColorStop(1, '#12303c');
      rr(ctx, 0, 0, w, h, w * 0.09);
      ctx.fillStyle = bg;
      ctx.fill();
      // bevel
      ctx.strokeStyle = 'rgba(255,255,255,0.16)';
      ctx.lineWidth = Math.max(1, w * 0.03);
      rr(ctx, 0, 0, w, h, w * 0.09);
      ctx.stroke();
      // inner pattern frame
      ctx.strokeStyle = 'rgba(212,175,55,0.5)';
      ctx.lineWidth = Math.max(1, w * 0.025);
      rr(ctx, w * 0.12, h * 0.09, w * 0.76, h * 0.82, w * 0.05);
      ctx.stroke();
      // diagonal weave
      ctx.strokeStyle = 'rgba(212,175,55,0.22)';
      ctx.lineWidth = Math.max(1, w * 0.02);
      for (let i = -3; i <= 3; i++) {
        ctx.beginPath();
        ctx.moveTo(w * 0.12 + i * w * 0.18, h * 0.91);
        ctx.lineTo(w * 0.12 + i * w * 0.18 + w * 0.76, h * 0.09);
        ctx.stroke();
      }
      ctx.restore();
      return;
    }

    // ---- face up ----
    // 1) side thickness (bevel): draw the edge first, slightly offset
    const bevel = Math.max(1.5, h * 0.045);
    const edge = ctx.createLinearGradient(0, 0, w, h);
    edge.addColorStop(0, '#d8d0ba');
    edge.addColorStop(1, '#a89f88');
    rr(ctx, 0, bevel, w, h - bevel, w * 0.09);
    ctx.fillStyle = edge;
    ctx.fill();

    // 2) face body — same rect inset by bevel, vertical ivory gradient
    const face = ctx.createLinearGradient(0, bevel, 0, h);
    face.addColorStop(0, '#faf6e8');
    face.addColorStop(0.5, '#f3ecd8');
    face.addColorStop(1, '#e4dabf');
    rr(ctx, 0, bevel * 0.35, w, h - bevel * 0.35 - bevel * 0.3, w * 0.09);
    ctx.fillStyle = face;
    ctx.fill();

    // 3) the frame — one stroke around the SAME face rect
    ctx.strokeStyle = 'rgba(90,75,45,0.55)';
    ctx.lineWidth = Math.max(0.8, w * 0.022);
    rr(ctx, w * 0.045, bevel + h * 0.05, w * 0.91, h * 0.88, w * 0.065);
    ctx.stroke();

    // 4) symbols — centered in the same frame rect
    const fw = w * 0.91, fh = h * 0.88;
    ctx.save();
    ctx.translate(w * 0.045, bevel + h * 0.05);
    drawFaceSymbols(ctx, tile, fw, fh);
    ctx.restore();

    // 5) gloss highlight (top sheen)
    const gloss = ctx.createLinearGradient(0, 0, 0, h * 0.5);
    gloss.addColorStop(0, 'rgba(255,255,255,0.28)');
    gloss.addColorStop(1, 'rgba(255,255,255,0)');
    rr(ctx, 0, bevel * 0.35, w, h * 0.42, w * 0.09);
    ctx.fillStyle = gloss;
    ctx.fill();

    // hover lift glow
    if (hover > 0) {
      ctx.shadowColor = 'rgba(212,175,55,0.8)';
      ctx.shadowBlur = 18 * hover;
      rr(ctx, 0, bevel * 0.35, w, h - bevel, w * 0.09);
      ctx.strokeStyle = `rgba(212,175,55,${0.55 * hover})`;
      ctx.lineWidth = 2.4 * hover;
      ctx.stroke();
    }
    // selection ring
    if (sel > 0) {
      ctx.shadowColor = 'rgba(212,175,55,0.9)';
      ctx.shadowBlur = 22 * sel;
      rr(ctx, -2, -2 + bevel * 0.35, w + 4, h - bevel + 4, w * 0.1);
      ctx.strokeStyle = `rgba(240,208,120,${0.9 * sel})`;
      ctx.lineWidth = 3 * sel;
      ctx.stroke();
    }
    // dim (unusable / absence suit)
    if (dim > 0) {
      rr(ctx, 0, bevel * 0.35, w, h - bevel, w * 0.09);
      ctx.fillStyle = `rgba(30,20,10,${0.42 * dim})`;
      ctx.fill();
    }
    ctx.restore();
  };

  T.tileName = (t) => {
    if (t >= 27) return T.HONOR_NAMES[t - 27];
    return T.NUM[t % 9] + T.SUIT_NAMES[Math.floor(t / 9)];
  };
  T.tileShort = (t) => {
    if (t >= 27) return T.HONOR_NAMES[t - 27];
    return (t % 9 + 1) + T.SUIT_NAMES[Math.floor(t / 9)];
  };

  // Debug mode (?debug=1): draw ASCII tile labels so screen capture / OCR
  // can verify tile identity unambiguously.
  const DEBUG = (typeof location !== 'undefined' &&
    /[?&]debug=1/.test(location.search)) ||
    (typeof process !== 'undefined' && process.env.MJ_DEBUG === '1');
  T.DEBUG = DEBUG;
  if (DEBUG) {
    const old = drawFaceSymbols;
    drawFaceSymbols = function (ctx, t, fw, fh) {
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      let label;
      let color = '#1d2430';
      if (t >= 27) {
        label = { 0: 'E', 1: 'S', 2: 'W', 3: 'N', 4: 'Z', 5: 'F', 6: 'B' }[t - 27];
        if (t - 27 === 4) color = '#c12b1d';
        if (t - 27 === 5) color = '#1e7a34';
      } else {
        const v = t % 9 + 1;
        const s = T.SUIT_NAMES[Math.floor(t / 9)];
        label = v + s;
        if (s === '万' && v === 5) color = '#c12b1d';
        if (s === '筒' && v === 5) color = '#c12b1d';
      }
      ctx.font = `900 ${fh * 0.46}px "Consolas",monospace`;
      ctx.fillStyle = color;
      ctx.fillText(label, fw * 0.5, fh * 0.5);
    };
  }

  return T;
})();
