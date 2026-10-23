"""python -m pipeline stitch <room_folders...> --out house/

Seeds each room from shared doorways (door centres coincide, door normals oppose), refines with yaw+translation ICP
on the overlapping point clouds, writes house/alignment.json (editable; the viewer reads it) and house/house.json.
Transform of room i: p_world = R_y(yaw) @ p + [x, 0, z]   (three.js rotation.y convention).
"""
import json, struct
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from .common import mat4


def Ry(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def doors(room):
    out = []
    for d in room["doors"] + room["openings"]:
        T = mat4(d["transform"]); out.append((T[:3, 3].copy(), T[:3, 2].copy()))   # local +Z = interior normal
    return out


def load_points(root):
    raw = open(Path(root) / "build" / "pointcloud.bin", "rb").read(); n = struct.unpack("<I", raw[:4])[0]
    return np.frombuffer(raw, np.float32, n * 3, 4).reshape(-1, 3).astype(np.float64)


def apply(T, P):
    yaw, x, z = T; return P @ Ry(yaw).T + np.array([x, 0, z])


def seed(roomA, TA, roomB, used):
    """Place B so one of its doors meets one unused door of A, facing each other."""
    for ia, (pa, na) in enumerate(doors(roomA)):
        if ia in used:
            continue
        for ib, (pb, nb) in enumerate(doors(roomB)):
            wa_p = Ry(TA[0]) @ pa + [TA[1], 0, TA[2]]; wa_n = Ry(TA[0]) @ na
            # B's door normal must point opposite to A's door normal (B sits on the far side of the door)
            yaw = np.arctan2(-wa_n[0], -wa_n[2]) - np.arctan2(nb[0], nb[2])
            R = Ry(yaw); t = wa_p - R @ pb
            return (yaw, t[0], t[2]), ia
    return (0.0, 0.0, 0.0), None


def icp(PA, PB, T0, iters=30, max_d=0.3):
    """Refine B's (yaw, x, z) so B overlaps A; PA already in world frame. Only uses near-door slice via distance gating."""
    yaw, x, z = T0; kd = cKDTree(PA)
    for _ in range(iters):
        Q = apply((yaw, x, z), PB); d, i = kd.query(Q); m = d < max_d
        if m.sum() < 200:
            break
        src, dst = Q[m][:, [0, 2]], PA[i[m]][:, [0, 2]]
        cs, cd = src.mean(0), dst.mean(0)
        H = (src - cs).T @ (dst - cd); dth = np.arctan2(H[0, 1] - H[1, 0], H[0, 0] + H[1, 1])
        # rotation about origin in xz plane; three.js yaw positive == x->-z, so use -dth
        yaw -= dth * 0.5  # damped; ICP is only a polish on top of the door seed
        Q2 = apply((yaw, x, z), PB); d2, i2 = kd.query(Q2); m2 = d2 < max_d
        dt = (PA[i2[m2]][:, [0, 2]] - Q2[m2][:, [0, 2]]).mean(0); x += dt[0]; z += dt[1]
    return yaw, x, z


def run(folders, out, use_icp=False):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rooms = [(Path(f), json.load(open(Path(f) / "manifest.json")), json.load(open(Path(f) / json.load(open(Path(f) / "manifest.json"))["rooms"][0]["file"]))) for f in folders]
    T = [(0.0, 0.0, 0.0)]; pts = [load_points(rooms[0][0])]; used = {0: set()}
    for k in range(1, len(rooms)):
        best = None
        for j in range(k):                       # try attaching to each already placed room
            t, ia = seed(rooms[j][2], T[j], rooms[k][2], used[j])
            if ia is not None:
                best = (j, t, ia); break
        j, t, ia = best if best else (0, (0.0, rooms[0][2]["floorPolygon"][2][0] + 1.0, 0.0), None)
        if ia is not None: used[j].add(ia)
        PB = load_points(rooms[k][0])
        if use_icp and ia is not None:            # polish with ICP, using only geometry near the shared doorway (where the scans overlap)
            pa = Ry(T[j][0]) @ doors(rooms[j][2])[ia][0] + [T[j][1], 0, T[j][2]]
            near_a = pts[j][np.linalg.norm(pts[j] - pa, axis=1) < 1.5]
            near_b = PB[np.linalg.norm(apply(t, PB) - pa, axis=1) < 1.5]
            if len(near_a) > 500 and len(near_b) > 500: t = icp(near_a, near_b, t)
        T.append(t); pts.append(apply(t, PB)); used[k] = set()
    align = {r[0].name: dict(x=round(float(t[1]), 4), z=round(float(t[2]), 4), yaw=round(float(t[0]), 5)) for r, t in zip(rooms, T)}
    json.dump(align, open(out / "alignment.json", "w"), indent=2)
    json.dump(dict(units="meters", note="p_world = R_y(yaw) p + [x,0,z]; edit alignment.json to override", rooms=[dict(folder=r[0].name, name=r[1]["scanName"], **align[r[0].name]) for r in rooms]), open(out / "house.json", "w"), indent=2)
    print(json.dumps(align, indent=1)); return align
