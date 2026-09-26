import XCTest

@testable import CaneNavigation

/// The runbook acceptance test, offline: "a two-turn walking route
/// plays correct turn cues on the demo route; losing GPS degrades
/// navigation without affecting hazard warnings" (step-12 brief F3/F6).
final class TurnCuePolicyTests: XCTestCase {
    private struct Emitted {
        let timestampMs: Int64
        let cue: TurnCue
    }

    /// Runs follower + policy over a scripted walk, exactly as the
    /// engine does, and returns everything spoken.
    private func play(
        route: Route,
        samples: [WalkSample],
        displaceBearingDeg: Double? = nil,
        displaceMeters: Double = 0,
        headingOffsetDeg: Double = 0,
        speedMps: Double? = nil
    ) -> [Emitted] {
        let follower = RouteFollower(route: route)
        var followerState = follower.initialState
        var policyState = TurnCuePolicy.State()
        var emitted: [Emitted] = []

        for sample in samples {
            let point = displaceBearingDeg.map {
                Geo.destinationPoint(
                    from: sample.fix.latLng, bearingDeg: $0, distanceMeters: displaceMeters
                )
            } ?? sample.fix.latLng
            let fix = LocationFix(
                latLng: point,
                horizontalAccuracyM: sample.fix.horizontalAccuracyM,
                speedMps: speedMps ?? sample.fix.speedMps,
                timestampMs: sample.fix.timestampMs
            )
            let heading = HeadingSample(
                trueHeadingDeg: sample.heading.trueHeadingDeg + headingOffsetDeg,
                accuracyDeg: sample.heading.accuracyDeg,
                timestampMs: sample.heading.timestampMs
            )
            followerState = follower.advance(from: followerState, fix: fix, heading: heading)
            let (nextPolicy, cue) = TurnCuePolicy.evaluate(
                state: policyState, follower: followerState, nowMs: fix.timestampMs
            )
            policyState = nextPolicy
            if let cue { emitted.append(Emitted(timestampMs: fix.timestampMs, cue: cue)) }
        }
        return emitted
    }

    private func twoTurnWalk() throws -> (Route, [WalkSample]) {
        let route = try NavigationFixtures.twoTurnsRoute()
        return (route, RouteWalkSimulator(route: route).samples())
    }

    // MARK: - The acceptance sequence

    func testTheTwoTurnWalkPlaysExactlyTheAcceptanceCueSequence() throws {
        let (route, samples) = try twoTurnWalk()

        let emitted = play(route: route, samples: samples)

        XCTAssertEqual(
            emitted.map { "\($0.cue.kind)-\($0.cue.direction)" },
            ["prepare-left", "now-left", "prepare-right", "now-right", "arrived-arrive"]
        )
        XCTAssertEqual(emitted.map(\.cue.distanceM), [30, nil, 30, nil, nil])
        XCTAssertEqual(
            emitted.map(\.cue.phrase),
            ["Left in 30 metres", "Left now", "Right in 30 metres", "Right now", "Arrived"]
        )
        XCTAssertEqual(
            emitted.map(\.cue.hapticHint), [.leftTurn, .leftTurn, .rightTurn, .rightTurn, HapticHint.none]
        )
    }

    func testTheAcceptanceCuesLandAtThePlannedDistances() throws {
        let (route, samples) = try twoTurnWalk()

        let emitted = play(route: route, samples: samples)

        // 1.4 m/s from the origin: the first prepare at ~51 s, the
        // maneuver itself ~15 s later, arrival at ~172 s.
        XCTAssertEqual(emitted[0].timestampMs, 51_000)
        XCTAssertEqual(emitted[1].timestampMs, 66_000)
        XCTAssertEqual(emitted[2].timestampMs, 108_000)
        XCTAssertEqual(emitted[3].timestampMs, 123_000)
        XCTAssertEqual(emitted[4].timestampMs, 172_000)
    }

    func testEveryCueIsNavigationPriority() throws {
        let (route, samples) = try twoTurnWalk()

        for emitted in play(route: route, samples: samples) {
            XCTAssertEqual(emitted.cue.priority, .navigation)
        }
    }

    func testArrivalIsAnnouncedOnlyOnce() throws {
        let (route, samples) = try twoTurnWalk()

        let arrivals = play(route: route, samples: samples).filter { $0.cue.kind == .arrived }

        XCTAssertEqual(arrivals.count, 1)
    }

    // MARK: - Heading corrections

