import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { unzipSync } from 'fflate';
import { buildRoom, m4, type RoomJson, type Palette } from './rebuilt';

/** Where a scan's files come from: the dev server's /data folder or a dropped .zip. */
interface Source { label: string; json(p: string): Promise<any>; buf(p: string): Promise<ArrayBuffer>; has(p: string): Promise<boolean> }
const serverSource = (folder: string): Source => ({
  label: folder,
  json: async (p) => (await fetch(`/data/${folder}/${p}`)).json(),
  buf: async (p) => { const r = await fetch(`/data/${folder}/${p}`); if (!r.ok) throw new Error(p); return r.arrayBuffer(); },
  has: async (p) => (await fetch(`/data/${folder}/${p}`, { method: 'HEAD' })).ok,
});
function zipSource(name: string, bytes: Uint8Array): Source {
  const files = unzipSync(bytes); const keys = Object.keys(files);
  const man = keys.find((k) => k.endsWith('manifest.json') && k.split('/').length <= 2)!; const pre = man.slice(0, man.length - 'manifest.json'.length);
  const get = (p: string) => files[pre + p];
  return { label: name, json: async (p) => JSON.parse(new TextDecoder().decode(get(p))), buf: async (p) => { const f = get(p); if (!f) throw new Error(p); return f.buffer.slice(f.byteOffset, f.byteOffset + f.byteLength) as ArrayBuffer; }, has: async (p) => !!get(p) };
}

interface Loaded { folder: string; src: Source; tex: Partial<Record<'lod1' | 'full', THREE.Group>>; manifest: any; room: RoomJson; group: THREE.Group; shell: THREE.Group; objects: THREE.Group; points: THREE.Points | null; walls: { mesh: THREE.Mesh; center: THREE.Vector3; normal: THREE.Vector3 }[]; bbox: THREE.Box3 }

const $ = <T extends HTMLElement>(s: string) => document.querySelector(s) as T;
const canvas = $<HTMLCanvasElement>('#c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0d12);
const camera = new THREE.PerspectiveCamera(55, 1, 0.05, 200);
const orbit = new OrbitControls(camera, canvas);
orbit.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x444455, 1.7));
const sun = new THREE.DirectionalLight(0xffffff, 1.2); sun.position.set(3, 8, 2); scene.add(sun);

const ceilClip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 2.55); // hides ceiling points in cut-away views
const state = { view: 'dollhouse', src: 'rebuilt', quality: 'auto', tab: 'rooms', drone: false, measure: false, eye: 1.6 };
let current: Loaded | null = null;
let houseGroup: THREE.Group | null = null;
const houseRooms: Loaded[] = [];

const toast = (m: string) => { const t = $('#toast'); t.textContent = m; t.style.opacity = '1'; setTimeout(() => (t.style.opacity = '0'), 1800); };

// ---------------------------------------------------------------- loading
async function loadScan(src: Source, folder = src.label): Promise<Loaded> {
  const manifest = await src.json('manifest.json');
  let room: RoomJson = await src.json(manifest.rooms[0].file);
  let pal: Palette = {};
  if (await src.has('rebuilt/scene.json')) { const r = await src.json('rebuilt/scene.json'); room = r.room; pal = r.palette ?? {}; }  // scan-to-3JS output
  const group = new THREE.Group();
  const { shell, objects, walls } = buildRoom(room, pal);

  // point cloud
  let points: THREE.Points | null = null;
  try {
    const buf = await src.buf('build/pointcloud.bin');
    const n = new DataView(buf).getUint32(0, true);
    const pos = new Float32Array(buf, 4, n * 3);
    const col8 = new Uint8Array(buf, 4 + n * 12, n * 3);
    const col = new Float32Array(n * 3); for (let i = 0; i < col.length; i++) col[i] = col8[i] / 255;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos.slice(), 3)); g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    points = new THREE.Points(g, new THREE.PointsMaterial({ size: 0.025, vertexColors: true, sizeAttenuation: true, clippingPlanes: [ceilClip] }));
  } catch { /* no build yet */ }

  group.add(shell, objects); if (points) group.add(points);
  const bb = manifest.rooms[0].boundingBox;
  const bbox = new THREE.Box3(new THREE.Vector3(...(bb.min as [number, number, number])), new THREE.Vector3(...(bb.max as [number, number, number])));
  return { folder, src, tex: {}, manifest, room, group, shell, objects, points, walls, bbox };
}

