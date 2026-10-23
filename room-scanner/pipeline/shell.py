"""RoomPlan JSON -> clean architectural shell (floor, walls with door/window cutouts, ceiling) + snap planes."""
import numpy as np
import trimesh
from shapely.geometry import Polygon
from .common import mat4


def planes(room):
    """List of (name, point, inward_normal) for floor, ceiling, walls."""
    out = [("floor", np.array([0, 0, 0.]), np.array([0, 1, 0.])), ("ceiling", np.array([0, room["ceilingHeight"], 0.]), np.array([0, -1, 0.]))]
    for w in room["walls"]:
        T = mat4(w["transform"])
        out.append((w["id"], T[:3, 3], T[:3, 2] / np.linalg.norm(T[:3, 2])))
    return out


def flat_surface(poly2d, T):
    """Single-sided planar mesh from a shapely polygon (local x,y), moved by T; normal = local +Z."""
    v2, f = trimesh.creation.triangulate_polygon(poly2d, engine="earcut")
    v = np.column_stack([v2, np.zeros(len(v2))])
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    flip = np.cross(b - a, c - a)[:, 2] < 0
    f = np.where(flip[:, None], f[:, ::-1], f)
    m = trimesh.Trimesh(v, f, process=False); m.apply_transform(T); return m


def build_flat_shell(room):
    """Interior-facing single-sided shell (floor up, ceiling down, walls toward the room). Back-face culling in the viewer then gives
    an automatic dollhouse cut-away: walls vanish when seen from outside."""
    meshes = {}
    poly = Polygon(room["floorPolygon"])
    Rf = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1.]])     # local (x,y,0) -> world (x,0,-y), normal +Z -> +Y
    pm = Polygon([(x, -z) for x, z in room["floorPolygon"]])
    meshes["floor"] = flat_surface(pm, Rf)
    Rc = np.array([[1, 0, 0, 0], [0, 0, -1, room["ceilingHeight"]], [0, 1, 0, 0], [0, 0, 0, 1.]])  # normal +Z -> -Y (faces down)
    meshes["ceiling"] = flat_surface(Polygon([(x, z) for x, z in room["floorPolygon"]]), Rc)
    holes = [*room["doors"], *room["windows"], *room["openings"]]
    for w in room["walls"]:
        T = mat4(w["transform"]); inv = np.linalg.inv(T); W, H, _ = w["dimensions"]; hs = []
        for o in holes:
            if o.get("parent") != w["id"]: continue
            c = (inv @ mat4(o["transform"])[:, 3])[:3]; dw, dh = o["dimensions"][:2]
            hs.append([(c[0] - dw / 2, c[1] - dh / 2), (c[0] + dw / 2, c[1] - dh / 2), (c[0] + dw / 2, c[1] + dh / 2), (c[0] - dw / 2, c[1] + dh / 2)])
        meshes[w["id"]] = flat_surface(Polygon([(-W / 2, -H / 2), (W / 2, -H / 2), (W / 2, H / 2), (-W / 2, H / 2)], hs), T)
    return meshes


def build_shell(room, thickness=0.02):
    meshes = {}
    poly = Polygon(room["floorPolygon"])
    f = trimesh.creation.extrude_polygon(poly, thickness)       # extrudes along +z in (x,y)=(x,z-plane)
    # map shapely (x, y=z) plane -> world (x, 0, z): rotate so extrusion axis -> +Y
    R = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1.]])
    # (x,y,z)->(x,z,-y): polygon y becomes world -z, so mirror polygon y first
    poly_m = Polygon([(x, -z) for x, z in room["floorPolygon"]])
    for name, y in (("floor", 0.0), ("ceiling", room["ceilingHeight"])):
        m = trimesh.creation.extrude_polygon(poly_m, thickness)
        m.apply_transform(R)
        m.apply_translation([0, y - (0 if name == "floor" else thickness), 0])
        # make face normals point inward (floor up / ceiling down) by dropping the outer skin only
        meshes[name] = m
    holes = [*room["doors"], *room["windows"], *room["openings"]]
    for w in room["walls"]:
        T = mat4(w["transform"]); inv = np.linalg.inv(T)
        W, H, t = w["dimensions"]
        hs = []
        for o in holes:
            if o.get("parent") != w["id"]:
                continue
            c = (inv @ mat4(o["transform"])[:, 3])[:3]
            dw, dh = o["dimensions"][:2]
            hs.append([(c[0] - dw / 2, c[1] - dh / 2), (c[0] + dw / 2, c[1] - dh / 2), (c[0] + dw / 2, c[1] + dh / 2), (c[0] - dw / 2, c[1] + dh / 2)])
        p = Polygon([(-W / 2, -H / 2), (W / 2, -H / 2), (W / 2, H / 2), (-W / 2, H / 2)], hs)
        m = trimesh.creation.extrude_polygon(p, t)
        m.apply_translation([0, 0, -t / 2]); m.apply_transform(T)
        meshes[w["id"]] = m
    return meshes


def snap_to_planes(V, room, tol=0.08):
    """Project vertices lying within tol of a shell plane exactly onto it. Returns (V', snapped_mask)."""
    V = V.copy(); snapped = np.zeros(len(V), bool)
    best = np.full(len(V), np.inf)
    for _, p, n in planes(room):
        d = (V - p) @ n
        use = (np.abs(d) < tol) & (np.abs(d) < best)
        V[use] -= d[use, None] * n[None]; best[use] = np.abs(d[use]); snapped |= use
    return V, snapped


def dist_to_planes(P, room):
    return np.min(np.stack([np.abs((P - p) @ n) for _, p, n in planes(room)]), axis=0)


def inside_room(P, room, tol=0.05):
    """True for points inside the room's footprint polygon and floor..ceiling (drops TSDF ghost surfaces behind walls)."""
    import shapely
    poly = Polygon(room["floorPolygon"]).buffer(tol)
    return shapely.contains_xy(poly, P[:, 0], P[:, 2]) & (P[:, 1] > -tol) & (P[:, 1] < room["ceilingHeight"] + tol)
