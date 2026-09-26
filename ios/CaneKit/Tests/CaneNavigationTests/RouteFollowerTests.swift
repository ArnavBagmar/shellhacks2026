import XCTest

@testable import CaneNavigation

/// The follower walked along the recorded two-turn route at 1.4 m/s,
/// 1 Hz -- the acceptance walk from the runbook. Everything the policy
/// later reads (step index, distance to the next maneuver, heading
/// error, off-route distance) is pinned here (step-12 brief F6).
final class RouteFollowerTests: XCTestCase {
    private func walk() throws -> (RouteFollower, [WalkSample], [FollowerState]) {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let samples = RouteWalkSimulator(route: route).samples()

        var state = follower.initialState
        var states: [FollowerState] = []
        for sample in samples {
            state = follower.advance(from: state, fix: sample.fix, heading: sample.heading)
            states.append(state)
        }
        return (follower, samples, states)
    }

    // MARK: - Step advance

    func testStepIndexNeverSkipsAndNeverRegresses() throws {
        let (_, _, states) = try walk()
        let indices = states.map(\.currentStepIndex)

        XCTAssertEqual(indices.first, 0)
        XCTAssertEqual(indices.last, 3, "the walk ends on the arrival step")
        for (previous, next) in zip(indices, indices.dropFirst()) {
            XCTAssertTrue(
                next == previous || next == previous + 1,
                "index jumped from \(previous) to \(next)"
            )
        }
    }

    func testEachAdvanceHappensAtTheStepEndAndNotBefore() throws {
        let (follower, samples, states) = try walk()
        var transitions = 0

        for (index, pair) in zip(states, states.dropFirst()).enumerated()
        where pair.1.currentStepIndex > pair.0.currentStepIndex {
            transitions += 1
            let stepEnd = follower.route.steps[pair.0.currentStepIndex].end
            let atAdvance = Geo.haversineMeters(samples[index + 1].fix.latLng, stepEnd)
            let before = Geo.haversineMeters(samples[index].fix.latLng, stepEnd)
            XCTAssertLessThanOrEqual(atAdvance, RouteFollower.stepAdvanceRadiusM + 0.01)
            XCTAssertGreaterThan(before, RouteFollower.stepAdvanceRadiusM)
        }
        XCTAssertEqual(transitions, 3, "three step ends on a four-step route")
    }

    func testTheManeuverReachedAtEachStepEndIsTheOneTheWalkerMustPerform() throws {
        let (_, _, states) = try walk()

        let reached = states.compactMap(\.reachedManeuver)

        XCTAssertEqual(reached, [.turnLeft, .turnRight, .unspecified])
    }

    func testReachedManeuverIsOnlySetOnTheUpdateThatCrossesTheBoundary() throws {
        let (_, _, states) = try walk()

        XCTAssertEqual(states.filter { $0.reachedManeuver != nil }.count, 3)
    }

    func testPendingManeuverIsTheOneAtTheEndOfTheCurrentStep() throws {
        let (_, _, states) = try walk()

        XCTAssertEqual(states.first?.pendingManeuver, .turnLeft)
        XCTAssertEqual(states.first(where: { $0.currentStepIndex == 1 })?.pendingManeuver, .turnRight)
        XCTAssertEqual(
            states.first(where: { $0.currentStepIndex == 2 })?.pendingManeuver, .unspecified
        )
    }

    func testDistanceToNextManeuverStrictlyDecreasesWithinEachWalkedStep() throws {
        let (_, _, states) = try walk()

        for (previous, next) in zip(states, states.dropFirst())
        where next.currentStepIndex == previous.currentStepIndex && next.currentStepIndex < 3 {
            XCTAssertLessThan(
                next.distanceToNextManeuverM,
                previous.distanceToNextManeuverM,
                "step \(next.currentStepIndex) stalled at \(next.distanceToNextManeuverM) m"
            )
        }
    }

    func testDistanceToNextManeuverMatchesTheHandMeasuredLegs() throws {
        let (_, _, states) = try walk()

        // The first fix is at the origin, so the first maneuver is a
        // whole leg away; one second later, 1.4 m less.
        XCTAssertEqual(states[0].distanceToNextManeuverM, NavigationFixtures.firstLegM, accuracy: 0.05)
        XCTAssertEqual(
            states[1].distanceToNextManeuverM, NavigationFixtures.firstLegM - 1.4, accuracy: 0.05
        )
    }

