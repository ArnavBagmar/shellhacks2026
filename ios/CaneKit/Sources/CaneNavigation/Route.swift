import Foundation

/// A walking route, decoded from the Routes API and stripped to what the
/// follower needs. Value type end to end: a reroute produces a *new*
/// `Route` rather than editing the one the follower is holding.
public struct Route: Sendable, Equatable {
    public let distanceMeters: Int
    public let durationS: Double
    /// The whole route's decoded polyline, first point = origin, last =
    /// destination.
    public let points: [LatLng]
    public let legs: [RouteLeg]

    public init(distanceMeters: Int, durationS: Double, points: [LatLng], legs: [RouteLeg]) {
        self.distanceMeters = distanceMeters
        self.durationS = durationS
        self.points = points
        self.legs = legs
    }

    /// Every leg's steps in walking order. A walking route has one leg
    /// unless intermediate waypoints were requested (we never request
    /// them), but the follower indexes steps, not legs.
    public var steps: [RouteStep] { legs.flatMap(\.steps) }

    /// The point the walker is heading for; `nil` only for an empty
    /// polyline, which `RoutesClient` rejects.
    public var destination: LatLng? { points.last }
}

public struct RouteLeg: Sendable, Equatable {
    public let steps: [RouteStep]

    public init(steps: [RouteStep]) {
        self.steps = steps
    }
}

/// One instruction-sized piece of the route. `maneuver` is the maneuver
/// performed at this step's **start** (that is the Routes API's
/// convention), so the maneuver a walker is approaching while on step
/// `i` is `steps[i + 1].maneuver`.
public struct RouteStep: Sendable, Equatable {
    public let distanceMeters: Int
    public let staticDurationS: Double
    public let points: [LatLng]
    public let start: LatLng
    public let end: LatLng
    public let maneuver: Maneuver
    public let instruction: String

    public init(
        distanceMeters: Int,
        staticDurationS: Double,
        points: [LatLng],
        start: LatLng,
        end: LatLng,
        maneuver: Maneuver,
        instruction: String
    ) {
        self.distanceMeters = distanceMeters
        self.staticDurationS = staticDurationS
        self.points = points
        self.start = start
        self.end = end
        self.maneuver = maneuver
        self.instruction = instruction
    }
}
