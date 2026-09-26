import Foundation

/// Everything the cue policy needs to know about where the walker is.
/// A value: `RouteFollower.advance` returns a new one per fix and never
/// edits the old, so a state can be kept for comparison (the policy
/// does exactly that).
public struct FollowerState: Sendable, Equatable {
    /// The step being walked. Never decreases, never jumps by more than
    /// one per fix.
    public let currentStepIndex: Int
    /// Along-track distance still to walk in this step, i.e. to the
    /// maneuver at its end. Clamped at 0.
    public let distanceToNextManeuverM: Double
    /// The maneuver at the end of the current step -- the one the
    /// walker is approaching (`steps[currentStepIndex + 1].maneuver`).
    public let pendingManeuver: Maneuver
    /// Set only on the update that crossed a step boundary: the
    /// maneuver the walker has just arrived at.
    public let reachedManeuver: Maneuver?
    /// The step index whose start carries `reachedManeuver`; the
    /// policy uses it to announce each maneuver exactly once.
    public let reachedManeuverIndex: Int?
    /// `heading - segment bearing`, normalised to `(-180, 180]`;
    /// positive means facing right of the path (turn left to correct).
    /// `nil` when there is no heading.
    public let headingErrorDeg: Double?
    /// Distance to the nearest segment of the current step +/- 1.
    public let offRouteM: Double
    public let distanceToDestinationM: Double
    public let speedMps: Double?
    /// Timestamp of the fix this state was built from -- the policy's
    /// "when did we last hear from GPS".
    public let timestampMs: Int64
    public let isArrived: Bool

    public init(
        currentStepIndex: Int,
        distanceToNextManeuverM: Double,
        pendingManeuver: Maneuver,
        reachedManeuver: Maneuver?,
        reachedManeuverIndex: Int?,
        headingErrorDeg: Double?,
        offRouteM: Double,
        distanceToDestinationM: Double,
        speedMps: Double?,
        timestampMs: Int64,
        isArrived: Bool
    ) {
        self.currentStepIndex = currentStepIndex
        self.distanceToNextManeuverM = distanceToNextManeuverM
        self.pendingManeuver = pendingManeuver
        self.reachedManeuver = reachedManeuver
        self.reachedManeuverIndex = reachedManeuverIndex
        self.headingErrorDeg = headingErrorDeg
        self.offRouteM = offRouteM
        self.distanceToDestinationM = distanceToDestinationM
        self.speedMps = speedMps
        self.timestampMs = timestampMs
        self.isArrived = isArrived
    }

    /// The step index whose start carries `pendingManeuver`. Used as the
    /// "have I announced this maneuver yet" key.
    public var pendingManeuverIndex: Int { currentStepIndex + 1 }
}

/// Follows a `Route` with a stream of fixes. Pure: no clock, no
/// storage, no side effects -- `advance` is a function of (state, fix,
/// heading), which is what makes the whole acceptance walk testable in
/// microseconds.
public struct RouteFollower: Sendable {
    /// A fix this close to the end of a step counts as having reached
    /// the maneuver there (brief F1). It equals the policy's
    /// `NOW_DISTANCE_M`, so reaching a step end and saying "Left now"
    /// are the same moment by construction.
    public static let stepAdvanceRadiusM: Double = 8

    public let route: Route
    private let steps: [RouteStep]
    /// Distance from the route's start to the end of each step. Built
    /// from the steps themselves (they tile the route), so a step
    /// boundary and a distance always agree.
    private let stepEndCumulative: [Double]
    private let routeLengthM: Double

    public init(route: Route) {
        self.route = route
        self.steps = route.steps
        var running = 0.0
        self.stepEndCumulative = route.steps.map { step in
            running += RouteGeometry.totalLength(step.points)
            return running
        }
        self.routeLengthM = running
    }

