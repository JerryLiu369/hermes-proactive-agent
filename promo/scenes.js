/* Every frame is a pure function of time: render(t) sets all styles from t alone. */
const TL = window.TIMELINE;
const stage = document.getElementById('stage');
/* ?v=1 lays the same scenes out on a 1080x1920 canvas; LV(landscape, vertical) picks a value per orientation */
const V = new URLSearchParams(location.search).has('v');
document.documentElement.classList.toggle('v', V);
const LV = (l, v) => (V ? v : l);
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, k) => a + (b - a) * k;
const E = {
  lin: x => x,
  out: x => 1 - Math.pow(1 - x, 3),
  inOut: x => (x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  back: x => { const c = 1.6; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); },
};
const P = (t, a, d = .5, e = E.out) => e(clamp((t - a) / d));

function h(tag, cls, html, parent, css) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (html != null) el.innerHTML = html;
  if (css) el.style.cssText += css;
  (parent || stage).appendChild(el);
  return el;
}
function at(el, x, y, w, hh) {
  el.style.left = x + 'px'; el.style.top = y + 'px';
  if (w != null) el.style.width = w + 'px';
  if (hh != null) el.style.height = hh + 'px';
  return el;
}
function tr(el, { o = 1, x = 0, y = 0, s = 1, r = 0 } = {}) {
  el.style.opacity = o;
  el.style.transform = `translate(${x}px,${y}px) scale(${s}) rotate(${r}deg)`;
}
/* in at a, out at b; hidden (display:none) outside, so flex children take no space before they appear */
function vis(el, t, a, b = Infinity, o = {}) {
  const { d = .45, dy = 18, s0 = .97, dout = .35, yout = -14, e = E.out, r = 0 } = o;
  if (el._d === undefined) el._d = el.style.display === 'none' ? '' : el.style.display;
  if (t < a || t > b + dout) { el.style.display = 'none'; return 0; }
  el.style.display = el._d;
  const ki = P(t, a, d, e), ko = P(t, b, dout, E.inOut);
  tr(el, { o: ki * (1 - ko), y: (1 - ki) * dy + ko * yout, s: s0 + (1 - s0) * ki, r });
  return ki * (1 - ko);
}
/* a full-frame group drawn in landscape coordinates; in the vertical cut it is moved (and scaled) as one piece */
function block(parent, [dx, dy, sc = 1]) {
  const el = h('div', 'abs', null, parent, 'left:0;top:0;width:1920px;height:1080px;transform-origin:0 0');
  if (V) el.style.transform = `translate(${dx}px,${dy}px) scale(${sc})`;
  return el;
}
const typed = (s, t, a, cps) => [...s].slice(0, Math.max(0, Math.floor((t - a) * cps))).join('');
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
function setHTML(el, html) { if (el._h !== html) { el.innerHTML = html; el._h = html; } }
function setText(el, s) { if (el._t !== s) { el.textContent = s; el._t = s; } }

const CUES = [];
const scenes = [];
function scene(id, opts, build) {
  const sc = TL.scenes.find(s => s.id === id);
  const root = h('div', 'scene');
  const L = lid => sc.lines[lid].start;
  const D = lid => sc.lines[lid].dur;
  /* time inside line lid where `word` begins, by character position */
  const W = (lid, word) => { const tx = sc.lines[lid].text; return L(lid) + D(lid) * Math.max(0, tx.indexOf(word)) / tx.length; };
  const cue = (rel, k, g = 1) => CUES.push({ t: +(sc.start + rel).toFixed(3), k, g });
  const update = build(root, { L, D, W, cue, sc });
  scenes.push({ sc, root, update, fin: opts.fin ?? .5, fout: opts.fout ?? .45 });
}

/* ------------------------------------------------------------ shared pieces */
const ICON = {
  bubble: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"><path d="M10 14h44v28H30l-12 10v-10h-8z"/><circle cx="23" cy="28" r="2.5" fill="currentColor"/><circle cx="32" cy="28" r="2.5" fill="currentColor"/><circle cx="41" cy="28" r="2.5" fill="currentColor"/></svg>`,
  eye: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M8 28c7 10 15 14 24 14s17-4 24-14"/><path d="M16 38l-5 7M26 41l-2 8M38 41l2 8M48 38l5 7"/></svg>`,
  loop: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M50 32a18 18 0 1 1-6-13.4"/><path d="M45 8v11H34"/></svg>`,
  claim: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M8 34l10 10 18-22"/><path d="M42 20c0-5 4-8 8-8s8 3 8 7c0 6-8 6-8 12"/><circle cx="50" cy="44" r="1.8" fill="currentColor"/></svg>`,
  lost: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"><rect x="9" y="16" width="46" height="32" rx="3"/><path d="M9 18l23 17 23-17"/><path d="M6 54L58 10" stroke="#C8412A"/></svg>`,
  power: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M32 10v20"/><path d="M20 18a18 18 0 1 0 24 0"/></svg>`,
};

function chapter(root, num, lines, file) {
  const n = h('div', 'chap-num', num, root);
  const ti = h('div', 'chap-title', lines.map(l => `<div>${l}</div>`).join(''), root);
  const f = h('div', 'chap-file', V ? file.replace('<br>super-agent/', ' · ') : file, root);
  if (V) {
    n.style.cssText += 'left:52px;top:276px;font-size:150px';
    ti.style.cssText += 'left:286px;top:290px;font-size:58px;line-height:1.24';
    f.style.cssText += `left:288px;top:${290 + lines.length * 72 + 8}px;border-top:none;padding-top:0;font-size:22px`;
  } else if (lines.length > 2) f.style.top = '764px';
  return lt => {
    tr(n, { o: P(lt, .05, .8), y: (1 - P(lt, .05, .8)) * 40 });
    [...ti.children].forEach((c, i) => { const k = P(lt, .2 + i * .12, .6); tr(c, { o: k, y: (1 - k) * 30 }); });
    const k = P(lt, .6, .5); tr(f, { o: k, y: (1 - k) * 12 });
  };
}

function chatCard(root, x, y, w, hh, status = '在线') {
  const c = at(h('div', 'card chat', `<div class="hd"><div class="av"></div><div><div class="nm">助手</div>` +
    `<div class="st"><b></b><span>${status}</span></div></div></div><div class="bd"></div>`, root), x, y, w, hh);
  return { el: c, bd: c.querySelector('.bd'), st: c.querySelector('.st span') };
}
const msg = (bd, cls, html) => h('div', 'msg ' + cls, html, bd);

/* ------------------------------------------------------------ 0. opener: brand card, also the first-frame thumbnail */
scene('open', { fin: 0, fout: .5 }, (root, { cue, sc }) => {
  const g = at(h('div', 'abs', null, root, 'left:0;right:0;text-align:center'), 0, LV(300, 600));
  h('div', 'mono', 'HERMES AGENT PLUGIN · OPEN SOURCE', g, `font:700 ${LV(26, 24)}px var(--mono);letter-spacing:.3em;color:var(--muted)`);
  h('div', 'serif', LV('Hermes Super Agent', 'Hermes<br>Super Agent'), g, `font:700 ${LV('128px/1.25', '118px/1.14')} var(--serif);margin-top:${LV(16, 26)}px`);
  h('div', 'serif', '让聊天里的助手，像秘书一样做事', g, `font:500 ${LV(50, 48)}px var(--serif);color:var(--ink2);margin-top:${LV(10, 26)}px`);
  h('div', 'mono', '<b style="color:var(--amber);margin-right:12px">★</b>助手在线 · 子代理干活', g,
    `display:inline-block;margin-top:46px;font:500 ${LV(30, 26)}px var(--mono);padding:14px 30px;border-radius:999px;border:2px solid var(--ink)`);
  cue(.15, 'ok', .6);
  return lt => { tr(g, { s: 1 + .012 * lt / sc.dur }); };
});

