import CaneTransport
import XCTest

@testable import CaneFeedback

/// Step 24 through the existing actor: the `steer` frame arrives, the
/// staleness gate decides whether it is still true, the integrator renders it
/// on the 100 ms tick the app already runs, and the failsafe decays it to
/// neutral when the evidence stops.
///
/// Every test drives the clock by hand; nothing here sleeps.
final class SteeringManagerTests: XCTestCase {
    private let t0 = MakeSteer.t0

    private func manager(
        steer: RecordingSteerSink,
        clock: TestClock,
        sink: RecordingSink = RecordingSink()
    ) -> FeedbackManager {
        FeedbackManager(sink: sink, steerSink: steer, clock: clock.reader)
    }

    /// Runs the app's tick loop for `ms` in `SteerPolicy.integratorTickMs`
    /// steps, exactly as `Runtime.startLoops` does.
    private func run(_ subject: FeedbackManager, _ clock: TestClock, forMs ms: Int64) async {
        let step = SteerPolicy.integratorTickMs
        var elapsed: Int64 = 0
        while elapsed < ms {
            elapsed += step
            clock.set(clock.now + step)
            await subject.tick(now: clock.now)
        }
    }

    // MARK: - A: the staleness gate

    /// A steer command past its own `ttl_ms` is dropped and counted, exactly
    /// as a stale event is -- through `StalenessGate`, not a second mechanism.
    func testASteerCommandPastItsTtlIsDroppedAndCounted() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        let late = t0 + Int64(SteerLimits.ttlMs) + 1
        clock.set(late)
        await subject.ingest(try MakeSteer.command(tCaptureMs: t0, steer: 0.9, proximity: 1.0), now: late)
        await run(subject, clock, forMs: 1_000)

