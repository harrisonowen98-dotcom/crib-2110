import SwiftUI
import ARKit
import SceneKit

/// Camera feed + LiDAR mesh drawn as a wireframe: green = enough photos saw this part, red/pink = still missing.
struct ARScanView: UIViewRepresentable {
    @ObservedObject var session: ScanSession

    func makeCoordinator() -> Coordinator { Coordinator(session) }

    func makeUIView(context: Context) -> ARSCNView {
        let v = ARSCNView(frame: .zero)
        v.session = session.arSession
        v.delegate = context.coordinator
        v.automaticallyUpdatesLighting = false
        v.rendersContinuously = true
        context.coordinator.view = v
        return v
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {
        context.coordinator.showMesh = session.showMesh
        context.coordinator.refreshColors()
    }

    final class Coordinator: NSObject, ARSCNViewDelegate {
        let session: ScanSession
        weak var view: ARSCNView?
        var showMesh = true
        private var nodes: [UUID: SCNNode] = [:]
        init(_ s: ScanSession) { session = s }

        func color(for id: UUID) -> UIColor {
            let n = session.anchorViews[id, default: 0]
            return n >= 3 ? UIColor.systemGreen.withAlphaComponent(0.85) : UIColor(red: 1, green: 0.35, blue: 0.5, alpha: 0.75)
        }
        func refreshColors() {
            for (id, node) in nodes { node.geometry?.firstMaterial?.diffuse.contents = color(for: id); node.isHidden = !showMesh }
        }

        func renderer(_ renderer: SCNSceneRenderer, nodeFor anchor: ARAnchor) -> SCNNode? {
            guard let a = anchor as? ARMeshAnchor else { return nil }
            let node = SCNNode(geometry: Self.geometry(a.geometry, color: color(for: a.identifier)))
            node.isHidden = !showMesh; nodes[a.identifier] = node
            return node
        }
        func renderer(_ renderer: SCNSceneRenderer, didUpdate node: SCNNode, for anchor: ARAnchor) {
            guard let a = anchor as? ARMeshAnchor else { return }
            node.geometry = Self.geometry(a.geometry, color: color(for: a.identifier)); node.isHidden = !showMesh
        }
        func renderer(_ renderer: SCNSceneRenderer, didRemove node: SCNNode, for anchor: ARAnchor) { nodes[anchor.identifier] = nil }

        static func geometry(_ g: ARMeshGeometry, color: UIColor) -> SCNGeometry {
            let v = g.vertices
            let vSrc = SCNGeometrySource(buffer: v.buffer, vertexFormat: v.format, semantic: .vertex, vertexCount: v.count, dataOffset: v.offset, dataStride: v.stride)
            let f = g.faces
            let data = Data(bytesNoCopy: f.buffer.contents(), count: f.bytesPerIndex * f.count * f.indexCountPerPrimitive, deallocator: .none)
            let element = SCNGeometryElement(data: data, primitiveType: .triangles, primitiveCount: f.count, bytesPerIndex: f.bytesPerIndex)
            let geo = SCNGeometry(sources: [vSrc], elements: [element])
            let m = SCNMaterial(); m.fillMode = .lines; m.diffuse.contents = color; m.isDoubleSided = true; m.lightingModel = .constant
            geo.materials = [m]
            return geo
        }
    }
}
