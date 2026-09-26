import CaneTransport
import XCTest

@testable import CaneFeedback

/// Step 24's maths, with no actor, no clock and no renderer in sight
/// (`docs/shepherd-steering-plan.md` step 24). Shepherd's stage 8 is a leaky
/// integrator and stage 9 a decay to neutral; both are pure value types here,
/// so the acceptance criteria are arithmetic, not timing.
final class LeakyIntegratorTests: XCTestCase {

    /// Acceptance 1, first half: `INTEGRATOR_K = 0.35` per 100 ms tick puts a
    /// step change at 90% of target on the sixth tick. Pinned as a number so
    /// a change to `k` is a deliberate act with a failing test attached.
    func testAStepChangeReachesNinetyPercentOnTheSixthTick() {
        var integrator = LeakyIntegrator()
        var reachedOnTick: Int?
        for tick in 1...20 {
            integrator = integrator.advanced(toward: 1.0, elapsedMs: SteerPolicy.integratorTickMs)
            if reachedOnTick == nil, integrator.value >= 0.9 { reachedOnTick = tick }
        }
        XCTAssertEqual(reachedOnTick, 6)
        // The continuous-time figure the brief quotes, from the same law.
        XCTAssertEqual(LeakyIntegrator.msToReach(fraction: 0.9), 535, accuracy: 1)
    }

    /// Acceptance 1, second half: a leaky integrator cannot overshoot, so
    /// assert it -- a future change to the filter (a PID term, a predictive
    /// lead) cannot silently introduce ringing.
    func testItNeverOvershootsOrReversesForAnyStepOrTickLength() {
        for target in [-1.0, -0.42, 0.0, 0.42, 1.0] {
            for elapsed in [1, 33, 100, 333, 5_000] as [Int64] {
                var integrator = LeakyIntegrator(value: target < 0 ? 0.7 : -0.7)
                var previous = integrator.value
                for _ in 0..<64 {
                    integrator = integrator.advanced(toward: target, elapsedMs: elapsed)
                    let label = "target \(target) elapsed \(elapsed)"
                    // Never past the target, from either side.
                    if target >= previous {
                        XCTAssertLessThanOrEqual(integrator.value, target + 1e-12, label)
                    } else {
                        XCTAssertGreaterThanOrEqual(integrator.value, target - 1e-12, label)
                    }
                    // Monotone: it approaches and never backs away.
                    XCTAssertLessThanOrEqual(
                        abs(target - integrator.value), abs(target - previous) + 1e-12, label
                    )
                    previous = integrator.value
                }
            }
        }
    }

    /// The tick jitters (`Task.sleep` is a floor, not a promise), so the
    /// response has to depend on elapsed time and not on how many times it
    /// was sampled -- otherwise a stuttering main thread retunes the filter.
    func testTheResponseDependsOnElapsedTimeNotOnTickCount() {
        let once = LeakyIntegrator().advanced(toward: 1.0, elapsedMs: 100)
        var twice = LeakyIntegrator()
        twice = twice.advanced(toward: 1.0, elapsedMs: 50)
        twice = twice.advanced(toward: 1.0, elapsedMs: 50)
        XCTAssertEqual(once.value, twice.value, accuracy: 1e-12)
        XCTAssertEqual(once.value, SteerPolicy.integratorK, accuracy: 1e-12)
    }

    func testZeroElapsedTimeChangesNothing() {
        let integrator = LeakyIntegrator(value: 0.5).advanced(toward: 1.0, elapsedMs: 0)
        XCTAssertEqual(integrator.value, 0.5)
        XCTAssertEqual(LeakyIntegrator(value: 0.5).decayed(elapsedMs: 0).value, 0.5)
    }

    /// A clock that goes backwards (NTP step, a test setting `now` down) must
    /// not run the filter in reverse.
    func testNegativeElapsedTimeIsTreatedAsNoTime() {
        XCTAssertEqual(LeakyIntegrator(value: 0.5).advanced(toward: 1.0, elapsedMs: -400).value, 0.5)
        XCTAssertEqual(LeakyIntegrator(value: 0.5).decayed(elapsedMs: -400).value, 0.5)
    }

