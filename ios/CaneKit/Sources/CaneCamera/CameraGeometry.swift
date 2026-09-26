import Foundation

/// How the phone is strapped on. The screen faces out, so the front
/// (selfie) camera looks where the wearer looks.
public enum MountOrientation: String, Sendable, CaseIterable, Codable {
    /// Long edge horizontal -- the wider view (camera-sync-design default).
    case landscape
    case portrait
}

/// Pixel column -> torso-relative bearing, for the phone's front camera.
///
/// Sign convention is the contract's: **0 straight ahead, negative is the
/// wearer's left.** That holds only because capture turns mirroring off:
/// an unmirrored front-camera frame shows the scene from the camera's
/// viewpoint, and the camera faces the same way as the wearer, so
/// image-left is the wearer's left. A mirrored ("selfie") frame would flip
/// every bearing.
public enum CameraGeometry {
    /// The horizontal FOV of the *upright* image, from the sensor's long-side
    /// FOV (`AVCaptureDevice.Format.videoFieldOfView`) and aspect (>= 1).
    /// In portrait the image's horizontal axis is the sensor's short side.
    public static func imageHorizontalFOV(
        sensorLongSideFOVDeg: Double, sensorAspect: Double, mount: MountOrientation
    ) -> Double {
        switch mount {
        case .landscape:
            return sensorLongSideFOVDeg
        case .portrait:
            let halfLong = sensorLongSideFOVDeg * .pi / 360
            return 2 * atan(tan(halfLong) / max(sensorAspect, 1)) * 180 / .pi
        }
    }

    /// From the intrinsic matrix, when the camera delivers one:
    /// `2 * atan(cx / fx)` in the buffer the matrix describes.
    public static func horizontalFOV(fx: Double, cx: Double) -> Double? {
        guard fx > 0, cx > 0 else { return nil }
        return 2 * atan(cx / fx) * 180 / .pi
    }

    /// Exact pinhole bearing of a point at normalised column `x` (0 left
    /// edge, 1 right edge) in an upright, unmirrored image.
    public static func bearingDeg(normalizedX x: Double, hfovDeg: Double) -> Double {
        let clamped = min(max(x, 0), 1)
        let halfTan = tan(hfovDeg * .pi / 360)
        return atan((2 * clamped - 1) * halfTan) * 180 / .pi
    }
}
