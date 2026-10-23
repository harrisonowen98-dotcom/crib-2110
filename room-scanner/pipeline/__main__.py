import argparse, sys
from pathlib import Path
from .common import CFG


def main():
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build point cloud, textured GLB, shell, objects, report")
    b.add_argument("export"); b.add_argument("--fill-gaps", dest="fill", action="store_true", default=CFG["fill_gaps"]); b.add_argument("--no-fill-gaps", dest="fill", action="store_false")
    b.add_argument("--voxel", type=float); b.add_argument("--tex-res", type=int); b.add_argument("--frame-step", type=int, default=1); b.add_argument("--skip-texture", action="store_true")
    s = sub.add_parser("stitch", help="align rooms into one house frame")
    s.add_argument("rooms", nargs="+"); s.add_argument("--out", default="house"); s.add_argument("--icp", action="store_true", help="refine door seed with ICP on overlapping geometry")
    r = sub.add_parser("refine", help="scan-to-3JS refinement loop")
    r.add_argument("export"); r.add_argument("--iters", type=int); r.add_argument("--frames", type=int, default=12); r.add_argument("--resume", action="store_true", help="continue from rebuilt/scene.json (e.g. after hand edits)")
    i = sub.add_parser("import", help="import a mesh from Polycam/Scaniverse/3D Scanner App (.glb/.obj/.ply)")
    i.add_argument("mesh"); i.add_argument("--out", default="data"); i.add_argument("--name"); i.add_argument("--up", default="y", choices=["y", "z"]); i.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    if a.cmd == "build":
        from .build import run
        run(a.export, a.fill, a.voxel, a.tex_res, a.frame_step, a.skip_texture)
    elif a.cmd == "stitch":
        from .stitch import run
        run(a.rooms, a.out, a.icp)
    elif a.cmd == "import":
        from .importer import run
        run(a.mesh, a.out, a.name, a.up, a.scale)
    elif a.cmd == "refine":
        from .refine import run
        run(a.export, a.iters, a.frames, a.resume)


main()
