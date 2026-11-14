#!/usr/bin/env node
// Render the README and catalog images with headless Chromium over CDP.
//   node render.mjs still <page?query> <out.png> <width> <height> [scale]
//   node render.mjs gif   <page?query> <out.gif> <width> <height> <fps> <seconds> [start]
// A gif loops from `start` seconds in, so its first frame (all a still preview shows) can be a full one.
// A page sets document.body.dataset.ready = "1" once its fonts are in; an animated page also
// defines render(t) (seconds), which draws one frame as a pure function of time.
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const [mode, page, out, W, H, a5, a6, a7] = process.argv.slice(2);
if (!['still', 'gif'].includes(mode) || !page || !out || !W || !H) {
  console.error('usage: node render.mjs still|gif <page?query> <out> <width> <height> [scale | fps seconds]');
  process.exit(2);
}
const width = +W, height = +H, scale = mode === 'still' ? +(a5 || 2) : 1;
const PROFILE = `${process.env.HOME}/snap/chromium/common/super-agent-assets/profile`;
const CHROME = process.env.CHROME || '/snap/bin/chromium';

const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
  '--hide-scrollbars', '--force-color-profile=srgb', '--font-render-hinting=none', `--user-data-dir=${PROFILE}`,
  '--remote-debugging-port=0', `--window-size=${width},${height}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
  if (m.id && pending.has(m.id)) {
    const { res, rej } = pending.get(m.id); pending.delete(m.id);
    m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
  }
});
const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
  const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId }));
});

try {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  const S = (m, p) => send(m, p, sessionId);
  await S('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile: false });
  await S('Page.enable');
  const [file, query = ''] = page.split('?');
  await S('Page.navigate', { url: 'file://' + resolve(HERE, file) + (query ? '?' + query : '') });
  const evalJS = async (expression) => {
    const r = await S('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  for (let i = 0; ; i++) {
    if (await evalJS('document.body && document.body.dataset.ready === "1"').catch(() => false)) break;
    if (i > 200) throw new Error('page never became ready: ' + page);
    await new Promise(r => setTimeout(r, 50));
  }
  const errs = await evalJS('window.__errors || []');
  if (errs.length) throw new Error('page errors: ' + errs.join('; '));
  const shot = async () => Buffer.from((await S('Page.captureScreenshot', { format: 'png' })).data, 'base64');
  mkdirSync(dirname(resolve(out)), { recursive: true });

  if (mode === 'still') {
    writeFileSync(out, await shot());
    console.log(out);
  } else {
    const fps = +a5, n = Math.round(+a6 * fps), start = Math.round(+(a7 || 0) * fps);
    // One palette for the whole loop keeps flat colours stable; diff mode only redraws what moves.
    const ff = spawn('ffmpeg', ['-hide_banner', '-loglevel', 'warning', '-y', '-f', 'image2pipe', '-framerate', String(fps),
      '-c:v', 'png', '-i', '-', '-filter_complex',
      '[0:v]split[a][b];[a]palettegen=max_colors=128:stats_mode=full[p];[b][p]paletteuse=dither=none:diff_mode=rectangle',
      '-loop', '0', out], { stdio: ['pipe', 'inherit', 'inherit'] });
    for (let f = 0; f < n; f++) {
      await evalJS(`render(${((f + start) % n) / fps})`);
      if (!ff.stdin.write(await shot())) await new Promise(r => ff.stdin.once('drain', r));
    }
    ff.stdin.end();
    await new Promise(r => ff.on('exit', r));
    console.log(out, n + ' frames');
  }
} finally {
  ws.close(); chrome.kill();
}
