import Foundation

/// One position report. `speedMps` is optional because a fix taken
/// standing still often has none, and the policy must treat "no speed"
/// differently from "stopped" (brief F3: never nag at a curb).
public struct LocationFix: Sendable, Equatable {
    public let latLng: LatLng
    public let horizontalAccuracyM: Double
    public let speedMps: Double?
    public let timestampMs: Int64

    public init(latLng: LatLng, horizontalAccuracyM: Double, speedMps: Double?, timestampMs: Int64) {
        self.latLng = latLng
        self.horizontalAccuracyM = horizontalAccuracyM
        self.speedMps = speedMps
        self.timestampMs = timestampMs
    }
}

/// One true-heading report, normalised into `[0, 360)` on the way in so
/// no downstream code has to wonder. Only ever produced from a
/// `CLHeading` whose `trueHeading` was non-negative -- a negative value
/// means "undetermined" and is dropped at the adapter.
public struct HeadingSample: Sendable, Equatable {
    public let trueHeadingDeg: Double
    public let accuracyDeg: Double
    public let timestampMs: Int64

    public init(trueHeadingDeg: Double, accuracyDeg: Double, timestampMs: Int64) {
        self.trueHeadingDeg = Geo.normalizedHeadingDeg(trueHeadingDeg)
        self.accuracyDeg = accuracyDeg
        self.timestampMs = timestampMs
    }
}

/// Where fixes come from. `CoreLocationSources` implements this on iOS;
/// the tests implement it with a scripted fake, which is why nothing in
/// this module needs a device to be tested.
///
/// **Boundary rule (build-plan F7):** these two protocols are the only
/// way CoreLocation enters the app, and `CaneNavigation` has no
/// dependency on `CaneContracts`/`CaneFeedback`, so a fix can never
/// reach the awareness path, where bearings stay torso-relative.
public protocol LocationSource: Sendable {
    /// A fresh stream of fixes. Calling it starts the underlying
    /// hardware updates if they are not already running.
    func locations() -> AsyncStream<LocationFix>
    /// Stops hardware updates and finishes any stream handed out.
    func stop()
}

public protocol HeadingSource: Sendable {
    func headings() -> AsyncStream<HeadingSample>
    func stop()
}