/* ------------------------------------------------------------ 1. cold open */
scene('cold', { fin: .3, fout: .5 }, (root, { L, D, cue, sc }) => {
  root.classList.add('night'); h('div', 'bg', null, root);
  const chat = chatCard(block(root, [-100, 170]), 300, 150, 680, 660);
  const m1 = msg(chat.bd, 'u', '帮我构建项目，再查查测试为啥挂了。');
  const m2 = msg(chat.bd, 'a', '好的，我看看。');
  const m3 = msg(chat.bd, 'u', '还在吗？');
  const ty = h('div', 'typing', '<i></i><i></i><i></i>', chat.bd);
  const clk = at(h('div', 'abs', null, root, V ? 'text-align:center' : ''), LV(1100, 220), LV(300, 1025), 640);
  const lb = h('div', '', '等待回复', clk, 'font:500 32px var(--sans);color:var(--muted);letter-spacing:.08em');
  const v = h('div', 'serif', '0:00', clk, `font:700 ${LV(240, 200)}px/1.08 var(--serif);font-variant-numeric:tabular-nums;letter-spacing:-.02em`);
  const sub = h('div', '', '聊天里，没有任何回音。', clk, 'font:400 34px var(--sans);color:var(--ink2);margin-top:10px');
  const tM1 = .45, tM2 = L('c2') + .15, T0 = L('c2') + .75, T1 = sc.dur - 1.15;
  /* the nudge lands as the counter passes five minutes */
  const tM3 = T0 + (T1 - T0) * Math.log(1 + .5 * (Math.exp(3.2) - 1)) / 3.2;
  cue(tM1, 'pop'); cue(tM2, 'pop', .8); cue(tM3, 'pop', .8);
  let last = -1;
  for (let lt = T0; lt < T1; lt += 1 / 30) {
    const u = clamp((lt - T0) / (T1 - T0)), s = Math.floor(600 * (Math.exp(3.2 * u) - 1) / (Math.exp(3.2) - 1));
    const m = Math.floor(s / 60); if (m !== last && m > 0) { cue(lt, 'tick', .5 + .05 * m); last = m; }
  }
  return lt => {
    vis(chat.el, lt, 0, Infinity, { d: .6, dy: 24 });
    chat.el.style.transform += ` translateX(${LV(320, 0) * (1 - P(lt, T0 - .5, .9, E.inOut))}px)`;
    vis(m1, lt, tM1, Infinity, { d: .4, s0: .85, e: E.back });
    vis(m2, lt, tM2, Infinity, { d: .4, s0: .85, e: E.back });
    vis(m3, lt, tM3, Infinity, { d: .4, s0: .85, e: E.back });
    vis(ty, lt, tM2 + .5, Infinity, { d: .35 });
    if (lt >= tM3) ty.style.transform += ` translateY(${-84 * (1 - P(lt, tM3, .3))}px)`;
    [...ty.children].forEach((d, i) => { d.style.opacity = .3 + .7 * Math.max(0, Math.sin(lt * 6 - i * .9)); });
    setText(chat.st, lt > tM2 + .5 ? '正在输入…' : '在线');
    const u = clamp((lt - T0) / (T1 - T0)), s = 600 * (Math.exp(3.2 * u) - 1) / (Math.exp(3.2) - 1);
    setText(v, `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`);
    v.style.color = s >= 120 ? 'var(--red)' : s >= 60 ? 'var(--amber)' : 'var(--ink)';
    const kc = P(lt, T0 - .3, .6); tr(clk, { o: kc, y: (1 - kc) * 20 });
    tr(lb, { o: 1 }); tr(sub, { o: P(lt, T1 - .5, .6) });
  };
});

/* ------------------------------------------------------------ 2. six problems */
scene('problems', { fin: .45, fout: 0 }, (root, { L, cue, sc }) => {
  root.classList.add('night'); h('div', 'bg', null, root);
  const head = at(h('div', 'abs', '在聊天软件里用 Hermes 派活，常会遇到六件糟心事', root,
    'font:500 30px var(--sans);color:var(--ink2);letter-spacing:.04em'), LV(174, 36), LV(112, 302));
  const data = [
    ['01', '聊天冷场', '助手亲自跑长活，十分钟没有回音', ICON.bubble],
    ['02', '两眼一抹黑', '子代理看不到聊天，也没有记忆', ICON.eye],
    ['03', '原地打转', '同一个报错的命令，翻来覆去地跑', ICON.loop],
    ['04', '全凭自报', '说“做完了”，却拿不出证据', ICON.claim],
    ['05', '结果漏报', '后台结果恰好撞上你的新消息', ICON.lost],
    ['06', '一重启全丢', '查到什么、改过什么，无从查起', ICON.power],
  ];
  const cards = data.map(([n, t, d, ic], i) => {
    const c = at(h('div', 'card', `<div class="mono" style="position:absolute;left:36px;top:32px;font:700 24px var(--mono);color:var(--muted)">${n}</div>` +
      `<div style="position:absolute;right:30px;top:24px;width:68px;height:68px">${ic}</div>` +
      `<div class="serif" style="position:absolute;left:36px;top:118px;font:700 56px/1.2 var(--serif)">${t}</div>` +
      `<div style="position:absolute;left:36px;right:30px;top:204px;font:400 27px/1.55 var(--sans);color:var(--ink2)">${d}</div>`, root),
      LV(174 + (i % 3) * 536, 30 + (i % 2) * 520), LV(176 + Math.floor(i / 3) * 336, 362 + Math.floor(i / 2) * 322), 500, 300);
    return c;
  });
  const times = [.1, ...[1, 2, 3, 4, 5].map(i => L('p' + i) - .05)];
  times.forEach((t, i) => cue(t, 'pop', .7));
  const allOn = L('p5') + 1.6;
  /* the lights come on: a disc of paper opens from the centre and the title scene starts on it */
  const dawn = h('div', 'abs', null, root, 'left:50%;top:46%;width:2700px;height:2700px;margin:-1350px 0 0 -1350px;border-radius:50%;' +
    'background:#F2EEE5;box-shadow:0 0 140px 70px rgba(242,238,229,.5)');
  cue(sc.dur - .75, 'whoosh', .5);
  return lt => {
    const kd = P(lt, sc.dur - .8, .78, E.inOut);
    dawn.style.display = kd ? '' : 'none'; dawn.style.transform = `scale(${kd})`;
    const kh = P(lt, 0, .5); tr(head, { o: kh });
    cards.forEach((c, i) => {
      const k = vis(c, lt, times[i], Infinity, { d: .55, dy: 30, s0: .95 });
      const newer = i < 5 && lt >= times[i + 1];
      const dim = newer && lt < allOn ? .45 : 1;
      c.style.opacity = k * lerp(dim, 1, P(lt, allOn, .5));
      c.style.borderColor = !newer && lt < allOn ? 'var(--ink)' : 'var(--line)';
    });
  };
});

