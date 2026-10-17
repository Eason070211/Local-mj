/* sound.js — tiny WebAudio synthesizer (no assets, works offline). */
'use strict';

const Sound = (function () {
  let ctx = null;
  let enabled = true;

  function ac() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) { return null; }
    }
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }

  function tone(freq, dur, type, vol, when, slideTo) {
    const c = ac();
    if (!c || !enabled) return;
    const t0 = c.currentTime + (when || 0);
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = type || 'sine';
    o.frequency.setValueAtTime(freq, t0);
    if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(vol || 0.12, t0 + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g).connect(c.destination);
    o.start(t0);
    o.stop(t0 + dur + 0.05);
  }

  function noise(dur, vol, when, lp) {
    const c = ac();
    if (!c || !enabled) return;
    const t0 = c.currentTime + (when || 0);
    const len = Math.max(1, Math.floor(c.sampleRate * dur));
    const buf = c.createBuffer(1, len, c.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = c.createBufferSource();
    src.buffer = buf;
    const f = c.createBiquadFilter();
    f.type = 'lowpass';
    f.frequency.value = lp || 900;
    const g = c.createGain();
    g.gain.setValueAtTime(vol || 0.2, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(f).connect(g).connect(c.destination);
    src.start(t0);
  }

  const S = {
    setEnabled(v) { enabled = v; },
    isEnabled() { return enabled; },
    click() { tone(880, 0.06, 'triangle', 0.08); },
    draw() { tone(620, 0.07, 'sine', 0.07, 0, 900); },
    discard() { noise(0.09, 0.22, 0, 700); tone(180, 0.12, 'sine', 0.10, 0, 90); },
    claim() { tone(523, 0.1, 'triangle', 0.10); tone(784, 0.14, 'triangle', 0.10, 0.08); },
    gang() { tone(392, 0.12, 'triangle', 0.12); tone(494, 0.16, 'triangle', 0.12, 0.10); tone(587, 0.2, 'triangle', 0.12, 0.20); },
    hu() {
      tone(523, 0.16, 'triangle', 0.12);
      tone(659, 0.16, 'triangle', 0.12, 0.09);
      tone(784, 0.24, 'triangle', 0.14, 0.18);
      tone(1046, 0.4, 'triangle', 0.16, 0.28);
    },
    win() {
      [523, 659, 784, 1046, 784, 1046, 1318].forEach((f, i) =>
        tone(f, 0.22, 'triangle', 0.13, i * 0.11));
    },
    error() { tone(200, 0.15, 'square', 0.06, 0, 140); },
  };
  return S;
})();
