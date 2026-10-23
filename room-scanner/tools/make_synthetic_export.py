#!/usr/bin/env python3
"""Generate a fake but schema-valid Room Scanner export (box room, door, window, table).

    python tools/make_synthetic_export.py [--out data] [--name "Test Room"] [--frames 150]

Depth/RGB are ray-cast analytically, so pipeline + viewer can be tested with no iPhone.
Coordinates: meters, right-handed, Y-up, floor at y=0, room spans x:[0,W], z:[0,D].
Camera-to-world transform: ARKit convention (camera looks down -Z, +Y up, +X right).
"""
import argparse, datetime, hashlib, json, math, os, struct, sys
from pathlib import Path
import numpy as np
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "1.0.0"

README_FOR_AI = (
    "ROOM SCAN EXPORT (Room Scanner schema 1.0.0). Units: meters. Right-handed, Y-up (ARKit). "
    "Camera space: +X right, +Y up, camera looks down -Z. Layout: manifest.json (this file), "
    "depth/frames.json (one entry per captured frame: 4x4 camera-to-world 'transform' row-major, depth 'intrinsics' "
    "for 256x192 depth, 'rgbIntrinsics' for the color jpg, exposure/iso/white balance, confidence), "
    "depth/*.png (16-bit PNG, millimeters, 0=invalid), depth/*_conf.png (0/1/2 ARKit confidence), depth/*.jpg (RGB frame "
    "matching each depth map), photos/ (full-res keyframes, same frameIndex naming), mesh/mesh.ply (ARKit mesh, per-vertex "
    "'classification': 0 none,1 wall,2 floor,3 ceiling,4 table,5 seat,6 window,7 door), rooms/room-N.json (RoomPlan "
    "CapturedRoom: walls, doors, windows, openings, objects with category+dimensions+transform) and optional .usdz. "
    "To rebuild: back-project each depth pixel (u,v,d): X=(u-cx)/fx*d, Y=-(v-cy)/fy*d, Z=-d, then multiply by 'transform'. "
    "Colour points from the RGB jpg using rgbIntrinsics. Build the architectural shell from rooms/*.json, place each object "
    "at its true-scale dimensions, and compare renders from each frame's pose against its photo to refine."
)

# ---------------------------------------------------------------- scene
def scene(cfg):
    r = cfg["room"]
    W, D, H = r["width"], r["depth"], r["height"]
    table = dict(c=np.array([3.7, 0.0, 2.8]), size=np.array([1.2, 0.75, 0.7]))  # base center x,z ; (sx, h, sz)
    door = dict(wall="z0", u0=0.9, u1=1.8, h=2.05)      # on wall z=0, u along +x
    window = dict(wall="x1", u0=1.2, u1=2.8, v0=0.95, v1=2.1)  # on wall x=W, u along +z
    return W, D, H, table, door, window


def surf_color(p, n_id, W, D, H, table, door, window):
    """Albedo for hit points p (N,3) on surface ids (N,)."""
    col = np.zeros((len(p), 3), np.float32)
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    wall = np.array([0.86, 0.84, 0.78], np.float32)
    # ids: 0 floor, 1 ceiling, 2 x0, 3 x1, 4 z0, 5 z1, 6 table
    m = n_id == 0
    chk = ((np.floor(x * 4) + np.floor(z * 4)) % 2).astype(np.float32)
    col[m] = (np.array([0.55, 0.38, 0.22]) * (0.85 + 0.15 * chk[m, None])).astype(np.float32)
    col[n_id == 1] = [0.95, 0.95, 0.95]
    for i in (2, 3, 4, 5):
        col[n_id == i] = wall
    # door on z=0
    m = (n_id == 4) & (x > door["u0"]) & (x < door["u1"]) & (y < door["h"])
    col[m] = [0.40, 0.25, 0.12]
    m2 = m & (np.abs(x - (door["u1"] - 0.1)) < 0.03) & (np.abs(y - 1.0) < 0.05)
    col[m2] = [0.85, 0.7, 0.2]
    # window on x=W
    m = (n_id == 3) & (z > window["u0"]) & (z < window["u1"]) & (y > window["v0"]) & (y < window["v1"])
    col[m] = [0.55, 0.78, 0.95]
    mf = m & ((np.abs(z - (window["u0"] + window["u1"]) / 2) < 0.03) | (np.abs(y - (window["v0"] + window["v1"]) / 2) < 0.03))
    col[mf] = [0.95, 0.95, 0.95]
    # table
    m = n_id == 6
    top = m & (y > table["size"][1] - 1e-3)
    col[m] = [0.45, 0.28, 0.14]
    col[top] = [0.62, 0.42, 0.22]
    # skirting
    m = np.isin(n_id, (2, 3, 4, 5)) & (y < 0.1)
    col[m] = [0.95, 0.95, 0.92]
    return col