/* ------------------------------------------------------------ 3. title + secretary diagram */
scene('title', { fin: .6, fout: .5 }, (root, { L, D, W, cue }) => {
  const g = at(h('div', 'abs', null, root, 'left:0;right:0;text-align:center;transform-origin:50% 0'), 0, LV(300, 540));
  const kick = h('div', 'mono', 'HERMES AGENT PLUGIN', g, 'font:700 26px var(--mono);letter-spacing:.32em;color:var(--muted)');
  const name = h('div', 'serif', ['Hermes', 'Super', 'Agent'].map(w => `<span style="display:inline-block;margin:0 .14em">${w}</span>`).join(''), g,
    `font:700 ${LV('132px/1.25', '118px/1.14')} var(--serif);letter-spacing:-.01em;margin-top:${LV(14, 24)}px`);
  if (V) name.children[0].insertAdjacentHTML('afterend', '<br>');
  const tag = h('div', 'serif', '让聊天里的助手，像一位靠谱的秘书那样做事', g, `font:500 ${LV(52, 46)}px var(--serif);color:var(--ink2);margin-top:${LV(14, 24)}px`);
  const svgNS = 'http://www.w3.org/2000/svg';
  const dg = block(root, [-409, 340, .92]);
  const Y = 690, YOU = [560, Y], AS = [960, Y], SUBS = [[1390, 570], [1390, 700], [1390, 830]];
  dg.innerHTML = `<svg width="1920" height="1080" style="position:absolute;left:0;top:0">
    <line x1="616" y1="${Y}" x2="884" y2="${Y}" stroke="#17171B" stroke-width="2.5"/>
    ${SUBS.map(([x, y]) => `<path class="flow" d="M1036 ${Y} C 1180 ${Y}, 1220 ${y}, ${x - 48} ${y}" stroke="#2F5DA8" stroke-width="2.5" fill="none" stroke-dasharray="7 9"/>`).join('')}
    ${[0, 1, 2].map(() => `<circle class="dot" r="6" fill="#17171B"/>`).join('')}
    ${SUBS.map(([x, y]) => `<circle class="spin" cx="${x}" cy="${y}" r="58" fill="none" stroke="#2F5DA8" stroke-width="4" stroke-linecap="round" stroke-dasharray="70 295"/>`).join('')}
  </svg>`;
  const node = (x, y, r, html, css) => at(h('div', 'abs', html, dg,
    `border-radius:50%;display:flex;align-items:center;justify-content:center;${css}`), x - r, y - r, 2 * r, 2 * r);
  const nYou = node(...YOU, 56, '你', 'border:3px solid var(--ink);background:var(--card);font:700 40px var(--serif)');
  const nAs = node(...AS, 78, '助手', 'background:var(--ink);color:var(--paper);font:700 40px var(--serif)');
  const live = at(h('div', 'abs', null, dg, 'border-radius:50%;background:var(--green);border:5px solid var(--paper)'), AS[0] + 44, AS[1] - 74, 30, 30);
  const nSubs = SUBS.map(([x, y]) => node(x, y, 46, '', 'border:3px solid var(--blue);background:var(--card)'));
  const lab = (x, y, html, css = '') => at(h('div', 'abs', html, dg, `text-align:center;font:500 26px var(--sans);color:var(--ink2);${css}`), x - 160, y, 320);
  const lAs = lab(AS[0], Y + 98, '随时在线', 'color:var(--ink);font-weight:700');
  const subJobs = ['跑构建', '查日志', '改代码'];
  const lSubs = SUBS.map(([x, y], i) => at(h('div', 'abs', subJobs[i], dg, 'font:500 26px var(--sans);color:var(--ink2)'), x + 66, y - 18));
  const lGrp = at(h('div', 'abs', '后台子代理 · 干慢活', dg, 'font:700 26px var(--sans);color:var(--blue)'), 1330, 480);
  const flows = [...dg.querySelectorAll('.flow')], dots = [...dg.querySelectorAll('.dot')], spins = [...dg.querySelectorAll('.spin')];
  const t2 = L('t2') - .35, tSub = W('t2', '子代理') - .1;
  cue(t2 + .2, 'whoosh', .6); cue(tSub, 'pop', .6); cue(W('t2', '随时在线'), 'ok', .6);
  return lt => {
    tr(kick, { o: P(lt, .15, .6), y: (1 - P(lt, .15, .6)) * 16 });
    [...name.querySelectorAll('span')].forEach((s, i) => { const k = P(lt, .3 + i * .14, .7); tr(s, { o: k, y: (1 - k) * 40 }); });
    const kt = P(lt, W('t1', '让'), .7); tr(tag, { o: kt, y: (1 - kt) * 20 });
    const k2 = P(lt, t2, 1.0, E.inOut); tr(g, { y: LV(-196, -210) * k2, s: 1 - LV(.24, .2) * k2 });
    dg.style.opacity = P(lt, t2 + .2, .6);
    vis(nYou, lt, t2 + .25, Infinity, { s0: .7, e: E.back, dy: 0 });
    vis(nAs, lt, t2 + .4, Infinity, { s0: .7, e: E.back, dy: 0 });
    vis(live, lt, W('t2', '随时在线'), Infinity, { s0: .2, e: E.back, dy: 0 }); vis(lAs, lt, W('t2', '随时在线'));
    nSubs.forEach((n, i) => vis(n, lt, tSub + i * .12, Infinity, { s0: .7, e: E.back, dy: 0 }));
    lSubs.forEach((n, i) => vis(n, lt, tSub + .2 + i * .12));
    vis(lGrp, lt, tSub);
    flows.forEach((f, i) => { f.style.strokeDashoffset = -lt * 36; f.style.opacity = P(lt, tSub + i * .12, .5); });
    spins.forEach((c, i) => { const [x, y] = SUBS[i]; c.setAttribute('transform', `rotate(${lt * 220 + i * 100} ${x} ${y})`); c.style.opacity = P(lt, tSub + .2 + i * .12, .5); });
    dots.forEach((d, i) => {
      const u = ((lt - t2) * .55 + i / 3) % 1, fwd = i % 2 === 0;
      d.setAttribute('cx', lerp(624, 876, fwd ? u : 1 - u)); d.setAttribute('cy', Y);
      d.style.opacity = P(lt, t2 + .6, .5) * Math.sin(Math.PI * u);
    });
  };
});

/* ------------------------------------------------------------ 4. chapter 1: responsive */
scene('ch1', {}, (root, { L, D, W, cue }) => {
  const intro = chapter(root, '01', ['聊天始终', '保持响应'], 'super-agent/secretary.py');
  const st = block(root, [-720, 452]);
  const gA = h('div', 'abs', null, st, 'left:0;top:0;width:1920px;height:1080px');
  const lab = at(h('div', 'abs', '一轮对话的时间', gA, 'font:500 30px var(--sans);color:var(--ink2)'), 760, 222);
  const read = at(h('div', 'abs mono', '0s', gA, 'font:700 70px var(--mono);text-align:right'), 1460, 186, 300);
  const X0 = 760, PX = 1000 / 150, BY = 300;
  const bar = at(h('div', 'abs', null, gA, 'background:#E3DCCD;border-radius:9px;overflow:hidden'), X0, BY, 1000, 18);
  at(h('div', 'abs', null, bar, 'background:var(--amber-l)'), 60 * PX, 0, 60 * PX, 18);
  at(h('div', 'abs', null, bar, 'background:var(--red-l)'), 120 * PX, 0, 30 * PX, 18);
  const fills = ['#2B2A2F', 'var(--amber)', 'var(--red)'].map(c => at(h('div', 'abs', null, bar, `background:${c}`), 0, 0, 0, 18));
  const head = at(h('div', 'abs', null, gA, 'border-radius:50%;border:5px solid var(--paper);box-shadow:0 2px 8px rgba(0,0,0,.2)'), X0 - 17, BY - 8, 34, 34);
  [0, 30, 60, 90, 120, 150].forEach(s => {
    const c = s === 60 ? 'var(--amber)' : s === 120 ? 'var(--red)' : 'var(--muted)';
    at(h('div', 'abs mono', s + 's', gA, `text-align:center;font:${s % 60 === 0 && s ? 700 : 500} 22px var(--mono);color:${c}`), X0 + s * PX - 50, BY + 32, 100);
  });
  const n1 = at(h('div', 'note', `<h4><span class="pill" style="background:var(--amber-l);color:var(--amber)">60s</span>提醒：一两步之内先回复你，或把剩下的活派出去</h4>` +
    `<p>[super-agent] This turn has run for 60s. Within the next 1-2 steps, answer the user or hand the remaining work to delegate_task. Editing and execution tools pause at 120s.</p>`, gA), X0, 420, 1000);
  n1.style.setProperty('--c', 'var(--amber)');
  const chips = (names, cls) => names.map(n => `<span class="chip ${cls}" style="margin:0 10px 12px 0">${n}</span>`).join('');
  const n2 = at(h('div', 'note', `<h4><span class="pill" style="background:var(--red-l);color:var(--red)">120s</span>本轮暂停：不再改文件、跑命令</h4>` +
    `<div style="display:flex;gap:18px;margin-top:6px"><div style="flex:none;width:64px;font:700 24px/46px var(--sans);color:var(--red)">暂停</div><div>${chips(['terminal', 'write_file', 'patch'], 'pz')}</div></div>` +
    `<div style="display:flex;gap:18px"><div style="flex:none;width:64px;font:700 24px/46px var(--sans);color:var(--ink)">照常</div><div>${chips(['read_file', 'search_files', 'vision_analyze', 'delegate_task', 'memory', 'todo_list', 'clarify', 'process_manage · list / poll / log / kill'], 'ok')}</div></div>`, gA), X0, 420, 1000);
  n2.style.setProperty('--c', 'var(--red)');
  const paused = [...n2.querySelectorAll('.pz')];

  const gB = h('div', 'abs', null, st, 'left:0;top:0;width:1920px;height:1080px');
  const lane = at(h('div', 'abs', `<div style="position:absolute;left:24px;top:18px;font:700 24px var(--sans);color:var(--green)">后台 · 仍在运行</div>`, gB,
    'border:2.5px dashed #9DBFA8;border-radius:22px;background:rgba(214,232,217,.35)'), 1430, 200, 330, 400);
  const term = at(h('div', 'term', `<div class="tb"><i></i><i></i><i></i><span>00:00</span></div><pre></pre>`, gB, 'transform-origin:50% 50%'), X0, 220, 640, 380);
  const tclock = term.querySelector('.tb span'), tpre = term.querySelector('pre');
  const tlines = ['<span class="dim">~/app $</span> npm run build', '<span class="dim">&gt;</span> vite build',
    'transforming (1824) modules…', '<span class="y">rendering chunks…</span>'];
  const spin = at(h('div', 'abs', null, gB, 'border-radius:50%;border:4px solid #9DBFA8;border-top-color:var(--green)'), 1700, 214, 34, 34);
  const n3 = at(h('div', 'note', `<h4><span class="pill" style="background:var(--green-l);color:var(--green)">30s</span>仍在运行，并非失败</h4>` +
    `<p><span style="color:var(--green);font-weight:700">"status": "yielded_to_background"</span><br>[super-agent] Still running: this command took longer than 30s, so it moved to the background. It has NOT failed. You will be notified automatically when it finishes.</p>`, gB), X0, 220, 640);
  n3.style.setProperty('--c', 'var(--green)');
  const reply = at(h('div', 'abs', null, gB, 'display:flex;gap:16px;align-items:flex-end'), X0, 660);
  h('div', '', '<i style="position:absolute;left:17px;top:17px;width:18px;height:18px;border-radius:50%;background:var(--paper)"></i>', reply, 'width:52px;height:52px;border-radius:50%;background:var(--ink);flex:none;position:relative');
  const rb = h('div', 'msg a', '构建转到后台了，跑完我告诉你。你先说别的也行。', reply, 'max-width:none');

  const a1 = L('a1'), a2 = L('a2'), a3 = L('a3'), a4 = L('a4');
  const T60 = a2 + .9, T120 = a3 + 1.0, TY = a4 + 2.3;
  cue(T60, 'warn', .7); cue(T120, 'stop', .8); cue(a4 - .05, 'whoosh', .5); cue(TY, 'ok', .8); cue(a4 + 3.3, 'pop');
  const sec = lt => {
    if (lt < a1) return 0;
    if (lt < a2) return 45 * P(lt, a1, a2 - a1 - .1, E.inOut);
    if (lt < T60) return lerp(45, 60, P(lt, a2, T60 - a2, E.inOut));
    if (lt < a3) return lerp(60, 68, (lt - T60) / (a3 - T60));
    if (lt < T120) return lerp(68, 120, P(lt, a3, T120 - a3, E.inOut));
    return Math.min(150, 120 + (lt - T120) * 2);
  };
  return lt => {
    intro(lt);
    const s = sec(lt), out = P(lt, a4 - .5, .4, E.inOut);
    tr(gA, { o: P(lt, a1 - .5, .6) * (1 - out), y: 48 - 30 * out });
    setText(read, Math.floor(s) + 's');
    const col = s >= 120 ? 'var(--red)' : s >= 60 ? 'var(--amber)' : 'var(--ink)';
    read.style.color = col; head.style.background = col;
    head.style.left = (X0 + s * PX - 17) + 'px';
    fills[0].style.width = Math.min(s, 60) * PX + 'px';
    at(fills[1], 60 * PX, 0, clamp(s - 60, 0, 60) * PX); at(fills[2], 120 * PX, 0, clamp(s - 120, 0, 30) * PX);
    vis(n1, lt, T60, T120 - .2, { dy: 24 });
    vis(n2, lt, T120 + .1, Infinity, { dy: 24 });
    paused.forEach((c, i) => {
      const k = P(lt, T120 + .4 + i * .12, .3);
      c.style.borderColor = k > .5 ? 'var(--red)' : 'var(--ink)'; c.style.color = k > .5 ? 'var(--red)' : 'var(--ink)';
      c.style.textDecoration = k > .5 ? 'line-through' : 'none'; c.style.opacity = 1 - .45 * k;
    });
    gB.style.opacity = P(lt, a4 - .22, .4);
    const el = 30 * P(lt, a4 + .1, TY - a4 - .2, E.lin);
    setText(tclock, `00:${String(Math.floor(el)).padStart(2, '0')}`);
    setHTML(tpre, tlines.filter((_, i) => lt >= a4 + [0, .35, .9, 1.5][i]).join('\n'));
    const ky = P(lt, TY, .8, E.inOut);
    tr(term, { x: 520 * ky, y: 30 * ky, s: 1 - .5 * ky });
    tr(lane, { o: P(lt, TY - .1, .5) });
    tr(spin, { o: P(lt, TY + .3, .4), r: lt * 400 });
    vis(n3, lt, TY + .45, Infinity, { dy: 24 });
    vis(reply, lt, a4 + 3.3, Infinity, { dy: 20, s0: .9, e: E.back });
    tr(rb, {});
  };
});

