// node tools/render_frames.mjs <job.json>   job = {scene:{room,palette}, frames:[...], outDir, chrome?}
// Requires the viewer dev server (npm run dev, :5187). Writes color_K.png / id_K.png / depth_K.png and ids.json.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '../viewer/node_modules/playwright-core/index.mjs';
const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const b = await chromium.launch({ executablePath: process.env.CHROME || undefined, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 800, height: 600 } });
p.on('pageerror', (e) => console.error('pageerror:', e.message));
await p.goto('http://127.0.0.1:' + (process.env.PORT || 5187) + '/render.html');
await p.waitForFunction('window.__ready', null, { timeout: 30000 });
const ids = await p.evaluate(([r, pal]) => window.setup(r, pal), [job.scene.room, job.scene.palette || {}]);
fs.mkdirSync(job.outDir, { recursive: true });
fs.writeFileSync(path.join(job.outDir, 'ids.json'), JSON.stringify(ids));
for (let k = 0; k < job.frames.length; k++) {
  const o = await p.evaluate((f) => window.renderFrame(f), job.frames[k]);
  for (const pass of ['color', 'id', 'depth']) fs.writeFileSync(path.join(job.outDir, `${pass}_${k}.png`), Buffer.from(o[pass].split(',')[1], 'base64'));
}
await b.close();
