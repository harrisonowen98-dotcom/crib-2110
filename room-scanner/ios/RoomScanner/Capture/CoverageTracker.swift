import Foundation
import ARKit
import simd

struct MapPoint: Identifiable { let id: Int; let x: Float; let z: Float; let progress: Float }

/// "Photo detail": the mesh surface is bucketed into 10 cm cells; a cell is "done" once `target` sharp, well-angled keyframes saw it
/// (visibility checked against the LiDAR depth map). Progress = mean(min(views, target) / target). Drives the ring, mini-map and
/// the "save now" hint.
final class CoverageTracker {
    struct Cell { var pos: SIMD3<Float>; var normal: SIMD3<Float>; var views: UInt8 }
    private let queue = DispatchQueue(label: "coverage", qos: .utility)
    private var index: [SIMD3<Int32>: Int] = [:]
    private var cells: [Cell] = []
    let target: Float = 3
    let cell: Float = 0.10
    private(set) var history: [Float] = []

    /// Merge mesh vertices into cells (cheap sampling: every 4th vertex).
    func ingest(anchors: [ARMeshAnchor]) {
        struct V { var p: SIMD3<Float>; var n: SIMD3<Float> }
        var verts: [V] = []
        for a in anchors {
            let g = a.geometry; let vb = g.vertices; let nb = g.normals
            let vp = vb.buffer.contents(); let np = nb.buffer.contents()
            for i in stride(from: 0, to: vb.count, by: 4) {
                let v = vp.advanced(by: vb.offset + vb.stride * i).assumingMemoryBound(to: SIMD3<Float>.self).pointee
                let n = np.advanced(by: nb.offset + nb.stride * i).assumingMemoryBound(to: SIMD3<Float>.self).pointee
                let w = a.transform * SIMD4(v, 1); let nw = a.transform * SIMD4(n, 0)
                verts.append(V(p: w.xyz, n: simd_normalize(nw.xyz)))
            }
        }
        queue.async { [self] in
            for v in verts {
                let k = SIMD3<Int32>(Int32(floor(v.p.x / cell)), Int32(floor(v.p.y / cell)), Int32(floor(v.p.z / cell)))
                if index[k] == nil { index[k] = cells.count; cells.append(Cell(pos: v.p, normal: v.n, views: 0)) }
            }
        }
    }

    /// Register one saved keyframe. `depth` is the Float32 depth map copied out of the ARFrame.
    func register(transform: simd_float4x4, intrinsics: simd_float3x3, imageSize: CGSize, depth: [Float], depthW: Int, depthH: Int) {
        queue.async { [self] in
            let inv = transform.inverse
            let sx = Float(depthW) / Float(imageSize.width), sy = Float(depthH) / Float(imageSize.height)
            let fx = intrinsics.columns.0.x * sx, fy = intrinsics.columns.1.y * sy, cx = intrinsics.columns.2.x * sx, cy = intrinsics.columns.2.y * sy
            let camPos = transform.columns.3.xyz
            for i in cells.indices where cells[i].views < 255 {
                let c = inv * SIMD4(cells[i].pos, 1)
                let d = -c.z
                guard d > 0.3, d < 4.0 else { continue }
                let u = fx * c.x / d + cx, v = cy - fy * c.y / d
                guard u >= 2, v >= 2, u < Float(depthW - 2), v < Float(depthH - 2) else { continue }
                let toCam = simd_normalize(camPos - cells[i].pos)
                guard simd_dot(toCam, cells[i].normal) > 0.35 else { continue }                 // reasonably head-on (< ~70°)
                let meas = depth[Int(v) * depthW + Int(u)]
                guard meas > 0, abs(meas - d) < 0.15 else { continue }                           // visible, not occluded
                cells[i].views += 1
            }
            history.append(progressLocked())
        }
    }

    private func progressLocked() -> Float {
        guard !cells.isEmpty else { return 0 }
        var s: Float = 0; for c in cells { s += min(Float(c.views), target) / target }
        return s / Float(cells.count)
    }

    /// Snapshot for the UI (progress 0...1, downsampled mini-map points, "detail stopped improving" flag).
    func snapshot(completion: @escaping (Float, [MapPoint], Bool) -> Void) {
        queue.async { [self] in
            let p = progressLocked()
            let step = max(1, cells.count / 1500)
            var pts: [MapPoint] = []; pts.reserveCapacity(1500)
            for i in stride(from: 0, to: cells.count, by: step) { pts.append(MapPoint(id: i, x: cells[i].pos.x, z: cells[i].pos.z, progress: min(Float(cells[i].views), target) / target)) }
            let plateau = history.count >= 6 && (history.last! - history[history.count - 6]) < 0.01 && p > 0.3
            DispatchQueue.main.async { completion(p, pts, plateau) }
        }
    }
}