/* ------------------------------------------------------------ 5. chapter 2: informed handoff */
scene('ch2', {}, (root, { L, D, W, cue }) => {
  const intro = chapter(root, '02', ['子代理出发前', '心里有数'], 'super-agent/handoff.py');
  const st = block(root, [-720, 452]);
  const X0 = 760;
  const card = at(h('div', 'card', null, st, 'padding:28px 34px'), X0, 128, 1000, 372);
  card.innerHTML = `<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">
      <span class="mono" style="font:700 28px var(--mono)">delegate_task</span>
      <span class="pill" style="background:var(--paper2);color:var(--ink2)">派活单</span>
      <span class="verdict pill" style="margin-left:auto"></span></div>
    <div style="display:flex;gap:20px;margin-bottom:14px"><span class="mono" style="flex:none;width:110px;font:500 22px/38px var(--mono);color:var(--muted)">goal</span>
      <span style="font:700 28px/38px var(--sans)">修复登录接口的测试失败</span></div>
    <div style="display:flex;gap:20px"><span class="mono" style="flex:none;width:110px;font:500 22px/36px var(--mono);color:var(--muted)">context</span>
      <div class="ctx" style="font:400 24px/36px var(--sans);color:var(--ink);min-height:180px"></div></div>
    <div class="cnt mono" style="position:absolute;right:30px;bottom:20px;font:700 22px var(--mono)"></div>`;
  const ctx = card.querySelector('.ctx'), cnt = card.querySelector('.cnt'), verdict = card.querySelector('.verdict');
  const SHORT = '修一下测试';
  const LONG = '登录接口的 3 个测试在 token 过期分支报 KeyError: \'exp\'，已排除环境问题。只改 src/auth/token.py，让测试通过；不要动 config/prod.yaml 和数据库。改完跑 pytest tests/auth，全部通过并附上输出即算完成；同样的错两次就停下来汇报。';
  const why = at(h('div', 'abs', 'context 太短：不足 120 字符', st,
    'font:700 24px var(--sans);color:var(--red);background:var(--red-l);padding:8px 18px;border-radius:12px'), X0 + 160, 400);
  const stamp = at(h('div', 'abs serif', '退回一次', st,
    'font:700 64px var(--serif);color:var(--red);border:6px solid var(--red);border-radius:14px;padding:4px 26px;letter-spacing:.08em;background:rgba(251,249,244,.6)'), 1400, 250);
  const ahead = at(h('div', 'abs mono', '+ 自动附上的背景材料', st, 'font:700 24px var(--mono);color:var(--ink2)'), X0, 530);
  const items = [
    ['“', '你最近 6 条消息的原话', '“别动 config/prod.yaml”'],
    ['M', 'USER.md · MEMORY.md', '用户与环境记忆，整份附上'],
    ['◎', '正在运行的子代理', '以及它们正在改的文件'],
    ['↺', '未完成任务的记录', '查到什么、卡在哪、改过哪些文件'],
    ['§', '做事规矩', '同样的错两次就停 · 不瞎猜 · 不绕路'],
    ['{}', '回报格式', 'status · summary · evidence · blockers'],
  ].map(([ic, t, d], i) => at(h('div', 'card', `<div class="serif" style="position:absolute;left:20px;top:20px;width:52px;height:52px;border-radius:14px;background:var(--ink);color:var(--paper);display:flex;align-items:center;justify-content:center;font:700 26px var(--serif)">${ic}</div>` +
    `<div style="position:absolute;left:90px;right:14px;top:13px;font:700 25px/1.35 var(--sans);white-space:nowrap">${t}</div>` +
    `<div style="position:absolute;left:90px;right:14px;top:50px;font:400 20px/1.45 var(--sans);color:var(--ink2);white-space:nowrap">${d}</div>`, st, 'border-radius:18px'),
    X0 + (i % 2) * 508, 570 + Math.floor(i / 2) * 104, 492, 92));
  const b1 = L('b1'), b2 = L('b2');
  const tShort = b1 + .25, tWhy = W('b1', '交代太单薄'), tStamp = W('b1', '退回一次') - .1, tLong = b2 - .2, tPass = tLong + 1.3;
  const itemT = [W('b3', '你最近'), W('b3', '你的记忆'), W('b3', '正在运行'), W('b3', '上一个'), W('b4', '做事规矩'), W('b4', '统一的回报')].map(x => x - .1);
  cue(tStamp, 'stamp'); cue(tPass, 'ok', .7); itemT.forEach(t => cue(t, 'pop', .6));
  return lt => {
    intro(lt);
    vis(card, lt, .7, Infinity, { dy: 24 });
    const longPhase = lt >= tLong;
    const text = longPhase ? typed(LONG, lt, tLong, 120) : typed(SHORT, lt, tShort, 14);
    const caret = (lt * 2) % 1 < .5 && lt > tShort - .3 && lt < tPass + .4 ? '<span style="color:var(--blue)">▍</span>' : '';
    setHTML(ctx, esc(text) + caret);
    const n = longPhase ? text.length : text.length;
    const ok = longPhase && n >= 120;
    setHTML(cnt, lt > tShort ? `<span style="color:${ok ? 'var(--green)' : lt > tWhy && !longPhase ? 'var(--red)' : 'var(--muted)'}">${n}</span><span style="color:var(--muted)"> / 120 字符${ok ? ' ✓' : ''}</span>` : '');
    if (lt >= tPass) { setText(verdict, '放行'); verdict.style.cssText += ';background:var(--green-l);color:var(--green)'; }
    else if (lt >= tStamp && lt < tLong) { setText(verdict, '退回一次'); verdict.style.cssText += ';background:var(--red-l);color:var(--red)'; }
    else setText(verdict, '');
    verdict.style.opacity = verdict._t ? 1 : 0;
    vis(why, lt, tWhy, tLong - .3, { dy: 10 });
    const ks = vis(stamp, lt, tStamp, tLong - .3, { d: .28, dy: 0, s0: 1.8, e: E.out, r: -8 });
    if (ks) tr(stamp, { o: ks, s: lerp(1.8, 1, P(lt, tStamp, .28)), r: -8 });
    vis(ahead, lt, W('b2', '背景材料'));
    items.forEach((it, i) => vis(it, lt, itemT[i], Infinity, { dy: 26, s0: .92, e: E.back }));
  };
});

