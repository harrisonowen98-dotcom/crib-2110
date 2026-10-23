---
name: scan-to-3js
description: Rebuild a scanned room (Room Scanner export folder) as a clean, true-scale, object-based Three.js scene by iterating render-vs-photo comparisons until it converges. Use when the user drops/points at a scan export folder (manifest.json + depth/frames.json + rooms/) and wants a "Rebuilt" model, dollhouse, knolling objects, or a Three.js scene of the room.
---

# scan-to-3JS

Goal: turn `<export>/` into `<export>/rebuilt/scene.ts|glb|json` — architectural shell (from RoomPlan) + **every object as its own
true-scale mesh/primitive group** with real colours, then **loop: render from each real photo's exact pose → compare to the photo
and the LiDAR depth → fix the worst error → repeat** until improvement is small. Dimensions must stay true to the LiDAR scan.

Units: meters. Right-handed, Y-up. Camera looks down −Z. See `docs/SCHEMA.md`.

## 0. Prepare
1. `python tools/validate_export.py <export>` must say VALID.
2. `python -m pipeline build <export>` (point cloud, textured GLB, shell, objects, build_report.json). Read `build/build_report.json`;
   note warnings (e.g. "ceiling under-scanned").
3. Viewer dev server must be able to start: `cd viewer && npm install` once (the refine script starts it if it is not running).

## 1. Automatic loop
`python -m pipeline refine <export> --iters 8 --frames 12`
Per iteration it writes `rebuilt/iter_###/{scene.json,color_K.png,contact.png}` and appends `rebuilt/scores.csv`
(composite, ssim, edge_corr, color_l1, depth_mae_m, depth_within_5cm, dim_err_m, objects, actions).
Automatic fixes: colour re-estimation from photos, dimension/position snap to LiDAR points (only on axes the scan covers ≥85 %),
primitive-variant search (table solid vs legs; kept only if its region error drops), boxes for unexplained LiDAR clusters.
Earlier iterations are never deleted; re-running continues numbering.

## 2. You are the judge — look at the pictures
After the automatic run, **open every new `rebuilt/iter_###/contact.png`** (real | render | diff) and `ranked_errors` in
`scene.json`. Then improve what the numbers can't see, one fix per ranked error (worst first):
- missing object → add an object to `scene.json` (`category`, `dimensions`, `transform`, `color`, optional `variant`) sized from the
  LiDAR points / `build/objects/*.glb` bbox; never invent sizes — use measured extents.
- wrong size/position → re-measure from `build/pointcloud.bin`; floating objects must touch the floor/wall they rest on.
- wrong colour → sample the photo at that object's pixels; update `color` (or `palette`).
- richer model → extend `buildObject()` in `viewer/src/rebuilt.ts` (new `variant`, e.g. chair = seat + back + 4 legs, shelf = frame + boards),
  keep the bounding box identical to the measured one.
Then re-run `python -m pipeline refine <export> --iters 3` (it starts from RoomPlan again unless you edit the start; to continue from your
edited scene copy it to `rebuilt/scene.json` — the loop will prefer it) and compare `scores.csv`. Stop when the composite gain is < 0.002
for two iterations, or the contact sheets look right.

## 3. Deliver
- `rebuilt/scene.json` (source of truth), `scene.glb`, `scene.ts` (Three.js, `buildScene()`), `scores.csv`, per-iteration contact sheets.
- Report: final composite, depth-within-5cm, mean absolute dimension error vs scan (`dim_err_m`), objects count, remaining known issues.
- The Room Explorer (`viewer/`) shows it automatically in **Rebuilt** mode and in Knolling (each object separate).
