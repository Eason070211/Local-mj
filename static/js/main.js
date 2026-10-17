/* main.js — application controller: solo/LAN flows, polling, event
 * handling, seat-aware input, hand reordering, round analytics. */
'use strict';

(function () {
  const $ = (id) => document.getElementById(id);
  const canvas = $('table');
  const ctx = canvas.getContext('2d');

  const POLL_MS = 280;
  const LOBBY_MS = 800;
  const AUTOPASS_MS = 10000;

  let pollTimer = null;
  let busy = false;
  let autopassTimer = null;
  let roundEvents = [];       // hu/ma/wall_empty events of current round
  let running = false;
  let winningSet = new Set();
  let mySeat = 0;

  // ---- event queue for discard delay ----
  let eventQueue = [];
  let eventTimer = null;
  function getDelay() {
    const v = parseInt(($('opt-delay') || {}).value || '0', 10);
    return isNaN(v) ? 0 : v;
  }

  // ---- hand custom order (drag to group tiles) ----
  let handOrder = [];
  let prevHand = null;
  function refreshHandOrder(st) {
    const hand = st.hand || [];
    if (prevHand && hand.length !== prevHand.length) {
      // length changed (draw/discard): keep the user's arrangement by
      // matching tiles against the previous display sequence
      const oldSeq = handOrder.map((i) => (prevHand[i] != null ? prevHand[i] : -1));
      const used = new Set();
      const arr = [];
      for (const t of oldSeq) {
        const k = hand.findIndex((x, i) => x === t && !used.has(i));
        if (k >= 0) { used.add(k); arr.push(k); }
      }
      for (let i = 0; i < hand.length; i++) {
        if (!used.has(i)) arr.push(i);
      }
      handOrder = arr;
    } else if (!prevHand) {
      handOrder = hand.map((_, i) => i);
    }
    // length unchanged: keep the user's handOrder as-is
    prevHand = hand;
    Scene.setHandDisplay(handOrder.map((i) => hand[i]));
  }

  // ---- drag state ----
  let dragging = null;     // {from, tile, x, y}
  let dragMoved = false;

  // ---- round analytics (task: analysis / 复盘) ----
  const analytics = { rounds: [], perSeat: {}, seatNames: [] };
  let lastAnalyticRound = -1;

  // ------------------------------------------------------------ boot
  function boot() {
    UI.init();
    UI.showStart();
    $('btn-start').addEventListener('click', startGame);
    $('btn-create-room').addEventListener('click', createRoom);
    $('btn-join-room').addEventListener('click', joinRoom);
    $('btn-room-start').addEventListener('click', startRoom);
    $('btn-room-leave').addEventListener('click', leaveRoom);
    $('tab-solo').addEventListener('click', () => setMode('solo'));
    $('tab-multi').addEventListener('click', () => setMode('multi'));
    $('btn-stats').addEventListener('click', () => UI.statsModal(analytics));
    bindGameInput();
    Net.info().then((d) => { if (d.ok) UI.lanHint(d.ips, d.port); }).catch(() => {});
    requestAnimationFrame(loop);
  }

  function setMode(mode) {
    $('tab-solo').classList.toggle('active', mode === 'solo');
    $('tab-multi').classList.toggle('active', mode === 'multi');
    $('panel-solo').classList.toggle('hidden', mode !== 'solo');
    $('panel-multi').classList.toggle('hidden', mode !== 'multi');
  }

  // ------------------------------------------------------------ entry points
  async function startGame() {
    const cfg = UI.config();
    UI.hideStart();
    try {
      const data = await Net.newGame(cfg.rules, cfg.difficulty, cfg.buy_ma, null);
      enterGame(data);
    } catch (e) {
      UI.modal(`<h2>无法开始</h2><p>${e.message}</p>`);
      UI.showStart();
    }
  }

  async function createRoom() {
    const name = ($('m-name').value || '').trim();
    if (!name) { UI.modal('<h2>提示</h2><p>请先填写你的昵称。</p>'); return; }
    const rules = $('m-rules').value;
    const diff = $('m-diff').value;
    const ma = parseInt($('m-ma').value, 10);
    try {
      const d = await Net.roomNew(rules, diff, ma, name);
      enterLobby(d.room, true);
      const info = await Net.info();
      if (info.ok) UI.showShare(info.ips, info.port, d.room.code);
      startLobbyPoll(d.room.code);
    } catch (e) {
      UI.modal(`<h2>无法创建</h2><p>${e.message}</p>`);
    }
  }

  async function joinRoom() {
    const code = ($('join-code').value || '').trim().toUpperCase();
    const name = ($('join-name').value || '').trim();
    if (!code) { UI.modal('<h2>提示</h2><p>请输入房间码。</p>'); return; }
    if (!name) { UI.modal('<h2>提示</h2><p>请填写你的昵称。</p>'); return; }
    try {
      const d = await Net.roomJoin(code, name);
      if (d.started && d.game_id) {
        enterGame(d);
      } else {
        enterLobby(d.room, false);
        startLobbyPoll(code);
      }
    } catch (e) {
      UI.modal(`<h2>无法加入</h2><p>${e.message}</p>`);
    }
  }

  async function startRoom() {
    try {
      const d = await Net.roomStart(Net.roomCode || '', Net.seat, Net.token);
      stopLobbyPoll();
      enterGame(d);
    } catch (e) {
      UI.modal(`<h2>无法开始</h2><p>${e.message}</p>`);
    }
  }

  function leaveRoom() {
    stopLobbyPoll();
    Net.reset();
    running = false;
    UI.hideStart();
    setMode('multi');
  }

  // ------------------------------------------------------------ lobby
  let lobbyTimer = null;
  function enterLobby(room, isHost) {
    UI.closeModal();  // a failed attempt may have left a modal open
    $('start-screen').classList.add('hidden');
    $('room-screen').classList.remove('hidden');
    UI.renderRoom(room, Net.seat);
  }

  function startLobbyPoll(code) {
    stopLobbyPoll();
    lobbyTimer = setInterval(async () => {
      try {
        const d = await Net.roomGet(code);
        if (!d.ok || !d.room) return;
        UI.renderRoom(d.room, Net.seat);
        if (d.room.phase === 'playing' && d.room.gid && !Net.gameId) {
          Net.gameId = d.room.gid;
          Net.lastSeq = 0;
          const st = await Net.state();
          stopLobbyPoll();
          enterGame(st);
        }
      } catch (e) { /* keep polling; server may be briefly busy */ }
    }, LOBBY_MS);
  }

  function stopLobbyPoll() {
    if (lobbyTimer) { clearInterval(lobbyTimer); lobbyTimer = null; }
  }

  // ------------------------------------------------------------ enter game
  function enterGame(data) {
    if (!data || !data.state) return;
    UI.closeModal();  // a failed attempt may have left a modal open
    running = true;
    roundEvents = [];
    handOrder = [];
    prevHand = null;
    lastAnalyticRound = -1;
    mySeat = Net.seat;
    stopLobbyPoll();
    Scene.clearAll();
    Scene.setMySeat(mySeat);
    $('start-screen').classList.add('hidden');
    $('room-screen').classList.add('hidden');
    $('game-screen').classList.remove('hidden');
    UI.syncPerfState();
    syncAll(data);
    startPolling();
  }

  function stopPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  function startPolling() {
    if (pollTimer) clearTimeout(pollTimer);
    // Self-scheduling chain: the next poll is armed BEFORE this one runs,
    // so a slow/hung request can never stop polling forever.
    const tick = async () => {
      if (busy || !running) {
        pollTimer = setTimeout(tick, POLL_MS);
        return;
      }
      busy = true;
      try {
        const data = await Net.state();
        syncAll(data);
      } catch (e) {
        if (running) console.warn('poll error', e && e.message);
      } finally {
        busy = false;
      }
      pollTimer = setTimeout(tick, POLL_MS);
    };
    pollTimer = setTimeout(tick, POLL_MS);
  }

  function syncAll(data) {
    const big = (data.events || []).length > 6;
    processEvents(data.events || [], big, data.state);
    Scene.sync(data.state);
    refreshHandOrder(data.state);
    ensureRendering();
    UI.scoreboard(data.state);
    UI.roundInfo(data.state);
    refreshUI(data.state);
  }

  // ------------------------------------------------------------ events
  function processEvents(events, catchUp, freshState) {
    if (catchUp || getDelay() === 0) {
      // Fast path: process all immediately
      for (const ev of events) processOneEvent(ev, catchUp, freshState);
      return;
    }
    // Queue events for delayed replay
    for (const ev of events) eventQueue.push({ ev, freshState });
    drainEventQueue();
  }

  function drainEventQueue() {
    if (eventTimer) { clearTimeout(eventTimer); eventTimer = null; }
    if (eventQueue.length === 0) return;
    processNextEvent();
  }

  function processNextEvent() {
    if (eventQueue.length === 0) { eventTimer = null; return; }
    const { ev, freshState } = eventQueue.shift();
    processOneEvent(ev, false, freshState);
    // Delay after a human-visible discard so the order is readable
    const delay = (ev.type === 'discard') ? getDelay() : 0;
    if (eventQueue.length > 0) {
      eventTimer = setTimeout(processNextEvent, delay);
    } else {
      eventTimer = null;
    }
  }

  function processOneEvent(ev, catchUp, freshState) {
    switch (ev.type) {
      case 'round_start':
        roundEvents = [];
        if (!catchUp) UI.banner(`第 ${ev.round} 局 · 开始`);
        break;
      case 'draw':
        if (!catchUp && ev.seat === mySeat) Sound.draw();
        break;
      case 'discard':
        if (!catchUp) Sound.discard();
        break;
      case 'peng':
      case 'chi':
        if (!catchUp) Sound.claim();
        break;
      case 'gang':
        if (!catchUp) Sound.gang();
        roundEvents.push({ kind: 'gang', seat: ev.seat, kind2: ev.kind, deltas: ev.deltas });
        break;
      case 'absence':
        if (!catchUp) UI.banner(`${nameOf(ev.seat)} 缺${ {man: '万', pin: '筒', sou: '条'}[ev.suit] }`, 1200);
        break;
      case 'hu':
        if (!catchUp) {
          Sound.hu();
          if (ev.seat === mySeat) Sound.win();
        }
        roundEvents.push({ kind: 'hu', seat: ev.seat, from: ev.from, zimo: ev.zimo,
                           fan_names: ev.fan_names, fan: ev.fan, deltas: ev.deltas, tile: ev.tile });
        break;
      case 'ma':
        roundEvents.push({ kind: 'ma', horses: ev.horses, deltas: ev.deltas });
        break;
      case 'wall_empty':
        roundEvents.push({ kind: 'wall_empty', deltas: ev.deltas, tenpai: ev.tenpai });
        if (!catchUp) UI.banner('牌墙摸完', 1400);
        break;
      case 'round_end':
        if (!catchUp || true) showRoundResult(freshState);
        break;
      default:
        break;
    }
  }

  const NAMES = ['你', '南家', '西家', '北家'];
  function nameOf(s) {
    const st = Scene.get();
    if (st && st.players && st.players[s]) {
      return (s === mySeat ? '你' : (st.players[s].name || NAMES[s]));
    }
    return s === mySeat ? '你' : NAMES[s];
  }

  // ------------------------------------------------------------ result overlay
  function showRoundResult(freshState) {
    const st = freshState || Scene.get();
    if (!st) return;
    const wins = roundEvents.filter((e) => e.kind === 'hu');
    const ma = roundEvents.find((e) => e.kind === 'ma');
    const wallEmpty = roundEvents.find((e) => e.kind === 'wall_empty');
    const scores = st.players.map((p) => p.score);

    // accumulate analytics once per round
    if (lastAnalyticRound !== st.round) {
      lastAnalyticRound = st.round;
      recordRound(st, wins, ma, wallEmpty);
    }

    let html = `<div class="win-card">`;
    if (wins.length) {
      html += `<div class="win-title">胡牌</div><div class="win-sub">本局结算</div>`;
      for (const w of wins) {
        const isYou = w.seat === mySeat;
        html += `<div style="margin:6px 0">
          <b style="color:${isYou ? '#d4af37' : '#efe6d0'}">${nameOf(w.seat)}</b>
          ${w.zimo ? '自摸' : `点炮 <span style="color:#888">(${nameOf(w.from)})</span>`}
          · ${(w.fan_names || []).map((f) => f[0]).join('、')} = <b style="color:#f0d078">${w.fan} 番</b>
          <div style="font-size:12px;color:#888;margin-top:2px">${w.deltas
            .filter((d, i) => i % 2 === 0)
            .map((d) => `${nameOf(d[0])} ${d[1] >= 0 ? '+' : ''}${d[1]}`).join('　')}</div>
        </div>`;
      }
    } else {
      html += `<div class="win-title" style="font-size:34px">流局</div>
               <div class="win-sub">本局无人和牌</div>`;
    }
    if (ma) {
      html += `<div class="win-sub" style="margin-top:8px">买马</div><div class="ma-row-win">`;
      const horseSeat = (t) => {
        if (t >= 27) return t - 27 < 4 ? t - 27 : 0;
        return { 1: 0, 2: 1, 3: 2, 4: 3, 5: 0, 6: 1, 7: 2, 8: 3, 9: 0 }[t % 9 + 1];
      };
      for (const h of ma.horses) {
        const seat = horseSeat(h);
        const hit = wins.length && wins.some((w) => w.seat === seat);
        html += `<span class="ma-chip ${hit ? 'hit' : 'miss'}">${Tiles.tileName(h)} → ${nameOf(seat)}${hit ? ' ✓' : ''}</span>`;
      }
      html += `</div>`;
    }
    if (wallEmpty) {
      html += `<div style="margin-top:8px;font-size:12px;color:#888">
        查大叫/花猪：${wallEmpty.deltas.filter((d, i) => i % 2 === 0)
          .map((d) => `${nameOf(d[0])} ${d[1] >= 0 ? '+' : ''}${d[1]}`).join('　')}</div>`;
    }
    html += `<div class="win-score">${scores.map((s, i) => `${nameOf(i)} ${s}`).join('　')}</div>
      <div class="win-actions">
        <button class="win-btn primary" id="win-next">下一局</button>
        <button class="win-btn" id="win-stats">战绩统计</button>
        <button class="win-btn ghost" id="win-close">稍后</button>
      </div></div>`;

    const ov = $('win-overlay');
    ov.innerHTML = html;
    ov.classList.remove('hidden');
    $('win-next').addEventListener('click', () => {
      ov.classList.add('hidden');
      sendAction({ type: 'next_round' });
    });
    $('win-stats').addEventListener('click', () => UI.statsModal(analytics));
    $('win-close').addEventListener('click', () => {
      ov.classList.add('hidden');
      UI.showActions('round_end', {});
    });
  }

  function recordRound(st, wins, ma, wallEmpty) {
    const names = st.players.map((p) => p.name);
    analytics.seatNames = names;
    const r = {
      round: st.round,
      dealerName: names[st.dealer] || '东',
      wins: wins.map((w) => ({
        seat: w.seat, name: names[w.seat] || NAMES[w.seat],
        zimo: w.zimo, from: w.from,
        fromName: w.from == null ? '' : (names[w.from] || NAMES[w.from]),
        fan: w.fan, fan_names: w.fan_names || [],
      })),
      hands: st.players.map((p) => ({
        seat: p.seat, name: p.name,
        tiles: Array.isArray(p.hand) ? p.hand : [],
      })).filter((h) => h.tiles.length > 0),
    };
    analytics.rounds.push(r);
    st.players.forEach((p) => {
      const s = analytics.perSeat[p.seat] || (analytics.perSeat[p.seat] = {
        rounds: 0, hu: 0, zimo: 0, dianpao: 0, fanSum: 0, topFan: 0,
      });
      s.rounds += 1;
    });
    wins.forEach((w) => {
      const s = analytics.perSeat[w.seat] || (analytics.perSeat[w.seat] = {
        rounds: 0, hu: 0, zimo: 0, dianpao: 0, fanSum: 0, topFan: 0,
      });
      s.hu += 1;
      s.zimo += w.zimo ? 1 : 0;
      s.dianpao += w.zimo ? 0 : 1;
      s.fanSum += w.fan;
      s.topFan = Math.max(s.topFan, w.fan);
    });
  }

  // ------------------------------------------------------------ input
  function bindGameInput() {
    canvas.addEventListener('mousemove', (e) => {
      const r = canvas.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      if (dragging) {
        dragging.x = x; dragging.y = y;
        if (Math.hypot(x - dragging.sx, y - dragging.sy) > 10) dragMoved = true;
        Scene.setDrag({ tile: dragging.tile, x, y });
        ensureRendering();
        return;
      }
      Scene.setHover(hitTestHand(x, y));
      ensureRendering();
    });
    canvas.addEventListener('mouseleave', () => {
      Scene.setHover(-1);
      if (dragging) { Scene.setDrag(null); dragging = null; }
      ensureRendering();
    });
    canvas.addEventListener('mousedown', (e) => {
      const st = Scene.get();
      if (!st || st.phase !== 'wait_human' || !st.legal || !st.legal.can_discard) return;
      const r = canvas.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      const idx = hitTestHand(x, y);
      if (idx < 0) return;
      const hand = Scene.handDisplay();
      if (idx >= hand.length) return;
      dragging = { from: idx, tile: hand[idx], sx: x, sy: y, x, y };
      dragMoved = false;
    });
    canvas.addEventListener('mouseup', (e) => {
      if (!dragging) return;
      const st = Scene.get();
      const { from, tile } = dragging;
      const moved = dragMoved;
      if (moved && st) {
        const r = canvas.getBoundingClientRect();
        const x = e.clientX - r.left, y = e.clientY - r.top;
        const len = (st.hand || []).length;
        // snap to the nearest display slot
        let best = from, bestD = Infinity;
        for (let i = 0; i < len; i++) {
          const p = Scene.handSlot(0, i, len);
          const d = Math.hypot(x - p.x, y - p.y);
          if (d < bestD) { bestD = d; best = i; }
        }
        if (best !== from) {
          const item = handOrder.splice(from, 1)[0];
          handOrder.splice(Math.min(best, handOrder.length), 0, item);
          prevHand = st.hand;      // next poll keeps this arrangement
          Scene.setHandDisplay(handOrder.map((i) => st.hand[i]));
          ensureRendering();
          Sound.click();
        }
      }
      dragging = null;
      dragMoved = false;
      Scene.setDrag(null);
      ensureRendering();
    });
    canvas.addEventListener('click', (e) => {
      if (dragMoved) return;               // a drag happened; skip discard
      const st = Scene.get();
      if (!st) return;
      if (st.phase !== 'wait_human' || !st.legal || !st.legal.can_discard) return;
      const r = canvas.getBoundingClientRect();
      const idx = hitTestHand(e.clientX - r.left, e.clientY - r.top);
      if (idx < 0) return;
      const hand = Scene.handDisplay();
      if (idx >= hand.length) return;
      const tile = hand[idx];
      // Sichuan 定缺: declared-absence tiles MUST be cleared first; other
      // suits may not be discarded while any absence tile remains in hand.
      const pm = st.players[mySeat];
      if (pm && pm.absence && tile < 27 &&
          Tiles.suit(tile) === { man: 0, pin: 1, sou: 2 }[pm.absence]) {
        sendAction({ type: 'discard', tile });  // absence tile: priority discard
        return;
      }
      if (pm && pm.absence && tile < 27) {
        const absIdx = { man: 0, pin: 1, sou: 2 }[pm.absence];
        const hasAbs = st.hand.some((t) => t < 27 && Tiles.suit(t) === absIdx);
        if (hasAbs) { Sound.error(); UI.banner('先打出缺门牌', 1200); return; }
      }
      sendAction({ type: 'discard', tile });
    });

    $('act-hu').addEventListener('click', () => {
      const st = Scene.get();
      if (!st || !st.legal) return;
      if (st.phase === 'wait_human' && st.legal.zimo) {
        sendAction({ type: 'hu' });
      } else if (st.phase === 'claim_wait') {
        sendClaimByType('hu');
      }
    });
    $('act-peng').addEventListener('click', () => sendClaimByType('peng'));
    $('act-gang').addEventListener('click', () => sendClaimByType('gang_ming'));
    $('act-chi').addEventListener('click', () => sendClaimByType('chi'));
    $('act-pass').addEventListener('click', () => {
      const st = Scene.get();
      if (!st || st.phase !== 'claim_wait') return;
      const idx = (st.legal.claims || []).findIndex((o) => o.type === 'pass');
      if (idx >= 0) sendAction({ type: 'claim', index: idx });
    });
    $('act-next').addEventListener('click', () => sendAction({ type: 'next_round' }));

    document.querySelectorAll('.abs-btn').forEach((b) => {
      b.addEventListener('click', () => sendAction({ type: 'absence', suit: b.dataset.suit }));
    });
  }

  function sendClaimByType(type) {
    const st = Scene.get();
    if (!st || st.phase !== 'claim_wait' || !st.legal) return;
    const idx = (st.legal.claims || []).findIndex((o) => o.type === type);
    if (idx >= 0) sendAction({ type: 'claim', index: idx });
  }

  function hitTestHand(x, y) {
    const st = Scene.get();
    if (!st) return -1;
    const hand = Scene.handDisplay();
    for (let i = 0; i < hand.length; i++) {
      const p = Scene.handSlot(0, i, hand.length);
      const dx = x - p.x, dy = y - p.y;
      if (dx * dx + dy * dy < Math.pow(Scene.tileW() * 0.85, 2)) return i;
    }
    return -1;
  }

  async function sendAction(action, attempt) {
    attempt = attempt || 0;
    if (!running) return;
    if (busy) {
      // a poll is mid-flight; retry shortly instead of silently dropping
      if (attempt < 6) {
        setTimeout(() => sendAction(action, attempt + 1), 350);
        return;
      }
      return;
    }
    busy = true;
    try {
      const data = await Net.act(action);
      syncAll(data);
      // if still waiting for a claim, arm auto-pass
      armAutoPass(data.state);
    } catch (e) {
      Sound.error();
      UI.banner(e.message, 1600);
    } finally {
      busy = false;
    }
  }

  function armAutoPass(st) {
    clearTimeout(autopassTimer);
    autopassTimer = null;
    if (st.phase !== 'claim_wait' || !$('opt-autopass').checked) return;
    const claims = st.legal.claims || [];
    const hasPass = claims.some((o) => o.type === 'pass');
    if (!hasPass) return;
    autopassTimer = setTimeout(() => {
      const cur = Scene.get();
      if (cur && cur.phase === 'claim_wait' && cur.pending_seat === mySeat) {
        const idx = (cur.legal.claims || []).findIndex((o) => o.type === 'pass');
        if (idx >= 0) sendAction({ type: 'claim', index: idx });
      }
    }, AUTOPASS_MS);
  }

  // ------------------------------------------------------------ refresh UI
  function refreshUI(st) {
    UI.hideAbsence();
    const legal = st.legal || {};
    UI.claimWarn(null);
    if (st.phase === 'absence_wait' && legal.absence_options && legal.absence_options.length) {
      UI.showAbsence();
      UI.hideActions();
      UI.turnInfo('定缺：选择缺门花色');
    } else if (st.phase === 'wait_human') {
      UI.hideActions();
      const gangOps = legal.gang_options || [];
      if (legal.zimo || gangOps.length) {
        UI.showActions('zimo', {});
        $('act-hu').style.display = legal.zimo ? '' : 'none';
        $('act-gang').style.display = gangOps.length ? '' : 'none';
        $('act-pass').style.display = 'none';
      }
      UI.turnInfo(st.turn === mySeat ? '你的回合' : `等待 ${nameOf(st.turn)} 出牌`);
    } else if (st.phase === 'claim_wait') {
      const claims = legal.claims || [];
      if (claims.length) {
        UI.showActions('claims', {
          has_hu: claims.some((o) => o.type === 'hu'),
          has_peng: claims.some((o) => o.type === 'peng'),
          has_gang: claims.some((o) => o.type === 'gang_ming'),
          has_chi: claims.some((o) => o.type === 'chi'),
        });
        // decision hint: claiming would break a built sequence
        const warn = claims.map((o) => o.warn).filter(Boolean);
        if (warn.length) UI.claimWarn(warn.join('；'));
        UI.turnInfo('轮到你了：胡 / 碰 / 杠 / 吃 或 过');
      } else {
        UI.hideActions();
        UI.turnInfo(`等待 ${nameOf(st.pending_seat)} 叫牌…`);
      }
    } else if (st.phase === 'play') {
      UI.hideActions();
      UI.turnInfo(`${nameOf(st.turn)} 思考中…`);
    } else if (st.phase === 'round_end') {
      // overlay already shown by round_end event
      UI.turnInfo('本局结束');
    } else {
      UI.hideActions();
      UI.turnInfo('');
    }

    // winning-tile highlight
    const showTip = $('opt-tip').checked;
    if (showTip && st.phase === 'wait_human' && st.turn === mySeat && legal.winning_tiles) {
      winningSet = new Set(legal.winning_tiles);
    } else if (!showTip || st.turn !== mySeat) {
      winningSet = new Set();
    }
    Scene.setWinning(winningSet);
  }

  // ------------------------------------------------------------ loop
  let last = performance.now();
  let renderRunning = false;

  function loop(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    const needsDraw = Scene.update(dt);
    if (needsDraw) {
      Scene.draw(ctx);
    }
    // Stop the loop when nothing is animating — restart on next sync/fly
    if (Scene.isIdle()) {
      renderRunning = false;
      return;
    }
    requestAnimationFrame(loop);
  }

  function ensureRendering() {
    if (!renderRunning) {
      renderRunning = true;
      last = performance.now();
      requestAnimationFrame(loop);
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
