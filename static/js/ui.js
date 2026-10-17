/* ui.js — DOM layer: start screen, HUD, scoreboard, action buttons,
 * modals, banners.  All table visuals live on canvas; everything that
 * needs crisp text lives here. */
'use strict';

const UI = (function () {
  const U = {};
  const $ = (id) => document.getElementById(id);

  let config = { rules: 'classic', difficulty: 'medium', buy_ma: 0 };

  const RULE_HELP = {
    classic: [
      '136 张牌（含字牌），可吃、碰、杠，第一家和牌即结束本局。',
      '自摸：其余三家各付 1 倍底分 × 番数；点炮：点炮者单独付。',
      '一炮多响允许（多家同时胡时各结各的）。',
      '番种：平胡1 / 对对胡2 / 混一色2 / 清一色4 / 七对4 / 龙七对8 / 十三幺8 / 字一色8；杠上开花2 / 抢杠2 / 海底捞月2。',
      '流局无赔付，庄家连庄。',
    ],
    sichuan: [
      '108 张牌（无字牌），血战到底：一家胡牌后本局继续，直到三家胡或牌墙摸完。',
      '定缺：开局先选一门缺牌；缺门花色必须优先打出（摸到缺门牌必须立刻打掉，哪怕会点炮），缺门未清空前不能打其他花色的牌；胡牌时手牌必须不含缺门。',
      '不能吃，只能碰/杠。',
      '刮风下雨：直杠点杠者付 2，补杠每家付 1，暗杠每家付 2。',
      '自摸每家付番数；点炮仅点炮者付。',
      '番种：平胡1 / 对对2 / 清一色2 / 七对4 / 龙七对8 / 全带幺2 / 根（每根+1）/ 杠上花2 / 杠上炮2 / 抢杠2。',
      '牌墙摸完：未听牌者赔每个听牌者 1；花猪（未缺门）赔每家 2。',
    ],
    guangdong: [
      '136 张牌，可吃、碰、杠，第一家和牌即结束本局，番种同经典推倒胡。',
      '可选“买马”（庄家买马）：和牌后从牌墙尾摸 N 张马牌，按牌面方位结算：',
      '  1/5/9 及中发白→庄家；2/6→南；3/7→西；4/8→北。',
      '马中胡牌者方位：其余三家各追加赔付 1 倍；马中其他方位：该方位玩家追加赔付 1 倍。',
    ],
  };

  // ------------------------------------------------------------ start screen
  U.init = function () {
    document.querySelectorAll('.rule-card').forEach((b) => {
      b.addEventListener('click', () => {
        document.querySelectorAll('.rule-card').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        config.rules = b.dataset.rules;
        const showMa = config.rules === 'guangdong';
        $('ma-label').style.display = showMa ? 'block' : 'none';
        $('ma-row').style.display = showMa ? 'flex' : 'none';
      });
    });
    document.querySelectorAll('.diff-btn').forEach((b) => {
      b.addEventListener('click', () => {
        document.querySelectorAll('.diff-btn').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        config.difficulty = b.dataset.diff;
      });
    });
    document.querySelectorAll('.ma-btn').forEach((b) => {
      b.addEventListener('click', () => {
        document.querySelectorAll('.ma-btn').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        config.buy_ma = parseInt(b.dataset.ma, 10);
      });
    });
    $('btn-sound').addEventListener('click', toggleSound);
    $('btn-perf').addEventListener('click', togglePerf);
    $('btn-help').addEventListener('click', () => showHelp(config.rules));
    $('btn-help-start').addEventListener('click', () => showHelp(config.rules));
    $('btn-settings').addEventListener('click', showSettings);
    $('btn-quit').addEventListener('click', () => {
      U.hideActions();
      $('game-screen').classList.add('hidden');
      $('start-screen').classList.remove('hidden');
    });
  };

  U.config = () => config;

  U.showStart = () => {
    $('start-screen').classList.remove('hidden');
    $('game-screen').classList.add('hidden');
  };
  U.hideStart = () => {
    $('start-screen').classList.add('hidden');
    $('game-screen').classList.remove('hidden');
  };

  // ------------------------------------------------------------ scoreboard
  const WIND = ['东', '南', '西', '北'];
  const LAST_SCORES = [0, 0, 0, 0];
  U.scoreboard = function (state) {
    const sb = $('scoreboard');
    let html = '';
    state.players.forEach((p) => {
      const delta = p.score - LAST_SCORES[p.seat];
      LAST_SCORES[p.seat] = p.score;
      const dHtml = delta !== 0
        ? `<span class="sb-delta ${delta < 0 ? 'neg' : ''}">${delta > 0 ? '+' : ''}${delta}</span>`
        : '';
      html += `<div class="sb-row">
        <span class="sb-name ${p.is_human ? 'you' : ''}">${p.is_dealer ? '<span class="sb-dealer">庄</span>' : ''}${p.name}</span>
        <span class="sb-score">${p.score}</span>${dHtml}</div>`;
    });
    sb.innerHTML = html;
  };

  // ------------------------------------------------------------ actions
  U.showActions = function (kind, payload) {
    const bar = $('action-bar');
    bar.classList.remove('hidden');
    const set = (id, on, label) => {
      const b = $(id);
      b.style.display = on ? '' : 'none';
      if (label) b.textContent = label;
    };
    set('act-hu', kind === 'zimo' || (kind === 'claims' && payload.has_hu), '胡');
    set('act-peng', kind === 'claims' && payload.has_peng, '碰');
    set('act-gang', kind === 'claims' && payload.has_gang, '杠');
    set('act-chi', kind === 'claims' && payload.has_chi, '吃');
    set('act-pass', kind === 'claims', '过');
    set('act-next', kind === 'round_end', '下一局');
  };
  U.hideActions = function () {
    $('action-bar').classList.add('hidden');
  };

  U.showAbsence = function () {
    $('absence-bar').classList.remove('hidden');
  };
  U.hideAbsence = function () {
    $('absence-bar').classList.add('hidden');
  };

  // ------------------------------------------------------------ banner
  U.banner = function (text, dur) {
    const b = $('banner');
    b.textContent = text;
    b.classList.remove('hidden');
    setTimeout(() => b.classList.add('hidden'), dur || 1600);
  };

  // ------------------------------------------------------------ modal
  function modal(html) {
    const m = $('modal');
    m.innerHTML = `<div class="modal">${html}
      <div style="text-align:center"><button class="m-close" id="m-close">关闭</button></div></div>`;
    m.classList.remove('hidden');
    $('m-close').addEventListener('click', () => m.classList.add('hidden'));
    return m;
  }
  U.modal = modal;
  U.closeModal = () => $('modal').classList.add('hidden');

  function showHelp(rules) {
    const items = RULE_HELP[rules] || [];
    modal(`<h2>玩法说明 · ${ {classic: '经典推倒胡', sichuan: '四川血战', guangdong: '广东鸡平胡'}[rules] }</h2>
      <ul>${items.map((s) => `<li>${s}</li>`).join('')}</ul>
      <p style="margin-top:10px">操作：点手牌直接打出；亮起的按钮（胡/碰/杠/吃/过）为当前可操作项。点击手牌时按住可预览，听牌提示会高亮你的和牌。右上角 ⚡ 可切换流畅模式（关闭光效动画以提升性能）。</p>`);
  }
  U.showHelp = showHelp;

  function showSettings() {
    modal(`<h2>设置</h2>
      <p>规则：${ {classic: '经典推倒胡', sichuan: '四川血战', guangdong: '广东鸡平胡'}[config.rules] }</p>
      <p>电脑强度：${ {easy: '易', medium: '中', hard: '难 · 蒙特卡洛'}[config.difficulty] }</p>
      <p>买马：${config.buy_ma} 只（仅广东）</p>
      <p>音效：${Sound.isEnabled() ? '开' : '关'} · 自动过牌：${$('opt-autopass').checked ? '开' : '关'} · 听牌提示：${$('opt-tip').checked ? '开' : '关'} · 流畅模式：${Scene.isPerfMode() ? '开' : '关'}</p>
      <p>出牌间隔：${($('opt-delay') || {}).value === '0' ? '关' : (($('opt-delay') || {}).value || '0') + 'ms'}</p>
      <p>要更换规则或强度，请点右上角“退出”返回主菜单。</p>`);
  }

  // ------------------------------------------------------------ sound
  function toggleSound() {
    const on = !Sound.isEnabled();
    Sound.setEnabled(on);
    $('btn-sound').textContent = on ? '♪' : '✕';
    if (on) Sound.click();
  }

  U.setSoundState = function () {
    $('btn-sound').textContent = Sound.isEnabled() ? '♪' : '✕';
  };

  // ------------------------------------------------------------ perf mode
  function togglePerf() {
    const on = !Scene.isPerfMode();
    Scene.setPerfMode(on);
    $('btn-perf').classList.toggle('active', on);
  }

  U.syncPerfState = function () {
    const on = $('opt-perf').checked;
    Scene.setPerfMode(on);
    $('btn-perf').classList.toggle('active', on);
  };

  // ------------------------------------------------------------ round info
  U.roundInfo = function (state) {
    $('round-info').textContent = `第 ${state.round} 局`;
    $('wall-info').textContent = `余 ${state.wall_remaining}`;
    $('dealer-info').textContent = `庄 ${WIND[state.dealer]}`;
  };

  // ------------------------------------------------------------ turn info
  U.turnInfo = function (text) {
    const el = $('turn-info');
    if (!text) { el.textContent = ''; return; }
    el.textContent = text;
  };

  // ------------------------------------------------------------ claim warn
  U.claimWarn = function (text) {
    const el = $('claim-warn');
    if (text) {
      el.textContent = '⚠ ' + text;
      el.classList.remove('hidden');
    } else {
      el.classList.add('hidden');
    }
  };

  // ------------------------------------------------------------ room lobby
  U.renderRoom = function (room, mySeat) {
    $('room-code').textContent = room.code;
    const meta = [];
    const rname = { classic: '经典推倒胡', sichuan: '四川血战', guangdong: '广东鸡平胡' }[room.rules] || room.rules;
    meta.push(`规则：${rname}`);
    if (room.rules === 'guangdong') meta.push(`买马 ${room.buy_ma}`);
    meta.push(`AI 强度：${ {easy: '易', medium: '中', hard: '难'}[room.difficulty] || room.difficulty }`);
    $('room-meta').textContent = meta.join(' · ');

    const list = $('seat-list');
    list.innerHTML = '';
    const winds = ['东', '南', '西', '北'];
    (room.seats || []).forEach((s, i) => {
      const div = document.createElement('div');
      div.className = 'seat-row' + (i === mySeat ? ' me' : '') + (s ? '' : ' empty');
      div.innerHTML = `<span class="seat-wind">${winds[i]}</span>
        <span class="seat-name">${s ? escapeHtml(s.name) + (s.is_host ? ' <b class="host-mark">房主</b>' : '') : '（空位 · AI 补位）'}</span>
        <span class="seat-tag">${i === mySeat ? '你' : (s ? '已加入' : '等待')}</span>`;
      list.appendChild(div);
    });

    // host control: start early with AI fills (host is always seat 0)
    const isHost = mySeat === 0;
    const btn = $('btn-room-start');
    const wait = $('room-waiting');
    const auto = $('room-auto');
    btn.style.display = 'none'; wait.style.display = 'none'; auto.style.display = 'none';
    if (room.phase === 'lobby') {
      if (isHost) {
        btn.style.display = '';
      } else {
        auto.style.display = '';
        wait.style.display = '';
      }
    }
  };

  U.showShare = function (ips, port, code) {
    const el = $('room-share');
    let html = '<div class="share-title">告诉朋友这样加入：</div><ol>';
    if (ips && ips.length) {
      html += `<li>同一 WiFi / 热点下，打开 <b>http://${ips[0]}:${port}</b></li>`;
    }
    html += `<li>输入房间码 <b class="gold">${code}</b> 和昵称，点“加入房间”</li>`;
    html += '<li>凑满 4 人自动开局；房主也可以提前开始（空位由 AI 补）</li>';
    html += '</ol><div class="share-copy">';
    if (ips && ips.length) {
      html += `<button class="btn-ghost" id="btn-copy-link" data-link="http://${ips[0]}:${port}">复制邀请链接</button>`;
    }
    html += '</div>';
    el.innerHTML = html;
    const cp = $('btn-copy-link');
    if (cp) cp.addEventListener('click', () => {
      const ok = copyText(cp.dataset.link);
      cp.textContent = ok ? '已复制 ✓' : '复制失败，请手动输入';
      if (ok) setTimeout(() => { cp.textContent = '复制邀请链接'; }, 1500);
    });
  };

  U.lanHint = function (ips, port) {
    const el = $('lan-hint');
    if (ips && ips.length) {
      el.innerHTML = `本机局域网地址：<b>http://${ips[0]}:${port}</b>（朋友浏览器打开它即可进入）`;
    } else {
      el.textContent = '未检测到局域网地址：请确认本机已连接 WiFi/热点。';
    }
  };

  // ------------------------------------------------------------ stats modal
  U.statsModal = function (analytics) {
    const per = analytics.perSeat || {};
    const rounds = analytics.rounds || [];
    let html = `<h2>战绩统计</h2>
      <table class="stats-tbl"><tr><th>玩家</th><th>局数</th><th>胡牌</th>
        <th>自摸</th><th>点炮胡</th><th>累计番</th><th>最高番</th></tr>`;
    const seats = analytics.seatNames || ['东', '南', '西', '北'];
    for (let i = 0; i < 4; i++) {
      const s = per[i] || {};
      html += `<tr><td>${escapeHtml(seats[i] || ('玩家' + (i + 1)))}</td>
        <td>${s.rounds || 0}</td><td>${s.hu || 0}</td>
        <td>${s.zimo || 0}</td><td>${s.dianpao || 0}</td>
        <td>${s.fanSum || 0}</td><td>${s.topFan || 0}</td></tr>`;
    }
    html += '</table>';
    if (rounds.length) {
      html += '<div class="stats-rounds"><h3>逐局明细</h3>';
      rounds.forEach((r, i) => {
        html += `<div class="stats-round"><b>第 ${r.round} 局</b> · 庄 ${r.dealerName || '东'} · 和牌：`;
        html += (r.wins.length
          ? r.wins.map((w) => `${escapeHtml(w.name)} ${w.zimo ? '自摸' : `点炮(${escapeHtml(w.fromName)})`} ${w.fan}番`).join('、')
          : '流局');
        html += '</div>';
        if (r.hands) {
          html += '<div class="stats-hands">';
          r.hands.forEach((h) => {
            html += `<span class="hand-chip">${escapeHtml(h.name)}：${h.tiles.map((t) => Tiles.tileShort(t)).join(' ')}</span>`;
          });
          html += '</div>';
        }
      });
      html += '</div>';
    }
    modal(html);
  };

  // ------------------------------------------------------------ helpers
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
  U.escapeHtml = escapeHtml;

  function copyText(t) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t);
        return true;
      }
      const ta = document.createElement('textarea');
      ta.value = t; document.body.appendChild(ta);
      ta.select(); document.execCommand('copy');
      document.body.removeChild(ta);
      return true;
    } catch (e) { return false; }
  }
  U.copyText = copyText;

  return U;
})();