    func testAHeadingErrorWhileWalkingIsCorrectedOncePerCooldown() throws {
        let (route, samples) = try twoTurnWalk()
        let stretch = Array(samples[5...20])

        let emitted = play(route: route, samples: stretch, headingOffsetDeg: 60)

        let corrections = emitted.filter { $0.cue.kind == .correctHeading }
        XCTAssertEqual(corrections.first?.timestampMs, 5_000)
        // Positive error (facing right of the path) means turn left.
        XCTAssertEqual(corrections.first?.cue.direction, .left)
        XCTAssertEqual(corrections.first?.cue.phrase, "Left to rejoin route")
        let withinCooldown = corrections.filter { $0.timestampMs < 5_000 + 6_000 }
        XCTAssertEqual(withinCooldown.count, 1, "one correction per \(TurnCuePolicy.correctCooldownS) s")
        XCTAssertEqual(corrections.map(\.timestampMs), [5_000, 11_000, 17_000])
    }

    func testAHeadingErrorToTheOtherSideAsksForARightTurn() throws {
        let (route, samples) = try twoTurnWalk()

        let emitted = play(route: route, samples: Array(samples[5...7]), headingOffsetDeg: -60)

        XCTAssertEqual(emitted.first?.cue.kind, .correctHeading)
        XCTAssertEqual(emitted.first?.cue.direction, .right)
    }

    /// Brief F3: standing at a curb, heading is advisory -- never nag.
    func testStandingStillWithASixtyDegreeErrorSaysNothing() throws {
        let (route, samples) = try twoTurnWalk()

        let emitted = play(
            route: route, samples: Array(samples[5...20]), headingOffsetDeg: 60, speedMps: 0.2
        )

        XCTAssertEqual(emitted.count, 0)
    }

    func testAnUnknownSpeedIsTreatedAsStandingStill() throws {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let sample = RouteWalkSimulator(route: route).samples()[10]
        let fix = LocationFix(
            latLng: sample.fix.latLng, horizontalAccuracyM: 5, speedMps: nil, timestampMs: 10_000
        )
        let heading = HeadingSample(trueHeadingDeg: 60, accuracyDeg: 5, timestampMs: 10_000)

        let state = follower.advance(from: follower.initialState, fix: fix, heading: heading)
        let (_, cue) = TurnCuePolicy.evaluate(
            state: TurnCuePolicy.State(), follower: state, nowMs: 10_000
        )

        XCTAssertNil(cue)
    }

    /// The straight reminder confirms a correction worked; it is not a
    /// metronome (see `TurnCuePolicy`'s note on F3 vs F6).
    func testStraightOnConfirmsAfterACorrection() throws {
        let (route, samples) = try twoTurnWalk()
        let follower = RouteFollower(route: route)
        var followerState = follower.initialState
        var policyState = TurnCuePolicy.State()
        var kinds: [CueKind] = []

        for (index, sample) in samples[5...12].enumerated() {
            let skewed = index < 2 ? 60.0 : 0.0
            let heading = HeadingSample(
                trueHeadingDeg: sample.heading.trueHeadingDeg + skewed,
                accuracyDeg: 5,
                timestampMs: sample.fix.timestampMs
            )
            followerState = follower.advance(from: followerState, fix: sample.fix, heading: heading)
            let (next, cue) = TurnCuePolicy.evaluate(
                state: policyState, follower: followerState, nowMs: sample.fix.timestampMs
            )
            policyState = next
            if let cue { kinds.append(cue.kind) }
        }

        XCTAssertEqual(kinds, [.correctHeading, .continueStraight])
    }

    func testNoStraightReminderOnACleanWalk() throws {
        let (route, samples) = try twoTurnWalk()

        let emitted = play(route: route, samples: samples)

        XCTAssertFalse(emitted.contains { $0.cue.kind == .continueStraight })
    }

    // MARK: - Off route

    func testThirtyMetresOffRouteForFiveSecondsAnnouncesOnce() throws {
        let (route, samples) = try twoTurnWalk()

        let emitted = play(
            route: route,
            samples: Array(samples[0...20]),
            displaceBearingDeg: 90,
            displaceMeters: 30
        )

        let offRoute = emitted.filter { $0.cue.kind == .offRoute }
        XCTAssertEqual(offRoute.count, 1)
        XCTAssertEqual(offRoute.first?.timestampMs, 5_000, "five seconds of hold, then speak")
        XCTAssertEqual(offRoute.first?.cue.phrase, "Off route, rerouting")
    }

