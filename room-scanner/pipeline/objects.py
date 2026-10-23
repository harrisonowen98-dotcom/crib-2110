"""Object separation: RoomPlan boxes + mesh faces away from shell planes + connected-component fallback. Optional ML hook."""
import json
from pathlib import Path
import numpy as np
import trimesh
from .common import mat4
from .shell import dist_to_planes

SEGMENTATION_HOOK = None   # set to a callable(mesh)->list[trimesh.Trimesh] to plug in an ML segmenter


def in_box(P, obj, pad=0.1):
    T = mat4(obj["transform"]); inv = np.linalg.inv(T)
    q = P @ inv[:3, :3].T + inv[:3, 3]
    half = np.array(obj["dimensions"]) / 2 + pad
    return np.all(np.abs(q) <= half, axis=1)


def measured_extent(P, o, room, pad=0.15):
    """AABB (object's yaw frame; vertical in world) of LiDAR points belonging to the object, floor/walls excluded."""
    Q = P[in_box(P, o, pad)]; Q = Q[dist_to_planes(Q, room) > 0.04]
    if len(Q) < 200: return None
    T = mat4(o["transform"]); q = (Q - T[:3, 3]) @ T[:3, :3]
    lo, hi = np.percentile(q, 0.5, axis=0), np.percentile(q, 99.5, axis=0)
    if lo[1] + T[1, 3] < 0.08: lo[1] = -T[1, 3]   # stands on the floor: extend to y=0
    return lo, hi


def valid_axes(lo, hi, o, min_cover=0.85):
    """Axes where the scan covers >= min_cover of the RoomPlan size (partial scans must not shrink an object)."""
    return (hi - lo) >= min_cover * np.array(o["dimensions"])


def dim_error(P, room):
    errs = []
    for o in room["objects"]:
        m = measured_extent(P, o, room)
        if m:
            ok = valid_axes(m[0], m[1], o)
            if ok.any(): errs.extend(np.abs((m[1] - m[0]) - np.array(o["dimensions"]))[ok].tolist())
    return float(np.mean(errs)) if errs else None


def separate(mesh, room, out_dir: Path, shell_tol=0.08, min_area=0.1):
    out_dir.mkdir(parents=True, exist_ok=True)
    cent = mesh.triangles_center
    furniture = dist_to_planes(cent, room) > shell_tol
    index, claimed = [], np.zeros(len(mesh.faces), bool)
    for o in room["objects"]:
        sel = furniture & in_box(cent, o)
        claimed |= sel
        if sel.sum() < 4:
            continue
        sub = mesh.submesh([np.where(sel)[0]], append=True)
        fn = f"{o['id']}.glb"; sub.export(out_dir / fn)
        index.append(dict(id=o["id"], label=o["category"], source="roomplan", file=fn, dimensions=o["dimensions"], transform=o["transform"],
                          bbox=sub.bounds.tolist(), faces=int(len(sub.faces))))
    rest = np.where(furniture & ~claimed)[0]
    if len(rest) > 10:
        rem = mesh.submesh([rest], append=True)
        comps = SEGMENTATION_HOOK(rem) if SEGMENTATION_HOOK else rem.split(only_watertight=False)
        for i, c in enumerate(comps):
            if c.area < min_area:
                continue
            fn = f"unlabeled-{i}.glb"; c.export(out_dir / fn)
            index.append(dict(id=f"unlabeled-{i}", label="unknown", source="connected-components", file=fn, bbox=c.bounds.tolist(), faces=int(len(c.faces))))
    json.dump(index, open(out_dir / "objects.json", "w"), indent=1)
    return index
