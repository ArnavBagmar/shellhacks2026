import Foundation

/// One simulated second of walking.
public struct WalkSample: Sendable, Equatable {
    public let fix: LocationFix
    public let heading: HeadingSample

    public init(fix: LocationFix, heading: HeadingSample) {
        self.fix = fix
        self.heading = heading
    }
}

/// Replays a route as if someone walked it: exact on-path fixes at a
/// fixed cadence, each with the heading of the leg being walked.
///
/// Used by `nav-cli` for the live check (brief F7) and by the follower
/// and policy tests, so the cue sequence the tests assert is produced by
/// the same walk the CLI prints. Pure and instant -- no `Task.sleep`
/// anywhere.
public struct RouteWalkSimulator: Sendable {
    public static let defaultSpeedMps = 1.4
    public static let defaultIntervalMs: Int64 = 1_000
    private static let horizontalAccuracyM = 5.0
    private static let headingAccuracyDeg = 5.0

    private let points: [LatLng]
    private let speedMps: Double
    private let intervalMs: Int64
    private let startMs: Int64

    public init(
        route: Route,
        speedMps: Double = defaultSpeedMps,
        intervalMs: Int64 = defaultIntervalMs,
        startMs: Int64 = 0
    ) {
        self.points = route.points
        self.speedMps = speedMps
        self.intervalMs = intervalMs
        self.startMs = startMs
    }

    /// Fixes from the origin to the destination inclusive; the last one
    /// sits exactly on the destination even if it falls mid-interval.
    public func samples() -> [WalkSample] {
        let total = RouteGeometry.totalLength(points)
        guard total > 0 else { return [] }

        var samples: [WalkSample] = []
        var index = 0
        while true {
            let travelled = speedMps * Double(index) * Double(intervalMs) / 1_000
            let distance = min(travelled, total)
            if let sample = sample(atDistance: distance, index: index) {
                samples.append(sample)
            }
            if travelled >= total { break }
            index += 1
        }
        return samples
    }

    private func sample(atDistance distance: Double, index: Int) -> WalkSample? {
        guard let (position, bearing) = RouteGeometry.point(at: distance, along: points) else {
            return nil
        }
        let timestampMs = startMs + Int64(index) * intervalMs
        return WalkSample(
            fix: LocationFix(
                latLng: position,
                horizontalAccuracyM: RouteWalkSimulator.horizontalAccuracyM,
                speedMps: speedMps,
                timestampMs: timestampMs
            ),
            heading: HeadingSample(
                trueHeadingDeg: bearing,
                accuracyDeg: RouteWalkSimulator.headingAccuracyDeg,
                timestampMs: timestampMs
            )
        )
    }
}