/* ------------------------------------------------------------ 6. chapter 3: no spinning, no guessing */
scene('ch3', {}, (root, { L, D, W, cue }) => {
  const intro = chapter(root, '03', ['不空转', '不瞎猜'], 'super-agent/workers.py');
  const st = block(root, [-720, 452]);
  const X0 = 760;
  const gA = h('div', 'abs', null, st, 'left:0;top:0;width:1920px;height:1080px');
  const log = at(h('div', 'card', `<div style="display:flex;align-items:center;gap:14px;height:62px;padding:0 26px;border-bottom:1.5px solid var(--line)">
      <span style="font:700 24px var(--sans)">子代理的工具调用</span><span class="mono" style="font:500 20px var(--mono);color:var(--muted)">sa-1-7f3a</span></div><div class="rows" style="padding:8px 26px"></div>`, gA, 'overflow:hidden'), X0, 128, 740, 470);
  const rowsEl = log.querySelector('.rows');
  /* the stop note rides on the fifth result, so the log ends there */
  const rows = [1, 2, 3, 4, 5].map(i => h('div', '',
    `<span style="color:var(--muted)">#${i}</span>&nbsp; <b>terminal</b>&nbsp; pytest tests/test_sync.py<br><span style="color:var(--red);opacity:.85">&nbsp;&nbsp;&nbsp; → ConnectionError: db:5432 refused</span>`,
    rowsEl, 'font:400 21px/1.5 var(--mono);padding:6px 0;border-bottom:1px dashed var(--line)'));
  const cntBox = at(h('div', 'abs', null, gA, 'text-align:center'), 1540, 150, 220);
  const cl = h('div', '', '同一步骤<br>同一结果', cntBox, 'font:500 24px/1.4 var(--sans);color:var(--muted)');
  const cn = h('div', 'serif', '0', cntBox, 'font:700 230px/1.05 var(--serif);font-variant-numeric:tabular-nums');
  const ctag = h('div', 'pill', '', cntBox, 'font-size:24px;padding:6px 18px');
  const nW = at(h('div', 'note', `<h4>第 3 次：换个思路，或上报阻碍</h4><p>[super-agent] You ran the same step 3 times with the same result: terminal(pytest tests/test_sync.py) -> "ConnectionError…". Change approach or report the blocker; at 5 repeats you will be stopped.</p>`, gA), X0, 626, 1000);
  nW.style.setProperty('--c', 'var(--amber)');
  const nS = at(h('div', 'note', `<h4>第 5 次：叫停，只能以 blocked 交卷</h4><p>[super-agent] STOP: ran the same step 5 times with the same result. Do not call more tools. Write your final report now: status "blocked", what you tried, the exact blocker, and what you need.</p>`, gA), X0, 626, 1000);
  nS.style.setProperty('--c', 'var(--red)');

  const gB = h('div', 'abs', null, st, 'left:0;top:0;width:1920px;height:1080px');
  gB.innerHTML = `<svg width="1920" height="1080" style="position:absolute;left:0;top:0">
    <line x1="790" y1="236" x2="960" y2="236" stroke="#2F5DA8" stroke-width="3"/>
    <path class="br" d="M984 236 C 1040 236, 1050 176, 1110 176" stroke="#8C867A" stroke-width="3" fill="none" stroke-dasharray="8 8"/>
    <path class="br" d="M984 236 C 1040 236, 1050 296, 1110 296" stroke="#8C867A" stroke-width="3" fill="none" stroke-dasharray="8 8"/>
    <circle class="ag" cx="790" cy="236" r="16" fill="#2F5DA8"/>
  </svg>`;
  const q = at(h('div', 'abs serif', '?', gB, 'width:52px;height:52px;border-radius:50%;background:var(--amber);color:#fff;font:700 34px/52px var(--serif);text-align:center'), 946, 210);
  const brA = at(h('div', 'abs', '方案 A · 在线迁移', gB, 'font:500 24px var(--sans);color:var(--ink2)'), 1124, 160);
  const brB = at(h('div', 'abs', '方案 B · 停机迁移', gB, 'font:500 24px var(--sans);color:var(--ink2)'), 1124, 280);
  const json = at(h('div', 'term', `<div class="tb"><i></i><i></i><i></i><span>子代理的回报</span></div><pre></pre>`, gB), X0, 360, 540, 270);
  const jpre = json.querySelector('pre'); jpre.style.fontSize = '21px';
  const jl = ['{', '  <span class="k">"status"</span>: <span class="y">"needs_decision"</span>,', '  <span class="k">"question"</span>: <span class="s">"要停机迁移吗？"</span>,',
    '  <span class="k">"options"</span>: [<span class="s">"在线迁移"</span>, <span class="s">"停机 10 分钟"</span>]', '}'];
  const gate1 = at(h('div', 'abs', '<b>聊天、记忆里有答案</b>&nbsp; → 助手自己定', gB,
    'font:400 24px var(--sans);padding:12px 20px;border-radius:14px;border:2px solid var(--line);color:var(--ink2);background:var(--card)'), X0, 660, 540);
  const gate2 = at(h('div', 'abs', '<b>该你拿主意</b>&nbsp; → 问你', gB,
    'font:400 24px var(--sans);padding:12px 20px;border-radius:14px;background:var(--ink);color:var(--paper)'), X0, 740, 540);
  const arrow = at(h('div', 'abs', `<svg width="44" height="40"><path d="M2 20h34M24 8l14 12-14 12" stroke="#17171B" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`, gB), 1306, 750);
  const chat = chatCard(gB, 1360, 150, 400, 700);
  const cq = msg(chat.bd, 'a', '子代理需要你定一下：<b>要停机迁移吗？</b>');
  const opts = h('div', '', '<span class="chip">在线迁移</span> <span class="chip">停机 10 分钟</span>', chat.bd, 'align-self:flex-start;display:flex;gap:10px');
  const cu = msg(chat.bd, 'u', '停机吧，今晚做。');
  const cs = h('div', 'msg sys', '→ 带着答案派出新的子代理', chat.bd);

  const d1 = L('d1'), d2 = L('d2'), d3 = L('d3');
  const rt = [d1 + .15, d1 + .8, W('d1', '第三次') - .15, W('d1', '第三次') + .4, W('d1', '第五次') - .1];
  rt.slice(0, 5).forEach((t, i) => cue(t, i === 2 ? 'warn' : i === 4 ? 'stop' : 'tick', i < 2 ? .6 : 1));
  const tQ = d2 + 1.0, tJ = d2 + 1.5, tG1 = d3 - .1, tG2 = W('d3', '该你'), tC = tG2 + .3, tU = tC + 1.5;
  cue(d2 - .1, 'whoosh', .5); cue(tQ, 'pop'); cue(tJ, 'pop', .7); cue(tC, 'pop'); cue(tU, 'pop'); cue(tU + .5, 'ok', .6);
  return lt => {
    intro(lt);
    const outA = P(lt, d2 - .5, .4, E.inOut);
    tr(gA, { o: P(lt, d1 - .5, .5) * (1 - outA), y: -30 * outA });
    vis(log, lt, d1 - .5, Infinity, { dy: 20 });
    rows.forEach((r, i) => vis(r, lt, rt[i], Infinity, { dy: 10, d: .3 }));
    const count = rt.slice(0, 5).filter(t => lt >= t).length;
    setText(cn, String(count));
    const col = count >= 5 ? 'var(--red)' : count >= 3 ? 'var(--amber)' : 'var(--ink)';
    cn.style.color = col; log.style.borderColor = count >= 5 ? 'var(--red)' : 'var(--line)';
    const kb = P(lt, rt[count - 1] || 0, .3, E.back); tr(cn, { s: count ? lerp(1.25, 1, kb) : 1 });
    setText(ctag, count >= 5 ? '叫停' : count >= 3 ? '警告' : '');
    ctag.style.background = count >= 5 ? 'var(--red-l)' : 'var(--amber-l)'; ctag.style.color = col; ctag.style.opacity = count >= 3 ? 1 : 0;
    tr(cntBox, { o: P(lt, d1 - .3, .5) });
    vis(nW, lt, rt[2] + .1, rt[4] - .1, { dy: 20, dout: .2 });
    vis(nS, lt, rt[4] + .1, Infinity, { dy: 20 });
    gB.style.opacity = P(lt, d2 - .22, .4);
    const ag = gB.querySelector('.ag'); ag.setAttribute('cx', lerp(790, 930, P(lt, d2, .9, E.inOut)));
    gB.querySelectorAll('.br').forEach(b => { b.style.opacity = P(lt, d2 + .3, .5); });
    vis(q, lt, tQ, Infinity, { s0: .3, e: E.back, dy: 0 }); vis(brA, lt, d2 + .4); vis(brB, lt, d2 + .5);
    vis(json, lt, tJ, Infinity, { dy: 24 });
    setHTML(jpre, jl.filter((_, i) => lt >= tJ + .15 + i * .22).join('\n'));
    vis(gate1, lt, tG1, Infinity, { dy: 16 });
    vis(gate2, lt, tG2, Infinity, { dy: 16 });
    gate1.style.opacity = (+gate1.style.opacity) * lerp(1, .55, P(lt, tG2, .4));
    vis(arrow, lt, tG2 + .15, Infinity, { dy: 0 });
    vis(chat.el, lt, tJ + .3, Infinity, { dy: 24 });
    vis(cq, lt, tC, Infinity, { s0: .85, e: E.back }); vis(opts, lt, tC + .35, Infinity, { s0: .9 });
    vis(cu, lt, tU, Infinity, { s0: .85, e: E.back }); vis(cs, lt, tU + .5, Infinity, { s0: .9 });
  };
});

