import Foundation
import RoomPlan
import simd

/// CapturedRoom -> rooms/room-N.json (schemas/room.schema.json). Wall local +Z is forced to point INTO the room.
enum RoomPlanExporter {
    static func category(_ c: CapturedRoom.Object.Category) -> String {
        switch c {
        case .storage: return "storage"; case .refrigerator: return "refrigerator"; case .stove: return "stove"; case .bed: return "bed"
        case .sink: return "sink"; case .washerDryer: return "washerDryer"; case .toilet: return "toilet"; case .bathtub: return "bathtub"
        case .oven: return "oven"; case .dishwasher: return "dishwasher"; case .table: return "table"; case .sofa: return "sofa"
        case .chair: return "chair"; case .fireplace: return "fireplace"; case .television: return "television"; case .stairs: return "stairs"
        @unknown default: return "other"
        }
    }
    static func conf(_ c: CapturedRoom.Confidence) -> String { switch c { case .high: return "high"; case .medium: return "medium"; case .low: return "low"; @unknown default: return "low" } }

    static func flipY(_ t: simd_float4x4) -> simd_float4x4 { t * simd_float4x4(simd_quatf(angle: .pi, axis: SIMD3(0, 1, 0))) }

    static func make(from room: CapturedRoom, index: Int, name: String) -> RoomJSON {
        let wallsRaw = room.walls
        let centers = wallsRaw.map { $0.transform.columns.3.xyz }
        let centroid = centers.isEmpty ? SIMD3<Float>(0, 0, 0) : centers.reduce(SIMD3<Float>(0, 0, 0), +) / Float(centers.count)

        func inward(_ t: simd_float4x4) -> simd_float4x4 {
            let z = simd_normalize(t.columns.2.xyz); let toCenter = centroid - t.columns.3.xyz
            return simd_dot(z, SIMD3(toCenter.x, 0, toCenter.z)) < 0 ? flipY(t) : t
        }
        func dims(_ d: SIMD3<Float>) -> [Double] { [Double(d.x), Double(d.y), Double(max(d.z, 0.1))] }

        var walls: [RoomSurface] = []; var wallInfo: [(id: String, T: simd_float4x4, dim: SIMD3<Float>)] = []
        for (i, w) in wallsRaw.enumerated() {
            let T = inward(w.transform); let id = "wall-\(i)"
            walls.append(RoomSurface(id: id, parent: nil, dimensions: dims(w.dimensions), transform: rowMajor(T), confidence: conf(w.confidence)))
            wallInfo.append((id, T, w.dimensions))
        }
        func parent(of t: simd_float4x4) -> (id: String, T: simd_float4x4)? {
            let p = t.columns.3.xyz; var best: (String, simd_float4x4, Float)?
            for w in wallInfo {
                let n = simd_normalize(w.T.columns.2.xyz); let d = abs(simd_dot(p - w.T.columns.3.xyz, n))
                let along = abs(simd_dot(p - w.T.columns.3.xyz, simd_normalize(w.T.columns.0.xyz)))
                if along <= w.dim.x / 2 + 0.2, best == nil || d < best!.2 { best = (w.id, w.T, d) }
            }
            return best.map { ($0.0, $0.1) }
        }
        func surfaces(_ list: [CapturedRoom.Surface], prefix: String) -> [RoomSurface] {
            list.enumerated().map { i, s in
                let p = parent(of: s.transform)
                var T = s.transform
                if let pw = p, simd_dot(T.columns.2.xyz, pw.T.columns.2.xyz) < 0 { T = flipY(T) }     // match the parent wall's inward orientation
                return RoomSurface(id: "\(prefix)-\(i)", parent: p?.id, dimensions: dims(s.dimensions), transform: rowMajor(T), confidence: conf(s.confidence))
            }
        }
        let objects = room.objects.enumerated().map { i, o in
            RoomObjectJSON(id: "object-\(i)", category: category(o.category), dimensions: [Double(o.dimensions.x), Double(o.dimensions.y), Double(o.dimensions.z)], transform: rowMajor(o.transform), confidence: conf(o.confidence))
        }
        let ends: [SIMD3<Float>] = wallInfo.flatMap { w -> [SIMD3<Float>] in
            let ax = simd_normalize(w.T.columns.0.xyz) * (w.dim.x / 2); let c = w.T.columns.3.xyz; return [c - ax, c + ax]
        }
        let poly = FloorPolygon.make(wallEnds: ends.map { SIMD2($0.x, $0.z) })
        let ceiling = Double(wallInfo.map { $0.dim.y }.max() ?? 2.5)
        return RoomJSON(id: "room-\(index)", name: name, ceilingHeight: ceiling, floorPolygon: poly.map { [Double($0.x), Double($0.y)] },
                        walls: walls, doors: surfaces(room.doors, prefix: "door"), windows: surfaces(room.windows, prefix: "window"),
                        openings: surfaces(room.openings, prefix: "opening"), objects: objects)
    }

