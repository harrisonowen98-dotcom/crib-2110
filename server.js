/* Crib 21-10 sync server: serves the game page and relays shared docs over WebSocket. No accounts. */
const http = require('http'), fs = require('fs'), path = require('path');
const { WebSocketServer } = require('ws');
const PORT = +process.env.PORT || 3000;
const PUB = fs.existsSync(path.join(__dirname, 'public', 'index.html')) ? path.join(__dirname, 'public') : __dirname; // flat repo layout: index.html next to server.js
const DATA = process.env.DATA_FILE || path.join(__dirname, 'data.json');
const PATH_OK = /^crib\/(game|seat[0-4]|peg[0-4]|disc[0-4])$/;
const MAX_DOC = 64 * 1024;
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.webp': 'image/webp', '.ico': 'image/x-icon' };

const rooms = new Map(); // name -> { docs: Map, leases: Map, clients: Set, touched }
function room(name) {
  let r = rooms.get(name);
  if (!r) { r = { docs: new Map(), leases: new Map(), clients: new Set(), touched: Date.now() }; rooms.set(name, r); }
  r.touched = Date.now(); return r;
}
try { const j = JSON.parse(fs.readFileSync(DATA, 'utf8')); for (const [n, docs] of Object.entries(j)) { const r = room(n); for (const [p, d] of Object.entries(docs)) r.docs.set(p, d); } } catch (e) { }
let saveT = 0;
function saveSoon() { clearTimeout(saveT); saveT = setTimeout(() => { const o = {}; rooms.forEach((r, n) => { if (r.docs.size) o[n] = Object.fromEntries(r.docs); }); fs.writeFile(DATA, JSON.stringify(o), () => { }); }, 1500); }

const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname === '/healthz') { res.writeHead(200); return res.end('ok'); }
  let p = decodeURIComponent(u.pathname); if (p === '/') p = '/index.html';
  const f = path.normalize(path.join(PUB, p));
  if (!f.startsWith(PUB)) { res.writeHead(403); return res.end(); }
  if (PUB === __dirname && path.basename(f) !== 'index.html') { res.writeHead(404); return res.end('Not found'); }
  fs.readFile(f, (err, buf) => {
    if (err) { res.writeHead(404); return res.end('Not found'); }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
    res.end(buf);
  });
});
const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 128 * 1024 });
const send = (ws, o) => { if (ws.readyState === 1) ws.send(JSON.stringify(o)); };
function broadcast(r, p) { const m = JSON.stringify({ t: 'doc', path: p, data: r.docs.has(p) ? r.docs.get(p) : null }); r.clients.forEach(c => { if (c.readyState === 1) c.send(m); }); }

wss.on('connection', ws => {
  let r = null; ws.alive = true; ws.on('pong', () => { ws.alive = true; });
  ws.on('message', raw => {
    let m; try { m = JSON.parse(raw); } catch (e) { return; }
    if (!m || typeof m !== 'object') return;
    if (m.t === 'join') {
      const name = String(m.room || 'main').toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 32) || 'main';
      if (r) r.clients.delete(ws);
      r = room(name); r.clients.add(ws); return send(ws, { t: 'joined', room: name });
    }
    if (!r) return;
    r.touched = Date.now();
    const p = m.path;
    if (m.t === 'sub') { if (PATH_OK.test(p)) send(ws, { t: 'doc', path: p, data: r.docs.has(p) ? r.docs.get(p) : null }); return; }
    const ack = (o) => { if (m.id != null) send(ws, Object.assign({ t: 'ack', id: m.id }, o)); };
    if (!PATH_OK.test(p)) return ack({ ok: false, code: 'invalid_argument' });
    if (m.t === 'set' || m.t === 'update') {
      if (!m.data || typeof m.data !== 'object' || Array.isArray(m.data) || JSON.stringify(m.data).length > MAX_DOC) return ack({ ok: false, code: 'invalid_argument' });
      if (m.t === 'update') { if (!r.docs.has(p)) return ack({ ok: false, code: 'invalid_argument' }); r.docs.set(p, Object.assign({}, r.docs.get(p), m.data)); }
      else r.docs.set(p, m.data);
      broadcast(r, p); saveSoon(); return ack({ ok: true });
    }
    if (m.t === 'delete') { r.docs.delete(p); broadcast(r, p); saveSoon(); return ack({ ok: true }); }
    if (m.t === 'acquire') {
      const l = r.leases.get(p), now = Date.now(), h = String(m.holder || '').slice(0, 64), ttl = Math.min(+m.ttlMs || 2500, 10000);
      if (l && l.exp > now && l.h !== h) return ack({ ok: true, acquired: false });
      r.leases.set(p, { h, exp: now + ttl }); return ack({ ok: true, acquired: true });
    }
  });
  ws.on('close', () => { if (r) r.clients.delete(ws); });
  ws.on('error', () => { });
});
setInterval(() => { wss.clients.forEach(ws => { if (!ws.alive) return ws.terminate(); ws.alive = false; try { ws.ping(); } catch (e) { } }); }, 25000);
setInterval(() => { const now = Date.now(); rooms.forEach((r, n) => { if (!r.clients.size && now - r.touched > 3 * 86400e3) { rooms.delete(n); saveSoon(); } }); }, 3600e3);
server.listen(PORT, () => console.log('crib server on :' + PORT));