/* ------------------------------------------------------------ 7. chapter 4: checked, reported, remembered */
scene('ch4', {}, (root, { L, D, W, cue }) => {
  const intro = chapter(root, '04', ['有核实', '有交代', '有记录'], 'super-agent/reports.py<br>super-agent/ledger.py');
  const st = block(root, [-720, 452]);
  const X0 = 760;
  const gA = h('div', 'abs', null, st, 'left:0;top:0;width:1920px;height:1080px');
  const rep = (x, name, ev, footer, fc, fbg) => {
    const c = at(h('div', 'card', `<div style="display:flex;align-items:center;gap:14px;margin-bottom:22px"><span style="font:700 28px var(--sans)">${name}</span>
      <span class="pill" style="background:var(--green-l);color:var(--green);margin-left:auto">completed</span></div>
      <div class="mono" style="font:500 20px var(--mono);color:var(--muted);margin-bottom:10px">evidence</div>
      <div style="font:400 23px/1.75 var(--mono);min-height:90px">${ev}</div>
      <div class="ft" style="margin-top:22px;font:700 25px var(--sans);color:${fc};background:${fbg};padding:12px 18px;border-radius:12px">${footer}</div>`, gA, 'padding:28px 30px'), x, 140, 480);
    return [c, c.querySelector('.ft')];
  };
  const [cA, fA] = rep(X0, '子代理 A', '<span style="color:var(--green)">✓</span> pytest tests/auth: 128 passed<br><span style="color:var(--green)">✓</span> GET /health → 200 OK', '✓ 有证据：先抽查，再报喜', 'var(--green)', 'var(--green-l)');
  const [cB, fB] = rep(X0 + 520, '子代理 B', '<span style="color:var(--muted)">（空）</span>', '⚠ 空口说完成：直接标出', 'var(--amber)', 'var(--amber-l)');
  const nE = at(h('div', 'note', `<h4>核验说明会出现在助手的下一轮开头</h4><p>- It claims success without evidence. Verify the real end state before telling the user.</p>`, gA), X0, 590, 1000);
  nE.style.setProperty('--c', 'var(--amber)');

  const gB = h('div', 'abs', null, st, 'left:0;top:0;width:1920px;height:1080px');
  const chat = chatCard(gB, X0, 128, 560, 730);
  const s1 = h('div', 'msg sys', '✓ 后台任务完成：修复登录测试', chat.bd);
  const u1 = msg(chat.bd, 'u', '对了，明天几点开会？');
  const a1 = msg(chat.bd, 'a', '明早 10 点，3 楼会议室。');
  const s2 = h('div', 'msg sys', '插件唤醒助手', chat.bd);
  const a2 = msg(chat.bd, 'a', '另外，登录测试修好了：128 个测试全部通过。');
  const stc = at(h('div', 'card', `<div style="font:500 24px var(--sans);color:var(--muted)">这条后台结果</div><div class="sv serif" style="font:700 54px/1.4 var(--serif);margin-top:6px"></div><div class="sp pill" style="margin-top:8px;font-size:21px"></div>`, gB, 'padding:26px 30px'), 1370, 128, 390, 230);
  const sv = stc.querySelector('.sv'), sp = stc.querySelector('.sp');
  const steps = ['结果送达助手', '你的新消息插了进来', '助手只回答了你', '插件唤醒助手补报', '转达给你，才算完'].map((s, i) =>
    at(h('div', 'abs', `<span class="mono" style="display:inline-block;width:40px;font-weight:700">${i + 1}</span>${s}`, gB, 'font:500 26px var(--sans);color:var(--ink)'), 1380, 400 + i * 62));

  const gC = h('div', 'abs', null, st, 'left:0;top:0;width:1920px;height:1080px');
  const tbl = at(h('div', 'card', null, gC, 'overflow:hidden'), X0, 128, 1000, 340);
  const cols = 'display:grid;grid-template-columns:330px 260px 280px 1fr;align-items:center;padding:0 30px';
  tbl.innerHTML = `<div style="display:flex;align-items:center;gap:14px;height:66px;padding:0 30px;background:var(--paper2)"><span class="mono" style="font:700 26px var(--mono)">ledger.db</span>
    <span class="pill" style="background:var(--ink);color:var(--paper)">SQLite</span><span class="mono" style="margin-left:auto;font:500 20px var(--mono);color:var(--muted)">plugin-data/super-agent/</span></div>
    <div style="${cols};height:56px;font:500 22px var(--sans);color:var(--muted);border-bottom:1.5px solid var(--line)"><span>任务</span><span>状态</span><span>改过的文件</span><span>已告知</span></div>`;
  const R = [['修复登录测试', 'completed', 'green', 'src/auth/token.py', '✓'], ['数据库迁移', 'needs_decision', 'amber', '—', '✓'], ['重建搜索索引', 'running', 'blue', 'search/index.py', '—']];
  const rowEls = R.map(([t, s, c, f, r]) => h('div', '', `<span style="font:700 26px var(--sans)">${t}</span><span><span class="pill st" style="background:var(--${c}-l);color:var(--${c})">${s}</span></span>` +
    `<span class="mono" style="font:500 21px var(--mono);color:var(--ink2)">${f}</span><span style="font:700 26px var(--sans);color:var(--green)">${r}</span>`, tbl, `${cols};height:72px;border-bottom:1px solid var(--line)`));
  const lostPill = rowEls[2].querySelector('.st');
  const band = at(h('div', 'abs', `<svg width="44" height="44" viewBox="0 0 64 64" fill="none" stroke="#F2EEE5" stroke-width="5" stroke-linecap="round"><path d="M32 10v20"/><path d="M20 18a18 18 0 1 0 24 0"/></svg><span>Hermes 网关重启</span>`, gC,
    'display:flex;align-items:center;justify-content:center;gap:20px;background:var(--ink);color:var(--paper);font:700 40px var(--serif);border-radius:16px'), X0 - 20, 250, 1040, 110);
  const nL = at(h('div', 'note', `<h4>下一轮告诉助手：哪个任务中断了、动过哪些文件，先核实现场</h4><p>[super-agent] Hermes restarted while a subagent was running: "重建搜索索引" (started 12 min ago). Its result is lost and its side effects are unknown. It had changed: search/index.py. Check the real state before re-dispatching or telling the user it is done.</p>`, gC), X0, 500, 1000);
  nL.style.setProperty('--c', 'var(--red)');

  const e1 = L('e1'), e2 = L('e2'), e3 = L('e3');
  const tFA = W('e1', '助手先看证据'), tFB = W('e1', '再向你报喜');
  const ev = [e2 + .1, e2 + .75, e2 + 1.7, e2 + 2.9, e2 + 3.5], tDone = e2 + 4.3;
  const tBand = W('e3', '网关重启') - .1, tLost = tBand + 1.0;
  cue(e1 + .1, 'pop', .6); cue(e1 + .5, 'pop', .6); cue(tFA, 'ok', .6); cue(tFB, 'warn', .6);
  cue(e2 - .1, 'whoosh', .5); ev.forEach(t => cue(t, 'pop', .7)); cue(tDone, 'ok', .8);
  cue(e3 - .1, 'whoosh', .5); cue(tBand, 'glitch'); cue(tLost, 'stop', .7);
  return lt => {
    intro(lt);
    const outA = P(lt, e2 - .5, .4, E.inOut);
    tr(gA, { o: 1 - outA, y: -30 * outA });
    vis(cA, lt, e1 + .1, Infinity, { dy: 24 }); vis(cB, lt, e1 + .5, Infinity, { dy: 24 });
    vis(fA, lt, tFA, Infinity, { dy: 8, s0: .9, e: E.back }); vis(fB, lt, tFB, Infinity, { dy: 8, s0: .9, e: E.back });
    vis(nE, lt, tFB + .3, Infinity, { dy: 20 });
    const outB = P(lt, e3 - .5, .4, E.inOut);
    tr(gB, { o: P(lt, e2 - .22, .4) * (1 - outB), y: -30 * outB });
    [s1, u1, a1, s2, a2].forEach((m, i) => vis(m, lt, ev[i], Infinity, { s0: .85, e: E.back }));
    const done = lt >= tDone;
    setText(sv, done ? '已转达' : '尚未告知'); sv.style.color = done ? 'var(--green)' : 'var(--amber)';
    setText(sp, done ? 'reported' : 'unreported');
    sp.style.background = done ? 'var(--green-l)' : 'var(--amber-l)'; sp.style.color = sv.style.color;
    tr(sv, { s: done ? lerp(1.15, 1, P(lt, tDone, .35, E.back)) : 1 });
    stc.style.borderColor = done ? 'var(--green)' : 'var(--amber)';
    vis(stc, lt, ev[0], Infinity, { dy: 20 });
    const stepT = [ev[0], ev[1], ev[2], ev[3], tDone];
    steps.forEach((s, i) => {
      vis(s, lt, stepT[i], Infinity, { dy: 10 });
      const cur = lt >= stepT[i] && (i === 4 || lt < stepT[i + 1]);
      s.style.color = cur ? (i === 4 ? 'var(--green)' : 'var(--ink)') : 'var(--muted)';
    });
    gC.style.opacity = P(lt, e3 - .22, .4);
    vis(tbl, lt, e3 - .22, Infinity, { dy: 24 });
    rowEls.forEach((r, i) => vis(r, lt, e3 + .15 + i * .25, Infinity, { dy: 10 }));
    const kb = vis(band, lt, tBand, tBand + .75, { d: .12, dy: 0, s0: 1.08, dout: .2 });
    tbl.style.filter = kb ? `blur(${2 * kb}px)` : 'none';
    const lost = lt >= tLost;
    setText(lostPill, lost ? 'lost' : 'running');
    lostPill.style.background = lost ? 'var(--red-l)' : 'var(--blue-l)'; lostPill.style.color = lost ? 'var(--red)' : 'var(--blue)';
    rowEls[2].style.background = lost ? 'rgba(242,211,202,.45)' : 'transparent';
    vis(nL, lt, tLost + .25, Infinity, { dy: 20 });
  };
});