NORMALS = np.array([[0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]], np.float32)


def raycast(o, d, W, D, H, table):
    """o:(3,), d:(N,3) unit. Returns t (N,), surface id (N,). Interior of box + table AABB."""
    N = len(d)
    best = np.full(N, np.inf, np.float32)
    sid = np.full(N, -1, np.int8)
    eps = 1e-9
    planes = [(1, 0.0, 0), (1, H, 1), (0, 0.0, 2), (0, W, 3), (2, 0.0, 4), (2, D, 5)]
    for axis, val, idx in planes:
        t = (val - o[axis]) / np.where(np.abs(d[:, axis]) < eps, eps, d[:, axis])
        hit = t > 0.02
        p = o[None, :] + t[:, None] * d
        ok = hit & (p[:, 0] > -1e-3) & (p[:, 0] < W + 1e-3) & (p[:, 1] > -1e-3) & (p[:, 1] < H + 1e-3) & (p[:, 2] > -1e-3) & (p[:, 2] < D + 1e-3)
        upd = ok & (t < best)
        best[upd] = t[upd]; sid[upd] = idx
    # table AABB slab
    lo = table["c"] + np.array([-table["size"][0] / 2, 0, -table["size"][2] / 2])
    hi = table["c"] + np.array([table["size"][0] / 2, table["size"][1], table["size"][2] / 2])
    inv = 1.0 / np.where(np.abs(d) < eps, eps, d)
    t1 = (lo[None] - o[None]) * inv; t2 = (hi[None] - o[None]) * inv
    tmin = np.minimum(t1, t2).max(axis=1); tmax = np.maximum(t1, t2).min(axis=1)
    hit = (tmax >= tmin) & (tmin > 0.02) & (tmin < best)
    best[hit] = tmin[hit]; sid[hit] = 6
    return best, sid


def table_normal(p, table):
    lo = table["c"] + np.array([-table["size"][0] / 2, 0, -table["size"][2] / 2])
    hi = table["c"] + np.array([table["size"][0] / 2, table["size"][1], table["size"][2] / 2])
    n = np.zeros_like(p)
    dl = np.abs(p - lo); dh = np.abs(p - hi)
    d = np.concatenate([dl, dh], axis=1)
    k = d.argmin(axis=1)
    for i, ax in enumerate([0, 1, 2]):
        n[k == i, ax] = -1
        n[k == i + 3, ax] = 1
    return n


def cam_rays(w, h, K):
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    x = (u - K[0][2]) / K[0][0]; y = -(v - K[1][2]) / K[1][1]
    r = np.stack([x, y, -np.ones_like(x)], -1).reshape(-1, 3)
    return r  # camera-space, z=-1 (not normalised): t along it == planar depth


def pose(pos, yaw, pitch):
    fwd = np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch)])
    right = np.cross(fwd, [0, 1, 0]); right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    T = np.eye(4)
    T[:3, 0], T[:3, 1], T[:3, 2], T[:3, 3] = right, up, -fwd, pos
    return T


def hashfile(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mat(T):
    return [[round(float(v), 6) for v in row] for row in T]


def build_mesh_ply(path, W, D, H, table, step=0.25):
    """Tessellated ARKit-style mesh with per-vertex classification."""
    verts, faces, cls = [], [], []

    def quad_grid(origin, du, dv, nu, nv, c):
        base = len(verts)
        for j in range(nv + 1):
            for i in range(nu + 1):
                verts.append(origin + du * i / nu + dv * j / nv); cls.append(c)
        for j in range(nv):
            for i in range(nu):
                a = base + j * (nu + 1) + i
                faces.extend([(a, a + 1, a + nu + 2), (a, a + nu + 2, a + nu + 1)])

    n = lambda L: max(1, int(round(L / step)))
    O = np.array
    quad_grid(O([0, 0, 0.]), O([W, 0, 0.]), O([0, 0, D]), n(W), n(D), 2)       # floor
    quad_grid(O([0, H, 0.]), O([W, 0, 0.]), O([0, 0, D]), n(W), n(D), 3)       # ceiling
    quad_grid(O([0, 0, 0.]), O([W, 0, 0.]), O([0, H, 0.]), n(W), n(H), 1)      # z=0 wall
    quad_grid(O([0, 0, D]), O([W, 0, 0.]), O([0, H, 0.]), n(W), n(H), 1)       # z=D
    quad_grid(O([0, 0, 0.]), O([0, 0, D]), O([0, H, 0.]), n(D), n(H), 1)       # x=0
    quad_grid(O([W, 0, 0.]), O([0, 0, D]), O([0, H, 0.]), n(D), n(H), 1)       # x=W
    c, s = table["c"], table["size"]
    lo = c + O([-s[0] / 2, 0, -s[2] / 2])
    quad_grid(lo + O([0, s[1], 0.]), O([s[0], 0, 0.]), O([0, 0, s[2]]), 4, 3, 4)
    for o, a, b in [(lo, [s[0], 0, 0], [0, s[1], 0]), (lo + O([0, 0, s[2]]), [s[0], 0, 0], [0, s[1], 0]),
                    (lo, [0, 0, s[2]], [0, s[1], 0]), (lo + O([s[0], 0, 0]), [0, 0, s[2]], [0, s[1], 0])]:
        quad_grid(o, O(a, float), O(b, float), 4, 3, 4)
    V = np.array(verts, np.float32); F = np.array(faces, np.int32); C = np.array(cls, np.uint8)
    with open(path, "wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\ncomment units meters, Y-up right-handed\n"
                 f"element vertex {len(V)}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar classification\n"
                 f"element face {len(F)}\nproperty list uchar int vertex_indices\nend_header\n").encode())
        for v, k in zip(V, C):
            f.write(struct.pack("<fffB", *v, int(k)))
        for tri in F:
            f.write(struct.pack("<Biii", 3, *tri))