    /// Acceptance 2's shape, on the value type: full scale to neutral in
    /// exactly `FAILSAFE_DECAY_MS`, monotonically, from either sign, and the
    /// rate does not depend on how the interval was chopped up.
    func testDecayReachesNeutralInFiveHundredMillisecondsAndStops() {
        for start in [1.0, -1.0, 0.4] {
            var integrator = LeakyIntegrator(value: start)
            var previous = abs(integrator.value)
            for _ in 0..<5 {
                integrator = integrator.decayed(elapsedMs: 100)
                XCTAssertLessThan(abs(integrator.value), previous + 1e-12, "start \(start)")
                XCTAssertEqual(integrator.value.sign, start.sign, "decay must not cross zero")
                previous = abs(integrator.value)
            }
            XCTAssertEqual(integrator.value, 0, accuracy: 1e-12, "start \(start)")
            // Already neutral: stays neutral, never overshoots to the far side.
            XCTAssertEqual(integrator.decayed(elapsedMs: 10_000).value, 0)
        }
    }

    func testDecayFromFullScaleTakesExactlyTheFailsafeWindow() {
        let halfway = LeakyIntegrator(value: 1.0).decayed(elapsedMs: SteerPolicy.failsafeDecayMs / 2)
        XCTAssertEqual(halfway.value, 0.5, accuracy: 1e-12)
        let done = LeakyIntegrator(value: 1.0).decayed(elapsedMs: SteerPolicy.failsafeDecayMs)
        XCTAssertEqual(done.value, 0, accuracy: 1e-12)
    }
}

final class SteerBlendTests: XCTestCase {

    /// Acceptance 3 (Shepherd stage 6): the navigation bias is scaled by
    /// `(1 - proximityFactor)`, so obstacles always win.
    func testNavigationContributesNothingAtFullProximityAndFullyAtZero() {
        XCTAssertEqual(
            SteerBlend.lateralTarget(steer: 0.2, navigationBias: -0.8, proximityFactor: 1.0), 0.2,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            SteerBlend.lateralTarget(steer: 0.0, navigationBias: -0.8, proximityFactor: 0.0), -0.8,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            SteerBlend.lateralTarget(steer: 0.2, navigationBias: -0.8, proximityFactor: 0.5), -0.2,
            accuracy: 1e-12
        )
    }

    /// Continuous in between, and monotone in `proximityFactor`: no step, no
    /// band, nothing for the wearer to feel as a lurch.
    func testTheBlendIsContinuousAndMonotoneInProximity() {
        var previous = SteerBlend.lateralTarget(steer: 0.1, navigationBias: 0.9, proximityFactor: 0)
        for step in 1...100 {
            let factor = Double(step) / 100
            let value = SteerBlend.lateralTarget(steer: 0.1, navigationBias: 0.9, proximityFactor: factor)
            XCTAssertLessThan(value, previous + 1e-12, "factor \(factor)")
            XCTAssertLessThan(abs(value - previous), 0.02, "a 1% step in proximity moved the mix \(value - previous)")
            previous = value
        }
        XCTAssertEqual(previous, 0.1, accuracy: 1e-12)
    }

    /// The sum is clamped to the contract's own steering range: the wearer
    /// cannot be told to lean harder than "all the way".
    func testTheSumIsClampedToTheSteeringRange() {
        XCTAssertEqual(
            SteerBlend.lateralTarget(steer: 0.9, navigationBias: 1.0, proximityFactor: 0.0),
            SteerLimits.steerMax
        )
        XCTAssertEqual(
            SteerBlend.lateralTarget(steer: -0.9, navigationBias: -1.0, proximityFactor: 0.0),
            SteerLimits.steerMin
        )
    }

    /// A bias older than its hold window is not a bias: a turn cue from two
    /// blocks ago must not still be pulling on the mix.
    func testAnExpiredNavigationBiasIsWorthZero() {
        let bias = NavigationBias(lateral: 1.0, expiresAtMs: 1_000)
        XCTAssertEqual(bias.lateral(now: 999), 1.0)
        XCTAssertEqual(bias.lateral(now: 1_000), 1.0, "at its expiry it is still true")
        XCTAssertEqual(bias.lateral(now: 1_001), 0.0)
    }
}

final class SteerPolicyTests: XCTestCase {

    /// Acceptance 5 / plan risk 3: the steering cue is the third audio and
    /// tactile channel, so below the quiet threshold it does not exist.
    func testNothingIsAudibleBelowTheQuietThreshold() {
        XCTAssertFalse(SteerPolicy.isAudible(proximity: 0.0, wasAudible: false))
        XCTAssertFalse(SteerPolicy.isAudible(proximity: SteerPolicy.quietProximity - 0.01, wasAudible: false))
        XCTAssertTrue(SteerPolicy.isAudible(proximity: SteerPolicy.quietProximity, wasAudible: false))
        XCTAssertTrue(SteerPolicy.isAudible(proximity: 1.0, wasAudible: false))
    }