// ---------------------------------------------------------------- state -> scene
function applyMode() {
  const rooms = state.tab === 'house' ? houseRooms : current ? [current] : [];
  const walk = state.view === 'walk', knoll = state.view === 'knolling', layout = state.view === 'layout';
  for (const L of rooms) {
    const tex = state.src === 'tex';
    const showShell = (state.src === 'rebuilt' || state.src === 'overlay' || state.src === 'wire') && !knoll;
    L.shell.visible = showShell; L.objects.visible = (state.src !== 'scan' && !tex) || knoll;
    syncTex(L, tex && !knoll);
    if (L.points) L.points.visible = (state.src === 'scan' || state.src === 'overlay') && !knoll;
    const wire = state.src === 'wire';
    L.shell.traverse((o: any) => { if (o.material && o.isMesh) { o.material.wireframe = wire; } });
    L.objects.traverse((o: any) => { if (o.material) o.material.wireframe = wire; });
    if (L.points) (L.points.material as THREE.PointsMaterial).clippingPlanes = walk ? [] : [ceilClip];
    const ceil = L.shell.getObjectByName('ceiling'); if (ceil) ceil.visible = walk;
    L.shell.getObjectByName('floor')!.visible = true;
  }
  layoutKnolling(rooms);
  orbit.enabled = !walk;
  document.querySelectorAll<HTMLButtonElement>('#views button').forEach((b) => b.classList.toggle('on', b.dataset.view === state.view));
  document.querySelectorAll<HTMLButtonElement>('#sources button').forEach((b) => b.classList.toggle('on', b.dataset.src === state.src));
  document.querySelectorAll<HTMLButtonElement>('#quality button').forEach((b) => b.classList.toggle('on', b.dataset.q === state.quality));
  $('#hint').textContent = walk ? 'WASD + mouse/drag to look · Shift = run · touch: left thumb moves, right thumb looks' : layout ? 'Top-down floor plan' : knoll ? 'Every object laid out flat' : 'Drag to orbit · scroll to zoom · walls facing you fade';
  resize();
}

function syncTex(L: Loaded, show: boolean) {
  const want = state.quality === 'full' ? 'full' : 'lod1';
  for (const k of ['lod1', 'full'] as const) if (L.tex[k]) L.tex[k]!.visible = show && k === want;
  if (show && !L.tex[want]) {
    const file = want === 'full' ? 'build/room.glb' : 'build/room_lod1.glb';
    L.src.buf(file).then((buf) => new GLTFLoader().parse(buf, '', (g) => { g.scene.traverse((o: any) => { if (o.isMesh) { o.material.side = THREE.FrontSide; o.material.metalness = 0; o.material.roughness = 1; } }); L.tex[want] = g.scene; L.group.add(g.scene); applyMode(); }, () => toast('No textured model — run the pipeline build'))).catch(() => toast('No textured model — run the pipeline build'));
  }
}
const knollGroups = new WeakMap<Loaded, THREE.Group>();
function layoutKnolling(rooms: Loaded[]) {
  for (const L of rooms) {
    let g = knollGroups.get(L);
    if (!g) { g = new THREE.Group(); knollGroups.set(L, g); L.group.add(g); }
    g.clear();
    if (state.view !== 'knolling') { L.objects.position.set(0, 0, 0); continue; }
    L.objects.visible = false;
    const sx = L.bbox.max.x + 1.2; let x = sx, z = L.bbox.min.z;
    for (const o of L.objects.children) {
      const [w, h, d] = o.userData.dims;
      const c = o.clone(true); c.matrixAutoUpdate = true; c.position.set(x + w / 2, h / 2 + 0.001, z + d / 2); c.quaternion.identity(); c.scale.set(1, 1, 1);
      c.traverse((m: any) => { if (m.isMesh) m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), new THREE.LineBasicMaterial({ color: 0xffffff }))); });
      g.add(c); x += w + 0.4;
    }
    const grid = new THREE.GridHelper(8, 16, 0x334, 0x223); grid.position.set(sx + 3, 0, L.bbox.min.z + 1.5); g.add(grid);
  }
}

