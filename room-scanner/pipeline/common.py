"""Shared helpers: config, export loading, transforms. Meters, right-handed, Y-up."""
import json
from pathlib import Path
import numpy as np
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load(open(ROOT / "config.yaml"))["pipeline"]


class Export:
    def __init__(self, root):
        self.root = Path(root)
        self.manifest = json.load(open(self.root / "manifest.json"))
        self.frames = json.load(open(self.root / "depth" / "frames.json"))["frames"]
        self.rooms = [json.load(open(self.root / r["file"])) for r in self.manifest["rooms"]]
        self.build = self.root / "build"
        self.build.mkdir(exist_ok=True)
        self._cache = {}

    def depth(self, fr):
        return np.array(Image.open(self.root / fr["file"]), np.float32) / 1000.0

    def conf(self, fr):
        return np.array(Image.open(self.root / fr["confidenceFile"]))

    def rgb(self, fr):
        k = ("rgb", fr["frameIndex"])
        if k not in self._cache:
            self._cache[k] = np.array(Image.open(self.root / fr["rgb"]).convert("RGB"))
        return self._cache[k]


def mat4(m):
    return np.array(m, dtype=np.float64).reshape(4, 4)


def sharpness(img):
    g = img.astype(np.float32).mean(2)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def ev(fr):
    c = fr["camera"]
    return max(c["exposureDuration"] * c["iso"], 1e-6)


def project(P, T, K, w, h):
    """World points (N,3) -> pixel u,v and planar depth d for a frame (ARKit camera: -Z forward)."""
    inv = np.linalg.inv(T)
    c = P @ inv[:3, :3].T + inv[:3, 3]
    d = -c[:, 2]
    safe = np.where(d > 1e-6, d, 1e-6)
    u = K[0][0] * c[:, 0] / safe + K[0][2]
    v = K[1][2] - K[1][1] * c[:, 1] / safe
    return u, v, d
