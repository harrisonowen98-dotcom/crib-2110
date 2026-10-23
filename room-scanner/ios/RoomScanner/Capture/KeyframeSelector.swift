import Foundation
import simd

struct CaptureConfig {
    var minTranslation: Float = GeneratedConfig.keyframeTranslationM   // m   (config.yaml capture.keyframe_translation_m)
    var minRotationDeg: Float = GeneratedConfig.keyframeRotationDeg   // deg (capture.keyframe_rotation_deg)
    var blurLaplacianMin: Double = GeneratedConfig.blurLaplacianMin   // capture.blur_laplacian_min
    var maxAngularSpeed: Float = 1.2          // rad/s above which we say "move slower"
    var minInterval: TimeInterval = 0.25
    var lowLightLumens: CGFloat = 120
}

enum SkipReason: String { case none, tracking, tooClose, blurry, fast }

/// Decides when a frame becomes a keyframe: moved > minTranslation OR rotated > minRotation since the last keyframe,
/// tracking normal, not blurry (Laplacian variance) and not moving too fast.
struct KeyframeSelector {
    var config = CaptureConfig()
    private(set) var lastKeyTransform: simd_float4x4?
    private(set) var lastKeyTime: TimeInterval = -1
    private var prevTransform: simd_float4x4?
    private var prevTime: TimeInterval = 0
    private(set) var angularSpeed: Float = 0

    static func angleBetween(_ a: simd_float4x4, _ b: simd_float4x4) -> Float {
        let qa = simd_quatf(a), qb = simd_quatf(b)
        let d = abs(simd_dot(qa.vector, qb.vector)).clamped(0, 1)
        return 2 * acos(d)
    }

    mutating func evaluate(transform: simd_float4x4, time: TimeInterval, trackingNormal: Bool, sharpness: () -> Double, force: Bool = false) -> SkipReason {
        if let p = prevTransform, time > prevTime { angularSpeed = Self.angleBetween(p, transform) / Float(time - prevTime) }
        prevTransform = transform; prevTime = time
        if force { accept(transform, time); return .none }
        guard trackingNormal else { return .tracking }
        if time - lastKeyTime < config.minInterval { return .tooClose }
        if let last = lastKeyTransform {
            let dt = simd_length(transform.columns.3.xyz - last.columns.3.xyz)
            let dr = Self.angleBetween(last, transform) * 180 / .pi
            if dt < config.minTranslation && dr < config.minRotationDeg { return .tooClose }
        }
        if angularSpeed > config.maxAngularSpeed { return .fast }
        if sharpness() < config.blurLaplacianMin { return .blurry }     // only computed once movement thresholds are met
        accept(transform, time)
        return .none
    }

    private mutating func accept(_ t: simd_float4x4, _ time: TimeInterval) { lastKeyTransform = t; lastKeyTime = time }
}

extension Comparable { func clamped(_ lo: Self, _ hi: Self) -> Self { min(max(self, lo), hi) } }
extension SIMD4 where Scalar == Float { var xyz: SIMD3<Float> { SIMD3(x, y, z) } }
