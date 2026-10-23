// node tools/screenshot.mjs "<query>" out.png   (needs viewer dev server on :5187)
import { chromium } from '/home/user/crib-2110/room-scanner/viewer/node_modules/playwright-core/index.mjs';
const [q = '', out = 'shot.png'] = process.argv.slice(2);
const b = await chromium.launch({ executablePath: process.env.CHROME || undefined, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 1440, height: 860 } });
p.on('console', (m) => m.type() === 'error' && console.log('console:', m.text()));
p.on('pageerror', (e) => console.log('pageerror:', e.message));
await p.goto('http://127.0.0.1:5187/' + q);
await p.waitForFunction('window.__ready', null, { timeout: 30000 });
await p.waitForTimeout(1500);
for (const sel of (process.env.CLICKS || '').split(',').filter(Boolean)) { await p.click(sel); await p.waitForTimeout(2500); }
await p.screenshot({ path: out });
await b.close();
