/* =====================================================================
 * render.js — canvas scene: table, lighting, wall, hands, discards,
 * melds, turn/dealer markers, and the tween/smoothing engine that makes
 * every tile glide instead of teleport.
 * ===================================================================== */
'use strict';

const Scene = (function () {
  const S = {};
  const TILES = Tiles;

  // ---- layout metrics (recomputed on resize) ----
  let CW = 0, CH = 0, DPR = 1;
  let tw = 46;            // tile width (base)
  let th = 0;             // tile height
  let table = { x: 0, y: 0, w: 0, h: 0 };   // felt area (center rect)

  const SEAT_ANG = [0.5, 1.0, 0.0, 0.5];    // seat -> hand anchor direction
  const SEAT_ANCHOR = [];                   // {x, y} per seat
  const DISC_ANCHOR = [];

  // ---- entities ----
  const ents = new Map();     // id -> {tile, x,y,rot,scale,faceUp,alpha, tx,ty,trot, mode, ...}
  let entSeq = 0;
  const flights = [];         // {id, sx,sy,tx,ty,t0,dur,lift,rot0,rot1}

  // ---- scene state ----
  let state = null;          // last synced snapshot
  let hoverIdx = -1;         // human hand hovered index
  let time = 0;
  let winningSet = new Set();
  let perfMode = false;      // 流畅模式: skip glow/lighting effects
  let dirty = true;          // needs redraw (state changed)
  let animating = false;     // flights or smoothing active
  let mySeat = 0;            // my absolute seat; bottom of the screen is
                             // always ME regardless of absolute seat
  let handDisplay = null;    // optional custom order of MY hand tiles
  let drag = null;           // {tile, x, y} ghost while dragging a hand tile

  function localSeat(s) {
    return (s - mySeat + 4) % 4;
  }

  // ================================================================ layout
  function resize() {
    DPR = window.devicePixelRatio || 1;
    const c = document.getElementById('table');
    CW = window.innerWidth;
    CH = window.innerHeight;
    c.width = CW * DPR;
    c.height = CH * DPR;
    c.style.width = CW + 'px';
    c.style.height = CH + 'px';

    tw = Math.max(34, Math.min(52, CW / 26));
    th = tw * 1.42;

    table = { x: CW * 0.06, y: CH * 0.10, w: CW * 0.88, h: CH * 0.80 };
    SEAT_ANCHOR.length = 0;
    DISC_ANCHOR.length = 0;
    for (let s = 0; s < 4; s++) {
      const p = seatPoint(s);
      SEAT_ANCHOR.push(p);
      const dir = { x: table.x + table.w / 2 - p.x, y: table.y + table.h / 2 - p.y };
      const dl = Math.hypot(dir.x, dir.y) || 1;
      DISC_ANCHOR.push({ x: p.x + (dir.x / dl) * tw * 2.2, y: p.y + (dir.y / dl) * tw * 2.2 });
    }
  }

  function seatPoint(seat) {
    // 0 bottom, 1 right, 2 top, 3 left
    const cx = table.x + table.w / 2, cy = table.y + table.h / 2;
    if (seat === 0) return { x: cx, y: table.y + table.h - 16 };
    if (seat === 1) return { x: table.x + table.w - 14, y: cy };
    if (seat === 2) return { x: cx, y: table.y + 14 };
    return { x: table.x + 14, y: cy };
  }

  function handSlot(seat, idx, count) {
    const p = SEAT_ANCHOR[seat];
    if (seat === 0) {
      const totalW = count * tw + (count - 1) * 2.5;
      const x0 = p.x - totalW / 2 + tw / 2;
      const y = p.y - th / 2 - 8;
      return { x: x0 + idx * (tw + 2.5), y };
    }
    // opponents: backs along the edge
    const gap = tw * 0.98;
    const totalW = count * gap;
    if (seat === 2) {
      return { x: p.x - totalW / 2 + gap / 2 + idx * gap, y: p.y - th / 2 };
    }
    if (seat === 1) {
      return { x: p.x - th / 2, y: p.y - totalW / 2 + gap / 2 + idx * gap, rot: Math.PI / 2 };
    }
    return { x: p.x + th / 2, y: p.y - totalW / 2 + gap / 2 + idx * gap, rot: -Math.PI / 2 };
  }

  function discardSlot(seat, idx) {
    // each player's discards are stacked inside the central box, against
    // their own edge: south along the bottom, north along the top, east/west
    // along the left/right edges.  7 tiles per row, rows grow inward.
    const bx = discardBox();
    const col = idx % 7, row = Math.floor(idx / 7);
    const spread = (col - 3) * tw * 0.86;
    if (seat === 0) {
      return { x: bx.x + bx.w / 2 + spread,
               y: bx.y + bx.h - th * 0.55 - row * th * 1.0, rot: 0 };
    }
    if (seat === 2) {
      return { x: bx.x + bx.w / 2 + spread,
               y: bx.y + th * 0.55 + row * th * 1.0, rot: Math.PI };
    }
    if (seat === 1) {
      return { x: bx.x + bx.w - tw * 0.55 - row * tw * 1.0,
               y: bx.y + bx.h / 2 + spread, rot: Math.PI / 2 };
    }
    return { x: bx.x + tw * 0.55 + row * tw * 1.0,
             y: bx.y + bx.h / 2 + spread, rot: -Math.PI / 2 };
  }

  function meldSlot(seat, start, tIdx, blockSize) {
    // start = block offset from the seat anchor (centered total); each block
    // lays its tiles along one axis.
    const p = SEAT_ANCHOR[seat];
    const cellW = tw * 0.92;
    const off = start + tIdx * cellW + cellW / 2;
    if (seat === 0) return { x: p.x + off, y: p.y - th * 2.05 };
    if (seat === 2) return { x: p.x + off, y: p.y + th * 0.85, rot: Math.PI };
    if (seat === 1) return { x: p.x - th * 0.9, y: p.y + off, rot: Math.PI / 2 };
    return { x: p.x + th * 0.9, y: p.y + off, rot: -Math.PI / 2 };
  }

  function discardBox() {
    // center discard area (the framed square in the middle of the table)
    const cx = table.x + table.w / 2, cy = table.y + table.h / 2;
    const bw = table.w * 0.52, bh = table.h * 0.46;
    return { x: cx - bw / 2, y: cy - bh / 2, w: bw, h: bh };
  }

  function wallGeom() {
    // the remaining wall wraps the discard box on its top / left / right
    // edges (real mahjong wall layout) instead of one bar across the middle.
    const n = Math.max(1, Math.round((state ? state.wall_remaining : 0) / 2));
    const bx = discardBox();
    const sw = tw * 0.62, sh = tw * 0.86;
    const gap = tw * 0.66;
    const nTop = Math.ceil(n * 0.42);
    const nLeft = Math.floor((n - nTop) / 2);
    const nRight = n - nTop - nLeft;
    const segs = [];
    if (nTop > 0) {
      const w = nTop * gap;
      segs.push({ x0: bx.x + bx.w / 2 - w / 2, y0: bx.y - sh - tw * 0.5,
                  n: nTop, dx: gap, dy: 0, rot: 0 });
    }
    if (nLeft > 0) {
      const h = nLeft * gap;
      segs.push({ x0: bx.x - sw - tw * 0.5, y0: bx.y + bx.h / 2 - h / 2,
                  n: nLeft, dx: 0, dy: gap, rot: Math.PI / 2 });
    }
    if (nRight > 0) {
      const h = nRight * gap;
      segs.push({ x0: bx.x + bx.w + sw * 0.5, y0: bx.y + bx.h / 2 - h / 2,
                  n: nRight, dx: 0, dy: gap, rot: Math.PI / 2 });
    }
    return { segs, sw, sh,
             flyFrom: { x: bx.x + bx.w / 2, y: bx.y - sh - tw * 0.5 } };
  }

  // ============================================================== entities
  function spawn(tile, x, y, faceUp, rot) {
    const id = 'e' + (entSeq++);
    const e = {
      id, tile, faceUp: faceUp !== false,
      x, y, rot: rot || 0, scale: 1, alpha: 1,
      tx: x, ty: y, trot: rot || 0, tscale: 1,
      lift: 0,
    };
    ents.set(id, e);
    return e;
  }

  function setTarget(id, tx, ty, trot, tscale) {
    const e = ents.get(id);
    if (!e) return;
    e.tx = tx; e.ty = ty;
    if (trot !== undefined) e.trot = trot;
    if (tscale !== undefined) e.tscale = tscale;
    dirty = true;
  }

  function remove(id) { ents.delete(id); dirty = true; }
  function clearAll() { ents.clear(); flights.length = 0; dirty = true; }

  function fly(id, sx, sy, tx, ty, dur, lift, rot0, rot1) {
    const e = ents.get(id);
    if (!e) return;
    dirty = true;
    e.x = sx; e.y = sy; e.rot = rot0 !== undefined ? rot0 : e.rot;
    flights.push({ id, sx, sy, tx, ty, t0: time, dur: dur || 0.45,
                   lift: lift === undefined ? 26 : lift,
                   rot0: e.rot, rot1: rot1 !== undefined ? rot1 : e.rot });
  }

  function update(dt) {
    time += dt;
    let moved = false;
    // flights
    for (let i = flights.length - 1; i >= 0; i--) {
      const f = flights[i];
      const e = ents.get(f.id);
      if (!e) { flights.splice(i, 1); continue; }
      const p = Math.min(1, (time - f.t0) / f.dur);
      const t = easeOutCubic(p);
      e.x = f.sx + (f.tx - f.sx) * t;
      e.y = f.sy + (f.ty - f.sy) * t - Math.sin(p * Math.PI) * f.lift;
      e.rot = f.rot0 + (f.rot1 - f.rot0) * t;
      moved = true;
      if (p >= 1) {
        e.x = f.tx; e.y = f.ty; e.rot = f.rot1;
        flights.splice(i, 1);
      }
    }
    // smoothing toward targets — always run (cheap math, no Canvas calls)
    const k = Math.min(1, dt * 9);
    const EPS = 0.01;
    for (const e of ents.values()) {
      if (Math.abs(e.tx - e.x) > EPS || Math.abs(e.ty - e.y) > EPS
          || Math.abs(e.trot - e.rot) > 0.001
          || Math.abs(e.tscale - e.scale) > 0.001) {
        e.x += (e.tx - e.x) * k;
        e.y += (e.ty - e.y) * k;
        e.rot += (e.trot - e.rot) * k;
        e.scale += (e.tscale - e.scale) * k;
        moved = true;
      } else {
        e.x = e.tx; e.y = e.ty;
        e.rot = e.trot; e.scale = e.tscale;
      }
    }
    animating = moved;
    return moved || dirty;
  }

  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  // ============================================================ reconciliation
  // Called by main.js on every state sync.  state = server snapshot.
  function sync(newState) {
    state = newState;
    if (!state) return;
    dirty = true;
    reconcileHumanHand();
    reconcileOpponents();
    reconcileDiscards();
    reconcileMelds();
  }

  function reconcileHumanHand() {
    const hand = handDisplay || state.hand || [];
    const used = new Set();
    const slots = [];
    for (let i = 0; i < hand.length; i++) {
      const p = handSlot(0, i, hand.length);
      slots.push(p);
    }
    // assign existing entities (tile match, unused)
    const assignments = new Map(); // entityId -> slotIdx
    for (let i = 0; i < hand.length; i++) {
      const tile = hand[i];
      let best = null;
      for (const [id, e] of ents) {
        if (e.tile === tile && !used.has(id) && e.handSlot !== undefined) {
          best = id; break;
        }
      }
      if (best === null) {
        for (const [id, e] of ents) {
          if (e.tile === tile && !used.has(id) && !e.faceUp) {
            best = id; break;
          }
        }
      }
      if (best === null) {
        // spawn fresh (fly from the wall)
        const wg = wallGeom();
        const e = spawn(tile, wg.flyFrom.x, wg.flyFrom.y, true);
        e.handSlot = i;
        fly(e.id, e.x, e.y, slots[i].x, slots[i].y, 0.4, 30, 0, 0);
        setTarget(e.id, slots[i].x, slots[i].y, 0, 1);
        used.add(e.id);
        assignments.set(i, e.id);
      } else {
        used.add(best);
        const e = ents.get(best);
        e.faceUp = true;
        e.handSlot = i;
        setTarget(best, slots[i].x, slots[i].y, 0, hoverIdx === i ? 1.06 : 1);
        assignments.set(i, best);
      }
    }
    // remove entities that were in hand slots but no longer needed
    const dead = [];
    for (const [id, e] of ents) {
      if (e.handSlot !== undefined && !used.has(id)) {
        dead.push(id);
      }
    }
    for (const id of dead) {
      const e = ents.get(id);
      if (!e) continue;
      if (state.last_discard && state.last_discard.seat === 0) {
        // convert this hand tile into the discard entity — one continuous motion
        const discs = state.players[0].discards || [];
        const p = discardSlot(0, Math.max(0, discs.length - 1));
        fly(id, e.x, e.y, p.x, p.y, 0.42, 22, 0, p.rot);
        setTarget(id, p.x, p.y, p.rot, 1);
        e.discSeat = 0;
        e.discIdx = Math.max(0, discs.length - 1);
        delete e.handSlot;
      } else {
        ents.delete(id);
      }
    }
    S.hoverIdx = hoverIdx;
  }

  function reconcileOpponents() {
    const revealed = state.phase === 'round_end';
    const others = [0, 1, 2, 3].filter((s) => s !== mySeat);
    for (const s of others) {
      const ls = localSeat(s);
      const count = state.players[s].hand_count;
      const ours = [];
      for (const [id, e] of ents) {
        if (e.oppSeat === s) ours.push(id);
      }
      const want = Math.max(1, count);
      while (ours.length < want) {
        const p = handSlot(ls, ours.length, count);
        const e = spawn(-1, p.x, p.y + 30, false);
        e.oppSeat = s; e.tscale = 0.96;
        ours.push(e.id);
      }
      while (ours.length > want) {
        ents.delete(ours.pop());
      }
      ours.forEach((id, idx) => {
        const p = handSlot(ls, idx, count);
        const e = ents.get(id);
        // Reveal if round is over, OR if the server sent hand data
        // (Sichuan 血战: winner's hand is exposed immediately)
        const playerHand = state.players[s].hand;
        if (revealed || (playerHand && playerHand.length > 0)) {
          const hand = playerHand || [];
          const t = hand[idx];
          if (t !== undefined && e.tile !== t) e.faceUp = true;
          e.tile = (t !== undefined) ? t : -1;
        } else {
          // New round: reset to face-down
          e.faceUp = false;
          e.tile = -1;
        }
        setTarget(id, p.x, p.y, p.rot !== undefined ? p.rot : 0, 1);
      });
    }
  }

  function reconcileDiscards() {
    for (let s = 0; s < 4; s++) {
      const ls = localSeat(s);
      const discs = state.players[s].discards || [];
      const ours = [];
      for (const [id, e] of ents) {
        if (e.discSeat === s) ours.push(id);
      }
      while (ours.length < discs.length) {
        const idx = ours.length;
        const p = discardSlot(ls, idx);
        const e = spawn(discs[idx], p.x, p.y - 20, true, p.rot);
        e.discSeat = s; e.discIdx = idx;
        fly(e.id, DISC_ANCHOR[ls].x, DISC_ANCHOR[ls].y, p.x, p.y, 0.42, 18, 0, p.rot);
        setTarget(e.id, p.x, p.y, p.rot, 1);
        ours.push(e.id);
      }
      while (ours.length > discs.length) {
        ents.delete(ours.pop());
      }
      ours.forEach((id, idx) => {
        const p = discardSlot(ls, idx);
        const e = ents.get(id);
        e.discIdx = idx;
        e.tile = discs[idx];
        setTarget(id, p.x, p.y, p.rot, 1);
      });
    }
  }

  function reconcileMelds() {
    const cellW = tw * 0.92, blockGap = tw * 0.25;
    for (let s = 0; s < 4; s++) {
      const ls = localSeat(s);
      const melds = state.players[s].melds || [];
      const ours = [];
      for (const [id, e] of ents) {
        if (e.meldSeat === s) ours.push(id);
      }
      const want = melds.reduce((a, m) => a + m.tiles.length, 0);
      while (ours.length < want) {
        const e = spawn(-1, SEAT_ANCHOR[ls].x, SEAT_ANCHOR[ls].y, false);
        e.meldSeat = s; e.meldIdx = ours.length;
        ours.push(e.id);
      }
      while (ours.length > want) {
        ents.delete(ours.pop());
      }
      // lay each meld block end-to-end; blocks never overlap
      const blockW = melds.map((m) => m.tiles.length * cellW);
      const totalW = blockW.reduce((a, w) => a + w, 0) +
                     blockGap * Math.max(0, melds.length - 1);
      let start = -totalW / 2;
      let k = 0;
      melds.forEach((m, mi) => {
        for (let ti = 0; ti < m.tiles.length; ti++) {
          const p = meldSlot(ls, start, ti, m.tiles.length);
          const id = ours[k++];
          const e = ents.get(id);
          const was = e.tile !== m.tiles[ti] || !e.faceUp;
          e.tile = m.tiles[ti];
          // Reveal concealed gang if: it's my hand, or the player
          // has already won (Sichuan 血战: winner's hand is fully exposed)
          const isWinner = state.players[s].hand && state.players[s].hand.length > 0;
          e.faceUp = m.kind !== 'gang_an' || s === mySeat || isWinner;
          e.tscale = 0.82;
          if (was) {
            fly(id, e.x, e.y, p.x, p.y, 0.4, 16, e.rot, p.rot);
          }
          setTarget(id, p.x, p.y, p.rot, 0.82);
        }
        start += blockW[mi] + blockGap;
      });
    }
  }

  // ================================================================ drawing
  function draw(ctx) {
    dirty = false;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, CW, CH);
    if (!state) return;

    drawTable(ctx);
    drawWall(ctx);
    if (!perfMode) drawDiscardGlow(ctx);
    drawEntities(ctx);
    drawMarkers(ctx);
    if (drag) {
      Tiles.drawTile(ctx, drag.tile, drag.x, drag.y, tw, th,
                     { scale: 1.08, hover: 1 });
    }
    if (!perfMode) drawLighting(ctx);
  }

  function drawTable(ctx) {
    // wood background
    const g = ctx.createLinearGradient(0, 0, CW, CH);
    g.addColorStop(0, '#2a1a10');
    g.addColorStop(0.5, '#22150c');
    g.addColorStop(1, '#180e08');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, CW, CH);

    // felt
    const f = ctx.createRadialGradient(
      table.x + table.w / 2, table.y + table.h / 2, 40,
      table.x + table.w / 2, table.y + table.h / 2, table.w * 0.62);
    f.addColorStop(0, '#1b6a47');
    f.addColorStop(0.7, '#14593c');
    f.addColorStop(1, '#0e3f2b');
    ctx.fillStyle = f;
    roundRect(ctx, table.x, table.y, table.w, table.h, 26);
    ctx.fill();
    ctx.strokeStyle = 'rgba(212,175,55,0.28)';
    ctx.lineWidth = 2;
    roundRect(ctx, table.x + 5, table.y + 5, table.w - 10, table.h - 10, 22);
    ctx.stroke();

    // framed central discard box (the square in the middle of the table)
    const bx = discardBox();
    ctx.fillStyle = 'rgba(0,0,0,0.18)';
    roundRect(ctx, bx.x, bx.y, bx.w, bx.h, 10);
    ctx.fill();
    ctx.strokeStyle = 'rgba(212,175,55,0.45)';
    ctx.lineWidth = 2;
    roundRect(ctx, bx.x + 1.5, bx.y + 1.5, bx.w - 3, bx.h - 3, 9);
    ctx.stroke();
  }

  function drawWall(ctx) {
    const wg = wallGeom();
    for (const seg of wg.segs) {
      for (let i = 0; i < seg.n; i++) {
        const x = seg.x0 + seg.dx * i;
        const y = seg.y0 + seg.dy * i;
        for (let r = 0; r < 2; r++) {
          ctx.save();
          ctx.translate(x + wg.sw / 2, y + r * (wg.sh * 0.96) + wg.sh / 2);
          ctx.rotate(seg.rot + (r === 0 ? 0 : Math.PI));
          const bg = ctx.createLinearGradient(0, -wg.sh / 2, 0, wg.sh / 2);
          bg.addColorStop(0, '#1b4a5a');
          bg.addColorStop(1, '#12303c');
          roundRect(ctx, -wg.sw / 2, -wg.sh / 2, wg.sw, wg.sh, 3);
          ctx.fillStyle = bg;
          ctx.fill();
          ctx.strokeStyle = 'rgba(212,175,55,0.35)';
          ctx.lineWidth = 1;
          roundRect(ctx, -wg.sw / 2 + 2, -wg.sh / 2 + 2, wg.sw - 4, wg.sh - 4, 2);
          ctx.stroke();
          ctx.restore();
        }
      }
    }
  }

  function drawDiscardGlow(ctx) {
    if (!state.last_discard) return;
    const s = state.last_discard.seat;
    const discs = state.players[s].discards || [];
    if (!discs.length) return;
    const p = discardSlot(s, discs.length - 1);
    const g = ctx.createRadialGradient(p.x, p.y, 2, p.x, p.y, tw * 1.6);
    g.addColorStop(0, 'rgba(255,214,120,0.32)');
    g.addColorStop(1, 'rgba(255,214,120,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(p.x, p.y, tw * 1.6, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawEntities(ctx) {
    // sort: opponents first, then center discards, then human hand on top
    const order = [];
    for (const e of ents.values()) {
      let z = 0;
      if (e.handSlot !== undefined) z = 3;
      else if (e.meldSeat !== undefined) z = 2;
      else if (e.discSeat !== undefined) z = 1;
      else if (e.oppSeat !== undefined) z = 0;
      order.push([z, e]);
    }
    order.sort((a, b) => a[0] - b[0]);
    for (const [, e] of order) {
      let glow = 0;
      if (!perfMode && state.last_discard && e.discSeat === state.last_discard.seat
          && e.discIdx === (state.players[e.discSeat].discards || []).length - 1) {
        glow = 1;
      }
      const hover = (perfMode || e.handSlot !== S.hoverIdx) ? 0 : 1;
      let dim = 0;
      let winGlow = 0;
      if (e.handSlot !== undefined) {
        if (winningSet.has(e.tile)) winGlow = perfMode ? 0.25 : 0.55;
        if (state.legal && state.legal.can_discard) {
          const pm = state.players[mySeat];
          if (pm && pm.absence && e.tile < 27) {
            const absIdx = { man: 0, pin: 1, sou: 2 }[pm.absence];
            const hasAbs = (state.hand || []).some(
              (t) => t < 27 && Tiles.suit(t) === absIdx);
            if (Tiles.suit(e.tile) === absIdx) {
              winGlow = perfMode ? 0.2 : 0.5;   // 缺门牌: urgent — clear it first
            } else if (hasAbs) {
              dim = 0.75;              // other suits locked until cleared
            }
          }
        }
      }
      Tiles.drawTile(ctx, e.tile, e.x, e.y, tw, th, {
        rot: e.rot, scale: e.scale, faceUp: e.faceUp && e.tile >= 0,
        hover, dim, glow: glow + winGlow,
      });
    }
  }

  function badgePos(s) {
    // markers sit OUTSIDE the hand band (toward the screen edge), so they
    // never cover any tile
    const p = SEAT_ANCHOR[s];
    const off = th * 1.15;
    if (s === 0) return { x: p.x, y: p.y + off };
    if (s === 2) return { x: p.x, y: p.y - off };
    if (s === 1) return { x: p.x + off, y: p.y };
    return { x: p.x - off, y: p.y };
  }

  function drawMarkers(ctx) {
    const winds = ['东', '南', '西', '北'];
    for (let s = 0; s < 4; s++) {
      const ls = localSeat(s);
      const bp = badgePos(ls);
      const isTurn = state.turn === s &&
        (state.phase === 'play' || state.phase === 'wait_human' || state.phase === 'claim_wait');
      if (isTurn) {
        ctx.save();
        ctx.translate(bp.x, bp.y);
        if (!perfMode) ctx.rotate(time * 0.8);
        ctx.strokeStyle = 'rgba(255,214,120,0.75)';
        ctx.lineWidth = 3;
        if (!perfMode) {
          ctx.shadowColor = 'rgba(255,214,120,0.8)';
          ctx.shadowBlur = 14;
        }
        ctx.beginPath();
        ctx.arc(0, 0, tw * 0.85, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
      // dealer badge
      if (state.dealer === s) {
        ctx.save();
        ctx.translate(bp.x, bp.y);
        ctx.fillStyle = '#d4af37';
        ctx.beginPath();
        ctx.arc(0, 0, 13, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#241a06';
        ctx.font = '900 13px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(winds[s], 0, 1);
        ctx.restore();
      }
      // absence badge (sichuan) — flanked beside the turn marker
      const abs = state.players[s].absence;
      if (abs) {
        const label = { man: '缺万', pin: '缺筒', sou: '缺条' }[abs];
        ctx.save();
        ctx.font = '700 12px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = 'rgba(20,14,8,0.85)';
        const w = 52;
        const ax = bp.x + tw * 2.2;
        roundRect(ctx, ax - w / 2, bp.y - 10, w, 20, 8);
        ctx.fill();
        ctx.fillStyle = '#e8b84a';
        ctx.fillText(label, ax, bp.y);
        ctx.restore();
      }
      // tenpai badge for ME — recomputed from state every sync, so it
      // disappears the moment the hand is no longer tenpai
      if (s === mySeat && state.tenpai && state.phase !== 'round_end') {
        ctx.save();
        ctx.font = '900 14px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const ax = bp.x - tw * 2.2;
        ctx.fillStyle = 'rgba(212,175,55,0.14)';
        roundRect(ctx, ax - 30, bp.y - 11, 60, 22, 10);
        ctx.fill();
        ctx.strokeStyle = 'rgba(212,175,55,0.8)';
        ctx.lineWidth = 1.4;
        roundRect(ctx, ax - 30, bp.y - 11, 60, 22, 10);
        ctx.stroke();
        ctx.fillStyle = '#f0d078';
        ctx.fillText('听牌', ax, bp.y);
        ctx.restore();
      }
    }
  }

  function drawLighting(ctx) {
    // warm lamp glow from above
    const g = ctx.createRadialGradient(CW / 2, -CH * 0.12, 60, CW / 2, -CH * 0.12, CH * 0.85);
    g.addColorStop(0, 'rgba(255,226,160,0.16)');
    g.addColorStop(0.5, 'rgba(255,210,140,0.05)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, CW, CH);
    // vignette
    const v = ctx.createRadialGradient(CW / 2, CH / 2, CH * 0.35, CW / 2, CH / 2, CH * 0.95);
    v.addColorStop(0, 'rgba(0,0,0,0)');
    v.addColorStop(1, 'rgba(0,0,0,0.55)');
    ctx.fillStyle = v;
    ctx.fillRect(0, 0, CW, CH);
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // ============================================================== public API
  S.resize = resize;
  S.update = update;
  S.draw = draw;
  S.sync = sync;
  S.clearAll = clearAll;
  S.spawn = spawn;
  S.remove = remove;
  S.fly = fly;
  S.setTarget = setTarget;
  S.tileW = () => tw;
  S.tileH = () => th;
  S.get = () => state;
  S.setMySeat = (s) => { mySeat = s; dirty = true; };
  S.setHandDisplay = (arr) => { handDisplay = arr || null; dirty = true; };
  S.handDisplay = () => (handDisplay || (state && state.hand) || []);
  S.setDrag = (d) => { drag = d || null; dirty = true; };
  S.layout = () => ({ table, SEAT_ANCHOR, DISC_ANCHOR });
  S.handSlot = handSlot;
  S.discardSlot = discardSlot;
  S.hoverIdx = () => hoverIdx;
  S.setHover = (i) => {
    if (hoverIdx !== i) { hoverIdx = i; dirty = true; }
  };
  S.setWinning = (set) => { winningSet = set || new Set(); };
  S.wallGeom = wallGeom;
  S.setPerfMode = (v) => { perfMode = !!v; };
  S.isPerfMode = () => perfMode;
  S.isIdle = () => !animating && !dirty;
  S.requestRedraw = () => { dirty = true; };

  window.addEventListener('resize', () => { resize(); dirty = true; });
  resize();
  return S;
})();
