"""Stopgap importer: python -m pipeline import <mesh.glb|obj|ply|stl> --out data/ [--name "Basement"] [--up y|z] [--scale 1.0]

For scans from Polycam / Scaniverse / 3D Scanner App (export GLB, OBJ or PLY; convert USDZ to GLB first, e.g. with Reality Converter or Blender).
Creates a schema-valid export folder (no per-frame photos/poses -> `imported: true`, 0 frames) that the Room Explorer can show:
Textured (the imported model), Scan (point cloud sampled from it) and Rebuilt (an axis-aligned box shell fitted to the mesh).
"""
import datetime, hashlib, json, struct
from pathlib import Path
import numpy as np
import trimesh
from .common import ROOT

sys_readme = None


def _readme():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen", ROOT / "tools" / "make_synthetic_export.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m.README_FOR_AI, m.SCHEMA_VERSION, m.hashfile


def run(path, out="data", name=None, up="y", scale=1.0):
    src = Path(path); name = name or src.stem
    readme, schema_version, hashfile = _readme()
    scene = trimesh.load(src, force="scene")
    scene.apply_scale(scale)
    if up == "z":  # Z-up -> Y-up (right-handed): (x,y,z) -> (x,z,-y)
        scene.apply_transform(np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1.]]))
    mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    V = np.asarray(mesh.vertices)
    floor = np.percentile(V[:, 1], 0.5); ceil = np.percentile(V[:, 1], 99.5)
    lo, hi = V.min(0), V.max(0)
    shift = np.array([-lo[0], -floor, -lo[2]])
    scene.apply_translation(shift); V = V + shift
    W, D, H = float(V[:, 0].max()), float(V[:, 2].max()), float(ceil - floor)

    root_dir = Path(out); root_dir.mkdir(parents=True, exist_ok=True)
    prefix = name.replace(" ", "-") + "_v"
    nums = [int(d.name[len(prefix):].split("_")[0]) for d in root_dir.iterdir() if d.name.startswith(prefix) and d.name[len(prefix):].split("_")[0].isdigit()]
    version = max(nums, default=0) + 1
    folder = f"{name.replace(' ', '-')}_v{version}_{datetime.date.today().isoformat()}"
    root = root_dir / folder
    for sub in ("depth", "photos", "mesh", "rooms", "build"):
        (root / sub).mkdir(parents=True)

    scene.export(root / "build" / "room.glb"); scene.export(root / "build" / "room_lod1.glb")
    mesh.export(root / "mesh" / "mesh.ply")
    try:
        pts, _, col = trimesh.sample.sample_surface(mesh, 250000, sample_color=True)
        col = np.asarray(col)[:, :3].astype(np.uint8)
    except Exception:
        pts, _ = trimesh.sample.sample_surface(mesh, 250000); col = np.full((len(pts), 3), 200, np.uint8)
    pts = pts.astype(np.float32)
    with open(root / "build" / "pointcloud.bin", "wb") as f:
        f.write(struct.pack("<I", len(pts))); f.write(pts.tobytes()); f.write(col.tobytes())

    t = 0.1
    def T(x, y, z, yaw): c, s = np.cos(yaw), np.sin(yaw); return [[c, 0, s, x], [0, 1, 0, y], [-s, 0, c, z], [0, 0, 0, 1]]
    room = dict(id="room-0", name=name, units="meters", ceilingHeight=H, floorPolygon=[[0, 0], [W, 0], [W, D], [0, D]],
                walls=[dict(id="wall-0", dimensions=[W, H, t], transform=T(W / 2, H / 2, 0, 0)), dict(id="wall-1", dimensions=[D, H, t], transform=T(W, H / 2, D / 2, -np.pi / 2)),
                       dict(id="wall-2", dimensions=[W, H, t], transform=T(W / 2, H / 2, D, np.pi)), dict(id="wall-3", dimensions=[D, H, t], transform=T(0, H / 2, D / 2, np.pi / 2))],
                doors=[], windows=[], openings=[], objects=[])
    json.dump(room, open(root / "rooms" / "room-0.json", "w"), indent=1)
    json.dump(dict(schemaVersion=schema_version, units="meters", poseConvention="n/a (imported mesh, no frames)", frames=[]), open(root / "depth" / "frames.json", "w"))
    (root / "README_FOR_AI.md").write_text("# Imported scan (no camera poses)\n\n" + readme + "\n\nThis folder was imported from a third-party mesh: depth/frames.json is empty, so only the textured model (build/room.glb) and a box shell are available.\n")
    files = [dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size, sha256=hashfile(p)) for p in sorted(root.rglob("*")) if p.is_file()]
    man = dict(schemaVersion=schema_version, scanName=name, version=version, folderName=folder, createdAt=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               imported=True, synthetic=False, device=dict(model="imported", iosVersion="n/a", app=f"importer ({src.name})"), units="meters",
               coordinateSystem=dict(handedness="right", up="+Y", cameraForward="-Z"), counts=dict(frames=0, photos=0, rooms=1),
               depth=dict(format="none", width=0, height=0, scale="n/a"), files=files,
               rooms=[dict(id="room-0", name=name, file="rooms/room-0.json", usdz=None, floorArea=W * D, ceilingHeight=H, boundingBox=dict(min=[0, 0, 0], max=[W, H, D]), originTransform=np.eye(4).tolist())],
               README_FOR_AI=readme)
    json.dump(man, open(root / "manifest.json", "w"), indent=1)
    print(root); return root
