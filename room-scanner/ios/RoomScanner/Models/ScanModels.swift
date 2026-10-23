import Foundation
import simd

/// Schema 1.0.0 — see docs/SCHEMA.md. Units: meters. Right-handed, Y-up (ARKit). Matrices are 4x4 ROW-major nested arrays.
enum Schema {
    static let version = "1.0.0"
    static let readmeForAI = """
    ROOM SCAN EXPORT (Room Scanner schema 1.0.0). Units: meters. Right-handed, Y-up (ARKit). Camera space: +X right, +Y up, camera looks down -Z. \
    Layout: manifest.json (this file), depth/frames.json (one entry per captured frame: 4x4 camera-to-world 'transform' row-major, depth 'intrinsics' \
    for 256x192 depth, 'rgbIntrinsics' for the color jpg, exposure/iso/white balance, confidence), depth/*.png (16-bit PNG, millimeters, 0=invalid), \
    depth/*_conf.png (0/1/2 ARKit confidence), depth/*.jpg (RGB frame matching each depth map), photos/ (full-res keyframes, same frameIndex naming), \
    mesh/mesh.ply (ARKit mesh, per-vertex 'classification': 0 none,1 wall,2 floor,3 ceiling,4 table,5 seat,6 window,7 door), rooms/room-N.json \
    (RoomPlan CapturedRoom: walls, doors, windows, openings, objects with category+dimensions+transform) and optional .usdz. To rebuild: back-project \
    each depth pixel (u,v,d): X=(u-cx)/fx*d, Y=-(v-cy)/fy*d, Z=-d, then multiply by 'transform'. Colour points from the RGB jpg using rgbIntrinsics. \
    Build the architectural shell from rooms/*.json, place each object at its true-scale dimensions, and compare renders from each frame's pose against \
    its photo to refine.
    """
}

typealias Mat3 = [[Double]]
typealias Mat4 = [[Double]]

func rowMajor(_ m: simd_float4x4) -> Mat4 {
    (0..<4).map { r in (0..<4).map { c in Double(m[c][r]) } }   // simd is column-major: m[col][row]
}
func rowMajor(_ m: simd_float3x3) -> Mat3 {
    (0..<3).map { r in (0..<3).map { c in Double(m[c][r]) } }
}

struct WhiteBalance: Codable, Equatable { var r: Double; var g: Double; var b: Double }
struct CameraSettings: Codable, Equatable {
    var exposureDuration: Double
    var iso: Double
    var shutter: Double
    var whiteBalance: WhiteBalance
}

struct FrameEntry: Codable, Equatable {
    var frameIndex: Int
    var timestamp: Double
    var trackingState: String            // normal | limited | notAvailable
    var camera: CameraSettings
    var confidence: Double               // fraction of depth pixels with ARKit confidence >= medium
    var file: String                     // depth/NNNNN.png   uint16 mm
    var confidenceFile: String           // depth/NNNNN_conf.png  uint8 0/1/2
    var width: Int
    var height: Int
    var intrinsics: Mat3
    var rgb: String                      // depth/NNNNN.jpg
    var rgbWidth: Int
    var rgbHeight: Int
    var rgbIntrinsics: Mat3
    var photo: String?                   // photos/NNNNN.jpg  (full-res)
    var transform: Mat4                  // camera -> world
}

struct FramesFile: Codable {
    var schemaVersion = Schema.version
    var units = "meters"
    var poseConvention = "transform = 4x4 row-major camera-to-world; camera looks down -Z, +Y up, +X right"
    var frames: [FrameEntry]
}

struct FileRecord: Codable, Equatable { var path: String; var bytes: Int; var sha256: String }
struct BoundingBox: Codable, Equatable { var min: [Double]; var max: [Double] }
struct RoomSummary: Codable, Equatable {
    var id: String; var name: String; var file: String; var usdz: String?
    var floorArea: Double; var ceilingHeight: Double
    var boundingBox: BoundingBox; var originTransform: Mat4
}
struct DeviceInfo: Codable, Equatable { var model: String; var iosVersion: String; var app: String }
struct CoordinateSystem: Codable, Equatable { var handedness = "right"; var up = "+Y"; var cameraForward = "-Z" }
struct Counts: Codable, Equatable { var frames: Int; var photos: Int; var rooms: Int }
struct DepthInfo: Codable, Equatable { var format = "uint16 PNG"; var width: Int; var height: Int; var scale = "millimeters, 0 = invalid" }

struct Manifest: Codable, Equatable {
    var schemaVersion = Schema.version
    var scanName: String
    var version: Int
    var folderName: String
    var createdAt: String
    var device: DeviceInfo
    var units = "meters"
    var coordinateSystem = CoordinateSystem()
    var counts: Counts
    var depth: DepthInfo
    var files: [FileRecord]
    var rooms: [RoomSummary]
    var README_FOR_AI = Schema.readmeForAI
}

// RoomPlan -> simplified room JSON (rooms/room-N.json), matching schemas/room.schema.json
struct RoomSurface: Codable, Equatable {
    var id: String; var parent: String?; var dimensions: [Double]; var transform: Mat4; var confidence: String?
}
struct RoomObjectJSON: Codable, Equatable {
    var id: String; var category: String; var dimensions: [Double]; var transform: Mat4; var confidence: String?
}
struct RoomJSON: Codable, Equatable {
    var id: String; var name: String; var units = "meters"; var ceilingHeight: Double
    var floorPolygon: [[Double]]
    var walls: [RoomSurface]; var doors: [RoomSurface]; var windows: [RoomSurface]; var openings: [RoomSurface]
    var objects: [RoomObjectJSON]
}
