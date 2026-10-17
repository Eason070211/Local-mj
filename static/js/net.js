/* net.js — tiny JSON API client for the local server (single-player
 * and LAN multiplayer rooms).
 * Every request is raced against a timeout so a hung connection can
 * never wedge the polling loop (self-healing). */
'use strict';

const Net = (function () {
  const N = {};
  N.gameId = null;
  N.lastSeq = 0;
  N.seat = 0;        // my seat in the room game (0 in solo)
  N.token = '';      // per-seat auth token ('' in solo)
  N.roomCode = '';   // room I created / joined

  async function req(path, method, body, timeoutMs) {
    const opt = { method, headers: {}, cache: 'no-store' };
    if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    const ms = timeoutMs || 8000;
    const ctrl = new AbortController();
    opt.signal = ctrl.signal;
    const timer = setTimeout(() => ctrl.abort(), ms);
    let r;
    try {
      r = await fetch(path, opt);
    } finally {
      clearTimeout(timer);
    }
    let data = {};
    try { data = await r.json(); } catch (e) { /* ignore */ }
    if (!r.ok || data.ok === false) {
      throw new Error(data.error || ('HTTP ' + r.status));
    }
    return data;
  }

  N.info = () => req('/api/info', 'GET');

  // ---- solo ----
  N.newGame = (rules, difficulty, buyMa, seed) =>
    req('/api/game/new', 'POST', { rules, difficulty, buy_ma: buyMa, seed })
      .then((d) => {
        N.gameId = d.game_id; N.lastSeq = 0; N.seat = 0; N.token = '';
        return d;
      });

  // ---- rooms ----
  N.roomNew = (rules, difficulty, buyMa, name) =>
    req('/api/room/new', 'POST', { rules, difficulty, buy_ma: buyMa, name })
      .then((d) => {
        N.seat = 0; N.token = d.token; N.roomCode = d.room.code;
        N.gameId = null; N.lastSeq = 0;
        return d;
      });

  N.roomJoin = (code, name) =>
    req('/api/room/join', 'POST', { code, name })
      .then((d) => {
        N.seat = d.seat; N.token = d.token; N.roomCode = d.room.code;
        N.gameId = d.game_id || null; N.lastSeq = 0;
        return d;
      });

  N.roomGet = (code) => req('/api/room/' + encodeURIComponent(code), 'GET');

  N.roomStart = (code, seat, token) =>
    req('/api/room/' + encodeURIComponent(code) + '/start',
        'POST', { seat, token })
      .then((d) => { N.gameId = d.game_id; N.lastSeq = 0; return d; });

  // ---- game state / actions ----
  N.state = () =>
    req('/api/game/' + N.gameId + '/state?since=' + N.lastSeq
        + '&seat=' + N.seat
        + '&token=' + encodeURIComponent(N.token),
        'GET', undefined, 6000)
      .then((d) => { N.lastSeq = d.next_seq; return d; });

  N.act = (action) => {
    action.since = N.lastSeq;
    action.seat = N.seat;
    action.token = N.token;
    return req('/api/game/' + N.gameId + '/action', 'POST', action, 15000)
      .then((d) => { N.lastSeq = d.next_seq; return d; });
  };

  N.reset = () => { N.gameId = null; N.lastSeq = 0; N.seat = 0; N.token = '';
                    N.roomCode = ''; };

  return N;
})();