    func testArrivalIsReportedOnTheFinalStep() throws {
        let (_, _, states) = try walk()

        XCTAssertFalse(states[0].isArrived)
        XCTAssertTrue(states.last?.isArrived == true)
        XCTAssertEqual(states.last?.distanceToDestinationM ?? .infinity, 0, accuracy: 0.5)
    }

    /// `distanceToDestinationM` is the straight line to the
    /// destination, not the remaining path -- that is what "within 10 m
    /// of the destination" means in the arrive rule (brief F3). On this
    /// dog-leg route the two differ by 62 m at the start.
    func testDistanceToDestinationIsTheStraightLineAndShrinksOverTheWalk() throws {
        let (_, _, states) = try walk()

        XCTAssertEqual(
            states[0].distanceToDestinationM,
            Geo.haversineMeters(NavigationFixtures.origin, NavigationFixtures.destination),
            accuracy: 0.01
        )
        XCTAssertLessThan(states[0].distanceToDestinationM, NavigationFixtures.totalM)
        for (previous, next) in zip(states, states.dropFirst()) {
            XCTAssertLessThanOrEqual(next.distanceToDestinationM, previous.distanceToDestinationM + 0.01)
        }
    }

    // MARK: - Heading

    func testHeadingAlignedWithTheSegmentIsAlmostZeroError() throws {
        let (_, _, states) = try walk()

        for state in states {
            let error = try XCTUnwrap(state.headingErrorDeg)
            XCTAssertLessThan(abs(error), 1.0, "heading error \(error) on step \(state.currentStepIndex)")
        }
    }

    /// Found on the live FIU route: at a vertex both legs are equidistant,
    /// and picking the earlier one made a completed sharp left read as a
    /// large heading error. Within a few metres of a vertex the reference
    /// segment is the one whose bearing agrees with the walker.
    func testAtACornerTheHeadingErrorIsMeasuredAgainstTheLegTheWalkerFaces() throws {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let corner = NavigationFixtures.firstCorner
        let leg1 = Geo.initialBearingDeg(from: NavigationFixtures.origin, to: corner)
        let leg2 = Geo.initialBearingDeg(from: corner, to: NavigationFixtures.secondCorner)
        func errorFacing(_ bearing: Double, at point: LatLng) throws -> Double {
            let fix = LocationFix(
                latLng: point, horizontalAccuracyM: 5, speedMps: 1.4, timestampMs: 1_000
            )
            let heading = HeadingSample(trueHeadingDeg: bearing, accuracyDeg: 5, timestampMs: 1_000)
            let state = follower.advance(from: follower.initialState, fix: fix, heading: heading)
            return try XCTUnwrap(state.headingErrorDeg)
        }

        XCTAssertEqual(try errorFacing(leg2, at: corner), 0, accuracy: 1, "turned: no error")
        XCTAssertEqual(try errorFacing(leg1, at: corner), 0, accuracy: 1, "not yet turned: no nag")
        let onePast = Geo.destinationPoint(from: corner, bearingDeg: leg2, distanceMeters: 1)
        XCTAssertEqual(try errorFacing(leg1, at: onePast), 0, accuracy: 1, "GPS noise past the corner")
        let fivePast = Geo.destinationPoint(from: corner, bearingDeg: leg2, distanceMeters: 5)
        XCTAssertEqual(abs(try errorFacing(leg1, at: fivePast)), 90, accuracy: 1, "clearly past: correct")
    }

    func testMissingHeadingLeavesTheErrorNil() throws {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let sample = RouteWalkSimulator(route: route).samples()[10]

        let state = follower.advance(from: follower.initialState, fix: sample.fix, heading: nil)

        XCTAssertNil(state.headingErrorDeg)
    }

    func testHeadingErrorIsSignedAgainstTheSegmentBearing() throws {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let sample = RouteWalkSimulator(route: route).samples()[10]
        // The first leg runs due north; face 60 degrees east of it.
        let skewed = HeadingSample(trueHeadingDeg: 60, accuracyDeg: 5, timestampMs: sample.fix.timestampMs)

        let state = follower.advance(from: follower.initialState, fix: sample.fix, heading: skewed)

        XCTAssertEqual(try XCTUnwrap(state.headingErrorDeg), 60, accuracy: 0.5)
        XCTAssertEqual(TurnDirection.correction(forHeadingErrorDeg: 60), .left)
    }

