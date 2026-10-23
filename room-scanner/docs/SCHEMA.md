# Room Scanner export schema v1.0.0

Machine-readable specs: `schemas/manifest.schema.json`, `schemas/frames.schema.json`, `schemas/room.schema.json`.
Validate: `python tools/validate_export.py <export_folder>`

## Conventions (all files)
- **Units: meters.** Right-handed, **Y-up** (ARKit world). Floor ≈ y=0 per room.
- **Camera space:** +X right, +Y up, camera looks down **−Z**.
- **`transform`** (frames.json) = 4x4 **row-major** camera→world matrix (nested list of 4 rows).
- Never overwrite: folder name `<room-name>_v<N>_<YYYY-MM-DD>`; each save = new N.

## Folder
```
<room>_v<N>_<date>/
  manifest.json            self-describing index (counts, rooms, file list + sha256, README_FOR_AI)
  README_FOR_AI.md         plain-English rebuild instructions for an AI agent
  depth/
    frames.json            one entry per frame (see below)
    00035.png              depth, uint16 PNG, millimeters, 0 = invalid, 256x192
    00035_conf.png         uint8 PNG, ARConfidenceLevel 0 low / 1 medium / 2 high
    00035.jpg              RGB frame matching the depth map (rgbWidth x rgbHeight)
  photos/0008.jpg          full-res keyframe JPEGs (same frameIndex naming; photo path in frames.json)
  mesh/mesh.ply            ARKit mesh, binary PLY, per-vertex uchar `classification`:
                           0 none,1 wall,2 floor,3 ceiling,4 table,5 seat,6 window,7 door
  mesh/mesh.usdz           (device exports) merged mesh
  rooms/room-N.json        RoomPlan CapturedRoom (simplified, see room.schema.json)
  rooms/room-N.usdz        (device exports) RoomPlan USDZ
  build/                   (pipeline output, not part of a device export)
```

## frames.json entry
`frameIndex, timestamp, trackingState, camera{exposureDuration, iso, shutter, whiteBalance{r,g,b}}, confidence (0-1),
file, confidenceFile, width, height, intrinsics (3x3 for depth), rgb, rgbWidth, rgbHeight, rgbIntrinsics (3x3), photo|null, transform (4x4)`

Back-projection of depth pixel (u,v) with depth d (meters, planar along camera −Z):
`X=(u-cx)/fx*d, Y=-(v-cy)/fy*d, Z=-d` → `world = transform · [X,Y,Z,1]`. RGB pixel for a camera-space point:
`u'=fx'·X/(-Z)+cx', v'=-fy'·Y/(-Z)+cy'` using `rgbIntrinsics`.
Every photo/rgb file belongs to exactly one frame with a pose and intrinsics (no orphans; validator checks).

## room-N.json
`walls/doors/windows/openings`: `{id, parent?, dimensions[w,h,thickness], transform}`; wall local +Z = interior-facing normal.
`objects`: `{id, category, dimensions[w,h,d], transform, confidence}`. `floorPolygon` = [x,z] corners.

## Notes
Synthetic exports (`manifest.synthetic=true`) have no `.usdz`; validator only warns.