def room_json(W, D, H, table, door, window):
    def T(x, y, z, yaw=0.0):
        c, s = math.cos(yaw), math.sin(yaw)
        return mat([[c, 0, s, x], [0, 1, 0, y], [-s, 0, c, z], [0, 0, 0, 1]])
    t = 0.1
    walls = [
        dict(id="wall-0", dimensions=[W, H, t], transform=T(W / 2, H / 2, 0.0, 0.0)),
        dict(id="wall-1", dimensions=[D, H, t], transform=T(W, H / 2, D / 2, -math.pi / 2)),
        dict(id="wall-2", dimensions=[W, H, t], transform=T(W / 2, H / 2, D, math.pi)),
        dict(id="wall-3", dimensions=[D, H, t], transform=T(0.0, H / 2, D / 2, math.pi / 2)),
    ]
    dw = door["u1"] - door["u0"]
    doors = [dict(id="door-0", parent="wall-0", dimensions=[dw, door["h"], t], transform=T((door["u0"] + door["u1"]) / 2, door["h"] / 2, 0.0, 0.0))]
    ww, wh = window["u1"] - window["u0"], window["v1"] - window["v0"]
    windows = [dict(id="window-0", parent="wall-1", dimensions=[ww, wh, t], transform=T(W, (window["v0"] + window["v1"]) / 2, (window["u0"] + window["u1"]) / 2, -math.pi / 2))]
    objects = [dict(id="object-0", category="table", dimensions=[float(table["size"][0]), float(table["size"][1]), float(table["size"][2])],
                    transform=T(float(table["c"][0]), float(table["size"][1] / 2), float(table["c"][2])), confidence="high")]
    return dict(id="room-0", name="Test Room", units="meters", ceilingHeight=H,
                floorPolygon=[[0, 0], [W, 0], [W, D], [0, D]], walls=walls, doors=doors, windows=windows, openings=[], objects=objects)


