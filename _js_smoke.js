/* Headless smoke test for the frontend modules (tiles.js / render.js).
 * Stubs browser APIs so layout math and entity reconciliation run in Node. */
'use strict';
const fs = require('fs');
const path = require('path');

// ---- minimal browser stubs ----
const ctxStub = new Proxy({}, {
  get(t, k) {
    if (k === 'canvas') return {};
    if (k === 'createLinearGradient' || k === 'createRadialGradient') {
      return () => ({ addColorStop() {} });
    }
    if (k === 'measureText') return () => ({ width: 10 });
    return (...a) => {};
  },
  set() { return true; },
});
const canvasStub = { width: 0, height: 0, style: {}, getContext: () => ctxStub,
                     addEventListener() {}, getBoundingClientRect: () => ({ left: 0, top: 0 }) };

global.window = { addEventListener() {}, devicePixelRatio: 1, innerWidth: 1280, innerHeight: 800 };
global.document = { getElementById: () => canvasStub, querySelectorAll: () => [], addEventListener() {} };
global.requestAnimationFrame = () => {};

// ---- load modules (concatenated into ONE scope, like the browser) ----
const files = ['tiles.js', 'render.js'];
let all = '';
for (const f of files) {
  all += fs.readFileSync(path.join(__dirname, 'static', 'js', f), 'utf8') + '\n;\n';
}
all += 'globalThis.__Tiles = Tiles; globalThis.__Scene = Scene;\n';
(new Function(all))();
const Tiles = globalThis.__Tiles;
const Scene = globalThis.__Scene;

// ---- fake state ----
const mkState = () => ({
  game_id: 't', rules: 'classic', round: 1, dealer: 0, wall_remaining: 62,
  phase: 'wait_human', turn: 0, last_discard: null,
  players: [
    { seat: 0, name: '你', is_human: true, score: 0, hand_count: 13,
      discards: [], melds: [], absence: null, active: true, hu_count: 0, is_dealer: true },
    { seat: 1, name: '电脑·南', is_human: false, score: 0, hand_count: 13,
      discards: [0, 9, 27], melds: [], absence: null, active: true, hu_count: 0, is_dealer: false },
    { seat: 2, name: '电脑·西', is_human: false, score: 0, hand_count: 12,
      discards: [28, 30], melds: [{ kind: 'peng', tiles: [5, 5, 5], from: 0 }],
      absence: null, active: true, hu_count: 0, is_dealer: false },
    { seat: 3, name: '电脑·北', is_human: false, score: 0, hand_count: 13,
      discards: [], melds: [], absence: null, active: true, hu_count: 0, is_dealer: false },
  ],
  human_hand: [0, 0, 2, 3, 4, 5, 9, 10, 11, 18, 19, 20, 27, 31],
  legal: { phase: 'wait_human', absence_options: [], can_discard: true, zimo: false,
           gang_options: [], claims: [], passable: false, winning_tiles: [] },
  human_tenpai: false,
});

let pass = 0, fail = 0;
const ok = (name, cond) => { if (cond) pass++; else { fail++; console.log('FAIL:', name); } };

// 1. tiles naming
ok('tileName 0', Tiles.tileName(0) === '一万');
ok('tileName 27', Tiles.tileName(27) === '东');
ok('tileShort 14', Tiles.tileShort(14) === '6筒');

// 2. sync + reconcile with entities
Scene.sync(mkState());
const st = Scene.get();
ok('state stored', st && st.players.length === 4);
let handEnts = 0;
// draw once with stub ctx — must not throw / NaN
Scene.draw(ctxStub);
// hand entities exist
// (can't easily inspect internals; rely on draw not throwing + layout calls)
Scene.update(0.016);
Scene.draw(ctxStub);

// 3. layout math
const slot0 = Scene.handSlot(0, 5, 14);
ok('handSlot finite', Number.isFinite(slot0.x) && Number.isFinite(slot0.y));
const ds = Scene.discardSlot(1, 3);
ok('discardSlot finite', Number.isFinite(ds.x) && Number.isFinite(ds.y) && Number.isFinite(ds.rot));
const wg = Scene.wallGeom();
ok('wallGeom n>0', wg.n > 0 && Number.isFinite(wg.x0));

// 4. sync with discard (one tile removed from hand, added to discards)
const st2 = mkState();
st2.players[0].discards = [7];
st2.human_hand = [0, 0, 2, 3, 4, 5, 9, 10, 11, 18, 19, 20, 27, 31]; // 14 -> kept for draw
st2.last_discard = { tile: 7, seat: 0, seq: 3 };
st2.phase = 'play';
Scene.sync(st2);
Scene.update(0.016);
Scene.draw(ctxStub);

// 5. claim state (meld reconcile)
const st3 = mkState();
st3.players[0].melds = [{ kind: 'peng', tiles: [12, 12, 12], from: 2 }];
st3.phase = 'claim_wait';
Scene.sync(st3);
Scene.draw(ctxStub);

// 6. absence state
const st4 = mkState();
st4.phase = 'absence_wait';
st4.turn = 0;
st4.players[0].absence = null;
st4.legal = { phase: 'absence_wait', absence_options: ['man', 'pin', 'sou'], can_discard: false,
              zimo: false, gang_options: [], claims: [], passable: false, winning_tiles: [] };
Scene.sync(st4);
Scene.draw(ctxStub);

// 7. human hand with absence dim
const st5 = mkState();
st5.players[0].absence = 'man';
st5.legal.can_discard = true;
Scene.sync(st5);
Scene.setWinning(new Set([0, 1]));
Scene.draw(ctxStub);

// 8. round_end
const st6 = mkState();
st6.phase = 'round_end';
Scene.sync(st6);
Scene.draw(ctxStub);

console.log(`\n[smoke] pass=${pass} fail=${fail}`);
process.exit(fail ? 1 : 0);
