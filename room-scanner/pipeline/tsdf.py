"""Numpy TSDF fusion + marching cubes."""
import numpy as np
from skimage import measure
from .common import mat4, project


def fuse_tsdf(ex, voxel, bounds, min_conf=1, frame_step=1, trunc_mult=4):
    lo = np.array(bounds[0]) - 0.15; hi = np.array(bounds[1]) + 0.15
    dims = np.ceil((hi - lo) / voxel).astype(int) + 1
    gx, gy, gz = np.meshgrid(*[lo[i] + voxel * np.arange(dims[i]) for i in range(3)], indexing="ij")
    P = np.stack([gx, gy, gz], -1).reshape(-1, 3).astype(np.float32)
    tsdf = np.ones(len(P), np.float32); wgt = np.zeros(len(P), np.float32)
    trunc = voxel * trunc_mult
    for fr in ex.frames[::frame_step]:
        d = ex.depth(fr); c = ex.conf(fr)
        d = np.where(c >= min_conf, d, 0)
        u, v, z = project(P, mat4(fr["transform"]), fr["intrinsics"], fr["width"], fr["height"])
        ui, vi = np.floor(u).astype(int), np.floor(v).astype(int)
        ok = (z > 0.1) & (ui >= 0) & (ui < fr["width"]) & (vi >= 0) & (vi < fr["height"])
        meas = np.zeros(len(P), np.float32); meas[ok] = d[vi[ok], ui[ok]]
        lam = np.sqrt(1 + ((u - fr['intrinsics'][0][2]) / fr['intrinsics'][0][0]) ** 2 + ((v - fr['intrinsics'][1][2]) / fr['intrinsics'][1][1]) ** 2)
        sdf = (meas - z) * lam    # distance along the viewing ray (KinectFusion), not along the optical axis
        m = ok & (meas > 0.1) & (sdf > -trunc)
        s = np.clip(sdf[m] / trunc, -1, 1)
        w = wgt[m]
        tsdf[m] = (tsdf[m] * w + s) / (w + 1); wgt[m] = w + 1
    return tsdf.reshape(dims), wgt.reshape(dims), lo


def extract_mesh(tsdf, wgt, lo, voxel, min_weight=2):
    t = np.where(wgt >= min_weight, tsdf, 1.0)
    mask = wgt >= min_weight
    verts, faces, _, _ = measure.marching_cubes(t, 0.0, spacing=(voxel,) * 3, mask=mask, gradient_direction="ascent")   # normals toward free space
    return verts + lo, faces
