"""scan-to-3JS refinement loop.

    python -m pipeline refine <export_folder> [--iters N] [--frames K]

Each iteration: render the rebuilt Three.js scene from the exact pose + intrinsics of K real photos (headless Chromium,
viewer/render.html), compare against the photos (SSIM / edge / colour) and the LiDAR depth maps, rank errors per object,
apply the fixes below, log the score. Every iteration is kept in rebuilt/iter_###/ (scene.json, renders, contact sheet).

Automatic fixes: (1) colour re-estimation from photos, (2) dimension/position snap to the LiDAR point cloud,
(3) primitive-variant search (e.g. table: solid vs legs) kept only if its region score improves,
(4) add boxes for unexplained LiDAR clusters (missing objects).
Claude (see .claude/skills/scan-to-3js/SKILL.md) can add visual judgement + richer object models between iterations.

Needs the viewer dev server on :5187 (started automatically if not running).
"""
import copy, csv, io, json, os, shutil, struct, subprocess, sys, time, urllib.request
from pathlib import Path
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage.metrics import structural_similarity

from .common import Export, CFG, ROOT, mat4
from .shell import dist_to_planes
from .objects import in_box, measured_extent, dim_error, valid_axes

PORT = 5187


def log(m):
    print(f"[refine] {m}", flush=True)