// ---------------------------------------------------------------- camera
function frameRoom(L: Loaded, kind: 'home' | 'top' | 'front' | 'side', box: THREE.Box3 = L.bbox) {
  const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
  const r = Math.max(s.x, s.z);
  const targets = { home: new THREE.Vector3(c.x + r * 0.7, s.y * 2.2 + r * 0.45, c.z + r * 1.1), top: new THREE.Vector3(c.x, r * 1.5 + 2, c.z + 0.01), front: new THREE.Vector3(c.x, c.y, c.z + r * 1.6), side: new THREE.Vector3(c.x + r * 1.6, c.y, c.z) };
  state.view = kind === 'top' ? 'layout' : state.view === 'walk' ? 'dollhouse' : state.view;
  camera.position.copy(targets[kind]); orbit.target.copy(c); orbit.update();
}

function frameKnolling(L: Loaded) { const c = L.bbox; camera.position.set(c.max.x + 3.5, 5, c.min.z + 3.5); orbit.target.set(c.max.x + 3.2, 0, c.min.z + 0.8); orbit.update(); }
const keys = new Set<string>(); let yaw = 0, pitch = 0;
function enterWalk(L: Loaded) {
  const c = L.bbox.getCenter(new THREE.Vector3());
  camera.position.set(c.x - 1.2, state.eye, c.z - 1.2); yaw = Math.PI * 0.75; pitch = 0;
}
function updateWalk(dt: number) {
  if (state.view !== 'walk' || !current) return;
  const sp = (keys.has('shift') ? 3.2 : 1.6) * dt;
  const f = new THREE.Vector3(Math.sin(yaw), 0, -Math.cos(yaw)), r = new THREE.Vector3(Math.cos(yaw), 0, Math.sin(yaw));
  const mv = new THREE.Vector3();
  if (keys.has('w') || keys.has('arrowup')) mv.add(f); if (keys.has('s') || keys.has('arrowdown')) mv.sub(f);
  if (keys.has('d') || keys.has('arrowright')) mv.add(r); if (keys.has('a') || keys.has('arrowleft')) mv.sub(r);
  if (touchStick.lengthSq() > 0.01) { mv.addScaledVector(f, -touchStick.y).addScaledVector(r, touchStick.x); }
  if (mv.lengthSq()) camera.position.addScaledVector(mv.lengthSq() > 1 ? mv.normalize() : mv, sp);
  const b = current.bbox, m = 0.3; // collision: stay inside wall shell (floor & ceiling fixed by eye height)
  camera.position.x = THREE.MathUtils.clamp(camera.position.x, b.min.x + m, b.max.x - m);
  camera.position.z = THREE.MathUtils.clamp(camera.position.z, b.min.z + m, b.max.z - m);
  for (const o of current.objects.children) { // simple AABB push-out from furniture
    const [w, , d] = o.userData.dims, p = new THREE.Vector3().setFromMatrixPosition((o as THREE.Mesh).matrix);
    const dx = camera.position.x - p.x, dz = camera.position.z - p.z, hx = w / 2 + m, hz = d / 2 + m;
    if (Math.abs(dx) < hx && Math.abs(dz) < hz) { if (hx - Math.abs(dx) < hz - Math.abs(dz)) camera.position.x = p.x + Math.sign(dx || 1) * hx; else camera.position.z = p.z + Math.sign(dz || 1) * hz; }
  }
  camera.position.y = state.eye;
  camera.rotation.set(pitch, -yaw, 0, 'YXZ');
}

