import Foundation

/// Everything the phone's debug map draws, taken at one moment: the route
/// Google gave, where the walker is and faces, and what comes next.
/// Read-only; nothing in navigation depends on it.
public struct NavigationSnapshot: Sendable, Equatable {
    public let status: NavigationStatus
    public let placeName: String?
    public let destination: LatLng?
    public let routePoints: [LatLng]
    public let routeMeters: Int?
    /// The walker's newest fix and its error radius.
    public let fix: LocationFix?
    /// Which way the walker faces as navigation sees it (compass plus the
    /// learned offset, or the GPS course); nil when it does not know.
    public let headingDeg: Double?
    /// The learned compass offset; nil while still learning.
    public let compassOffsetDeg: Double?
    /// Where the next maneuver happens, and what it is.
    public let nextManeuverPoint: LatLng?
    public let nextManeuver: Maneuver?
    public let distanceToNextManeuverM: Double?
    public let offRouteM: Double?
    public let distanceToDestinationM: Double?
}

extension NavigationEngine {
    public func snapshot() -> NavigationSnapshot {
        let route = follower?.route
        let state = followerState
        let steps = route?.steps ?? []
        var nextPoint: LatLng?
        if let state, state.currentStepIndex < steps.count {
            nextPoint = steps[state.currentStepIndex].end
        }
        let heading = latestFix.flatMap { calibrator.heading(for: $0, compass: latestHeading)?.trueHeadingDeg }
        return NavigationSnapshot(
            status: status,
            placeName: resolved?.name,
            destination: destination,
            routePoints: route?.points ?? [],
            routeMeters: route?.distanceMeters,
            fix: latestFix,
            headingDeg: heading,
            compassOffsetDeg: calibrator.offsetDeg,
            nextManeuverPoint: nextPoint,
            nextManeuver: state?.pendingManeuver,
            distanceToNextManeuverM: state?.distanceToNextManeuverM,
            offRouteM: state?.offRouteM,
            distanceToDestinationM: state?.distanceToDestinationM
        )
    }
}
