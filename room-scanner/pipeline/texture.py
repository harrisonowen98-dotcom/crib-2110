"""UV-atlas texturing: project the sharpest, most head-on photos onto each texel (soft best-view blend), exposure-matched."""
import numpy as np
import trimesh, xatlas
from PIL import Image
from scipy import ndimage
from .common import mat4, project, sharpness, ev


def unwrap(mesh, res):
    vmap, idx, uv = xatlas.parametrize(np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces, np.uint32))
    return np.asarray(mesh.vertices)[vmap], idx.astype(np.int64), uv


def rasterize(V, F, uv, res):
    """Texel -> (3D position, normal, valid)."""
    pos = np.zeros((res, res, 3), np.float32); nrm = np.zeros((res, res, 3), np.float32); valid = np.zeros((res, res), bool)
    tri_uv = uv[F] * (res - 1)                      # (nF,3,2)
    n_all = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]); n_all /= np.linalg.norm(n_all, axis=1, keepdims=True) + 1e-12
    for i in range(len(F)):
        t = tri_uv[i]
        x0, y0 = np.floor(t.min(0)).astype(int); x1, y1 = np.ceil(t.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, res - 1) + 1), np.arange(max(y0, 0), min(y1, res - 1) + 1))
        if xs.size == 0:
            continue
        a, b, c = t
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-9:
            continue
        l1 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / den
        l2 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / den
        l3 = 1 - l1 - l2
        m = (l1 >= -0.02) & (l2 >= -0.02) & (l3 >= -0.02)
        if not m.any():
            continue
        p = l1[m, None] * V[F[i, 0]] + l2[m, None] * V[F[i, 1]] + l3[m, None] * V[F[i, 2]]
        pos[ys[m], xs[m]] = p; nrm[ys[m], xs[m]] = n_all[i]; valid[ys[m], xs[m]] = True
    return pos, nrm, valid


def bake(ex, V, F, uv, res, depth_tol=0.12, frame_step=1):
    """Return (res,res,3) uint8 texture + stats."""
    pos, nrm, valid = rasterize(V, F, uv, res)
    P = pos[valid]; N = nrm[valid]
    acc = np.zeros((len(P), 3), np.float64); wsum = np.zeros(len(P))
    frames = ex.frames[::frame_step]
    sharp = np.array([sharpness(ex.rgb(f)) for f in frames]); sharp = sharp / (sharp.max() + 1e-9) * 0.7 + 0.3
    evs = np.array([ev(f) for f in frames]); ref = np.median(evs)
    used = 0
    for fr, sw, e in zip(frames, sharp, evs):
        T = mat4(fr["transform"])
        u, v, z = project(P, T, fr["intrinsics"], fr["width"], fr["height"])
        ui, vi = np.floor(u).astype(int), np.floor(v).astype(int)
        ok = (z > 0.2) & (ui >= 1) & (ui < fr["width"] - 1) & (vi >= 1) & (vi < fr["height"] - 1)
        if not ok.any():
            continue
        d = ex.depth(fr)
        meas = np.zeros(len(P)); meas[ok] = d[vi[ok], ui[ok]]
        vis = ok & (meas > 0) & (z < meas + depth_tol) & (z > meas - 0.35)
        view = T[:3, 3][None] - P; dist = np.linalg.norm(view, axis=1); view /= dist[:, None] + 1e-9
        cosv = np.clip((view * N).sum(1), 0, 1)
        vis &= cosv > 0.15
        if not vis.any():
            continue
        Kc = fr["rgbIntrinsics"]
        sc = np.array(Kc)[0, 0] / np.array(fr["intrinsics"])[0, 0]
        ru = np.clip((u[vis] * sc).astype(int), 0, fr["rgbWidth"] - 1); rv = np.clip((v[vis] * sc).astype(int), 0, fr["rgbHeight"] - 1)
        col = ex.rgb(fr)[rv, ru].astype(np.float64) * (ref / e) ** 0.5   # exposure match
        w = (cosv[vis] ** 2) * sw / (dist[vis] ** 2 + 0.25)
        acc[vis] += col * w[:, None]; wsum[vis] += w; used += 1
    tex = np.zeros((res, res, 3), np.uint8)
    got = wsum > 0
    colors = np.zeros((len(P), 3)); colors[got] = acc[got] / wsum[got, None]
    img = np.zeros((res, res, 3), np.float64); known = np.zeros((res, res), bool)
    img[valid] = colors; kv = np.zeros(len(P), bool); kv[got] = True; known[valid] = kv
    # inpaint: unphotographed / gutter texels take nearest known texel colour
    idx = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    img = img[idx[0], idx[1]]
    tex = np.clip(img, 0, 255).astype(np.uint8)
    return tex, dict(texels=int(valid.sum()), photographed=float(got.mean()) if len(got) else 0.0, frames_used=used)


def textured_mesh(ex, mesh, res, frame_step=1):
    V, F, uv = unwrap(mesh, res)
    tex, stats = bake(ex, V, F, uv, res, frame_step=frame_step)
    # bake rows: y = v*(res-1) (row 0 = v 0). trimesh wants GL uv (v up) and flips on glTF export -> (u, 1-v)
    vis = trimesh.visual.TextureVisuals(uv=np.column_stack([uv[:, 0], 1 - uv[:, 1]]), image=Image.fromarray(tex))
    return trimesh.Trimesh(V, F, visual=vis, process=False), stats