    static func summary(_ r: RoomJSON, file: String, usdz: String?) -> RoomSummary {
        let xs = r.floorPolygon.map { $0[0] }, zs = r.floorPolygon.map { $0[1] }
        return RoomSummary(id: r.id, name: r.name, file: file, usdz: usdz, floorArea: FloorPolygon.area(r.floorPolygon), ceilingHeight: r.ceilingHeight,
                           boundingBox: BoundingBox(min: [xs.min() ?? 0, 0, zs.min() ?? 0], max: [xs.max() ?? 0, r.ceilingHeight, zs.max() ?? 0]),
                           originTransform: rowMajor(matrix_identity_float4x4))
    }
}

/// Chains wall endpoints into a closed outline (falls back to the convex hull when walls don't close).
enum FloorPolygon {
    static func make(wallEnds: [SIMD2<Float>]) -> [SIMD2<Float>] {
        guard wallEnds.count >= 6 else { return hull(wallEnds) }
        var segs = stride(from: 0, to: wallEnds.count - 1, by: 2).map { (wallEnds[$0], wallEnds[$0 + 1]) }
        var out = [segs[0].0, segs[0].1]; segs.removeFirst()
        while !segs.isEmpty {
            let tail = out.last!
            guard let (i, flip) = segs.enumerated().compactMap({ (i, s) -> (Int, Bool, Float)? in
                let a = simd_distance(s.0, tail), b = simd_distance(s.1, tail); return a < b ? (i, false, a) : (i, true, b)
            }).min(by: { $0.2 < $1.2 }).map({ ($0.0, $0.1) }) else { break }
            let s = segs.remove(at: i); out.append(flip ? s.0 : s.1)
        }
        if simd_distance(out.first!, out.last!) < 0.4 { out.removeLast(); return out.count >= 3 ? ensureCCW(out) : hull(wallEnds) }
        return hull(wallEnds)
    }
    static func area(_ p: [[Double]]) -> Double {
        guard p.count >= 3 else { return 0 }
        var a = 0.0; for i in p.indices { let j = (i + 1) % p.count; a += p[i][0] * p[j][1] - p[j][0] * p[i][1] }
        return abs(a) / 2
    }
    static func ensureCCW(_ p: [SIMD2<Float>]) -> [SIMD2<Float>] {
        var a: Float = 0; for i in p.indices { let j = (i + 1) % p.count; a += p[i].x * p[j].y - p[j].x * p[i].y }
        return a < 0 ? p.reversed() : p
    }
    static func hull(_ pts: [SIMD2<Float>]) -> [SIMD2<Float>] {
        let s = pts.sorted { $0.x == $1.x ? $0.y < $1.y : $0.x < $1.x }; guard s.count > 2 else { return s }
        func cross(_ o: SIMD2<Float>, _ a: SIMD2<Float>, _ b: SIMD2<Float>) -> Float { (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x) }
        var lo: [SIMD2<Float>] = []; for p in s { while lo.count >= 2 && cross(lo[lo.count - 2], lo[lo.count - 1], p) <= 0 { lo.removeLast() }; lo.append(p) }
        var up: [SIMD2<Float>] = []; for p in s.reversed() { while up.count >= 2 && cross(up[up.count - 2], up[up.count - 1], p) <= 0 { up.removeLast() }; up.append(p) }
        lo.removeLast(); up.removeLast(); return lo + up
    }
}
