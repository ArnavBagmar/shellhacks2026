import Foundation

/// A WGS-84 point in **degrees**. Immutable by construction: every
/// geometry helper returns a new `LatLng` rather than editing one, so a
/// fix that has been handed to the follower can never change underneath
/// it.
public struct LatLng: Hashable, Sendable, Codable {
    public let latitude: Double
    public let longitude: Double

    public init(latitude: Double, longitude: Double) {
        self.latitude = latitude
        self.longitude = longitude
    }
}

/// Spherical-earth geometry -- the only trigonometry in `CaneNavigation`.
///
/// Sign conventions, defined here once and relied on everywhere else:
/// * `crossTrackMeters` is **negative to the left** of the path and
///   positive to the right (looking along `start -> end`).
/// * `alongTrackMeters` is negative before `start`, positive after it.
/// * `normalizedAngleDeg` maps any angle into `(-180, 180]`.
public enum Geo {
    /// IUGG mean earth radius. One degree of latitude is
    /// `pi * R / 180` = 111 195 m, which the tests pin.
    public static let earthRadiusM: Double = 6_371_008.8

    // MARK: - Distance and bearing

    /// Great-circle distance in metres (haversine; stable for the short
    /// walking distances this module deals in).
    public static func haversineMeters(_ a: LatLng, _ b: LatLng) -> Double {
        let lat1 = radians(a.latitude)
        let lat2 = radians(b.latitude)
        let dLat = lat2 - lat1
        let dLon = radians(b.longitude - a.longitude)
        let h = sin(dLat / 2) * sin(dLat / 2)
            + cos(lat1) * cos(lat2) * sin(dLon / 2) * sin(dLon / 2)
        return 2 * earthRadiusM * asin(min(1, sqrt(max(0, h))))
    }

    /// Initial great-circle bearing from `from` to `to`, in degrees
    /// `[0, 360)`. Zero is true north, matching `CLHeading.trueHeading`.
    public static func initialBearingDeg(from: LatLng, to: LatLng) -> Double {
        let lat1 = radians(from.latitude)
        let lat2 = radians(to.latitude)
        let dLon = radians(to.longitude - from.longitude)
        let y = sin(dLon) * cos(lat2)
        let x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLon)
        let degrees = atan2(y, x) * 180 / .pi
        return degrees.truncatingRemainder(dividingBy: 360) < 0
            ? degrees.truncatingRemainder(dividingBy: 360) + 360
            : degrees.truncatingRemainder(dividingBy: 360)
    }

    /// Point reached by travelling `distanceMeters` from `from` on
    /// `bearingDeg`. Used by the walk simulator and by tests that need a
    /// point an exact number of metres off a path.
    public static func destinationPoint(
        from: LatLng, bearingDeg: Double, distanceMeters: Double
    ) -> LatLng {
        let angular = distanceMeters / earthRadiusM
        let bearing = radians(bearingDeg)
        let lat1 = radians(from.latitude)
        let lon1 = radians(from.longitude)
        let lat2 = asin(sin(lat1) * cos(angular) + cos(lat1) * sin(angular) * cos(bearing))
        let lon2 = lon1 + atan2(
            sin(bearing) * sin(angular) * cos(lat1),
            cos(angular) - sin(lat1) * sin(lat2)
        )
        return LatLng(latitude: lat2 * 180 / .pi, longitude: lon2 * 180 / .pi)
    }

    // MARK: - Projection onto a segment

    /// Signed perpendicular distance from `point` to the great circle
    /// through `start -> end`: negative left, positive right. Returns 0
    /// for a degenerate (zero-length) segment, whose direction is
    /// undefined.
    public static func crossTrackMeters(point: LatLng, start: LatLng, end: LatLng) -> Double {
        guard haversineMeters(start, end) > 0 else { return 0 }
        let angular13 = haversineMeters(start, point) / earthRadiusM
        let bearing13 = radians(initialBearingDeg(from: start, to: point))
        let bearing12 = radians(initialBearingDeg(from: start, to: end))
        return asin(clampUnit(sin(angular13) * sin(bearing13 - bearing12))) * earthRadiusM
    }

    /// Distance from `start` to the foot of `point`'s perpendicular,
    /// measured along `start -> end`. Negative when the foot lies behind
    /// `start`; may exceed the segment length when it lies beyond `end`.
    public static func alongTrackMeters(point: LatLng, start: LatLng, end: LatLng) -> Double {
        guard haversineMeters(start, end) > 0 else { return 0 }
        let angular13 = haversineMeters(start, point) / earthRadiusM
        let angularCross = crossTrackMeters(point: point, start: start, end: end) / earthRadiusM
        let cosRatio = clampUnit(cos(angular13) / cos(angularCross))
        let magnitude = acos(cosRatio) * earthRadiusM
        let bearingDelta = radians(
            initialBearingDeg(from: start, to: point) - initialBearingDeg(from: start, to: end)
        )
        return cos(bearingDelta) < 0 ? -magnitude : magnitude
    }

    /// Distance from `point` to the *segment* (not the infinite great
    /// circle), clamped at both endpoints. This is what "how far off the
    /// route am I" means near a corner, where the perpendicular to one
    /// leg can be arbitrarily far away.
    public static func distanceToSegmentMeters(point: LatLng, start: LatLng, end: LatLng) -> Double {
        let length = haversineMeters(start, end)
        guard length > 0 else { return haversineMeters(point, start) }
        let along = alongTrackMeters(point: point, start: start, end: end)
        if along <= 0 { return haversineMeters(point, start) }
        if along >= length { return haversineMeters(point, end) }
        return abs(crossTrackMeters(point: point, start: start, end: end))
    }

    // MARK: - Angles

    /// Normalises any angle (typically a heading difference) into
    /// `(-180, 180]`. Positive means "the heading is clockwise of the
    /// reference", i.e. the walker is facing right of the path.
    public static func normalizedAngleDeg(_ degrees: Double) -> Double {
        var angle = degrees.truncatingRemainder(dividingBy: 360)
        if angle <= -180 { angle += 360 }
        if angle > 180 { angle -= 360 }
        return angle
    }

    /// Normalises a compass heading into `[0, 360)`.
    public static func normalizedHeadingDeg(_ degrees: Double) -> Double {
        let angle = degrees.truncatingRemainder(dividingBy: 360)
        return angle < 0 ? angle + 360 : angle
    }

    private static func radians(_ degrees: Double) -> Double { degrees * .pi / 180 }

    private static func clampUnit(_ value: Double) -> Double { min(1, max(-1, value)) }
}