    func testABriefWobbleOffRouteSaysNothing() throws {
        let (route, samples) = try twoTurnWalk()
        let follower = RouteFollower(route: route)
        var followerState = follower.initialState
        var policyState = TurnCuePolicy.State()
        var cues: [TurnCue] = []

        for (index, sample) in samples[0...20].enumerated() {
            let strayed = (3...6).contains(index)
            let point = strayed
                ? Geo.destinationPoint(from: sample.fix.latLng, bearingDeg: 90, distanceMeters: 30)
                : sample.fix.latLng
            let fix = LocationFix(
                latLng: point, horizontalAccuracyM: 5, speedMps: 1.4, timestampMs: sample.fix.timestampMs
            )
            followerState = follower.advance(from: followerState, fix: fix, heading: sample.heading)
            let (next, cue) = TurnCuePolicy.evaluate(
                state: policyState, follower: followerState, nowMs: fix.timestampMs
            )
            policyState = next
            if let cue { cues.append(cue) }
        }

        XCTAssertEqual(cues.count, 0, "four seconds off route is GPS noise, not a wrong turn")
    }

    // MARK: - Losing GPS

    func testNoCueOfAnyKindWhileGPSIsLostThenGPSBack() throws {
        let (route, samples) = try twoTurnWalk()
        let follower = RouteFollower(route: route)
        // Stop the fixes 30 m before the first turn, where a prepare
        // cue would otherwise be due.
        var followerState = follower.initialState
        var policyState = TurnCuePolicy.State()
        for sample in samples[0...49] {
            followerState = follower.advance(
                from: followerState, fix: sample.fix, heading: sample.heading
            )
            (policyState, _) = TurnCuePolicy.evaluate(
                state: policyState, follower: followerState, nowMs: sample.fix.timestampMs
            )
        }

        var kinds: [CueKind?] = []
        for second in 1...15 {
            let (next, cue) = TurnCuePolicy.evaluate(
                state: policyState, follower: followerState, nowMs: 49_000 + Int64(second) * 1_000
            )
            policyState = next
            kinds.append(cue?.kind)
        }

        XCTAssertEqual(kinds.compactMap { $0 }, [.gpsLost], "one gpsLost, and nothing else at all")
        XCTAssertEqual(kinds.firstIndex(of: .gpsLost), 9, "ten seconds without a fix")

        // Fixes resume: GPS back first, and the missed prepare after.
        let resumed = samples[52]
        let restored = LocationFix(
            latLng: resumed.fix.latLng,
            horizontalAccuracyM: 5,
            speedMps: 1.4,
            timestampMs: 64_000
        )
        followerState = follower.advance(
            from: followerState, fix: restored, heading: resumed.heading
        )
        let (afterRestore, restoredCue) = TurnCuePolicy.evaluate(
            state: policyState, follower: followerState, nowMs: 64_000
        )
        let (_, followUp) = TurnCuePolicy.evaluate(
            state: afterRestore, follower: followerState, nowMs: 64_500
        )

        XCTAssertEqual(restoredCue?.kind, .gpsRestored)
        XCTAssertEqual(restoredCue?.phrase, "GPS back")
        XCTAssertEqual(followUp?.kind, .prepare)
    }

    func testGPSLostIsAnnouncedOnlyOnce() throws {
        let (route, samples) = try twoTurnWalk()
        let follower = RouteFollower(route: route)
        let state = follower.advance(
            from: follower.initialState, fix: samples[0].fix, heading: samples[0].heading
        )

        var policyState = TurnCuePolicy.State()
        var kinds: [CueKind] = []
        for second in 10...30 {
            let (next, cue) = TurnCuePolicy.evaluate(
                state: policyState, follower: state, nowMs: Int64(second) * 1_000
            )
            policyState = next
            if let cue { kinds.append(cue.kind) }
        }

        XCTAssertEqual(kinds, [.gpsLost])
    }

    // MARK: - Constants

    func testTheConstantsAreTheOnesTheBriefFixes() {
        XCTAssertEqual(TurnCuePolicy.prepareDistanceM, 30)
        XCTAssertEqual(TurnCuePolicy.nowDistanceM, 8)
        XCTAssertEqual(TurnCuePolicy.arriveRadiusM, 10)
        XCTAssertEqual(TurnCuePolicy.offRouteThresholdM, 25)
        XCTAssertEqual(TurnCuePolicy.offRouteHoldS, 5)
        XCTAssertEqual(TurnCuePolicy.headingCorrectDeg, 45)
        XCTAssertEqual(TurnCuePolicy.headingOkDeg, 20)
        XCTAssertEqual(TurnCuePolicy.correctCooldownS, 6)
        XCTAssertEqual(TurnCuePolicy.straightReminderS, 20)
        XCTAssertEqual(TurnCuePolicy.minWalkSpeedMps, 0.5)
        XCTAssertEqual(TurnCuePolicy.gpsLostAfterS, 10)
        XCTAssertEqual(RouteFollower.stepAdvanceRadiusM, 8)
    }
}
