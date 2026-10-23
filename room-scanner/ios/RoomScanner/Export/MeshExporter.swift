import Foundation
import ARKit
import ModelIO
import MetalKit

/// ARKit mesh anchors -> mesh/mesh.ply (world space, per-vertex `classification`) and mesh/mesh.usdz.
enum MeshExporter {
    /// ARMeshClassification raw values: 0 none, 1 wall, 2 floor, 3 ceiling, 4 table, 5 seat, 6 window, 7 door.
    static func writePLY(anchors: [ARMeshAnchor], to url: URL) throws {
        var positions: [SIMD3<Float>] = []; var classes: [UInt8] = []; var faces: [UInt32] = []
        for a in anchors {
            let g = a.geometry; let base = UInt32(positions.count)
            let vb = g.vertices; let vp = vb.buffer.contents()
            var vclass = [UInt8](repeating: 0, count: vb.count)
            for i in 0..<vb.count {
                let v = vp.advanced(by: vb.offset + vb.stride * i).assumingMemoryBound(to: SIMD3<Float>.self).pointee
                positions.append((a.transform * SIMD4(v, 1)).xyz)
            }
            let fb = g.faces; let fp = fb.buffer.contents(); let per = fb.indexCountPerPrimitive
            for f in 0..<fb.count {
                var cls: UInt8 = 0
                if let c = g.classification { cls = c.buffer.contents().advanced(by: c.offset + c.stride * f).assumingMemoryBound(to: UInt8.self).pointee }
                for k in 0..<per {
                    let idx = fp.advanced(by: (f * per + k) * fb.bytesPerIndex).assumingMemoryBound(to: UInt32.self).pointee
                    faces.append(base + idx); vclass[Int(idx)] = cls      // ARKit classifies faces; we store it on the vertices
                }
            }
            classes.append(contentsOf: vclass)
        }
        var data = Data()
        let header = "ply\nformat binary_little_endian 1.0\ncomment units meters, Y-up right-handed\nelement vertex \(positions.count)\nproperty float x\nproperty float y\nproperty float z\nproperty uchar classification\nelement face \(faces.count / 3)\nproperty list uchar int vertex_indices\nend_header\n"
        data.append(Data(header.utf8))
        for (i, p) in positions.enumerated() {
            var x = p.x, y = p.y, z = p.z
            withUnsafeBytes(of: &x) { data.append(contentsOf: $0) }; withUnsafeBytes(of: &y) { data.append(contentsOf: $0) }; withUnsafeBytes(of: &z) { data.append(contentsOf: $0) }
            data.append(classes[i])
        }
        for f in stride(from: 0, to: faces.count, by: 3) {
            data.append(3)
            for k in 0..<3 { var idx = Int32(faces[f + k]); withUnsafeBytes(of: &idx) { data.append(contentsOf: $0) } }
        }
        try data.write(to: url)
    }

    static func writeUSDZ(anchors: [ARMeshAnchor], to url: URL) throws {
        guard let device = MTLCreateSystemDefaultDevice() else { throw CocoaError(.featureUnsupported) }
        let allocator = MTKMeshBufferAllocator(device: device)
        let asset = MDLAsset(bufferAllocator: allocator)
        for a in anchors {
            let g = a.geometry
            let vData = Data(bytes: g.vertices.buffer.contents(), count: g.vertices.stride * g.vertices.count)
            let iCount = g.faces.count * g.faces.indexCountPerPrimitive
            let iData = Data(bytes: g.faces.buffer.contents(), count: g.faces.bytesPerIndex * iCount)
            let vBuf = allocator.newBuffer(with: vData, type: .vertex), iBuf = allocator.newBuffer(with: iData, type: .index)
            let sub = MDLSubmesh(indexBuffer: iBuf, indexCount: iCount, indexType: .uInt32, geometryType: .triangles, material: nil)
            let desc = MDLVertexDescriptor()
            desc.attributes[0] = MDLVertexAttribute(name: MDLVertexAttributePosition, format: .float3, offset: g.vertices.offset, bufferIndex: 0)
            desc.layouts[0] = MDLVertexBufferLayout(stride: g.vertices.stride)
            let mesh = MDLMesh(vertexBuffer: vBuf, vertexCount: g.vertices.count, descriptor: desc, submeshes: [sub])
            mesh.transform = MDLTransform(matrix: a.transform)
            asset.add(mesh)
        }
        try asset.export(to: url)
    }
}
