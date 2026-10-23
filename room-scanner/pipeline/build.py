"""python -m pipeline build <export_folder> [--fill-gaps/--no-fill-gaps]"""
import json, sys, time
from pathlib import Path
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from . import fuse as fusemod, tsdf as tsdfmod, shell as shellmod, texture as texmod, objects as objmod
from .common import Export, CFG, mat4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def boundary_info(mesh):
    e = mesh.edges_sorted
    uniq, cnt = np.unique(e, axis=0, return_counts=True)
    b = uniq[cnt == 1]
    loops = 0
    if len(b):
        import scipy.sparse as sp, scipy.sparse.csgraph as cg
        n = int(b.max()) + 1
        g = sp.coo_matrix((np.ones(len(b)), (b[:, 0], b[:, 1])), shape=(n, n))
        loops = cg.connected_components(g, directed=False)[0] - (n - len(np.unique(b)))
    return int(len(b)), int(loops)


def log(m):
    print(f"[build] {m}", flush=True)


def run(root, fill_gaps=True, voxel=None, tex_res=None, frame_step=1, skip_texture=False):
    t0 = time.time(); root = Path(root)
    from validate_export import validate
    errs, warns, info = validate(root)
    log(f"validate: {info} errors={len(errs)} warnings={len(warns)}")
    for e in errs: log("  ERROR " + e)
    if errs: raise SystemExit("export invalid")
    ex = Export(root); room = ex.rooms[0]; B = ex.build
    report = dict(warnings=[], scan=ex.manifest["folderName"])

    # 2-3. point cloud
    pc = fusemod.fuse(root); report["pointcloud"] = pc; log(f"pointcloud {pc}")
    pts = np.frombuffer(open(B / "pointcloud.bin", "rb").read()[4:4 + pc["points"] * 12 * 1], np.float32)[: pc["points"] * 3].reshape(-1, 3) if False else None
    import struct
    raw = open(B / "pointcloud.bin", "rb").read(); n = struct.unpack("<I", raw[:4])[0]
    P = np.frombuffer(raw, np.float32, n * 3, 4).reshape(-1, 3)

    # 4. TSDF mesh
    voxel = voxel or CFG["tsdf_voxel"]
    tsdf, wgt, lo = tsdfmod.fuse_tsdf(ex, voxel, (P.min(0), P.max(0)), CFG["min_confidence"], frame_step)
    V, F = tsdfmod.extract_mesh(tsdf, wgt, lo, voxel)
    raw_mesh = trimesh.Trimesh(V, F, process=True)
    keep = shellmod.inside_room(raw_mesh.triangles_center, room); raw_mesh.update_faces(keep); raw_mesh.remove_unreferenced_vertices()
    log(f"removed {int((~keep).sum())} TSDF faces outside the room shell")
    if len(raw_mesh.faces) > CFG["max_faces"]:
        raw_mesh = raw_mesh.simplify_quadric_decimation(face_count=CFG["max_faces"])
    holes_before = boundary_info(raw_mesh)
    log(f"TSDF mesh: {len(raw_mesh.vertices)} verts, {len(raw_mesh.faces)} faces, boundary edges/loops {holes_before}")

    # shell (always built; used for Rebuilt/Dollhouse/Layout and gap filling)
    shell = shellmod.build_flat_shell(room)
    sm = trimesh.util.concatenate(list(shell.values())); sm.export(B / "shell.glb")
    json.dump(dict(room=room["id"], parts=list(shell.keys())), open(B / "shell.json", "w"))

    res = tex_res or min(CFG["texture_size"], 2048)
    def tex_export(mesh, name, r):
        t1 = time.time()
        if skip_texture:
            mesh.export(B / name); return {}
        tm, st = texmod.textured_mesh(ex, mesh, r, frame_step); tm.export(B / name); log(f"  {name}: {len(mesh.faces)} faces, {time.time()-t1:.1f}s"); return st

    # raw version is always kept
    log("texturing raw mesh"); raw_stats = tex_export(raw_mesh, "room_raw.glb", res)

    # 5. gap filling: snap -> shell + furniture
    final = raw_mesh
    if fill_gaps:
        V2, snapped = shellmod.snap_to_planes(raw_mesh.vertices, room)
        snap = trimesh.Trimesh(V2, raw_mesh.faces, process=False); snap.update_faces(snap.nondegenerate_faces())
        cent = snap.triangles_center
        furn_faces = np.where(shellmod.dist_to_planes(cent, room) > 0.08)[0]
        parts = [sm]
        if len(furn_faces):
            furn = snap.submesh([furn_faces], append=True); furn.fill_holes(); trimesh.smoothing.filter_taubin(furn, iterations=5)
            parts.append(furn)
        final = trimesh.util.concatenate(parts)
        log(f"gap-filled model: {len(final.faces)} faces (shell + {len(furn_faces)} furniture faces)")
        # objects use the snapped scan mesh
        objs = objmod.separate(snap, room, B / "objects")
    else:
        objs = objmod.separate(raw_mesh, room, B / "objects")
    holes_after = boundary_info(final)
    log("texturing final mesh"); stats = tex_export(final, "room.glb", res)
    # LOD1
    lod = final.simplify_quadric_decimation(face_count=max(500, len(final.faces) // 4)) if len(final.faces) > 2000 else final
    tex_export(lod, "room_lod1.glb", max(512, res // 2))

    # 8. report
    surf, _ = trimesh.sample.sample_surface(sm, 30000); kd = cKDTree(P)
    covered = kd.query(surf)[0] < 0.05
    names = {}
    for name, m in shell.items():
        s, _ = trimesh.sample.sample_surface(m, 4000); names[name] = float((kd.query(s)[0] < 0.05).mean() * 100)
    if names.get("ceiling", 100) < 60: report["warnings"].append(f"ceiling under-scanned ({names['ceiling']:.0f}% covered)")
    if names.get("floor", 100) < 60: report["warnings"].append(f"floor under-scanned ({names['floor']:.0f}% covered)")
    dim_err = objmod.dim_error(P, room)
    report.update(
        coverage_percent=round(float(covered.mean() * 100), 1), surface_coverage_percent={k: round(v, 1) for k, v in names.items()},
        photos_total=len(ex.frames), photos_with_fullres=ex.manifest["counts"]["photos"], frames_used_for_texture=stats.get("frames_used"),
        texture_photographed_fraction=round(stats.get("photographed", 0), 3),
        holes=dict(before=dict(boundary_edges=holes_before[0], loops=holes_before[1]), after=dict(boundary_edges=holes_after[0], loops=holes_after[1])),
        mesh=dict(faces=int(len(final.faces)), raw_faces=int(len(raw_mesh.faces))), objects=len(objs),
        mean_abs_dimension_error_m=round(dim_err, 4) if dim_err is not None else None,
        fill_gaps=fill_gaps, seconds=round(time.time() - t0, 1))
    json.dump(report, open(B / "build_report.json", "w"), indent=2)
    log(json.dumps(report, indent=1))
    return report
