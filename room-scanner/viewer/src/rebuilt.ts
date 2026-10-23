// Shared by the Room Explorer ("Rebuilt" source) and the headless refinement renderer.
import * as THREE from 'three';

export type Mat = number[][];
export interface Surface { id: string; parent?: string | null; dimensions: number[]; transform: Mat }
export interface RoomObject extends Surface { category: string; color?: string; variant?: string }
export interface RoomJson { id: string; name: string; ceilingHeight: number; floorPolygon: number[][]; walls: Surface[]; doors: Surface[]; windows: Surface[]; openings: Surface[]; objects: RoomObject[] }
export interface Palette { floor?: string; wall?: string; ceiling?: string; door?: string; window?: string }
export interface Built { shell: THREE.Group; objects: THREE.Group; walls: { mesh: THREE.Mesh; center: THREE.Vector3; normal: THREE.Vector3 }[] }

export const m4 = (m: Mat) => new THREE.Matrix4().set(...(m.flat() as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]));
const CAT_COLOR: Record<string, string> = { table: '#9a6a3a', chair: '#4a7fb0', sofa: '#7a5a9a', storage: '#5a8a6a', bed: '#b0705a' };

/** Object primitive builders. `variant` lets the refinement loop try alternatives and keep the best-scoring one. */
export function buildObject(o: RoomObject): THREE.Group {
  const g = new THREE.Group();
  const [w, h, d] = o.dimensions;
  const mat = new THREE.MeshStandardMaterial({ color: o.color ?? CAT_COLOR[o.category] ?? '#888888', roughness: 0.7 });
  const box = (sx: number, sy: number, sz: number, x: number, y: number, z: number) => { const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat); m.position.set(x, y, z); g.add(m); };
  if (o.category === 'table' && o.variant === 'legs') {
    const top = Math.min(0.05, h * 0.1), leg = 0.05;
    box(w, top, d, 0, h / 2 - top / 2, 0);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) box(leg, h - top, leg, sx * (w / 2 - leg / 2), -top / 2, sz * (d / 2 - leg / 2));
  } else box(w, h, d, 0, 0, 0);
  g.matrixAutoUpdate = false; g.matrix.copy(m4(o.transform));
  g.userData = { id: o.id, category: o.category, dims: o.dimensions, variant: o.variant ?? 'solid' };
  return g;
}

export function buildRoom(room: RoomJson, pal: Palette = {}): Built {
  const shell = new THREE.Group(), objects = new THREE.Group(), walls: Built['walls'] = [];
  const wallMat = () => new THREE.MeshStandardMaterial({ color: pal.wall ?? '#d9d4c4', roughness: 0.95, side: THREE.DoubleSide, transparent: true });
  const sh = new THREE.Shape(room.floorPolygon.map(([x, z]) => new THREE.Vector2(x, -z)));
  const floor = new THREE.Mesh(new THREE.ShapeGeometry(sh).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial({ color: pal.floor ?? '#8a6240', roughness: 0.8, side: THREE.DoubleSide }));
  const ceil = new THREE.Mesh(new THREE.ShapeGeometry(sh).rotateX(-Math.PI / 2).translate(0, room.ceilingHeight, 0), new THREE.MeshStandardMaterial({ color: pal.ceiling ?? '#f2f2f2', roughness: 1, side: THREE.DoubleSide }));
  floor.name = 'floor'; ceil.name = 'ceiling'; floor.userData.id = 'floor'; ceil.userData.id = 'ceiling';
  shell.add(floor, ceil);
  for (const w of room.walls) {
    const T = m4(w.transform), inv = T.clone().invert();
    const [W, H, t] = w.dimensions;
    const shape = new THREE.Shape([new THREE.Vector2(-W / 2, -H / 2), new THREE.Vector2(W / 2, -H / 2), new THREE.Vector2(W / 2, H / 2), new THREE.Vector2(-W / 2, H / 2)]);
    for (const o of [...room.doors, ...room.windows, ...room.openings].filter((o) => o.parent === w.id)) {
      const c = new THREE.Vector3().setFromMatrixPosition(m4(o.transform)).applyMatrix4(inv);
      const [dw, dh] = o.dimensions, p = new THREE.Path();
      p.moveTo(c.x - dw / 2, c.y - dh / 2); p.lineTo(c.x + dw / 2, c.y - dh / 2); p.lineTo(c.x + dw / 2, c.y + dh / 2); p.lineTo(c.x - dw / 2, c.y + dh / 2); p.closePath();
      shape.holes.push(p);
    }
    const mesh = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: t, bevelEnabled: false }).translate(0, 0, -t / 2), wallMat());
    mesh.matrixAutoUpdate = false; mesh.matrix.copy(T); mesh.name = 'wall'; mesh.userData.id = w.id;
    shell.add(mesh);
    walls.push({ mesh, center: new THREE.Vector3().setFromMatrixPosition(T), normal: new THREE.Vector3(0, 0, 1).transformDirection(T) });
  }
  for (const d of room.doors) {
    const [dw, dh, t] = d.dimensions;
    const leaf = new THREE.Mesh(new THREE.BoxGeometry(dw * 0.96, dh * 0.98, t * 0.4), new THREE.MeshStandardMaterial({ color: pal.door ?? '#66411f', roughness: 0.7 }));
    leaf.matrixAutoUpdate = false; leaf.matrix.copy(m4(d.transform)); leaf.userData.id = d.id; shell.add(leaf);
  }
  for (const d of room.windows) {
    const [dw, dh, t] = d.dimensions;
    const glass = new THREE.Mesh(new THREE.BoxGeometry(dw, dh, t * 0.15), new THREE.MeshStandardMaterial({ name: 'glass', color: pal.window ?? '#8cc8ff', emissive: pal.window ?? '#8cc8ff', emissiveIntensity: 0.5, transparent: true, opacity: 0.55, roughness: 0.1 }));
    glass.matrixAutoUpdate = false; glass.matrix.copy(m4(d.transform)); glass.userData.id = d.id; shell.add(glass);
  }
  for (const o of room.objects) objects.add(buildObject(o));
  return { shell, objects, walls };
}
