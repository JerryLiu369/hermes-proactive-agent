#!/usr/bin/env node
// Drive headless Chromium over CDP: `stills t1 t2 ...` writes PNGs, `video` pipes every frame into FFmpeg,
// `cues` dumps the sound-effect cue list. Usage: node render.mjs <mode> [args]
// PROMO_VERTICAL=1 renders the 1080x1920 layout (index.html?v=1) to build/video-v.mp4 and build/stills-v.
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const BUILD = join(HERE, 'build');
const VERT = process.env.PROMO_VERTICAL === '1';
const [VW, VH] = VERT ? [1080, 1920] : [1920, 1080];
// one profile per orientation, so both can render at the same time
const PROFILE = `${process.env.HOME}/snap/chromium/common/promo-video-render/profile${VERT ? '-v' : ''}`;
const [mode = 'stills', ...args] = process.argv.slice(2);
mkdirSync(BUILD, { recursive: true });

const chrome = spawn('/snap/bin/chromium', ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
  '--hide-scrollbars', '--force-color-profile=srgb', '--font-render-hinting=none', `--user-data-dir=${PROFILE}`,
  '--remote-debugging-port=0', `--window-size=${VW},${VH}`, '--force-device-scale-factor=1', 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe'] });
const wsUrl = await new Promise((res, rej) => {
  let buf = '';
  chrome.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\/\/\S+)/); if (m) res(m[1]); });
  chrome.on('exit', c => rej(new Error('chromium exited ' + c + '\n' + buf)));
});

const ws = new WebSocket(wsUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map();
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
});
const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
  const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId }));
});

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
const S = (m, p) => send(m, p, sessionId);
await S('Emulation.setDeviceMetricsOverride', { width: VW, height: VH, deviceScaleFactor: 1, mobile: false });
await S('Page.enable');
await S('Page.navigate', { url: 'file://' + join(HERE, 'index.html') + (VERT ? '?v=1' : '') });
const evalJS = async (expression, awaitPromise = false) => {
  const r = await S('Runtime.evaluate', { expression, awaitPromise, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};
for (let i = 0; ; i++) {
  if (await evalJS('document.body && document.body.dataset.ready === "1"').catch(() => false)) break;
  if (i > 200) throw new Error('page never became ready');
  await new Promise(r => setTimeout(r, 50));
}
const errs = await evalJS('window.__errors || []');
if (errs.length) console.error(errs);
const TL = await evalJS('window.TIMELINE');
// The page is loaded once; each frame only calls render(t) (~15 ms). The cost is the screenshot encode:
// PNG ~250 ms/frame, JPEG q95 ~90 ms. PROMO_FRAMES=png keeps lossless frames for a master.
const FMT = process.env.PROMO_FRAMES === 'png' ? 'png' : 'jpeg';
const shot = async (t, fmt = 'png') => {
  await evalJS(`render(${t})`, true);
  const { data } = await S('Page.captureScreenshot', fmt === 'png' ? { format: 'png', optimizeForSpeed: true } : { format: 'jpeg', quality: 95, optimizeForSpeed: true });
  return Buffer.from(data, 'base64');
};

try {
  if (mode === 'bench') {
    // per-frame cost split: seek (render(t) + two rAFs) vs capture, PNG vs JPEG
    const N = +(args[0] || 60), t0 = +(args[1] || 40);
    for (const fmt of ['png', 'jpeg']) {
      let seek = 0, cap = 0, bytes = 0;
      for (let i = 0; i < N; i++) {
        let a = Date.now(); await evalJS(`render(${t0 + i / 30})`, true); seek += Date.now() - a;
        a = Date.now();
        const { data } = await S('Page.captureScreenshot', fmt === 'png' ? { format: 'png', optimizeForSpeed: true } : { format: 'jpeg', quality: 95, optimizeForSpeed: true });
        cap += Date.now() - a; bytes += data.length * .75;
      }
      console.log(`${fmt}: seek ${(seek / N).toFixed(0)}ms capture ${(cap / N).toFixed(0)}ms size ${(bytes / N / 1024).toFixed(0)}KB`);
    }
  } else if (mode === 'cues') {
    writeFileSync(join(BUILD, 'cues.json'), JSON.stringify(await evalJS('window.CUES'), null, 1));
    console.log('cues written');
  } else if (mode === 'stills') {
    const SD = join(BUILD, VERT ? 'stills-v' : 'stills');
    mkdirSync(SD, { recursive: true });
    for (const a of args) {
      const t = /^[a-z]/.test(a) ? (([id, off]) => TL.scenes.find(s => s.id === id).start + +(off || 0))(a.split('+')) : +a;
      const f = join(SD, `${a.replace(/[^\w.+-]/g, '_')}.png`);
      writeFileSync(f, await shot(t)); console.log(f, t.toFixed(2));
    }
  } else if (mode === 'video') {
    const fps = TL.fps, n = Math.ceil(TL.duration * fps);
    const from = +(args[0] || 0), to = Math.min(n, +(args[1] || n));
    const out = join(BUILD, args[2] || (VERT ? 'video-v.mp4' : 'video.mp4'));
    const ff = spawn('ffmpeg', ['-hide_banner', '-loglevel', 'warning', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', FMT === 'png' ? 'png' : 'mjpeg', '-i', '-',
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '15', '-tune', 'stillimage', '-pix_fmt', 'yuv420p', '-r', String(fps), out], { stdio: ['pipe', 'inherit', 'inherit'] });
    const t0 = Date.now();
    for (let f = from; f < to; f++) {
      const buf = await shot(f / fps, FMT);
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
      if (f % 150 === 0) console.log(`frame ${f}/${to} ${((Date.now() - t0) / 1000).toFixed(0)}s`);
    }
    ff.stdin.end();
    await new Promise(r => ff.on('exit', r));
    console.log('video written', out, ((Date.now() - t0) / 1000).toFixed(0) + 's');
  }
} finally {
  ws.close(); chrome.kill();
}
