import CaneContracts
import CaneTransport
import Foundation

/// The consumer (A13, A16). An `actor`: the gauntlet's state is mutated
/// from ingest, from presentation completion and from timeouts, and an
/// actor is the only way those three cannot interleave inside a stage.
///
/// Everything it decides is decided by the pure stages; this type owns the
/// state, the clock and the two sinks, and performs the intents they return.
public actor FeedbackManager {
    /// A11's `PRESENT_TIMEOUT_MS`.
    public static let defaultPresentTimeoutMs: Int64 = 2_500
    /// A pump round only repeats when a re-entrant caller asked for one;
    /// this bounds the loop even if something pathological keeps asking.
    private static let maxPumpRounds = 16

    private var state: FeedbackState
    private let sink: AnnouncementSink
    private let ambientSink: AmbientSink
    private let steerSink: SteerCueSink
    private let clock: @Sendable () -> Int64
    private let presentTimeoutMs: Int64

    private var presentTask: Task<Void, Never>?
    private var pumping = false
    private var pumpRequested = false

    public init(
        sink: AnnouncementSink,
        ambientSink: AmbientSink = NoAmbientSink(),
        steerSink: SteerCueSink = NoSteerCueSink(),
        presentTimeoutMs: Int64 = FeedbackManager.defaultPresentTimeoutMs,
        state: FeedbackState = FeedbackState(),
        clock: @escaping @Sendable () -> Int64
    ) {
        self.sink = sink
        self.ambientSink = ambientSink
        self.steerSink = steerSink
        self.presentTimeoutMs = presentTimeoutMs
        self.state = state
        self.clock = clock
    }

    // MARK: - Reading

    public var counters: FeedbackCounters { state.counters }
    public var snapshot: FeedbackState { state }

    public func staleDropRate(now: Int64) -> Double { state.staleDropRate(now: now) }
    public func isStaleDropRateUnhealthy(now: Int64) -> Bool { state.isStaleDropRateUnhealthy(now: now) }

    // MARK: - Entry points (A16)

    public func ingest(_ data: Data, now: Int64) async {
        let (next, outcome) = Gauntlet.ingest(data, into: state, now: now)
        await apply(next, outcome, now: now)
    }

    public func ingest(_ event: AwarenessEvent, now: Int64) async {
        let (next, outcome) = Gauntlet.ingest(event, into: state, now: now)
        await apply(next, outcome, now: now)
    }

    /// Runs the pump without an event: releases an item held by the speech
    /// throttle, and discards items that have gone stale in the queue.
    ///
    /// Also the loop Shepherd's stage 8 rides (step 24): the steering
    /// integrator is advanced here, so a 3 Hz `steer` renders as a continuous
    /// cue and a `steer` that has stopped arriving decays to neutral. The two
    /// halves share only the clock -- neither reads the other's state.
    public func tick(now: Int64) async {
        advanceSteering(now: now)
        await pump(now: now)
    }

    // MARK: - Steering (step 24: Shepherd's stages 6, 8, 9)

    /// One `steer` frame from the laptop. Gated on `STEER_TTL_MS` by
    /// `StalenessGate`; nothing is rendered until the next tick.
    public func ingest(_ command: SteerCommand, now: Int64) {
        state = Steering.accept(command, into: state, now: now)
    }

    /// The link's state, from whatever watches the socket. `false` starts the
    /// failsafe decay on the next tick without waiting out the TTL.
    public func setLinkUp(_ up: Bool) {
        state = Steering.setLink(up: up, in: state)
    }

    /// The current navigation turn cue's lateral pull, -1 (left) to +1
    /// (right). Scaled by `(1 - proximityFactor)` in the mix, so obstacles
    /// always win; it expires on its own after
    /// `SteerPolicy.navigationBiasTtlMs`.
    ///
    /// This is the *steering* mix only. Navigation **speech** arbitration is
    /// unchanged: `OutputArbiter` still drops a spoken turn cue that lands
    /// under a hazard announcement.
    public func setNavigationBias(lateral: Double, now: Int64) {
        state = Steering.bias(lateral: lateral, in: state, now: now)
    }

    private func advanceSteering(now: Int64) {
        let (next, effect) = Steering.advance(state, now: now)
        state = next
        switch effect {
        case .quiet:
            break
        case .pulse(let cue):
            steerSink.pulse(cue)
        case .silence:
            steerSink.silence()
        }
    }

    /// Waits until nothing is being presented. Used by tests and by an
    /// orderly shutdown; it does not wait for throttled items, which is
    /// what `tick` is for.
    public func settle(maxRounds: Int = 64) async {
        for _ in 0..<maxRounds {
            guard let task = presentTask else { return }
            await task.value
        }
    }

    // MARK: - Performing the intents

    private func apply(_ next: FeedbackState, _ outcome: Gauntlet.Outcome, now: Int64) async {
        state = next
        for effect in outcome.effects { perform(effect) }
        await pump(now: now)
    }

    /// One pump at a time. A re-entrant call (an ingest that lands while we
    /// are awaiting a cancellation) sets a flag instead of racing, and the
    /// owning loop picks its item up.
    private func pump(now: Int64) async {
        guard !pumping else {
            pumpRequested = true
            return
        }
        pumping = true
        defer { pumping = false }

        var rounds = 0
        repeat {
            pumpRequested = false
            rounds += 1
            let (next, decision) = Pump.decide(state, now: now)
            state = next
            switch decision {
            case .idle:
                break
            case let .present(announcement):
                perform(.present(announcement))
            case let .preempt(_, announcement):
                await cancelInFlight()
                perform(.present(announcement))
            }
        } while pumpRequested && rounds < Self.maxPumpRounds
    }

    /// The only place an intent reaches the outside world (A16).
    private func perform(_ effect: Effect) {
        switch effect {
        case let .ambient(event, urgency):
            ambientSink.record(event, urgency: urgency)
        case .cancelCurrent:
            sink.cancelCurrent()
        case let .present(announcement):
            startPresenting(announcement)
        }
    }

    private func startPresenting(_ announcement: Announcement) {
        let sink = self.sink
        let timeoutMs = presentTimeoutMs
        presentTask = Task { [weak self] in
            let timedOut = await Presentation.run(
                sink: sink,
                announcement: announcement,
                timeoutMs: timeoutMs
            )
            await self?.finishedPresenting(id: announcement.id, timedOut: timedOut)
        }
    }

    /// A11's order: cancel the renderer, cancel the task, await it, then
    /// present the next. The await is bounded -- `Presentation.run` resolves
    /// on cancellation without waiting for a wedged sink. The cancelled
    /// announcement's id is not needed: `Pump.decide` has already recorded
    /// its successor as in flight, which is what makes the old task's
    /// completion callback a no-op.
    private func cancelInFlight() async {
        perform(.cancelCurrent)
        let task = presentTask
        presentTask = nil
        task?.cancel()
        await task?.value
    }

    private func finishedPresenting(id: UInt64, timedOut: Bool) async {
        if timedOut { state.counters.sinkTimeouts += 1 }
        // A preempted announcement's task finishes after its successor has
        // already been recorded as in flight; it must not clear it.
        guard state.inFlight?.id == id else { return }
        state.inFlight = nil
        presentTask = nil
        await pump(now: clock())
    }
}
