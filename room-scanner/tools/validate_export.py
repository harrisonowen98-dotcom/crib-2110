#!/usr/bin/env python3
"""Validate an export folder against the JSON Schemas + structural checks.  python tools/validate_export.py <export_folder>"""
import hashlib, json, sys
from pathlib import Path
import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"


def check(schema_name, doc, label, errs):
    v = Draft202012Validator(json.load(open(SCHEMAS / schema_name)))
    for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
        errs.append(f"{label}: {'/'.join(map(str, e.path)) or '<root>'}: {e.message[:160]}")


def validate(root: Path):
    errs, warns = [], []
    mp = root / "manifest.json"
    if not mp.exists():
        return ["manifest.json missing"], warns, {}
    man = json.load(open(mp)); check("manifest.schema.json", man, "manifest", errs)
    for f in man.get("files", []):
        p = root / f["path"]
        if not p.exists(): errs.append(f"file listed but missing: {f['path']}"); continue
        if p.stat().st_size != f["bytes"]: errs.append(f"size mismatch: {f['path']}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != f["sha256"]: errs.append(f"hash mismatch: {f['path']}")
    fp = root / "depth" / "frames.json"
    frames = []
    if fp.exists():
        fr = json.load(open(fp)); check("frames.schema.json", fr, "frames", errs); frames = fr.get("frames", [])
    else:
        errs.append("depth/frames.json missing")
    if man.get("counts", {}).get("frames") != len(frames): errs.append("counts.frames != len(frames.json)")
    photos = 0
    for fr in frames:
        for k in ("file", "confidenceFile", "rgb"):
            if not (root / fr.get(k, "<none>")).exists(): errs.append(f"frame {fr.get('frameIndex')}: {k} missing")
        if fr.get("photo"):
            photos += 1
            if not (root / fr["photo"]).exists(): errs.append(f"frame {fr['frameIndex']}: photo missing")
        T = np.array(fr["transform"], float)
        R = T[:3, :3]
        if abs(np.linalg.det(R) - 1) > 1e-2 or np.abs(R @ R.T - np.eye(3)).max() > 1e-2: errs.append(f"frame {fr['frameIndex']}: transform not rigid")
    if man.get("counts", {}).get("photos") != photos: errs.append("counts.photos mismatch")
    if frames:
        d = np.array(Image.open(root / frames[0]["file"]))
        if d.dtype != np.uint16 or d.shape != (frames[0]["height"], frames[0]["width"]): errs.append("depth PNG must be uint16 with width/height from frames.json")
    for r in man.get("rooms", []):
        rp = root / r["file"]
        if rp.exists(): check("room.schema.json", json.load(open(rp)), r["file"], errs)
        else: errs.append(f"room file missing: {r['file']}")
        if r.get("usdz") is None: warns.append(f"{r['id']}: no .usdz (ok for synthetic data)")
    if not (root / "mesh").glob("*") : warns.append("mesh/ empty")
    return errs, warns, dict(frames=len(frames), photos=photos, rooms=len(man.get("rooms", [])))


if __name__ == "__main__":
    root = Path(sys.argv[1])
    errs, warns, info = validate(root)
    print(f"Export: {root}\n  {info}")
    for w in warns: print("  warn:", w)
    for e in errs: print("  ERROR:", e)
    print("VALID" if not errs else f"INVALID ({len(errs)} errors)")
    sys.exit(1 if errs else 0)