def main():
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))["synthetic"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data"))
    ap.add_argument("--name", default="Test Room")
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--frames", type=int, default=cfg["frames"])
    a = ap.parse_args()

    rng = np.random.default_rng(cfg["seed"])
    W, D, H, table, door, window = scene(cfg)
    date = datetime.date.today().isoformat()
    folder = f"{a.name.replace(' ', '-')}_v{a.version}_{date}"
    root = Path(a.out) / folder
    if root.exists():
        sys.exit(f"refusing to overwrite existing scan {root} (use --version N+1)")
    for sub in ("depth", "photos", "mesh", "rooms"):
        (root / sub).mkdir(parents=True)

    dW, dH = 256, 192
    dK = [[182.0, 0, 127.7], [0, 182.0, 96.2], [0, 0, 1]]
    rW, rH = 640, 480
    rK = [[455.0, 0, 320.0], [0, 455.0, 240.0], [0, 0, 1]]
    dRays = cam_rays(dW, dH, dK); rRays = cam_rays(rW, rH, rK)
    light = np.array([W / 2, H - 0.1, D / 2])

    frames = []
    n = a.frames
    for i in range(n):
        f = i / n
        ang = f * 2 * math.pi * 3
        r = 0.6 + 0.5 * math.sin(f * 2 * math.pi * 2)
        pos = np.array([2.1 + r * math.cos(ang * 0.5), 1.35 + 0.15 * math.sin(ang), 1.6 + r * 0.7 * math.sin(ang * 0.5)])
        yaw = ang; pitch = math.radians(-12 + 28 * math.sin(f * 2 * math.pi * 5))
        T = pose(pos, yaw, pitch)
        R, o = T[:3, :3], T[:3, 3]
        name = f"{i:05d}"

        # depth (planar, mm) + confidence
        t, _ = raycast(o, (dRays @ R.T), W, D, H, table)
        t = np.where(np.isfinite(t), t, 0)
        noise = rng.normal(0, 0.004, t.shape) * t
        dmm = np.clip((t + noise) * 1000, 0, 65535).astype(np.uint16).reshape(dH, dW)
        conf = np.where(t > 3.5, 1, 2).astype(np.uint8).reshape(dH, dW)
        conf[rng.random(conf.shape) < 0.01] = 0
        dmm[conf == 0] = 0
        Image.fromarray(dmm).save(root / "depth" / f"{name}.png")
        Image.fromarray(conf).save(root / "depth" / f"{name}_conf.png")

        # rgb
        dirs = rRays @ R.T
        tr, sid = raycast(o, dirs, W, D, H, table)
        ok = np.isfinite(tr)
        tr = np.where(ok, tr, 1.0); sid = np.where(ok, sid, 0)
        p = o[None] + tr[:, None] * dirs
        alb = surf_color(p, sid, W, D, H, table, door, window)
        nrm = NORMALS[sid].copy()
        tm = sid == 6
        if tm.any():
            nrm[tm] = table_normal(p[tm], table)
        L = light[None] - p; Ld = np.linalg.norm(L, axis=1, keepdims=True); L /= Ld
        lam = np.clip((nrm * L).sum(1), 0, 1)
        shade = 0.35 + 0.65 * lam / (1 + 0.04 * Ld[:, 0] ** 2)
        img = alb * shade[:, None] * 1.25 + rng.normal(0, 0.012, alb.shape)
        img = (np.clip(img, 0, 1) ** (1 / 1.1) * 255).astype(np.uint8).reshape(rH, rW, 3)
        im = Image.fromarray(img)
        im.save(root / "depth" / f"{name}.jpg", quality=88)
        if i % 3 == 0:  # full-res keyframe stand-in (synthetic: upscaled)
            im.resize((1280, 960), Image.BICUBIC).save(root / "photos" / f"{name}.jpg", quality=90)

        frames.append(dict(
            frameIndex=i, timestamp=round(i / 10.0, 3), trackingState="normal",
            camera=dict(exposureDuration=0.0167, iso=100.0 + 50 * math.sin(f * 6), shutter=0.0167, whiteBalance=dict(r=1.9, g=1.0, b=1.6)),
            confidence=round(float((conf >= 1).mean()), 4),
            file=f"depth/{name}.png", confidenceFile=f"depth/{name}_conf.png", width=dW, height=dH, intrinsics=dK,
            rgb=f"depth/{name}.jpg", rgbWidth=rW, rgbHeight=rH, rgbIntrinsics=rK,
            photo=f"photos/{name}.jpg" if i % 3 == 0 else None, transform=mat(T)))

    json.dump(dict(schemaVersion=SCHEMA_VERSION, units="meters",
                   poseConvention="transform = 4x4 row-major camera-to-world; camera looks down -Z, +Y up, +X right",
                   frames=frames), open(root / "depth" / "frames.json", "w"), indent=1)

    build_mesh_ply(root / "mesh" / "mesh.ply", W, D, H, table)
    room = room_json(W, D, H, table, door, window)
    json.dump(room, open(root / "rooms" / "room-0.json", "w"), indent=1)

    (root / "README_FOR_AI.md").write_text("# Room scan export\n\n" + README_FOR_AI + "\n\nSee docs/SCHEMA.md in the Room Scanner project for the full specification.\n")
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append(dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size, sha256=hashfile(p)))
    manifest = dict(
        schemaVersion=SCHEMA_VERSION, scanName=a.name, version=a.version, folderName=folder,
        createdAt=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), synthetic=True,
        device=dict(model="Synthetic (iPhone 16 Pro Max simulated)", iosVersion="17.0", app="make_synthetic_export.py"),
        units="meters", coordinateSystem=dict(handedness="right", up="+Y", cameraForward="-Z"),
        counts=dict(frames=n, photos=sum(1 for f in frames if f["photo"]), rooms=1),
        depth=dict(format="uint16 PNG", width=dW, height=dH, scale="millimeters, 0 = invalid"),
        files=files,
        rooms=[dict(id="room-0", name=room["name"], file="rooms/room-0.json", usdz=None, floorArea=W * D, ceilingHeight=H,
                    boundingBox=dict(min=[0, 0, 0], max=[W, H, D]), originTransform=mat(np.eye(4)))],
        README_FOR_AI=README_FOR_AI)
    json.dump(manifest, open(root / "manifest.json", "w"), indent=1)
    print(root)


if __name__ == "__main__":
    main()
