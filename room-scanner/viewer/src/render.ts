// Headless renderer for the scan-to-3JS loop: renders the rebuilt scene from a real photo's exact pose + intrinsics.
// Passes: color (lit), id (flat per-object colour), depth (packed millimetres in R/G). Driven by tools/render_frames.mjs.
import * as THREE from 'three';
import { buildRoom, m4, type RoomJson, type Palette } from './rebuilt';

interface FrameReq { transform: number[][]; rgbIntrinsics: number[][]; rgbWidth: number; rgbHeight: number }
const canvas = document.getElementById('c') as HTMLCanvasElement;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x000000);
let ids: string[] = [];
let meshes: { mesh: THREE.Mesh; id: string; orig: THREE.Material | THREE.Material[] }[] = [];

function load(room: RoomJson, pal: Palette) {
  scene.clear(); meshes = []; ids = [];
  const { shell, objects } = buildRoom(room, pal);
  scene.add(shell, objects);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x555566, 0.9));
  const xs = room.floorPolygon.map((p) => p[0]), zs = room.floorPolygon.map((p) => p[1]);
  const L = new THREE.PointLight(0xffffff, 10, 0, 2); L.position.set((Math.min(...xs) + Math.max(...xs)) / 2, room.ceilingHeight - 0.1, (Math.min(...zs) + Math.max(...zs)) / 2); scene.add(L);
  const walk = (o: THREE.Object3D, id?: string) => {
    id = o.userData.id ?? id;
    if ((o as THREE.Mesh).isMesh && id) { const m = o as THREE.Mesh; meshes.push({ mesh: m, id, orig: m.material }); if (!ids.includes(id)) ids.push(id); }
    o.children.forEach((c) => walk(c, id));
  };
  walk(shell); walk(objects);
  // opaque everywhere (cut-away transparency is a viewer feature only)
  for (const { mesh } of meshes) { const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]; for (const m of mats) { m.transparent = false; m.opacity = 1; } }
  return ids;
}

const depthMat = new THREE.ShaderMaterial({
  side: THREE.DoubleSide,
  vertexShader: 'varying float vZ; void main(){ vec4 mv = modelViewMatrix*vec4(position,1.0); vZ = -mv.z; gl_Position = projectionMatrix*mv; }',
  fragmentShader: 'varying float vZ; void main(){ float mm = clamp(vZ*1000.0,0.0,65535.0); float hi = floor(mm/256.0); float lo = mm-hi*256.0; gl_FragColor = vec4(hi/255.0, lo/255.0, 0.0, 1.0); }',
});

function setPass(pass: 'color' | 'id' | 'depth') {
  meshes.forEach(({ mesh, id, orig }, i) => {
    if (pass === 'color') mesh.material = orig;
    else if (pass === 'depth') mesh.material = depthMat;
    else { const n = ids.indexOf(id) + 1; mesh.material = new THREE.MeshBasicMaterial({ color: new THREE.Color(n / 255, 0, 0), side: THREE.DoubleSide }); }
  });
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.outputColorSpace = pass === 'color' ? THREE.SRGBColorSpace : THREE.LinearSRGBColorSpace;   // id/depth passes must be raw values
}

function setCamera(f: FrameReq) {
  const w = f.rgbWidth, h = f.rgbHeight, K = f.rgbIntrinsics;
  renderer.setSize(w, h, false);
  const cam = new THREE.PerspectiveCamera((2 * Math.atan(h / 2 / K[1][1]) * 180) / Math.PI, w / h, 0.05, 100);
  // principal-point offset (pixels) via view offset
  cam.setViewOffset(w, h, w / 2 - K[0][2], h / 2 - K[1][2], w, h);
  cam.matrixAutoUpdate = false; cam.matrixWorld.copy(m4(f.transform)); cam.matrixWorldInverse.copy(cam.matrixWorld).invert();
  return cam;
}

(window as any).setup = (room: RoomJson, pal: Palette) => load(room, pal);
(window as any).renderFrame = (f: FrameReq) => {
  const cam = setCamera(f); const out: Record<string, string> = {};
  for (const p of ['color', 'id', 'depth'] as const) { setPass(p); renderer.render(scene, cam); out[p] = canvas.toDataURL('image/png'); }
  setPass('color');
  return out;
};
(window as any).__ready = true;
