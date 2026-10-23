// node tools/test_zip_drop.mjs <export.zip> out.png — simulates dropping a zip on the viewer (dev server on :5187)
import fs from 'node:fs';
import { chromium } from '../viewer/node_modules/playwright-core/index.mjs';
const [zip, out = 'drop.png'] = process.argv.slice(2);
const b = await chromium.launch({ executablePath: process.env.CHROME, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 1440, height: 860 } });
p.on('console', (m) => m.type() === 'error' && console.log('console:', m.text()));
await p.goto('http://127.0.0.1:5187/'); await p.waitForFunction('window.__ready', null, { timeout: 60000 });
const b64 = fs.readFileSync(zip).toString('base64');
await p.evaluate(async (b64) => {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer(); dt.items.add(new File([bytes], 'dropped-scan.zip', { type: 'application/zip' }));
  window.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
}, b64);
await p.waitForTimeout(4000);
console.log('items:', await p.$$eval('#roomlist .item', (els) => els.map((e) => e.textContent)));
await p.screenshot({ path: out }); await b.close();
