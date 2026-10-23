import Foundation
import ARKit
import RoomPlan
import AVFoundation
import UIKit

enum ScanState: Equatable { case idle, scanning, paused, saving, saved(String), failed(String) }

/// Owns the ARSession (LiDAR scene reconstruction + sceneDepth), the RoomPlan session that shares it, keyframe capture,
/// coverage tracking and the final export. All published state is mutated on the main thread.
final class ScanSession: NSObject, ObservableObject, ARSessionDelegate, RoomCaptureSessionDelegate {
    // UI state
    @Published var state: ScanState = .idle
    @Published var photoCount = 0
    @Published var wallCount = 0
    @Published var roomCount = 0
    @Published var detail: Float = 0
    @Published var mapPoints: [MapPoint] = []
    @Published var cameraXZ = SIMD2<Float>(0, 0)
    @Published var cameraYaw: Float = 0
    @Published var hint: String?
    @Published var warnings: Set<String> = []
    @Published var plateau = false
    @Published var showMesh = true
    @Published var lockExposure = false { didSet { applyExposureLock() } }
    @Published var useRoomPlan = true
    @Published var scanName = "Room"

    let arSession = ARSession()
    var config = CaptureConfig()
    private(set) var meshAnchors: [UUID: ARMeshAnchor] = [:]
    private(set) var anchorViews: [UUID: Int] = [:]         // keyframes that saw each mesh anchor -> overlay colour

    private var selector = KeyframeSelector()
    private var writer: FrameWriter?
    private var folder: (url: URL, version: Int, folder: String)?
    private var coverage = CoverageTracker()
    private var frameIndex = 0
    private var lastMeshIngest = Date.distantPast
    private var lastSnapshot = Date.distantPast
    private var started = Date()
    private var gotDepth = false
    private var manualShutter = false
    private var lastCameraInfo = FrameWriter.cameraInfo()

    // RoomPlan
    private var captureSession: RoomCaptureSession?
    private var finishedRooms: [(json: RoomJSON, usdz: String?)] = []
    private var liveRoom: CapturedRoom?
    private var roomEnd: CheckedContinuation<CapturedRoomData?, Never>?

