import Foundation

/// Turns follower state into speech, once per update (~1 Hz). Pure:
/// `(previous state, FollowerState, now) -> (new state, TurnCue?)`, so
/// a three-minute walk is a few hundred microseconds of test.
///
/// At most one cue per evaluation, in this priority order:
/// 1. GPS lost / restored -- while lost, **nothing else is spoken**,
///    which is the runbook's "losing GPS degrades navigation";
/// 2. arrived (and then silence: the engine stops);
/// 3. off route (and then silence until the reroute lands);
/// 4. `now`, then `prepare` for the maneuver ahead;
/// 5. heading correction;
/// 6. the straight-on confirmation.
public enum TurnCuePolicy {
    // Brief F3 -- exact values, named once.
    public static let prepareDistanceM: Double = 30
    public static let nowDistanceM: Double = 8
    public static let arriveRadiusM: Double = 10
    public static let offRouteThresholdM: Double = 25
    public static let offRouteHoldS: Double = 5
    public static let headingCorrectDeg: Double = 45
    public static let headingOkDeg: Double = 20
    public static let correctCooldownS: Double = 6
    public static let straightReminderS: Double = 20
    public static let minWalkSpeedMps: Double = 0.5
    public static let gpsLostAfterS: Double = 10
    /// No `.correctHeading` this soon after a `.now`: the heading swings
    /// through the vertex while the walker turns (seen on the live FIU route).
    public static let postManeuverGraceS: Double = 6

    public static func evaluate(
        state: State, follower: FollowerState, nowMs: Int64
    ) -> (state: State, cue: TurnCue?) {
        var next = state

        if let gps = gpsDecision(&next, follower: follower, nowMs: nowMs) {
            return (next, gps.cue)
        }
        if next.gpsLostAnnounced { return (next, nil) }
        if let arrival = arrivalCue(&next, follower: follower) { return (next, arrival) }
        if next.arrivedAnnounced { return (next, nil) }
        if let offRoute = offRouteDecision(&next, follower: follower, nowMs: nowMs) {
            return (next, offRoute.cue)
        }
        if let maneuver = maneuverCue(&next, follower: follower, nowMs: nowMs) {
            return (next, maneuver)
        }
        if let correction = headingCue(&next, follower: follower, nowMs: nowMs) {
            return (next, correction)
        }
        // Bound first: in `return (next, f(&next))` Swift copies
        // `next` before the call mutates it, quietly dropping the
        // state change.
        let straightOn = straightCue(&next, follower: follower, nowMs: nowMs)
        return (next, straightOn)
    }

    /// A branch's verdict. Distinguishes "say this", "say nothing and
    /// stop here" (the GPS-lost and off-route silences) and -- by
    /// returning `nil` instead of a `Decision` -- "this branch has no
    /// opinion, carry on".
    private enum Decision {
        case speak(TurnCue)
        case stayQuiet

        var cue: TurnCue? {
            if case .speak(let cue) = self { return cue }
            return nil
        }
    }

    // MARK: - GPS

    private static func gpsDecision(
        _ next: inout State, follower: FollowerState, nowMs: Int64
    ) -> Decision? {
        let silentForS = Double(nowMs - follower.timestampMs) / 1_000
        if silentForS >= gpsLostAfterS {
            guard !next.gpsLostAnnounced else { return .stayQuiet }
            next.gpsLostAnnounced = true
            return .speak(TurnCue(kind: .gpsLost, direction: .straight, distanceM: nil))
        }
        guard next.gpsLostAnnounced else { return nil }
        next.gpsLostAnnounced = false
        return .speak(TurnCue(kind: .gpsRestored, direction: .straight, distanceM: nil))
    }

    // MARK: - Arrival

    private static func arrivalCue(_ next: inout State, follower: FollowerState) -> TurnCue? {
        guard !next.arrivedAnnounced,
            follower.isArrived || follower.distanceToDestinationM <= arriveRadiusM
        else { return nil }
        next.arrivedAnnounced = true
        return TurnCue(kind: .arrived, direction: .arrive, distanceM: nil)
    }

    // MARK: - Off route