/* ------------------------------------------------------------ 8. callback: the cold open again, with the plugin */
scene('after', { fin: .5, fout: .5 }, (root, { L, D, W, cue, sc }) => {
  const chat = chatCard(block(root, [-100, 170]), 300, 150, 680, 660);
  const m1 = msg(chat.bd, 'u', '帮我构建项目，再查查测试为啥挂了。');
  const m2 = msg(chat.bd, 'a', '好的。构建和排查交给了两个子代理，你接着说别的也行。');
  const m3 = msg(chat.bd, 'u', '那明天几点开会？');
  const m4 = msg(chat.bd, 'a', '明早 10 点，3 楼会议室。');
  const m5 = msg(chat.bd, 'a', '两件都办完了：构建通过；测试已修好，128 个全部通过。');
  const side = at(h('div', 'abs', null, root, V ? 'text-align:center' : ''), LV(1100, 220), LV(236, 1012), 640);
  h('div', '', '这一次', side, 'font:500 32px var(--sans);color:var(--muted);letter-spacing:.08em');
  const big = h('div', 'serif', `<i style="display:inline-block;width:.2em;height:.2em;border-radius:50%;background:var(--green);margin-right:.16em;vertical-align:.3em"></i>在线`, side,
    `font:700 ${LV(180, 120)}px/1.12 var(--serif);color:var(--green);transform-origin:${LV('0 60%', '50% 60%')}`);
  const sub = h('div', '', '活在后台跑，聊天一直有人接。', side, 'font:400 34px var(--sans);color:var(--ink2);margin-top:10px');
  const rowsEl = h('div', '', null, side, `display:flex;gap:14px;margin-top:${LV(34, 26)}px;${LV('flex-direction:column', 'justify-content:center')}`);
  const rows = [['跑构建', 'exit 0'], ['查测试', '128 passed']].map(([job, ev]) => {
    const r = h('div', 'card', `<span class="ic" style="flex:none;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:700 19px var(--sans);color:#fff"></span>` +
      `<span style="font:700 26px var(--sans)">${LV('子代理 · ', '')}${job}</span><span class="ev mono" style="margin-left:auto;font:500 21px var(--mono);color:var(--green)">${ev}</span>`,
      rowsEl, `position:static;display:flex;align-items:center;gap:14px;padding:${LV('16px 24px', '14px 18px')};border-radius:16px;width:${LV(520, 306)}px;box-shadow:none`);
    return { r, ic: r.querySelector('.ic'), ev: r.querySelector('.ev') };
  });
  const h1 = L('h1'), h2 = L('h2');
  const tM1 = .3, tM2 = W('h1', '这一次') - .15, tRows = W('h1', '活交给') + .25, tOn = W('h1', '助手一直在线');
  const tM3 = tOn - .55, tM4 = tOn + .35, tDone = [h2 + .15, h2 + .5], tM5 = W('h2', '才交到') - .1;
  cue(tM1, 'pop'); cue(tM2, 'pop', .8); cue(tM3, 'pop', .8); cue(tM4, 'pop', .8); cue(tOn, 'ok', .6); cue(tM5, 'pop'); cue(tM5 + .35, 'ok', .8);
  return lt => {
    vis(chat.el, lt, 0, Infinity, { d: .6, dy: 24 });
    [[m1, tM1], [m2, tM2], [m3, tM3], [m4, tM4], [m5, tM5]].forEach(([m, t]) => vis(m, lt, t, Infinity, { d: .4, s0: .85, e: E.back }));
    const ks = P(lt, .2, .6); tr(side, { o: ks, y: (1 - ks) * 20 });
    const kb = P(lt, tOn, .5, E.back); tr(big, { o: P(lt, tOn, .3), s: lerp(.8, 1, kb) });
    tr(sub, { o: P(lt, tOn + .3, .5) });
    rows.forEach(({ r, ic, ev }, i) => {
      vis(r, lt, tRows + i * .2, Infinity, { dy: 14, s0: .94 });
      const done = lt >= tDone[i];
      setText(ic, done ? '✓' : '');
      ic.style.background = done ? 'var(--green)' : 'transparent';
      ic.style.border = done ? 'none' : '4px solid var(--blue-l)';
      ic.style.borderTopColor = done ? '' : 'var(--blue)';
      ic.style.transform = done ? `scale(${lerp(1.3, 1, P(lt, tDone[i], .3, E.back))})` : `rotate(${lt * 400}deg)`;
      ev.style.opacity = P(lt, tDone[i], .3);
      r.style.borderColor = done ? 'var(--green)' : 'var(--line)';
    });
  };
});

