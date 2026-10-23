import XCTest
import simd
@testable import RoomScanner

final class ExporterTests: XCTestCase {
    func sampleFrame(_ i: Int) -> FrameEntry {
        FrameEntry(frameIndex: i, timestamp: Double(i) / 10, trackingState: "normal",
                   camera: CameraSettings(exposureDuration: 0.0167, iso: 100, shutter: 0.0167, whiteBalance: WhiteBalance(r: 1.9, g: 1, b: 1.6)),
                   confidence: 0.9, file: "depth/\(String(format: "%05d", i)).png", confidenceFile: "depth/\(String(format: "%05d", i))_conf.png", width: 256, height: 192,
                   intrinsics: [[182, 0, 127.7], [0, 182, 96.2], [0, 0, 1]], rgb: "depth/\(String(format: "%05d", i)).jpg", rgbWidth: 640, rgbHeight: 480,
                   rgbIntrinsics: [[455, 0, 320], [0, 455, 240], [0, 0, 1]], photo: "photos/\(String(format: "%05d", i)).jpg", transform: rowMajor(matrix_identity_float4x4))
    }

    func testFrameJSONHasEverySchemaKey() throws {
        let data = try JSONEncoder().encode(sampleFrame(3))
        let obj = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        for k in ["frameIndex", "timestamp", "trackingState", "camera", "confidence", "file", "confidenceFile", "width", "height", "intrinsics", "rgb", "rgbWidth", "rgbHeight", "rgbIntrinsics", "transform"] {
            XCTAssertNotNil(obj[k], "missing \(k)")
        }
        XCTAssertEqual((obj["transform"] as! [[Double]]).count, 4)
        let cam = obj["camera"] as! [String: Any]
        for k in ["exposureDuration", "iso", "shutter", "whiteBalance"] { XCTAssertNotNil(cam[k]) }
    }

    func testRowMajorMatrix() {
        var m = matrix_identity_float4x4; m.columns.3 = SIMD4(1, 2, 3, 1)          // translation lives in the last COLUMN
        let r = rowMajor(m)
        XCTAssertEqual(r[0][3], 1); XCTAssertEqual(r[1][3], 2); XCTAssertEqual(r[2][3], 3); XCTAssertEqual(r[3][3], 1)
    }

    func testManifestRoundTripAndRequiredFields() throws {
        let m = Manifest(scanName: "Office", version: 4, folderName: "Office_v4_2026-10-09", createdAt: "2026-10-09T12:00:00Z",
                         device: DeviceInfo(model: "iPhone17,2", iosVersion: "17.5", app: "Room Scanner 1.0"), counts: Counts(frames: 2, photos: 2, rooms: 1),
                         depth: DepthInfo(width: 256, height: 192), files: [FileRecord(path: "a", bytes: 1, sha256: String(repeating: "0", count: 64))],
                         rooms: [RoomSummary(id: "room-0", name: "Office", file: "rooms/room-0.json", usdz: nil, floorArea: 12, ceilingHeight: 2.5,
                                             boundingBox: BoundingBox(min: [0, 0, 0], max: [4, 2.5, 3]), originTransform: rowMajor(matrix_identity_float4x4))])
        let back = try JSONDecoder().decode(Manifest.self, from: JSONEncoder().encode(m))
        XCTAssertEqual(back, m)
        let obj = try JSONSerialization.jsonObject(with: JSONEncoder().encode(m)) as! [String: Any]
        XCTAssertEqual(obj["units"] as? String, "meters")
        XCTAssertEqual((obj["coordinateSystem"] as! [String: String])["up"], "+Y")
        XCTAssertGreaterThan((obj["README_FOR_AI"] as! String).count, 200)
        let pattern = try NSRegularExpression(pattern: "^.+_v[0-9]+_[0-9]{4}-[0-9]{2}-[0-9]{2}$")
        let f = obj["folderName"] as! String; XCTAssertEqual(pattern.numberOfMatches(in: f, range: NSRange(f.startIndex..., in: f)), 1)
    }

    func testVersioningNeverReusesANumber() {
        XCTAssertEqual(ScanStore.nextVersion(name: "Office", existing: []), 1)
        XCTAssertEqual(ScanStore.nextVersion(name: "Office", existing: ["Office_v1_2026-10-01", "Office_v3_2026-10-05", "Kitchen_v9_2026-10-05"]), 4)
        XCTAssertEqual(ScanStore.folderName(name: "Living Room", version: 2, date: Date(timeIntervalSince1970: 0)).hasPrefix("Living-Room_v2_"), true)
    }

    func testKeyframeThresholds() {
        var s = KeyframeSelector()
        let t0 = matrix_identity_float4x4
        XCTAssertEqual(s.evaluate(transform: t0, time: 0, trackingNormal: true, sharpness: { 500 }), .none)             // first frame accepted
        var t1 = t0; t1.columns.3.x = 0.1
        XCTAssertEqual(s.evaluate(transform: t1, time: 1, trackingNormal: true, sharpness: { 500 }), .tooClose)          // < 0.25 m, < 15°
        var t2 = t0; t2.columns.3.x = 0.3
        XCTAssertEqual(s.evaluate(transform: t2, time: 2, trackingNormal: true, sharpness: { 10 }), .blurry)             // moved enough but blurry
        XCTAssertEqual(s.evaluate(transform: t2, time: 3, trackingNormal: false, sharpness: { 500 }), .tracking)
        XCTAssertEqual(s.evaluate(transform: t2, time: 4, trackingNormal: true, sharpness: { 500 }), .none)
    }

    func testFloorPolygonChainsWalls() {
        // 4x3 rectangle given as 4 wall segments (endpoint pairs), out of order
        let e: [SIMD2<Float>] = [SIMD2(0, 0), SIMD2(4, 0), SIMD2(4, 3), SIMD2(4, 0), SIMD2(0, 3), SIMD2(4, 3), SIMD2(0, 0), SIMD2(0, 3)]
        let poly = FloorPolygon.make(wallEnds: e)
        XCTAssertEqual(poly.count, 4)
        XCTAssertEqual(FloorPolygon.area(poly.map { [Double($0.x), Double($0.y)] }), 12, accuracy: 1e-4)
    }
}