# ------------------------------------------------------------------ rendering
def ensure_server():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/render.html", timeout=2); return None
    except Exception:
        pass
    p = subprocess.Popen(["npm", "run", "dev"], cwd=ROOT / "viewer", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/render.html", timeout=2); return p
        except Exception:
            continue
    raise SystemExit("viewer dev server did not start (cd viewer && npm install && npm run dev)")


def render(scene, frames, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    job = dict(scene=scene, outDir=str(out_dir), frames=[dict(transform=f["transform"], rgbIntrinsics=f["rgbIntrinsics"], rgbWidth=f["rgbWidth"], rgbHeight=f["rgbHeight"]) for f in frames])
    jp = out_dir / "job.json"; json.dump(job, open(jp, "w"))
    env = dict(os.environ)
    if "CHROME" not in env:
        for c in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",):
            if os.path.exists(c): env["CHROME"] = c
    r = subprocess.run(["node", str(ROOT / "tools" / "render_frames.mjs"), str(jp)], capture_output=True, text=True, env=env)
    if r.returncode:
        raise SystemExit("render failed:\n" + r.stderr[-1500:])
    ids = json.load(open(out_dir / "ids.json"))
    outs = []
    for k in range(len(frames)):
        col = np.array(Image.open(out_dir / f"color_{k}.png").convert("RGB"))
        idm = np.array(Image.open(out_dir / f"id_{k}.png").convert("RGB"))[..., 0]
        dp = np.array(Image.open(out_dir / f"depth_{k}.png").convert("RGB")).astype(np.float32)
        outs.append(dict(color=col, id=idm, depth=(dp[..., 0] * 256 + dp[..., 1]) / 1000.0))
        job_cleanup = [out_dir / f"id_{k}.png", out_dir / f"depth_{k}.png"]
        for p in job_cleanup: p.unlink()
    return ids, outs


# ------------------------------------------------------------------ scoring
def norm_gray(img):
    g = img.astype(np.float32).mean(2)
    return (g - g.mean()) / (g.std() + 1e-6)


def edges(g):
    return np.hypot(ndimage.sobel(g, 0), ndimage.sobel(g, 1))


def lidar_on_rgb(ex, fr, shape):
    """LiDAR depth/conf resampled (nearest) to the RGB resolution."""
    d, c = ex.depth(fr), ex.conf(fr)
    d = np.where(c >= CFG["min_confidence"], d, 0)
    ys = np.minimum(((np.arange(shape[0]) + 0.5) * d.shape[0] / shape[0]).astype(int), d.shape[0] - 1)
    xs = np.minimum(((np.arange(shape[1]) + 0.5) * d.shape[1] / shape[1]).astype(int), d.shape[1] - 1)
    return d[np.ix_(ys, xs)]


def score_frames(ex, frames, rend):
    ss, ec, l1, dm, dw = [], [], [], [], []
    for fr, r in zip(frames, rend):
        real = ex.rgb(fr); gr, gx = norm_gray(real), norm_gray(r["color"])
        ss.append(structural_similarity(gr, gx, data_range=float(max(gr.max(), gx.max()) - min(gr.min(), gx.min()))))
        er, ex_ = edges(gr), edges(gx); ec.append(float(np.corrcoef(er.ravel(), ex_.ravel())[0, 1]))
        l1.append(float(np.abs(gr - gx).mean()))
        ld = lidar_on_rgb(ex, fr, real.shape[:2]); m = (ld > 0) & (r["depth"] > 0)
        err = np.abs(ld - r["depth"])[m]; dm.append(float(err.mean())); dw.append(float((err < 0.05).mean()))
    s = dict(ssim=float(np.mean(ss)), edge_corr=float(np.mean(ec)), color_l1=float(np.mean(l1)), depth_mae_m=float(np.mean(dm)), depth_within_5cm=float(np.mean(dw)))
    s["composite"] = 0.4 * s["ssim"] + 0.2 * max(s["edge_corr"], 0) + 0.4 * s["depth_within_5cm"]
    return s


def project_box(obj, fr):
    """2D bbox (x0,y0,x1,y1) of the object's solid box in the frame, or None."""
    T = mat4(obj["transform"]); hx, hy, hz = np.array(obj["dimensions"]) / 2
    corners = np.array([[sx * hx, sy * hy, sz * hz, 1] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) @ T.T
    inv = np.linalg.inv(mat4(fr["transform"])); c = corners[:, :3] @ inv[:3, :3].T + inv[:3, 3]
    if (c[:, 2] > -0.05).any():
        return None
    K = fr["rgbIntrinsics"]; d = -c[:, 2]
    u = K[0][0] * c[:, 0] / d + K[0][2]; v = K[1][2] - K[1][1] * c[:, 1] / d
    W, H = fr["rgbWidth"], fr["rgbHeight"]
    x0, x1, y0, y1 = int(max(u.min(), 0)), int(min(u.max(), W)), int(max(v.min(), 0)), int(min(v.max(), H))
    return (x0, y0, x1, y1) if x1 - x0 > 4 and y1 - y0 > 4 else None


def roi_error(ex, frames, rend, obj):
    """Depth + normalised-colour error inside the projected box of one object (comparable across variants)."""
    tot, n = 0.0, 0
    for fr, r in zip(frames, rend):
        b = project_box(obj, fr)
        if not b: continue
        x0, y0, x1, y1 = b
        ld = lidar_on_rgb(ex, fr, r["depth"].shape)[y0:y1, x0:x1]; rd = r["depth"][y0:y1, x0:x1]
        m = (ld > 0) & (rd > 0)
        if m.sum() < 30: continue
        gd = np.clip(np.abs(ld - rd)[m], 0, 1).mean()
        gr = norm_gray(ex.rgb(fr))[y0:y1, x0:x1]; gx = norm_gray(r["color"])[y0:y1, x0:x1]
        tot += gd + 0.1 * np.abs(gr - gx).mean(); n += 1
    return tot / n if n else None


def rank_errors(ex, frames, rend, ids, room):
    """Ranked list of per-object problems (worst first)."""
    out = []
    for o in room["objects"]:
        e = roi_error(ex, frames, rend, o)
        if e is not None: out.append(dict(id=o["id"], kind="object-region", error=round(float(e), 4)))
    for gid in ids:
        if gid in (o["id"] for o in room["objects"]): continue
        i = ids.index(gid) + 1; ds, n = 0.0, 0
        for fr, r in zip(frames, rend):
            m = r["id"] == i
            if m.sum() < 50: continue
            ld = lidar_on_rgb(ex, fr, r["depth"].shape); mm = m & (ld > 0)
            if mm.sum() < 30: continue
            ds += float(np.abs(ld - r["depth"])[mm].mean()); n += 1
        if n: out.append(dict(id=gid, kind="surface-depth", error=round(ds / n, 4)))
    return sorted(out, key=lambda d: -d["error"])


# ------------------------------------------------------------------ fixes
def hex2rgb(h): return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
def rgb2hex(c): return "#%02x%02x%02x" % tuple(int(round(float(x))) for x in np.clip(c, 0, 255))


def group_of(gid, room):
    if gid == "floor": return ("palette", "floor")
    if gid == "ceiling": return ("palette", "ceiling")
    if gid.startswith("wall"): return ("palette", "wall")
    if gid.startswith("door"): return ("palette", "door")
    if gid.startswith("window"): return ("palette", "window")
    return ("object", gid)


def fix_colors(ex, frames, rend, ids, scene):
    """Multiplicative per-channel correction so rendered region means match photo means (damped)."""
    acc = {}
    for fr, r in zip(frames, rend):
        real = ex.rgb(fr).astype(np.float32)
        for gid in ids:
            m = r["id"] == ids.index(gid) + 1
            if m.sum() < 80: continue
            g = group_of(gid, scene["room"]); a = acc.setdefault(g, [np.zeros(3), np.zeros(3), 0])
            a[0] += real[m].sum(0); a[1] += r["color"][m].astype(np.float32).sum(0); a[2] += int(m.sum())
    changed = 0
    for (kind, name), (rs, xs, n) in acc.items():
        gain = (rs / np.maximum(xs, 1)) ** 0.85
        if kind == "palette": cur = scene["palette"].get(name) or {"floor": "#8a6240", "wall": "#d9d4c4", "ceiling": "#f2f2f2", "door": "#66411f", "window": "#8cc8ff"}[name]
        else:
            o = next(o for o in scene["room"]["objects"] if o["id"] == name)
            cur = o.get("color") or {"table": "#9a6a3a"}.get(o["category"], "#888888")
        new = rgb2hex(hex2rgb(cur) * gain)
        if new != cur: changed += 1
        if kind == "palette": scene["palette"][name] = new
        else: o["color"] = new
    return changed


def load_points(ex):
    raw = open(ex.build / "pointcloud.bin", "rb").read(); n = struct.unpack("<I", raw[:4])[0]
    return np.frombuffer(raw, np.float32, n * 3, 4).reshape(-1, 3).astype(np.float64)


def snap_dimensions(P, room, tol=0.01):
    """Snap object size/position to the LiDAR extent on axes the scan covers well; keep the RoomPlan prior elsewhere."""
    ch = 0
    for o in room["objects"]:
        me = measured_extent(P, o, room)
        if not me: continue
        lo, hi = me; ok = valid_axes(lo, hi, o); old = np.array(o["dimensions"])
        dims = np.where(ok, hi - lo, old); centre_l = np.where(ok, (lo + hi) / 2, 0.0)
        if np.abs(dims - old).max() > tol or np.abs(centre_l).max() > tol:
            T = mat4(o["transform"]); T[:3, 3] = T[:3, :3] @ centre_l + T[:3, 3]
            o["transform"] = [[round(float(v), 5) for v in row] for row in T]; o["dimensions"] = [round(float(d), 4) for d in dims]; ch += 1
    return ch, None


def add_missing(P, scene, min_pts=400):
    room = scene["room"]; far = dist_to_planes(P, room) > 0.08
    for o in room["objects"]: far &= ~in_box(P, o, 0.15)
    Q = P[far]
    if len(Q) < min_pts: return 0
    g = np.floor(Q / 0.1).astype(int); g -= g.min(0)
    grid = np.zeros(g.max(0) + 1, bool); grid[tuple(g.T)] = True
    lab, n = ndimage.label(grid, structure=np.ones((3, 3, 3)))
    added = 0
    for k in range(1, n + 1):
        pts = Q[lab[tuple(g.T)] == k]
        if len(pts) < min_pts: continue
        lo, hi = pts.min(0), pts.max(0); c = (lo + hi) / 2; d = hi - lo
        T = np.eye(4); T[:3, 3] = [c[0], c[1], c[2]]
        room["objects"].append(dict(id=f"object-auto-{len(room['objects'])}", category="other", dimensions=[round(float(x), 4) for x in d], transform=T.tolist(), confidence="auto"))
        added += 1
    return added


# ------------------------------------------------------------------ outputs
def contact_sheet(ex, frames, rend, path, n=4):
    rows = []
    for fr, r in list(zip(frames, rend))[:n]:
        real = Image.fromarray(ex.rgb(fr)).resize((320, 240)); ren = Image.fromarray(r["color"]).resize((320, 240))
        gr, gx = norm_gray(ex.rgb(fr)), norm_gray(r["color"]); d = np.clip(np.abs(gr - gx) / 3, 0, 1)
        heat = Image.fromarray((np.stack([d, d * 0.4, 1 - d], -1) * 255).astype(np.uint8)).resize((320, 240))
        row = Image.new("RGB", (960, 240)); row.paste(real, (0, 0)); row.paste(ren, (320, 0)); row.paste(heat, (640, 0)); rows.append(row)
    sheet = Image.new("RGB", (960, 240 * len(rows)))
    for i, r in enumerate(rows): sheet.paste(r, (0, 240 * i))
    sheet.save(path)


def write_scene_glb(scene, path):
    import trimesh
    from .shell import build_shell
    pal = {"floor": "#8a6240", "wall": "#d9d4c4", "ceiling": "#f2f2f2", "door": "#66411f", "window": "#8cc8ff", **scene["palette"]}
    sc = trimesh.Scene()
    for name, m in build_shell(scene["room"]).items():
        col = hex2rgb(pal["floor" if name == "floor" else "ceiling" if name == "ceiling" else "wall"])
        m.visual.face_colors = np.append(col, 255).astype(np.uint8); sc.add_geometry(m, node_name=name)
    for o in scene["room"]["objects"]:
        w, h, d = o["dimensions"]; T = mat4(o["transform"])
        parts = [(trimesh.creation.box([w, h, d]), np.eye(4))]
        if o.get("variant") == "legs":
            top = min(0.05, h * 0.1); leg = 0.05; parts = []
            def M(x, y, z):
                A = np.eye(4); A[:3, 3] = [x, y, z]; return A
            parts.append((trimesh.creation.box([w, top, d]), M(0, h / 2 - top / 2, 0)))
            for sx in (-1, 1):
                for sz in (-1, 1): parts.append((trimesh.creation.box([leg, h - top, leg]), M(sx * (w / 2 - leg / 2), -top / 2, sz * (d / 2 - leg / 2))))
        col = np.append(hex2rgb(o.get("color") or "#888888"), 255).astype(np.uint8)
        for i, (b, A) in enumerate(parts):
            b.visual.face_colors = col; sc.add_geometry(b, node_name=f"{o['id']}-{i}", transform=T @ A)
    sc.export(path)


def write_scene_ts(scene, path):
    path.write_text("// Generated by `python -m pipeline refine`. True-scale rebuilt room (meters, Y-up). Each object is its own node.\n"
                    "import { buildRoom, type RoomJson, type Palette } from '../../viewer/src/rebuilt';\n\n"
                    f"export const room: RoomJson = {json.dumps(scene['room'])} as any;\n"
                    f"export const palette: Palette = {json.dumps(scene['palette'])};\n"
                    "export const buildScene = () => buildRoom(room, palette);\n")


# ------------------------------------------------------------------ main loop
def run(export, iters=None, n_frames=12, resume=False):
    iters = iters or CFG["max_iterations_refine"]
    ex = Export(export); P = load_points(ex) if (ex.build / "pointcloud.bin").exists() else None
    if P is None: raise SystemExit("run `python -m pipeline build` first")
    srv = ensure_server()
    out = ex.root / "rebuilt"; out.mkdir(exist_ok=True)
    existing = sorted(out.glob("iter_*")); start = len(existing)     # never overwrite earlier runs
    if resume and (out / "scene.json").exists():
        scene = json.load(open(out / "scene.json")); log("resuming from rebuilt/scene.json")
    else:
        scene = dict(room=copy.deepcopy(ex.rooms[0]), palette={})
        for o in scene["room"]["objects"]:
            if o["category"] == "table": o["variant"] = "legs"      # parametric guess; the loop checks it against LiDAR
    sel = np.linspace(0, len(ex.frames) - 1, n_frames).astype(int); frames = [ex.frames[i] for i in sel]
    csv_path = out / "scores.csv"; new = not csv_path.exists()
    f = open(csv_path, "a", newline=""); w = csv.writer(f)
    if new: w.writerow(["iter", "composite", "ssim", "edge_corr", "color_l1", "depth_mae_m", "depth_within_5cm", "dim_err_m", "objects", "actions"])
    best = -1; stall = 0; tried = {}; best_scene = None
    try:
        for it in range(iters):
            idx = start + it; d = out / f"iter_{idx:03d}"; ids, rend = render(scene, frames, d)
            s = score_frames(ex, frames, rend); s["dim_err_m"] = dim_error(P, scene["room"]); errors = rank_errors(ex, frames, rend, ids, scene["room"])
            json.dump(dict(scene=scene, score=s, ranked_errors=errors), open(d / "scene.json", "w"), indent=1)
            contact_sheet(ex, frames, rend, d / "contact.png")
            for k in range(len(frames)): (d / "job.json").unlink(missing_ok=True)
            actions = []
            if s["composite"] > best + 0.002: stall = 0
            else: stall += 1
            if s["composite"] > best: best, best_scene = s["composite"], copy.deepcopy(scene)
            log(f"iter {idx}: composite {s['composite']:.4f} ssim {s['ssim']:.3f} depth<5cm {s['depth_within_5cm']:.3f} dimErr {s['dim_err_m']} worst {errors[:2]}")
            # --- evaluate last variant flips: revert if worse
            for oid, (old_var, old_err) in list(tried.items()):
                o = next(o for o in scene["room"]["objects"] if o["id"] == oid); e_now = next((e["error"] for e in errors if e["id"] == oid), None)
                if e_now is not None and old_err is not None and e_now > old_err:
                    o["variant"] = old_var; actions.append(f"revert-variant:{oid}")
                else: actions.append(f"keep-variant:{oid}={o.get('variant')}")
                tried[oid] = None
            tried = {k: v for k, v in tried.items() if v}
            w.writerow([idx, f"{s['composite']:.4f}", f"{s['ssim']:.4f}", f"{s['edge_corr']:.4f}", f"{s['color_l1']:.4f}", f"{s['depth_mae_m']:.4f}", f"{s['depth_within_5cm']:.4f}", s["dim_err_m"] if s["dim_err_m"] is None else f"{s['dim_err_m']:.4f}", len(scene["room"]["objects"]), ";".join(actions)]); f.flush()
            if stall >= 2 or it == iters - 1: break
            # --- apply fixes for the next iteration
            nd, _ = snap_dimensions(P, scene["room"]); nc = fix_colors(ex, frames, rend, ids, scene); na = add_missing(P, scene)
            flips = 0
            for e in errors:   # variant search on the worst object that has alternatives and hasn't been tried
                o = next((o for o in scene["room"]["objects"] if o["id"] == e["id"]), None)
                if o and o["category"] == "table" and not o.get("_tried") and e["error"] > 0.03:
                    tried[o["id"]] = (o.get("variant", "solid"), e["error"]); o["variant"] = "solid" if o.get("variant") == "legs" else "legs"; o["_tried"] = True; flips += 1; break
            log(f"  fixes: dims {nd}, colours {nc}, added {na}, variant flips {flips}")
    finally:
        f.close()
        if srv: srv.terminate()
    final = best_scene or scene
    for o in final["room"]["objects"]: o.pop("_tried", None)
    json.dump(final, open(out / "scene.json", "w"), indent=1); write_scene_glb(final, out / "scene.glb"); write_scene_ts(final, out / "scene.ts")
    log(f"done. best composite {best:.4f}; outputs in {out}")
    return best