// touch: left half = virtual stick (move), right half = look
const touchStick = new THREE.Vector2(); const tch = { move: -1, look: -1, sx: 0, sy: 0, lx: 0, ly: 0 };
canvas.addEventListener('touchstart', (e) => {
  if (state.view !== 'walk') return; e.preventDefault(); const r = canvas.getBoundingClientRect();
  for (const t of Array.from(e.changedTouches)) {
    if (t.clientX - r.left < r.width / 2 && tch.move < 0) { tch.move = t.identifier; tch.sx = t.clientX; tch.sy = t.clientY; }
    else if (tch.look < 0) { tch.look = t.identifier; tch.lx = t.clientX; tch.ly = t.clientY; }
  }
}, { passive: false });
canvas.addEventListener('touchmove', (e) => {
  if (state.view !== 'walk') return; e.preventDefault();
  for (const t of Array.from(e.changedTouches)) {
    if (t.identifier === tch.move) touchStick.set(THREE.MathUtils.clamp((t.clientX - tch.sx) / 60, -1, 1), THREE.MathUtils.clamp((t.clientY - tch.sy) / 60, -1, 1));
    else if (t.identifier === tch.look) { yaw += (t.clientX - tch.lx) * 0.005; pitch = THREE.MathUtils.clamp(pitch - (t.clientY - tch.ly) * 0.005, -1.3, 1.3); tch.lx = t.clientX; tch.ly = t.clientY; }
  }
}, { passive: false });
const touchEnd = (e: TouchEvent) => { for (const t of Array.from(e.changedTouches)) { if (t.identifier === tch.move) { tch.move = -1; touchStick.set(0, 0); } if (t.identifier === tch.look) tch.look = -1; } };
canvas.addEventListener('touchend', touchEnd); canvas.addEventListener('touchcancel', touchEnd);
addEventListener('keydown', (e) => keys.add(e.key.toLowerCase()));
addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));
let drag = false;
canvas.addEventListener('pointerdown', (e) => { if (state.view === 'walk') drag = true; if (state.measure) pick(e); });
addEventListener('pointerup', () => (drag = false));
addEventListener('pointermove', (e) => { if (state.view === 'walk' && (drag || document.pointerLockElement)) { yaw += (e.movementX || 0) * 0.003; pitch = THREE.MathUtils.clamp(pitch - (e.movementY || 0) * 0.003, -1.3, 1.3); } });

// ---------------------------------------------------------------- measure
const mpts: THREE.Vector3[] = []; let mline: THREE.Line | null = null;
function pick(e: PointerEvent) {
  if (!current) return;
  const rc = new THREE.Raycaster(); const r = canvas.getBoundingClientRect();
  rc.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  rc.params.Points = { threshold: 0.03 };
  const hits = rc.intersectObjects(current.group.children.filter((o) => o.visible), true).filter((h) => !(h.object as any).isLineSegments);
  if (!hits.length) return;
  if (mpts.length >= 2) { mpts.length = 0; if (mline) scene.remove(mline); mline = null; $('#measureLabel').style.display = 'none'; }
  mpts.push(hits[0].point.clone());
  if (mpts.length === 2) {
    mline = new THREE.Line(new THREE.BufferGeometry().setFromPoints(mpts), new THREE.LineBasicMaterial({ color: 0xffd23f, depthTest: false })); scene.add(mline);
    const d = mpts[0].distanceTo(mpts[1]);
    $('#measureLabel').textContent = d < 1 ? `${(d * 1000).toFixed(0)} mm` : `${d.toFixed(3)} m`;
  }
}
function updateMeasureLabel() {
  const el = $('#measureLabel');
  if (mpts.length < 2) return;
  const mid = mpts[0].clone().add(mpts[1]).multiplyScalar(0.5).project(camera);
  const r = canvas.getBoundingClientRect();
  el.style.display = 'block'; el.style.left = `${((mid.x + 1) / 2) * r.width}px`; el.style.top = `${((1 - mid.y) / 2) * r.height}px`;
}

