import Foundation
import ARKit
import CoreImage
import ImageIO
import UniformTypeIdentifiers
import AVFoundation

/// Streams every keyframe to disk as it is captured (nothing accumulates in RAM) and appends one JSON line per frame to
/// frames.jsonl; `finalize()` turns that into depth/frames.json.
final class FrameWriter {
    let root: URL
    private let queue = DispatchQueue(label: "frame-writer", qos: .userInitiated)
    private let ciContext = CIContext(options: [.cacheIntermediates: false])
    private var jsonl: FileHandle?
    private(set) var count = 0
    private var pending = 0
    private let lock = NSLock()
    var onWritten: (() -> Void)?

    init(root: URL) {
        self.root = root
        let p = root.appendingPathComponent("depth/frames.jsonl")
        FileManager.default.createFile(atPath: p.path, contents: nil)
        jsonl = try? FileHandle(forWritingTo: p)
    }

    var inFlight: Int { lock.lock(); defer { lock.unlock() }; return pending }

    struct CameraInfo { var iso: Double; var wb: WhiteBalance }

    static func cameraInfo() -> CameraInfo {
        guard let d = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else { return .init(iso: 0, wb: .init(r: 1, g: 1, b: 1)) }
        let g = d.deviceWhiteBalanceGains
        return .init(iso: Double(d.iso), wb: .init(r: Double(g.redGain), g: Double(g.greenGain), b: Double(g.blueGain)))
    }