        let counters = await subject.counters
        XCTAssertEqual(counters.steerReceived, 1)
        XCTAssertEqual(counters.steerStaleDrops, 1)
        XCTAssertTrue(steer.pulses.isEmpty, "a stale command must never reach the wearer")
        // H4: only awareness-event staleness feeds the step-8 transport
        // fallback; a late steer frame is not a detector problem.
        XCTAssertEqual(counters.staleDrops, 0)
        XCTAssertEqual(counters.eventsReceived, 0)
    }

    /// Exactly at its TTL it is still true -- the same strict `>` the event
    /// gate uses.
    func testACommandExactlyAtItsTtlIsStillAccepted() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        let at = t0 + Int64(SteerLimits.ttlMs)
        clock.set(at)
        await subject.ingest(try MakeSteer.command(tCaptureMs: t0, steer: 0.5, proximity: 1.0), now: at)

        let counters = await subject.counters
        let held = await subject.snapshot.steering.command
        XCTAssertEqual(counters.steerStaleDrops, 0)
        XCTAssertEqual(held?.steer, 0.5)
    }

    /// A producer may be more conservative than the plan, never more
    /// optimistic: the same `min(producer, table)` clamp `TTLTable` applies.
    func testAProducerTtlLongerThanThePlansIsClampedDown() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        let generous = try MakeSteer.command(tCaptureMs: t0, ttlMs: 5_000, steer: 0.5, proximity: 1.0)
        let at = t0 + Int64(SteerLimits.ttlMs) + 1
        clock.set(at)
        await subject.ingest(generous, now: at)

        let counters = await subject.counters
        XCTAssertEqual(counters.steerStaleDrops, 1)
    }

    /// Frames can be reordered on the wire; an older one must not undo a
    /// newer one's steering.
    func testAnOlderCommandNeverReplacesANewerOne() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0 + 100)
        let subject = manager(steer: steer, clock: clock)

        await subject.ingest(try MakeSteer.command(tCaptureMs: t0 + 100, steer: 0.8, proximity: 1.0), now: clock.now)
        await subject.ingest(try MakeSteer.command(tCaptureMs: t0, steer: -0.8, proximity: 1.0), now: clock.now)

        let held = await subject.snapshot.steering.command
        let counters = await subject.counters
        XCTAssertEqual(held?.steer, 0.8)
        XCTAssertEqual(counters.steerOutOfOrder, 1)
    }

    // MARK: - B: the integrator on the existing tick

    /// Acceptance 1 at the actor level: 3 Hz of evidence, rendered as a
    /// continuous cue by the 100 ms tick (plan §1.3).
    func testAStepChangeInSteerIsRenderedGraduallyAndNeverOvershoots() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        await subject.ingest(try MakeSteer.command(tCaptureMs: t0, steer: 1.0, proximity: 1.0), now: t0)
        // One tick's worth of integration is 35% of the step, not all of it.
        clock.set(t0 + 100)
        await subject.tick(now: clock.now)
        let first = try XCTUnwrap(steer.pulses.first)
        XCTAssertEqual(first.lateral, SteerPolicy.integratorK, accuracy: 1e-9)

        // Refreshed every 333 ms, as the bridge sends it; the cue keeps
        // closing on the target and never passes it.
        for frame in 1...9 {
            let capture = t0 + Int64(frame) * 333
            clock.set(capture)
            await subject.ingest(try MakeSteer.command(tCaptureMs: capture, steer: 1.0, proximity: 1.0), now: capture)
            await run(subject, clock, forMs: 300)
        }
        let lateral = await subject.snapshot.steering.integrator.lateral.value
        XCTAssertGreaterThan(lateral, 0.99)
        XCTAssertLessThanOrEqual(lateral, 1.0)
        XCTAssertTrue(steer.pulses.allSatisfy { $0.lateral <= 1.0 && $0.lateral >= -1.0 })
    }

    /// The integrator lives on the tick, not on arrival: ingest alone must
    /// not move the cue, or a burst of frames would jump it.
    func testIngestAloneNeverMovesTheIntegrator() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        for _ in 0..<5 {
            await subject.ingest(try MakeSteer.command(tCaptureMs: t0, steer: 1.0, proximity: 1.0), now: t0)
        }
        let lateral = await subject.snapshot.steering.integrator.lateral.value
        XCTAssertEqual(lateral, 0)
        XCTAssertTrue(steer.pulses.isEmpty)
    }

    // MARK: - C: the quiet threshold (acceptance 5, plan risk 3)

    func testNoCueIsEmittedAtAllBelowTheQuietThreshold() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        // A clear corridor with a gap slightly to the left: worth steering
        // toward, not worth interrupting anyone about.
        for frame in 0...15 {
            let capture = t0 + Int64(frame) * 333
            clock.set(capture)
            await subject.ingest(
                try MakeSteer.command(tCaptureMs: capture, steer: -0.3, proximity: SteerPolicy.quietProximity - 0.05),
                now: capture
            )
            await run(subject, clock, forMs: 333)
        }

        XCTAssertTrue(steer.pulses.isEmpty, "the third channel must stay silent when nothing is close")
        XCTAssertEqual(steer.silences, 0, "silence is the resting state, not an event")
        let counters = await subject.counters
        XCTAssertEqual(counters.steerPulses, 0)
        // It is still tracking -- it is only not speaking.
        let lateral = await subject.snapshot.steering.integrator.lateral.value
        XCTAssertLessThan(lateral, -0.25)
    }

    func testTheCueStartsOnceProximityCrossesTheThreshold() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        for frame in 0...9 {
            let capture = t0 + Int64(frame) * 333
            clock.set(capture)
            await subject.ingest(try MakeSteer.command(tCaptureMs: capture, steer: 0.6, proximity: 1.0), now: capture)
            await run(subject, clock, forMs: 333)
        }

        XCTAssertGreaterThan(steer.pulses.count, 4)
        let last = try XCTUnwrap(steer.pulses.last)
        XCTAssertGreaterThan(last.proximity, 0.9)
        XCTAssertEqual(last.intervalMs, SteerPolicy.pulseIntervalMinMs)
        let counters = await subject.counters
        XCTAssertEqual(counters.steerPulses, steer.pulses.count)
    }

    // MARK: - D: the navigation blend (acceptance 3)

    func testANavigationBiasPullsTheCueOnlyWhileNothingIsClose() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        // Nothing in the way: the turn cue owns the mix.
        await subject.setNavigationBias(lateral: 1.0, now: t0)
        for frame in 0...5 {
            let capture = t0 + Int64(frame) * 300
            clock.set(capture)
            await subject.ingest(try MakeSteer.command(tCaptureMs: capture, steer: 0.0, proximity: 0.0), now: capture)
            await subject.setNavigationBias(lateral: 1.0, now: capture)
            await run(subject, clock, forMs: 300)
        }
        let biased = await subject.snapshot.steering.integrator.lateral.value
        XCTAssertGreaterThan(biased, 0.9)

        // Now a wall on the right, hard: the obstacle wins outright and the
        // turn cue contributes exactly nothing.
        for frame in 6...20 {
            let capture = t0 + Int64(frame) * 300
            clock.set(capture)
            await subject.ingest(try MakeSteer.command(tCaptureMs: capture, steer: -1.0, proximity: 1.0), now: capture)
            await subject.setNavigationBias(lateral: 1.0, now: capture)
            await run(subject, clock, forMs: 300)
        }
        let overridden = await subject.snapshot.steering.integrator.lateral.value
        XCTAssertLessThan(overridden, -0.9)
    }

    // MARK: - E: the failsafe (acceptance 2 and 6)

    /// Acceptance 2: stale -> silent within `FAILSAFE_DECAY_MS`, monotonically
    /// toward neutral, and nothing held afterwards.
    func testAStaleSteerDecaysToSilenceWithinFiveHundredMilliseconds() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        var capture = t0
        for frame in 0...9 {
            capture = t0 + Int64(frame) * 333
            clock.set(capture)
            await subject.ingest(try MakeSteer.command(tCaptureMs: capture, steer: 1.0, proximity: 1.0), now: capture)
            await run(subject, clock, forMs: 333)
        }
        XCTAssertFalse(steer.pulses.isEmpty)

        // The laptop stops sending. The last frame expires at capture + 400.
        let expiry = capture + Int64(SteerLimits.ttlMs)
        var readings: [(Int64, Double, Double)] = []
        var silencedAtMs: Int64?
        while clock.now < expiry + SteerPolicy.failsafeDecayMs {
            clock.set(clock.now + SteerPolicy.integratorTickMs)
            await subject.tick(now: clock.now)
            if silencedAtMs == nil, steer.silences == 1 { silencedAtMs = clock.now }
            let state = await subject.snapshot.steering
            readings.append((clock.now, state.integrator.lateral.value, state.integrator.proximity.value))
        }

        // The headline number: silent within the failsafe window of going stale.
        XCTAssertLessThanOrEqual(try XCTUnwrap(silencedAtMs) - expiry, SteerPolicy.failsafeDecayMs)

        // Monotone toward neutral on both channels, from the moment it starts.
        let decaying = readings.drop { $0.0 <= expiry }
        for (previous, next) in zip(decaying, decaying.dropFirst()) {
            XCTAssertLessThanOrEqual(abs(next.1), abs(previous.1) + 1e-12, "lateral rose again at \(next.0)")
            XCTAssertLessThanOrEqual(next.2, previous.2 + 1e-12, "proximity rose again at \(next.0)")
        }

        // Neutral, silent, and nothing held.
        let state = await subject.snapshot.steering
        XCTAssertEqual(state.integrator.lateral.value, 0, accuracy: 1e-9)
        XCTAssertEqual(state.integrator.proximity.value, 0, accuracy: 1e-9)
        XCTAssertNil(state.command, "a stale steer must never be held")
        XCTAssertEqual(steer.silences, 1)
        XCTAssertEqual(steer.log.last, "silence", "nothing may be emitted after the cue stops")
        let counters = await subject.counters
        XCTAssertEqual(counters.steerExpired, 1)
        XCTAssertEqual(counters.steerFailsafes, 1)

        // And it stays silent: no further pulses once the evidence is gone.
        let settled = steer.pulses.count
        await run(subject, clock, forMs: 5_000)
        XCTAssertEqual(steer.pulses.count, settled)
        XCTAssertEqual(steer.silences, 1, "silence is emitted once, not once per tick")
        XCTAssertEqual(steer.log.last, "silence")
    }

    /// Acceptance 6: link loss, not just staleness, triggers the same decay --
    /// the phone must not ride out the TTL on a socket it knows is gone.
    func testLinkLossTriggersTheSameDecayImmediately() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        for frame in 0...9 {
            let capture = t0 + Int64(frame) * 333
            clock.set(capture)
            await subject.ingest(try MakeSteer.command(tCaptureMs: capture, steer: -1.0, proximity: 1.0), now: capture)
            await run(subject, clock, forMs: 333)
        }
        XCTAssertFalse(steer.pulses.isEmpty)

        // The socket drops while the last frame is still inside its TTL.
        await subject.setLinkUp(false)
        await run(subject, clock, forMs: SteerPolicy.failsafeDecayMs)

        let state = await subject.snapshot.steering
        XCTAssertEqual(state.integrator.lateral.value, 0, accuracy: 1e-9)
        XCTAssertNil(state.command)
        XCTAssertEqual(steer.silences, 1)
        XCTAssertEqual(steer.log.last, "silence")
        let counters = await subject.counters
        XCTAssertEqual(counters.steerFailsafes, 1)
    }

    /// The link coming back is not a reason to resume a cue: the phone waits
    /// for evidence. No silent fakes (plan §4.2).
    func testTheLinkComingBackDoesNotResumeACueWithoutANewFrame() async throws {
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock)

        clock.set(t0)
        await subject.ingest(try MakeSteer.command(tCaptureMs: t0, steer: 1.0, proximity: 1.0), now: t0)
        await run(subject, clock, forMs: 300)
        await subject.setLinkUp(false)
        await run(subject, clock, forMs: 600)
        let after = steer.pulses.count

        await subject.setLinkUp(true)
        await run(subject, clock, forMs: 2_000)

        XCTAssertEqual(steer.pulses.count, after)
        let held = await subject.snapshot.steering.command
        XCTAssertNil(held)
    }

    /// The hazard path is untouched by any of this: a steer frame is not an
    /// awareness event and cannot announce, dedupe or suppress anything.
    func testSteeringNeverTouchesTheAnnouncementPath() async throws {
        let sink = RecordingSink()
        let steer = RecordingSteerSink()
        let clock = TestClock(now: t0)
        let subject = manager(steer: steer, clock: clock, sink: sink)

        await subject.ingest(try MakeSteer.command(tCaptureMs: t0, steer: 1.0, proximity: 1.0), now: t0)
        await run(subject, clock, forMs: 1_000)
        await subject.settle()

        XCTAssertTrue(sink.presented.isEmpty)
        let counters = await subject.counters
        XCTAssertEqual(counters.announced, 0)
        XCTAssertEqual(counters.eventsReceived, 0)
        XCTAssertEqual(counters.invalidRejected, 0)
    }
}