// ---------------------------------------------------------------- loop
const clock = new THREE.Clock();
function frame() {
  const dt = Math.min(clock.getDelta(), 0.1);
  if (state.drone && current) {
    const c = current.bbox.getCenter(new THREE.Vector3()), t = clock.elapsedTime * 0.25, r = Math.max(current.bbox.max.x, current.bbox.max.z) * 0.75;
    camera.position.set(c.x + Math.cos(t) * r, 2.2 + Math.sin(t * 1.7) * 0.9, c.z + Math.sin(t) * r); orbit.target.set(c.x, 1.1, c.z);
  }
  updateWalk(dt);
  if (state.view !== 'walk') orbit.update();
  // dollhouse cut-away: fade walls whose inside faces away from the camera
  if (current && (state.view === 'dollhouse' || state.view === 'layout')) {
    for (const w of (state.tab === 'house' ? houseRooms : [current]).flatMap((l) => l.walls)) {
      const toCam = camera.position.clone().sub(w.center);
      const out = state.view === 'layout' ? true : toCam.dot(w.normal) < 0;
      const mat = w.mesh.material as THREE.MeshStandardMaterial; mat.opacity = out ? 0.07 : 1; mat.depthWrite = !out;
    }
  }
  renderer.render(scene, camera);
  updateMeasureLabel();
  const p = camera.position, t = state.view === 'walk' ? p.clone().add(new THREE.Vector3(Math.sin(yaw), Math.sin(pitch), -Math.cos(yaw))) : orbit.target;
  $('#readout').textContent = `pos   ${p.x.toFixed(2)}, ${p.y.toFixed(2)}, ${p.z.toFixed(2)}\nlook  ${t.x.toFixed(2)}, ${t.y.toFixed(2)}, ${t.z.toFixed(2)}`;
  requestAnimationFrame(frame);
}
function resize() {
  const m = $('main'); const dpr = state.quality === 'full' ? Math.min(devicePixelRatio, 3) : Math.min(devicePixelRatio, 1.5);
  renderer.localClippingEnabled = true; renderer.setPixelRatio(dpr); renderer.setSize(m.clientWidth, m.clientHeight, false);
  camera.aspect = m.clientWidth / m.clientHeight; camera.updateProjectionMatrix();
}
addEventListener('resize', resize);

// ---------------------------------------------------------------- UI
function select(L: Loaded) {
  if (current) scene.remove(current.group);
  current = L; scene.add(L.group);
  frameRoom(L, 'home');
  applyMode();
}
async function enterHouse() {
  state.tab = 'house';
  if (current) scene.remove(current.group);
  if (houseGroup) scene.remove(houseGroup);
  houseGroup = new THREE.Group(); let x = 0;
  const alignment = await fetch('/data/house/alignment.json').then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  for (const L of houseRooms) {
    const a = alignment[L.folder]; L.group.rotation.y = a?.yaw ?? 0;
    L.group.position.set(a?.x ?? x, 0, a?.z ?? 0); x += L.bbox.max.x + 1; houseGroup.add(L.group);
  }
  scene.add(houseGroup); houseGroup.updateMatrixWorld(true);
  const all = new THREE.Box3(); for (const L of houseRooms) all.union(L.bbox.clone().applyMatrix4(L.group.matrixWorld));
  if (houseRooms[0]) frameRoom(houseRooms[0], 'home', all);
  applyMode();
}

function writeUrl() {
  const u = new URL(location.href); const p = camera.position, t = orbit.target;
  u.searchParams.set('room', current?.folder ?? ''); u.searchParams.set('view', state.view); u.searchParams.set('src', state.src);
  u.searchParams.set('cam', [p.x, p.y, p.z, t.x, t.y, t.z].map((v) => v.toFixed(2)).join(','));
  return u.toString();
}