    /// Hysteresis, the same reason `Hysteresis` exists for urgency: a
    /// proximity sitting on the threshold must not chatter the cue on and off.
    func testAnAudibleCueKeepsPlayingDownToTheReleaseThreshold() {
        XCTAssertTrue(SteerPolicy.isAudible(proximity: SteerPolicy.quietProximityRelease, wasAudible: true))
        XCTAssertFalse(SteerPolicy.isAudible(proximity: SteerPolicy.quietProximityRelease - 0.01, wasAudible: true))
        XCTAssertLessThan(SteerPolicy.quietProximityRelease, SteerPolicy.quietProximity)
    }

    /// Plan step 24: haptic pulse rate = proximity. Nearer is faster, and the
    /// fastest rate still clears the ~60 ms tactile fusion limit by a margin.
    func testPulseRateRisesWithProximityAndStaysInsideItsBounds() {
        var previous = Int64.max
        for step in 0...100 {
            let proximity = Double(step) / 100
            let interval = SteerPolicy.pulseIntervalMs(proximity: proximity, anchored: true)
            XCTAssertLessThanOrEqual(interval, previous, "proximity \(proximity)")
            XCTAssertGreaterThanOrEqual(interval, SteerPolicy.pulseIntervalMinMs)
            XCTAssertLessThanOrEqual(interval, SteerPolicy.pulseIntervalMaxMs)
            previous = interval
        }
        XCTAssertEqual(SteerPolicy.pulseIntervalMs(proximity: 1.0, anchored: true), SteerPolicy.pulseIntervalMinMs)
        XCTAssertEqual(
            SteerPolicy.pulseIntervalMs(proximity: SteerPolicy.quietProximity, anchored: true),
            SteerPolicy.pulseIntervalMaxMs
        )
        // Well clear of the fusion limit the haptic alphabet already respects.
        XCTAssertGreaterThan(SteerPolicy.pulseIntervalMinMs, 60)
    }

    /// Plan §2.3: with the ground-plane scale fit rejected, `proximity` is a
    /// band and not a number, so the rate may only take band-many values --
    /// the same "trust the band, not the metre" rule `RangeTrust` applies.
    func testAnUnanchoredProximityIsRenderedAsBandsNotAsANumber() {
        let intervals = Set(
            stride(from: 0.0, through: 1.0, by: 0.01)
                .map { SteerPolicy.pulseIntervalMs(proximity: $0, anchored: false) }
        )
        XCTAssertEqual(intervals.count, SteerPolicy.unanchoredBandCount)
        let anchored = Set(
            stride(from: 0.0, through: 1.0, by: 0.01)
                .map { SteerPolicy.pulseIntervalMs(proximity: $0, anchored: true) }
        )
        XCTAssertGreaterThan(anchored.count, intervals.count)
        XCTAssertEqual(SteerPolicy.pulseIntervalMs(proximity: 1.0, anchored: false), SteerPolicy.pulseIntervalMinMs)
    }
}

final class SteerPulseClockTests: XCTestCase {

    /// A cue that has just become audible pulses on the instant, not one
    /// interval later: the wearer is already walking at it.
    func testTheFirstPulseFiresImmediately() {
        let (_, fired) = SteerPulseClock().advanced(elapsedMs: 0, intervalMs: 700)
        XCTAssertTrue(fired)
    }

    func testItFiresOncePerIntervalAndNotPerTick() {
        var clock = SteerPulseClock()
        var fires = 0
        // 2 s of 100 ms ticks at a 400 ms interval: 1 immediate + 5 more.
        for _ in 0..<20 {
            let (next, fired) = clock.advanced(elapsedMs: 100, intervalMs: 400)
            clock = next
            if fired { fires += 1 }
        }
        XCTAssertEqual(fires, 5)
    }

    /// A long stall (the app was backgrounded) must not fire a burst of
    /// backlogged pulses when the tick resumes.
    func testALongStallFiresOnePulseNotABacklog() {
        var clock = SteerPulseClock()
        (clock, _) = clock.advanced(elapsedMs: 0, intervalMs: 200)
        let (_, fired) = clock.advanced(elapsedMs: 10_000, intervalMs: 200)
        XCTAssertTrue(fired)
        var next = clock
        var fires = 0
        (next, _) = next.advanced(elapsedMs: 10_000, intervalMs: 200)
        for _ in 0..<3 {
            let (after, fired) = next.advanced(elapsedMs: 0, intervalMs: 200)
            next = after
            if fired { fires += 1 }
        }
        XCTAssertEqual(fires, 0, "the backlog was spent on one pulse")
    }
}
