import Foundation

/// Which way the walker faces, from a compass that does not know how the
/// phone is worn.
///
/// An upright iPhone's compass reports where its *back* points. Strapped to
/// the chest with the screen out, the back points at the wearer, so the raw
/// heading is ~180 degrees wrong and every heading check said "Left to
/// rejoin route" / "Right to rejoin route" at a walker going the right way
/// (FIU, 2026-09-27). In a hand it is right; turned sideways it is 90 off.
///
/// So the offset is learned, not assumed: while the walker moves steadily,
/// GPS course says which way they really go; the circular mean of
/// (course - compass) over a few such fixes is the mount's offset. Until it
/// is learned, only the GPS course is trusted, and only while moving --
/// standing still there is no heading, so nothing is corrected.
public struct HeadingCalibrator: Sendable, Equatable {
    /// Below this, GPS course is noise.
    public static let minSpeedMps = 0.7
    public static let maxCourseAccuracyDeg = 30.0
    /// Agreeing samples needed before the compass is trusted.
    public static let samplesNeeded = 5
    /// Mean resultant length (0 random ... 1 identical) the samples must reach.
    public static let minConsistency = 0.8
    public static let window = 20

    private var diffs: [Double] = []
    /// compass + offset = the way the walker faces; nil until learned.
    public private(set) var offsetDeg: Double?

    public init() {}

    /// Folds in one fix and the compass reading of the same moment.
    public mutating func observe(_ fix: LocationFix, compass: HeadingSample?) {
        guard let course = Self.trustedCourse(fix), let compass else { return }
        diffs.append(Geo.normalizedAngleDeg(course - compass.trueHeadingDeg))
        if diffs.count > Self.window { diffs.removeFirst(diffs.count - Self.window) }
        guard diffs.count >= Self.samplesNeeded else { return }
        let (mean, consistency) = Self.circularMean(diffs)
        if consistency >= Self.minConsistency { offsetDeg = mean }
    }

    /// The walker's heading for this fix, or nil when there is nothing
    /// honest to say (compass not learned yet and not moving).
    public func heading(for fix: LocationFix, compass: HeadingSample?) -> HeadingSample? {
        if let offsetDeg, let compass {
            return HeadingSample(trueHeadingDeg: compass.trueHeadingDeg + offsetDeg,
                                 accuracyDeg: compass.accuracyDeg, timestampMs: compass.timestampMs)
        }
        guard let course = Self.trustedCourse(fix) else { return nil }
        return HeadingSample(trueHeadingDeg: course, accuracyDeg: fix.courseAccuracyDeg ?? Self.maxCourseAccuracyDeg,
                             timestampMs: fix.timestampMs)
    }

    static func trustedCourse(_ fix: LocationFix) -> Double? {
        guard let course = fix.courseDeg, let speed = fix.speedMps, speed >= minSpeedMps,
              (fix.courseAccuracyDeg ?? .infinity) <= maxCourseAccuracyDeg
        else { return nil }
        return course
    }

    /// Mean angle (-180, 180] and resultant length 0...1.
    static func circularMean(_ degrees: [Double]) -> (Double, Double) {
        let s = degrees.reduce(0) { $0 + sin($1 * .pi / 180) }
        let c = degrees.reduce(0) { $0 + cos($1 * .pi / 180) }
        let n = Double(max(degrees.count, 1))
        return (Geo.normalizedAngleDeg(atan2(s, c) * 180 / .pi), (s * s + c * c).squareRoot() / n)
    }
}