async function init() {
  resize();
  const index: string[] = await (await fetch('/data/index.json')).json();
  const list = $('#roomlist');
  const loaded: Record<string, Loaded> = {};
  for (const f of index) {
    const m = await (await fetch(`/data/${f}/manifest.json`)).json();
    const el = document.createElement('div'); el.className = 'item';
    el.innerHTML = `${m.scanName} v${m.version}<small>${m.createdAt.slice(0, 10)} · ${m.counts.frames} frames · ${m.counts.photos} photos</small>`;
    el.onclick = async () => {
      state.tab = 'rooms'; document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('on', (b as HTMLElement).dataset.tab === 'rooms'));
      houseGroup && scene.remove(houseGroup);
      loaded[f] ||= await loadScan(serverSource(f), f); list.querySelectorAll('.item').forEach((x) => x.classList.remove('on')); el.classList.add('on'); select(loaded[f]);
    };
    list.appendChild(el);
    el.dataset.f = f;
  }
  for (const f of index) { houseRooms.push((loaded[f] ||= await loadScan(serverSource(f), f))); }
  const params = new URLSearchParams(location.search);
  const want = params.get('room');
  const first = (list.querySelector(want ? `[data-f="${want}"]` : '.item') as HTMLElement) ?? (list.firstElementChild as HTMLElement);
  state.view = params.get('view') ?? state.view; state.src = params.get('src') ?? state.src;
  first?.click();
  await new Promise((r) => setTimeout(r, 50));
  const cam = params.get('cam')?.split(',').map(Number);
  if (cam?.length === 6 && current) { camera.position.set(cam[0], cam[1], cam[2]); orbit.target.set(cam[3], cam[4], cam[5]); orbit.update(); }
  const wantView = params.get('view') ?? 'dollhouse'; state.view = wantView;   // select() resets walk -> dollhouse; restore from the link
  if (current) { if (wantView === 'walk') enterWalk(current); else if (wantView === 'layout' && !cam) { frameRoom(current, 'top'); state.view = 'layout'; } else if (wantView === 'knolling' && !cam) frameKnolling(current); }
  applyMode();

  $('#views').onclick = (e) => { const v = (e.target as HTMLElement).dataset.view; if (!v || !current) return; state.view = v; if (v === 'walk') { enterWalk(current); camera.near = 0.05; } else if (v === 'layout') frameRoom(current, 'top'), (state.view = 'layout'); else if (v === 'knolling') frameKnolling(current); else frameRoom(current, 'home'); applyMode(); };
  $('#sources').onclick = (e) => { const s = (e.target as HTMLElement).dataset.src; if (s) { state.src = s; applyMode(); } };
  $('#quality').onclick = (e) => { const q = (e.target as HTMLElement).dataset.q; if (q) { state.quality = q; applyMode(); } };
  $('#presets').onclick = (e) => { const p = (e.target as HTMLElement).dataset.p as any; if (p && current) { if (state.view === 'walk') state.view = 'dollhouse'; frameRoom(current, p); applyMode(); } };
  $('#drone').onclick = () => { state.drone = !state.drone; if (state.view === 'walk') state.view = 'dollhouse'; $('#drone').classList.toggle('on', state.drone); applyMode(); };
  $('#measure').onclick = () => { state.measure = !state.measure; $('#measure').classList.toggle('on', state.measure); toast(state.measure ? 'Click two points to measure' : 'Measure off'); };
  $('#zin').onclick = () => { camera.position.lerp(orbit.target, 0.2); };
  $('#zout').onclick = () => { camera.position.sub(orbit.target).multiplyScalar(1.25).add(orbit.target); };
  orbit.addEventListener('start', () => (state.drone = false, $('#drone').classList.remove('on')));
  $('#copy').onclick = async () => { const u = writeUrl(); try { await navigator.clipboard.writeText(u); } catch { /* ignore */ } history.replaceState(null, '', u); toast('View link copied'); };
  document.querySelectorAll<HTMLButtonElement>('.tabs button').forEach((b) => (b.onclick = () => { document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('on', x === b)); b.dataset.tab === 'house' ? enterHouse() : first?.click(); }));
  canvas.addEventListener('click', () => { if (state.view === 'walk' && !state.measure) canvas.requestPointerLock?.(); });
  addEventListener('dragover', (e) => { e.preventDefault(); $('#drop').style.display = 'flex'; });
  addEventListener('dragleave', (e) => { if (!(e as DragEvent).relatedTarget) $('#drop').style.display = 'none'; });
  addEventListener('drop', async (e) => {
    e.preventDefault(); $('#drop').style.display = 'none';
    const file = e.dataTransfer?.files[0]; if (!file) return;
    try {
      const L = await loadScan(zipSource(file.name, new Uint8Array(await file.arrayBuffer())), file.name.replace(/\.zip$/i, ''));
      const el = document.createElement('div'); el.className = 'item'; el.innerHTML = `${L.manifest.scanName} v${L.manifest.version}<small>dropped · ${L.manifest.counts.frames} frames</small>`;
      el.onclick = () => { state.tab = 'rooms'; list.querySelectorAll('.item').forEach((x) => x.classList.remove('on')); el.classList.add('on'); select(L); };
      list.prepend(el); houseRooms.push(L); el.click(); toast('Loaded ' + file.name);
    } catch (err) { toast('Could not read that zip (needs manifest.json + build/)'); console.error(err); }
  });
  (window as any).__ready = true;
  frame();
}
init();