    private static func offRouteDecision(
        _ next: inout State, follower: FollowerState, nowMs: Int64
    ) -> Decision? {
        // Only the distance the GPS is *sure* of counts: a 45 m error circle
        // near buildings must not read as walking off the route
        // (2026-09-27: "Off route, rerouting" over and over at FIU).
        guard follower.offRouteM - offRouteUncertaintyM(follower.accuracyM) > offRouteThresholdM else {
            next.offRouteSinceMs = nil
            next.offRouteAnnounced = false
            return nil
        }
        // Already told them; stay quiet until the reroute resets state.
        if next.offRouteAnnounced { return .stayQuiet }

        let since = next.offRouteSinceMs ?? nowMs
        next.offRouteSinceMs = since
        guard Double(nowMs - since) / 1_000 >= offRouteHoldS else {
            // Still inside the hold: this may be GPS noise, so normal
            // cues continue.
            return nil
        }
        next.offRouteAnnounced = true
        return .speak(TurnCue(kind: .offRoute, direction: .straight, distanceM: nil))
    }

    /// How much of a fix's error radius is forgiven: nothing for a good
    /// fix (<= 10 m), at most 40 m for a poor one.
    static func offRouteUncertaintyM(_ accuracyM: Double) -> Double {
        min(max(accuracyM - 10, 0), 40)
    }

    // MARK: - Maneuvers

    /// One `now` and one `prepare` per maneuver, keyed by the step
    /// whose start carries it, so a wobbling fix cannot repeat either.
    private static func maneuverCue(
        _ next: inout State, follower: FollowerState, nowMs: Int64
    ) -> TurnCue? {
        if let reached = follower.reachedManeuver, let boundary = follower.reachedManeuverIndex,
            reached.isTurn, next.announcedBoundary != boundary
        {
            next.announcedBoundary = boundary
            next.preparedBoundary = boundary
            next.lastNowMs = nowMs
            return TurnCue(kind: .now, direction: reached.turnDirection, distanceM: nil)
        }

        let boundary = follower.pendingManeuverIndex
        guard follower.pendingManeuver.isTurn else { return nil }
        let direction = follower.pendingManeuver.turnDirection

        if follower.distanceToNextManeuverM <= nowDistanceM, next.announcedBoundary != boundary {
            next.announcedBoundary = boundary
            next.preparedBoundary = boundary
            next.lastNowMs = nowMs
            return TurnCue(kind: .now, direction: direction, distanceM: nil)
        }
        if follower.distanceToNextManeuverM <= prepareDistanceM, next.preparedBoundary != boundary {
            next.preparedBoundary = boundary
            return TurnCue(
                kind: .prepare,
                direction: direction,
                distanceM: Phrasing.roundedDistanceM(follower.distanceToNextManeuverM)
            )
        }
        return nil
    }

    // MARK: - Heading

    private static func headingCue(
        _ next: inout State, follower: FollowerState, nowMs: Int64
    ) -> TurnCue? {
        guard let error = follower.headingErrorDeg, abs(error) > headingCorrectDeg,
            let speed = follower.speedMps, speed >= minWalkSpeedMps,
            elapsed(since: next.lastCorrectHeadingMs, to: nowMs, atLeast: correctCooldownS),
            elapsed(since: next.lastNowMs, to: nowMs, atLeast: postManeuverGraceS)
        else { return nil }

        next.lastCorrectHeadingMs = nowMs
        next.straightReminderArmed = true
        return TurnCue(
            kind: .correctHeading,
            direction: TurnDirection.correction(forHeadingErrorDeg: error),
            distanceM: nil
        )
    }

    /// "Straight on" confirms that a correction worked.
    ///
    /// Brief F3 rate-limits this to one per `straightReminderS`, and
    /// brief F6 requires the clean two-turn walk to produce *exactly*
    /// five cues -- so it cannot also be a free-running metronome.
    /// Reading both together: the reminder is armed by a
    /// `.correctHeading`, fires once the walker has lined up again with
    /// no maneuver within `prepareDistanceM`, and then disarms.
    private static func straightCue(
        _ next: inout State, follower: FollowerState, nowMs: Int64
    ) -> TurnCue? {
        guard next.straightReminderArmed, let error = follower.headingErrorDeg,
            abs(error) < headingOkDeg, follower.distanceToNextManeuverM > prepareDistanceM,
            elapsed(since: next.lastContinueStraightMs, to: nowMs, atLeast: straightReminderS)
        else { return nil }

        next.lastContinueStraightMs = nowMs
        next.straightReminderArmed = false
        return TurnCue(kind: .continueStraight, direction: .straight, distanceM: nil)
    }

    private static func elapsed(since last: Int64?, to nowMs: Int64, atLeast seconds: Double) -> Bool {
        guard let last else { return true }
        return Double(nowMs - last) / 1_000 >= seconds
    }
}