    /// The state before any fix has arrived. The engine always calls
    /// `advance` before showing anything to the walker.
    public var initialState: FollowerState {
        FollowerState(
            currentStepIndex: 0,
            distanceToNextManeuverM: stepEndCumulative.first ?? 0,
            pendingManeuver: maneuver(startingStep: 1),
            reachedManeuver: nil,
            reachedManeuverIndex: nil,
            headingErrorDeg: nil,
            offRouteM: 0,
            distanceToDestinationM: routeLengthM,
            speedMps: nil,
            timestampMs: 0,
            isArrived: steps.isEmpty
        )
    }

    /// Folds one fix (and the latest heading, if any) into a new state.
    public func advance(
        from state: FollowerState, fix: LocationFix, heading: HeadingSample?
    ) -> FollowerState {
        guard !steps.isEmpty else { return initialState }

        let index = min(state.currentStepIndex, steps.count - 1)
        let advanced = index < steps.count - 1
            && hasPassedEnd(ofStep: index, at: fix.latLng, travelled: progress(around: index, to: fix.latLng).travelledM)
        let newIndex = advanced ? index + 1 : index
        let here = progress(around: newIndex, to: fix.latLng, headingDeg: heading?.trueHeadingDeg)
        let destination = route.destination ?? steps[steps.count - 1].end
        let toDestination = Geo.haversineMeters(fix.latLng, destination)

        return FollowerState(
            currentStepIndex: newIndex,
            distanceToNextManeuverM: max(0, stepEndCumulative[newIndex] - here.travelledM),
            pendingManeuver: maneuver(startingStep: newIndex + 1),
            reachedManeuver: advanced ? steps[newIndex].maneuver : nil,
            reachedManeuverIndex: advanced ? newIndex : nil,
            headingErrorDeg: headingError(heading, projection: here.projection),
            offRouteM: here.projection?.offsetM ?? toDestination,
            distanceToDestinationM: toDestination,
            speedMps: fix.speedMps,
            timestampMs: fix.timestampMs,
            isArrived: newIndex == steps.count - 1
                && (here.travelledM >= routeLengthM
                    || toDestination <= RouteFollower.stepAdvanceRadiusM)
        )
    }

    /// Either within the advance radius of the step's end point, or
    /// past it measured along the route (brief F1).
    private func hasPassedEnd(ofStep index: Int, at point: LatLng, travelled: Double) -> Bool {
        if Geo.haversineMeters(point, steps[index].end) <= RouteFollower.stepAdvanceRadiusM {
            return true
        }
        return travelled >= stepEndCumulative[index]
    }

    /// Where the walker is, measured along the route, plus which
    /// segment they are nearest. Both are computed over the current
    /// step and its neighbours only (brief F1), so a route that passes
    /// near itself cannot teleport the walker.
    ///
    /// Measuring the distance to the next maneuver along the *route*
    /// rather than within the current step is what keeps it strictly
    /// decreasing across a corner: in the last few metres before a left
    /// turn the step index has already advanced, and the walker's
    /// projection onto the new leg would otherwise sit motionless at
    /// that leg's start.
    private func progress(
        around index: Int, to point: LatLng, headingDeg: Double? = nil
    ) -> (projection: SegmentProjection?, travelledM: Double) {
        let lower = max(0, index - 1)
        let upper = min(steps.count - 1, index + 1)
        let windowPoints = (lower...upper).flatMap { steps[$0].points }
        let base = lower == 0 ? 0 : stepEndCumulative[lower - 1]
        return (
            RouteGeometry.nearestSegment(to: point, on: windowPoints, headingDeg: headingDeg),
            base + RouteGeometry.progressAlong(windowPoints, from: point)
        )
    }

    private func headingError(
        _ heading: HeadingSample?, projection: SegmentProjection?
    ) -> Double? {
        guard let heading, let projection else { return nil }
        return Geo.normalizedAngleDeg(heading.trueHeadingDeg - projection.bearingDeg)
    }

    /// The maneuver performed at the start of `index`, i.e. at the end
    /// of the step before it. `.unspecified` past the last step.
    private func maneuver(startingStep index: Int) -> Maneuver {
        index < steps.count ? steps[index].maneuver : .unspecified
    }
}