    /// Copies what it needs from the ARFrame synchronously (pixel buffers are retained), then encodes on a background queue.
    func write(frame: ARFrame, index: Int, info: CameraInfo) {
        guard let depth = frame.sceneDepth else { return }
        let image = frame.capturedImage, depthMap = depth.depthMap, confMap = depth.confidenceMap
        let cam = frame.camera
        let tracking: String = { if case .normal = cam.trackingState { return "normal" }; if case .limited = cam.trackingState { return "limited" }; return "notAvailable" }()
        lock.lock(); pending += 1; lock.unlock()
        queue.async { [self] in
            defer { lock.lock(); pending -= 1; lock.unlock(); onWritten?() }
            autoreleasepool {
                let name = String(format: "%05d", index)
                let dw = CVPixelBufferGetWidth(depthMap), dh = CVPixelBufferGetHeight(depthMap)
                let (depthPNG, confPNG, confFrac) = Self.encodeDepth(depthMap, confMap)
                try? depthPNG?.write(to: root.appendingPathComponent("depth/\(name).png"))
                try? confPNG?.write(to: root.appendingPathComponent("depth/\(name)_conf.png"))

                // RGB: small frame next to depth, full-res keyframe in photos/
                let ci = CIImage(cvPixelBuffer: image)
                let fullW = Int(ci.extent.width), fullH = Int(ci.extent.height)
                let cs = CGColorSpace(name: CGColorSpace.sRGB)!
                if let full = ciContext.jpegRepresentation(of: ci, colorSpace: cs, options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.85]) {
                    try? full.write(to: root.appendingPathComponent("photos/\(name).jpg"))
                }
                let rgbW = 640, rgbH = 480, s = CGFloat(rgbW) / CGFloat(fullW)
                let small = ci.transformed(by: CGAffineTransform(scaleX: s, y: s))
                if let sm = ciContext.jpegRepresentation(of: small, colorSpace: cs, options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.88]) {
                    try? sm.write(to: root.appendingPathComponent("depth/\(name).jpg"))
                }

                let K = cam.intrinsics   // for the full-resolution image
                let kd = simd_float3x3(columns: (SIMD3(K.columns.0.x * Float(dw) / Float(fullW), 0, 0),
                                                SIMD3(0, K.columns.1.y * Float(dh) / Float(fullH), 0),
                                                SIMD3(K.columns.2.x * Float(dw) / Float(fullW), K.columns.2.y * Float(dh) / Float(fullH), 1)))
                let kr = simd_float3x3(columns: (SIMD3(K.columns.0.x * Float(s), 0, 0), SIMD3(0, K.columns.1.y * Float(s), 0),
                                                SIMD3(K.columns.2.x * Float(s), K.columns.2.y * Float(s), 1)))
                let exp = frame.camera.exposureDuration
                let entry = FrameEntry(frameIndex: index, timestamp: frame.timestamp, trackingState: tracking,
                                       camera: CameraSettings(exposureDuration: exp, iso: info.iso, shutter: exp, whiteBalance: info.wb),
                                       confidence: confFrac, file: "depth/\(name).png", confidenceFile: "depth/\(name)_conf.png", width: dw, height: dh,
                                       intrinsics: rowMajor(kd), rgb: "depth/\(name).jpg", rgbWidth: rgbW, rgbHeight: Int(Float(fullH) * Float(s)),
                                       rgbIntrinsics: rowMajor(kr), photo: "photos/\(name).jpg", transform: rowMajor(cam.transform))
                if let data = try? JSONEncoder().encode(entry) {
                    jsonl?.write(data); jsonl?.write(Data("\n".utf8)); count += 1
                }
            }
        }
    }

    /// Waits for in-flight writes, then converts frames.jsonl -> depth/frames.json.
    func finalize() -> [FrameEntry] {
        queue.sync {}
        try? jsonl?.close(); jsonl = nil
        let p = root.appendingPathComponent("depth/frames.jsonl")
        let lines = ((try? String(contentsOf: p, encoding: .utf8)) ?? "").split(separator: "\n")
        let dec = JSONDecoder()
        let frames = lines.compactMap { try? dec.decode(FrameEntry.self, from: Data($0.utf8)) }.sorted { $0.frameIndex < $1.frameIndex }
        let enc = JSONEncoder(); enc.outputFormatting = [.prettyPrinted]
        if let data = try? enc.encode(FramesFile(frames: frames)) { try? data.write(to: root.appendingPathComponent("depth/frames.json")) }
        try? FileManager.default.removeItem(at: p)
        return frames
    }

    // MARK: depth encoding (uint16 millimeters PNG + uint8 confidence PNG)
    static func encodeDepth(_ depth: CVPixelBuffer, _ conf: CVPixelBuffer?) -> (Data?, Data?, Double) {
        CVPixelBufferLockBaseAddress(depth, .readOnly); defer { CVPixelBufferUnlockBaseAddress(depth, .readOnly) }
        let w = CVPixelBufferGetWidth(depth), h = CVPixelBufferGetHeight(depth)
        guard let base = CVPixelBufferGetBaseAddress(depth) else { return (nil, nil, 0) }
        let rowBytes = CVPixelBufferGetBytesPerRow(depth)
        var mm = [UInt16](repeating: 0, count: w * h)
        var c8 = [UInt8](repeating: 2, count: w * h)
        if let conf = conf {
            CVPixelBufferLockBaseAddress(conf, .readOnly)
            if let cb = CVPixelBufferGetBaseAddress(conf) {
                let cr = CVPixelBufferGetBytesPerRow(conf)
                for y in 0..<h { let row = cb.advanced(by: y * cr).assumingMemoryBound(to: UInt8.self); for x in 0..<w { c8[y * w + x] = row[x] } }
            }
            CVPixelBufferUnlockBaseAddress(conf, .readOnly)
        }
        var good = 0
        for y in 0..<h {
            let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: Float32.self)
            for x in 0..<w {
                let d = row[x]
                if d.isFinite && d > 0 { mm[y * w + x] = UInt16(min(65535, (d * 1000).rounded())) }
                if c8[y * w + x] >= 1 && mm[y * w + x] > 0 { good += 1 }
            }
        }
        let frac = Double(good) / Double(w * h)
        return (png16(mm, w, h), png8(c8, w, h), frac)
    }

    static func png16(_ px: [UInt16], _ w: Int, _ h: Int) -> Data? {
        // PNG stores 16-bit big-endian; CGImage with byteOrder16Little + ImageIO converts for us.
        let data = px.withUnsafeBufferPointer { Data(buffer: $0) }
        guard let provider = CGDataProvider(data: data as CFData),
              let img = CGImage(width: w, height: h, bitsPerComponent: 16, bitsPerPixel: 16, bytesPerRow: w * 2, space: CGColorSpaceCreateDeviceGray(),
                                bitmapInfo: CGBitmapInfo(rawValue: CGBitmapInfo.byteOrder16Little.rawValue | CGImageAlphaInfo.none.rawValue),
                                provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent) else { return nil }
        return encodePNG(img)
    }
    static func png8(_ px: [UInt8], _ w: Int, _ h: Int) -> Data? {
        guard let provider = CGDataProvider(data: Data(px) as CFData),
              let img = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: w, space: CGColorSpaceCreateDeviceGray(),
                                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue), provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent) else { return nil }
        return encodePNG(img)
    }
    static func encodePNG(_ img: CGImage) -> Data? {
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, img, nil)
        return CGImageDestinationFinalize(dest) ? out as Data : nil
    }
}