    func testHeadingSampleNormalisesIntoZeroToThreeSixty() {
        XCTAssertEqual(
            HeadingSample(trueHeadingDeg: -90, accuracyDeg: 5, timestampMs: 0).trueHeadingDeg, 270
        )
        XCTAssertEqual(
            HeadingSample(trueHeadingDeg: 370, accuracyDeg: 5, timestampMs: 0).trueHeadingDeg, 10
        )
    }

    // MARK: - Off route

    func testAPointThirtyMetresOffThePathReportsThirtyMetres() throws {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let sample = RouteWalkSimulator(route: route).samples()[30]
        let strayed = LocationFix(
            latLng: Geo.destinationPoint(from: sample.fix.latLng, bearingDeg: 90, distanceMeters: 30),
            horizontalAccuracyM: 5,
            speedMps: 1.4,
            timestampMs: sample.fix.timestampMs
        )

        let state = follower.advance(from: follower.initialState, fix: strayed, heading: nil)

        XCTAssertEqual(state.offRouteM, 30, accuracy: 0.2)
    }

    func testOnRouteFixesReportEssentiallyZeroOffRoute() throws {
        let (_, _, states) = try walk()

        for state in states {
            XCTAssertLessThan(state.offRouteM, 0.5)
        }
    }

    // MARK: - Robustness

    func testAGPSJumpToTheDestinationAdvancesOnlyOneStep() throws {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let jump = LocationFix(
            latLng: NavigationFixtures.destination,
            horizontalAccuracyM: 40,
            speedMps: nil,
            timestampMs: 1_000
        )

        let state = follower.advance(from: follower.initialState, fix: jump, heading: nil)

        XCTAssertEqual(state.currentStepIndex, 1)
    }

    func testAFixBackAtTheOriginDoesNotRegressTheStepIndex() throws {
        let (follower, samples, states) = try walk()
        let onSecondStep = try XCTUnwrap(states.first(where: { $0.currentStepIndex == 2 }))
        let backAtStart = samples[0].fix

        let state = follower.advance(from: onSecondStep, fix: backAtStart, heading: nil)

        XCTAssertEqual(state.currentStepIndex, 2)
        XCTAssertNil(state.reachedManeuver)
    }

    func testTheWalkerStandingStillKeepsTheSameStep() throws {
        let route = try NavigationFixtures.twoTurnsRoute()
        let follower = RouteFollower(route: route)
        let sample = RouteWalkSimulator(route: route).samples()[20]
        let stationary = LocationFix(
            latLng: sample.fix.latLng, horizontalAccuracyM: 5, speedMps: 0.1, timestampMs: 99_000
        )

        var state = follower.advance(from: follower.initialState, fix: sample.fix, heading: nil)
        state = follower.advance(from: state, fix: stationary, heading: nil)

        XCTAssertEqual(state.currentStepIndex, 0)
        XCTAssertEqual(state.speedMps, 0.1)
        XCTAssertEqual(state.timestampMs, 99_000)
    }

    // MARK: - The simulator itself

    func testTheSimulatorWalksTheWholeRouteAtTheRequestedSpeed() throws {
        let route = try NavigationFixtures.twoTurnsRoute()

        let samples = RouteWalkSimulator(route: route).samples()

        XCTAssertEqual(samples.first?.fix.latLng, NavigationFixtures.origin)
        XCTAssertEqual(
            Geo.haversineMeters(try XCTUnwrap(samples.last?.fix.latLng), NavigationFixtures.destination),
            0,
            accuracy: 0.01
        )
        XCTAssertEqual(samples[1].fix.timestampMs - samples[0].fix.timestampMs, 1_000)
        XCTAssertEqual(
            Geo.haversineMeters(samples[0].fix.latLng, samples[1].fix.latLng), 1.4, accuracy: 0.01
        )
        XCTAssertEqual(samples[0].fix.speedMps, 1.4)
        XCTAssertEqual(samples[0].heading.trueHeadingDeg, 0, accuracy: 0.01)
    }
}