    // MARK: lifecycle
    func start(store: ScanStore, name: String) throws {
        guard ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) else { throw NSError(domain: "RoomScanner", code: 1, userInfo: [NSLocalizedDescriptionKey: "This device has no LiDAR."]) }
        scanName = name
        let f = try store.createScanFolder(name: name); folder = f
        writer = FrameWriter(root: f.url); selector = KeyframeSelector(); selector.config = config
        coverage = CoverageTracker(); frameIndex = 0; photoCount = 0; wallCount = 0; roomCount = 0; detail = 0; mapPoints = []; finishedRooms = []
        meshAnchors = [:]; anchorViews = [:]; gotDepth = false; started = Date(); plateau = false; warnings = []
        let cfg = ARWorldTrackingConfiguration()
        cfg.sceneReconstruction = .meshWithClassification
        cfg.frameSemantics = [.sceneDepth]
        cfg.environmentTexturing = .none
        if let fmt = ARWorldTrackingConfiguration.recommendedVideoFormatForHighResolutionFrameCapturing { cfg.videoFormat = fmt }
        arSession.delegate = self
        arSession.run(cfg, options: [.resetTracking, .removeExistingAnchors])
        applyExposureLock()
        UIApplication.shared.isIdleTimerDisabled = true
        UIDevice.current.isBatteryMonitoringEnabled = true
        if useRoomPlan { startRoomPlan() }
        state = .scanning
        // If RoomPlan took over the camera configuration, depth stops arriving: say so instead of failing silently.
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
            guard let self, self.state == .scanning, !self.gotDepth else { return }
            self.warnings.insert("No LiDAR depth arriving — turn off “Detect walls & objects” in the scan options and rescan")
        }
    }

    func pause() { guard state == .scanning else { return }; arSession.pause(); captureSession?.stop(pauseARSession: false); state = .paused }
    func resume() {
        guard state == .paused, let cfg = arSession.configuration else { return }
        arSession.run(cfg, options: []); if useRoomPlan { startRoomPlan() }; state = .scanning
    }
    func requestManualShutter() { manualShutter = true }

    func cancel() {
        arSession.pause(); captureSession?.stop(pauseARSession: false); UIApplication.shared.isIdleTimerDisabled = false
        _ = writer?.finalize(); state = .idle
    }

    // MARK: ARSessionDelegate
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        guard state == .scanning else { return }
        let cam = frame.camera
        cameraXZ = SIMD2(cam.transform.columns.3.x, cam.transform.columns.3.z)
        let fwd = -cam.transform.columns.2.xyz; cameraYaw = atan2(fwd.x, -fwd.z)
        if frame.sceneDepth != nil { gotDepth = true }
        updateWarnings(frame)

        let normal: Bool = { if case .normal = cam.trackingState { return true }; return false }()
        let forced = manualShutter; manualShutter = false
        let reason = selector.evaluate(transform: cam.transform, time: frame.timestamp, trackingNormal: normal && frame.sceneDepth != nil,
                                       sharpness: { Self.laplacianVariance(frame.capturedImage) }, force: forced && normal)
        if reason == .fast { warnings.insert("Move slower") } else if reason == .none { warnings.remove("Move slower") }
        guard reason == .none, let depth = frame.sceneDepth, let writer = writer else { return }
        if writer.inFlight > 6 { return }          // disk can't keep up: skip rather than grow RAM
        if frameIndex % 8 == 0 { lastCameraInfo = FrameWriter.cameraInfo() }
        let idx = frameIndex; frameIndex += 1; photoCount = frameIndex
        writer.write(frame: frame, index: idx, info: lastCameraInfo)
        registerCoverage(frame: frame, depth: depth)
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) { track(anchors) }
    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) { track(anchors) }
    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) { for a in anchors { meshAnchors[a.identifier] = nil } }
    func session(_ session: ARSession, didFailWithError error: Error) { state = .failed(error.localizedDescription) }

    private func track(_ anchors: [ARAnchor]) {
        for a in anchors { if let m = a as? ARMeshAnchor { meshAnchors[m.identifier] = m } }
        if Date().timeIntervalSince(lastMeshIngest) > 2.5 {
            lastMeshIngest = Date(); coverage.ingest(anchors: Array(meshAnchors.values))
        }
    }

    private func registerCoverage(frame: ARFrame, depth: ARDepthData) {
        let map = depth.depthMap; CVPixelBufferLockBaseAddress(map, .readOnly)
        let w = CVPixelBufferGetWidth(map), h = CVPixelBufferGetHeight(map), rb = CVPixelBufferGetBytesPerRow(map)
        var copy = [Float](repeating: 0, count: w * h)
        if let base = CVPixelBufferGetBaseAddress(map) { for y in 0..<h { let row = base.advanced(by: y * rb).assumingMemoryBound(to: Float32.self); for x in 0..<w { copy[y * w + x] = row[x] } } }
        CVPixelBufferUnlockBaseAddress(map, .readOnly)
        coverage.register(transform: frame.camera.transform, intrinsics: frame.camera.intrinsics, imageSize: frame.camera.imageResolution, depth: copy, depthW: w, depthH: h)
        // overlay colour: count keyframes that had each anchor's centre in view within 4 m
        let inv = frame.camera.transform.inverse; let K = frame.camera.intrinsics; let res = frame.camera.imageResolution
        for (id, a) in meshAnchors {
            let c = inv * a.transform.columns.3; let d = -c.z
            guard d > 0.3, d < 4.5 else { continue }
            let u = K.columns.0.x * c.x / d + K.columns.2.x, v = K.columns.2.y - K.columns.1.y * c.y / d
            if u >= 0, v >= 0, u < Float(res.width), v < Float(res.height) { anchorViews[id, default: 0] += 1 }
        }
        if Date().timeIntervalSince(lastSnapshot) > 1.0 {
            lastSnapshot = Date()
            coverage.snapshot { [weak self] p, pts, plateau in
                guard let self else { return }
                self.detail = p; self.mapPoints = pts; self.plateau = plateau
                self.hint = plateau ? "Photo detail isn’t improving much — you can save and review what you have." : "Photos are saved automatically as you move around the room."
            }
        }
    }

    private func updateWarnings(_ frame: ARFrame) {
        if let l = frame.lightEstimate?.ambientIntensity, l < config.lowLightLumens { warnings.insert("Low light — add light for sharper texture") } else { warnings.remove("Low light — add light for sharper texture") }
        let hot = ProcessInfo.processInfo.thermalState.rawValue >= ProcessInfo.ThermalState.serious.rawValue
        if hot { warnings.insert("Phone is hot — consider saving and resting") } else { warnings.remove("Phone is hot — consider saving and resting") }
        let b = UIDevice.current.batteryLevel
        if b >= 0, b < 0.15, UIDevice.current.batteryState != .charging { warnings.insert("Battery low") } else { warnings.remove("Battery low") }
    }

    private func applyExposureLock() {
        guard let dev = ARWorldTrackingConfiguration.configurableCaptureDeviceForPrimaryCamera else { return }
        do {
            try dev.lockForConfiguration()
            if lockExposure {
                dev.exposureMode = .locked
                if dev.isWhiteBalanceModeSupported(.locked) { dev.whiteBalanceMode = .locked }
            } else {
                dev.exposureMode = .continuousAutoExposure
                if dev.isWhiteBalanceModeSupported(.continuousAutoWhiteBalance) { dev.whiteBalanceMode = .continuousAutoWhiteBalance }
            }
            dev.unlockForConfiguration()
        } catch { /* best effort */ }
    }

    /// Variance of a 4-neighbour Laplacian on the luma plane, sampled every 4th pixel (cheap; ~60 for a soft frame, 200+ when sharp).
    static func laplacianVariance(_ buf: CVPixelBuffer) -> Double {
        CVPixelBufferLockBaseAddress(buf, .readOnly); defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddressOfPlane(buf, 0) else { return 0 }
        let w = CVPixelBufferGetWidthOfPlane(buf, 0), h = CVPixelBufferGetHeightOfPlane(buf, 0), rb = CVPixelBufferGetBytesPerRowOfPlane(buf, 0)
        let p = base.assumingMemoryBound(to: UInt8.self); let s = 4
        var sum = 0.0, sum2 = 0.0, n = 0.0
        var y = s; while y < h - s { var x = s; while x < w - s {
            let c = Double(p[y * rb + x]) * 4 - Double(p[(y - s) * rb + x]) - Double(p[(y + s) * rb + x]) - Double(p[y * rb + x - s]) - Double(p[y * rb + x + s])
            sum += c; sum2 += c * c; n += 1; x += s }; y += s }
        guard n > 0 else { return 0 }
        let mean = sum / n; return sum2 / n - mean * mean
    }

    // MARK: RoomPlan (shares our ARSession)
    private func startRoomPlan() {
        let cs = RoomCaptureSession(arSession: arSession)     // iOS 17: reuse our session so room + photos share one world frame
        cs.delegate = self; captureSession = cs
        cs.run(configuration: RoomCaptureSession.Configuration())
    }

    func captureSession(_ session: RoomCaptureSession, didUpdate room: CapturedRoom) {
        DispatchQueue.main.async { self.liveRoom = room; self.wallCount = self.finishedRooms.reduce(0) { $0 + $1.json.walls.count } + room.walls.count }
    }
    func captureSession(_ session: RoomCaptureSession, didEndWith data: CapturedRoomData, error: Error?) {
        roomEnd?.resume(returning: error == nil ? data : nil); roomEnd = nil
    }

    /// Ends the current RoomPlan room, stores it, and (when `startNext`) begins another room in the same AR session.
    func finishRoom(startNext: Bool) async {
        guard useRoomPlan, let cs = captureSession, let folder = folder else { return }
        let data: CapturedRoomData? = await withCheckedContinuation { cont in roomEnd = cont; cs.stop(pauseARSession: false) }
        if let data = data, let room = try? await RoomBuilder(options: [.beautifyObjects]).capturedRoom(from: data) {
            let i = finishedRooms.count
            let json = RoomPlanExporter.make(from: room, index: i, name: i == 0 ? scanName : "\(scanName) \(i + 1)")
            let enc = JSONEncoder(); enc.outputFormatting = [.prettyPrinted]
            try? enc.encode(json).write(to: folder.url.appendingPathComponent("rooms/room-\(i).json"))
            var usdz: String? = nil
            if (try? room.export(to: folder.url.appendingPathComponent("rooms/room-\(i).usdz"), exportOptions: .parametric)) != nil { usdz = "rooms/room-\(i).usdz" }
            _ = try? room.export(to: folder.url.appendingPathComponent("rooms/room-\(i)-mesh.usdz"), exportOptions: .mesh)
            finishedRooms.append((json, usdz))
            await MainActor.run { self.roomCount = self.finishedRooms.count; self.wallCount = self.finishedRooms.reduce(0) { $0 + $1.json.walls.count } }
        }
        if startNext { await MainActor.run { self.startRoomPlan() } }
    }

    // MARK: save
    /// Writes mesh, rooms, frames.json, README_FOR_AI.md and manifest.json (with sha256 of every file). Creates a NEW version; never overwrites.
    func save(store: ScanStore) async {
        guard let folder = folder, let writer = writer else { return }
        await MainActor.run { self.state = .saving }
        await finishRoom(startNext: false)
        arSession.pause(); UIApplication.shared.isIdleTimerDisabled = false
        let frames = writer.finalize()
        let anchors = Array(meshAnchors.values)
        do {
            try MeshExporter.writePLY(anchors: anchors, to: folder.url.appendingPathComponent("mesh/mesh.ply"))
            try? MeshExporter.writeUSDZ(anchors: anchors, to: folder.url.appendingPathComponent("mesh/mesh.usdz"))
            try Schema.readmeForAI.write(to: folder.url.appendingPathComponent("README_FOR_AI.md"), atomically: true, encoding: .utf8)
            let summaries = finishedRooms.enumerated().map { i, r in RoomPlanExporter.summary(r.json, file: "rooms/room-\(i).json", usdz: r.usdz) }
            let first = frames.first
            let manifest = Manifest(scanName: scanName, version: folder.version, folderName: folder.folder,
                                    createdAt: ISO8601DateFormatter().string(from: started),
                                    device: DeviceInfo(model: Self.deviceModel(), iosVersion: UIDevice.current.systemVersion, app: "Room Scanner 1.0"),
                                    counts: Counts(frames: frames.count, photos: frames.filter { $0.photo != nil }.count, rooms: summaries.count),
                                    depth: DepthInfo(width: first?.width ?? 256, height: first?.height ?? 192),
                                    files: Hashing.fileRecords(in: folder.url), rooms: summaries)
            let enc = JSONEncoder(); enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            try enc.encode(manifest).write(to: folder.url.appendingPathComponent("manifest.json"))
            await MainActor.run { store.reload(); self.state = .saved(folder.folder) }
        } catch {
            await MainActor.run { self.state = .failed(error.localizedDescription) }
        }
    }

    static func deviceModel() -> String {
        var sys = utsname(); uname(&sys)
        return withUnsafePointer(to: &sys.machine) { $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) } }
    }
}