/* ------------------------------------------------------------ 9. principles */
scene('principles', {}, (root, { L, D, W, cue }) => {
  const head = at(h('div', 'abs serif', '这一切，都由普通代码完成', root, 'left:0;right:0;text-align:center;font:700 64px var(--serif)'), 0, LV(250, 380));
  const cols = [['零模型调用', '每一项检查都是确定性的代码<br>插件自己不消耗 token', '零模型调用'],
    ['只走公开接口', '只用钩子、中间件和插件 API<br>从不改写 Hermes 自身的代码', '只走公开接口'],
    ['只跟助手说话', '从不直接往你的聊天里发消息<br>说什么，由助手决定', '只跟助手说话']]
    .map(([t, d, w], i) => [at(h('div', 'abs', `<div class="serif" style="font:700 ${LV(66, 60)}px var(--serif);margin-bottom:${LV(22, 12)}px">${t}</div><div style="font:400 27px/1.6 var(--sans);color:var(--ink2)">${d}</div>`, root,
      `text-align:center;padding-top:${LV(34, 24)}px;border-top:3px solid var(--ink)`), LV(170 + i * 540, 290), LV(430, 520 + i * 250), 500), W('f1', w) - .1]);
  const foot = at(h('div', 'abs', '不搞定时播报 · 绝不添乱 · 只在你指定的平台生效', root, 'left:0;right:0;text-align:center;font:500 30px var(--sans);color:var(--muted);letter-spacing:.06em'), 0, LV(770, 1288));
  cols.forEach(([, t]) => cue(t, 'pop', .7));
  return lt => {
    const k = P(lt, .1, .7); tr(head, { o: k, y: (1 - k) * 24 });
    cols.forEach(([el, t]) => vis(el, lt, t, Infinity, { dy: 30 }));
    vis(foot, lt, L('f1') + D('f1') - .4);
  };
});

/* ------------------------------------------------------------ 10. end card */
scene('end', { fout: 1.1 }, (root, { L, D, W, cue }) => {
  const name = at(h('div', 'abs serif', LV('Hermes Super Agent', 'Hermes<br>Super Agent'), root, `left:0;right:0;text-align:center;font:700 ${LV('108px/1.45', '112px/1.14')} var(--serif)`), 0, LV(140, 370));
  const tag = at(h('div', 'abs serif', '让你的助手，随时在线', root, 'left:0;right:0;text-align:center;font:500 48px var(--serif);color:var(--ink2)'), 0, LV(300, 658));
  const promises = [['01', '聊天始终保持响应'], ['02', '子代理出发前心里有数'], ['03', '不空转 · 不瞎猜'], ['04', '有核实 · 有交代 · 有记录']];
  const grid = at(h('div', 'abs', promises.map(([n, p]) =>
    `<div class="card" style="position:relative;display:flex;align-items:center;gap:18px;padding:${LV('22px 30px', '20px 26px')};font:700 ${LV(32, 30)}px var(--sans);color:var(--ink)">` +
    `<span class="mono" style="font:700 ${LV(26, 24)}px var(--mono);color:var(--blue)">${n}</span>${p}</div>`).join(''), root,
    `display:grid;grid-template-columns:${LV('1fr 1fr', '1fr')};gap:${LV('18px 24px', '16px')}`), LV(330, 140), LV(410, 790), LV(1260, 800));
  const items = [...grid.children];
  const foot = at(h('div', 'abs', `<div style="font:500 26px var(--sans);color:var(--muted)">MIT · Hermes Agent 0.22+ · Python 3.10+ · 零运行时依赖</div>` +
    `<div class="srch" style="display:inline-block;margin-top:30px;font:700 30px var(--sans);color:var(--paper);background:var(--ink);padding:12px 30px;border-radius:999px"><span style="color:#E8C27A;margin-right:12px">★</span>Hermes Agent 开源插件</div>`, root, 'left:0;right:0;text-align:center'), 0, LV(686, 1240));
  const g = L('g1');
  const srch = root.querySelector('.srch');
  items.forEach((_, i) => cue(g + .2 + i * .3, 'pop', .6)); cue(W('g1', '开源插件') - .1, 'pop', .8); cue(W('g1', '让你的'), 'ok', .8);
  return lt => {
    const k = P(lt, .05, .8); tr(name, { o: k, y: (1 - k) * 30 });
    vis(tag, lt, W('g1', '让你的') - .1);
    items.forEach((el, i) => vis(el, lt, g + .2 + i * .3, Infinity, { dy: 24 }));
    vis(foot, lt, g + .9);
    vis(srch, lt, W('g1', '开源插件') - .1, Infinity, { s0: .9, e: E.back, dy: 10 });
  };
});

/* ------------------------------------------------------------ chrome, captions, render */
const chapEl = document.getElementById('chapters');
const CH = [['ch1', '01'], ['ch2', '02'], ['ch3', '03'], ['ch4', '04']];
const segs = CH.map(([, n]) => h('span', 'seg', n, chapEl));
const capEl = document.getElementById('caption');
const markEl = document.getElementById('mark');
const repoEl = document.getElementById('repo');
const S = id => TL.scenes.find(s => s.id === id);

function draw(t) {
  for (const s of scenes) {
    const lt = t - s.sc.start, d = s.sc.dur;
    if (lt < 0 || lt >= d) { s.root.style.display = 'none'; continue; }
    s.root.style.display = '';
    const fi = s.fin ? P(lt, 0, s.fin, E.inOut) : 1, fo = s.fout ? 1 - P(lt, d - s.fout, s.fout, E.inOut) : 1;
    s.root.style.opacity = Math.min(fi, fo);
    s.update(lt);
  }
  const c1 = S('ch1'), c4 = S('ch4'), pr = S('principles');
  const co = S('cold'), ti = S('title'), en = S('end');
  const brandOn = P(t, co.start + .3, .6) * (1 - P(t, en.start - .5, .5));
  /* the cold open and the problems play at night; chrome and captions follow, the repo line dipping as it flips */
  stage.classList.toggle('nc', t >= co.start - .05 && t < ti.start);
  repoEl.style.opacity = brandOn * clamp(Math.abs(t - ti.start) / .3);
  markEl.style.opacity = brandOn * (1 - P(t, ti.start - .4, .4) * (1 - P(t, c1.start, .5)));
  chapEl.style.opacity = P(t, c1.start + .2, .6) * (1 - P(t, c4.start + c4.dur - .45, .45));
  CH.forEach(([id], i) => { const s = S(id); segs[i].classList.toggle('on', t >= s.start && t < s.start + s.dur); });
  let cur = null;
  for (let i = 0; i < TL.lines.length; i++) {
    const l = TL.lines[i], next = TL.lines[i + 1];
    const until = Math.min(l.start + l.dur + .35, next ? next.start - .08 : Infinity);
    if (t >= l.start - .08 && t < until) cur = l;
  }
  if (cur) {
    const zh = V ? cur.cap.split('  ').map(g => `<span class="pg">${g.split(/(?<=[、。])/).map(x => `<span class="ph">${esc(x)}</span>`).join('<wbr>')}</span>`).join(' ') : esc(cur.cap);
    setHTML(capEl, `<div class="box"><div class="zh">${zh}</div><div class="en">${esc(cur.en)}</div></div>`);
    capEl.style.opacity = P(t, cur.start - .08, .15) * (1 - P(t, cur.start + cur.dur + .2, .15));
  } else capEl.style.opacity = 0;
}

window.render = t => new Promise(res => { draw(t); requestAnimationFrame(() => requestAnimationFrame(res)); });
window.renderSync = draw;
window.CUES = CUES.sort((a, b) => a.t - b.t);
const q = new URLSearchParams(location.search).get('t');
document.fonts.ready.then(() => { draw(q ? +q : 0); document.body.dataset.ready = '1'; });
