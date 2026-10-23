"""Back-project every depth map with pose+intrinsics, colour from RGB, voxel-downsample, write build/pointcloud.{ply,bin}."""
import json, struct
from pathlib import Path
import numpy as np
import yaml
from PIL import Image

CFG = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml"))["pipeline"]


def fuse(root: Path, voxel=None, min_conf=None, stride=1):
    voxel = voxel or CFG["voxel_size"]; min_conf = CFG["min_confidence"] if min_conf is None else min_conf
    frames = json.load(open(root / "depth" / "frames.json"))["frames"]
    pts, cols = [], []
    for fr in frames:
        d = np.array(Image.open(root / fr["file"]), np.float32) / 1000.0
        c = np.array(Image.open(root / fr["confidenceFile"]))
        K, Kc = np.array(fr["intrinsics"]), np.array(fr["rgbIntrinsics"])
        rgb = np.array(Image.open(root / fr["rgb"]).convert("RGB"))
        v, u = np.mgrid[0:fr["height"]:stride, 0:fr["width"]:stride]
        dd, cc = d[::stride, ::stride], c[::stride, ::stride]
        m = (dd > 0.1) & (dd < 5.0) & (cc >= min_conf)
        u, v, z = u[m] + 0.5, v[m] + 0.5, dd[m]
        cam = np.stack([(u - K[0, 2]) / K[0, 0] * z, -(v - K[1, 2]) / K[1, 1] * z, -z, np.ones_like(z)], 1)
        T = np.array(fr["transform"])
        w = (cam @ T.T)[:, :3]
        # depth px -> rgb px via the two intrinsics (same camera, different resolution)
        ru = np.clip(((cam[:, 0] / z) * Kc[0, 0] + Kc[0, 2]).astype(int), 0, fr["rgbWidth"] - 1)
        rv = np.clip(((-cam[:, 1] / z) * Kc[1, 1] + Kc[1, 2]).astype(int), 0, fr["rgbHeight"] - 1)
        pts.append(w.astype(np.float32)); cols.append(rgb[rv, ru])
    P, C = np.concatenate(pts), np.concatenate(cols)
    # voxel downsample (first point per voxel, colour averaged)
    k = np.floor(P / voxel).astype(np.int64)
    _, inv, cnt = np.unique(k, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    Pm = np.stack([np.bincount(inv, P[:, i]) / cnt for i in range(3)], 1).astype(np.float32)
    Cm = np.stack([np.bincount(inv, C[:, i].astype(np.float64)) / cnt for i in range(3)], 1).round().astype(np.uint8)
    out = root / "build"; out.mkdir(exist_ok=True)
    with open(out / "pointcloud.bin", "wb") as f:  # [uint32 N][N*3 float32 xyz][N*3 uint8 rgb]
        f.write(struct.pack("<I", len(Pm))); f.write(Pm.tobytes()); f.write(Cm.tobytes())
    with open(out / "pointcloud.ply", "wb") as f:
        f.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {len(Pm)}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n".encode())
        rec = np.zeros(len(Pm), dtype=[("p", "<f4", 3), ("c", "u1", 3)]); rec["p"], rec["c"] = Pm, Cm
        f.write(rec.tobytes())
    return dict(points=int(len(Pm)), raw_points=int(len(P)), voxel=voxel)


if __name__ == "__main__":
    import sys
    print(fuse(Path(sys.argv[1])))
