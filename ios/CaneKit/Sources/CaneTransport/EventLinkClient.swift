import CaneContracts
import Foundation
import os

/// Wraps a `URLSessionWebSocketTask` connected to the laptop's event link
/// server. Auto-answers `ping` with `pong` (`PhoneClockResponder`); yields
/// decoded `AwarenessEvent`s on `events`; reconnects with capped
/// exponential backoff (`ReconnectBackoff`) when the socket drops. The
/// phone never estimates an offset (D1) -- there is no clock-sync math
/// here beyond answering PONG.
///
/// An `actor`, not a class: `task`/`runLoopTask`/`isUserDisconnected` are
/// touched both by `connect()`/`disconnect()` (whatever thread the caller
/// is on) and by the reconnect loop (a cooperative-pool thread); actor
/// isolation serializes every read/write of that state instead of relying
/// on the caller to synchronize it. `connect()`/`disconnect()` stay
/// synchronous, callable from any (non-isolated) context, by handing the
/// actual work to the actor via an unstructured `Task`.
///
/// `events` outlives any single connection: `disconnect()` does not finish
/// it, so a later `connect()` resumes yielding on the same stream (see
/// `stop()`). Only `deinit` finishes it.
///
/// The reconnect loop itself (`runReconnectLoop` and its helpers) lives in
/// `EventLinkClientRunLoop.swift` as a set of `static` functions operating
/// on a *weak* reference to this actor -- see that file's header comment
/// for why, and `deinit` below for the deallocation guarantee that buys.
public actor EventLinkClient {
    public nonisolated let events: AsyncStream<AwarenessEvent>
    /// Step 20: the laptop's replies to `send(_:)`, matched by `queryId`.
    /// Same lifetime rules as `events`.
    public nonisolated let answers: AsyncStream<SceneAnswer>
    /// Step 24: the laptop's continuous steering commands. Same lifetime rules
    /// as `events`; see `InboundSinks.steerBuffer` for why only the newest is
    /// kept.
    public nonisolated let steerCommands: AsyncStream<SteerCommand>
    /// Step 29: ground discontinuities ahead. Buffered like `steerCommands`
    /// -- only the newest matters, because an older one describes a patch of
    /// pavement the wearer has already walked over.
    public nonisolated let groundCues: AsyncStream<GroundCue>
    /// Natural-voice clips, matched by `speakId`.
    public nonisolated let voices: AsyncStream<VoiceClip>
    let sinks: InboundSinks
    let url: URL
    let session: URLSession
    let now: @Sendable () -> Int64
    let logger = Logger(subsystem: "cane", category: "transport")

    var task: URLSessionWebSocketTask?
    var runLoopTask: Task<Void, Never>?
    var isUserDisconnected = false

    public init(
        url: URL,
        session: URLSession = .shared,
        now: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.url = url
        self.session = session
        self.now = now
        let (events, eventContinuation) = AsyncStream<AwarenessEvent>.makeStream()
        let (answers, answerContinuation) = AsyncStream<SceneAnswer>.makeStream(bufferingPolicy: .bufferingNewest(4))
        let (steers, steerContinuation) = AsyncStream<SteerCommand>.makeStream(
            bufferingPolicy: .bufferingNewest(InboundSinks.steerBuffer)
        )
        let (grounds, groundContinuation) = AsyncStream<GroundCue>.makeStream(
            bufferingPolicy: .bufferingNewest(InboundSinks.steerBuffer)
        )
        let (voices, voiceContinuation) = AsyncStream<VoiceClip>.makeStream(
            bufferingPolicy: .bufferingNewest(8)
        )
        self.events = events
        self.answers = answers
        self.steerCommands = steers
        self.groundCues = grounds
        self.voices = voices
        self.sinks = InboundSinks(
            events: eventContinuation,
            answers: answerContinuation,
            steers: steerContinuation,
            grounds: groundContinuation,
            voices: voiceContinuation
        )
    }

    /// Sends one `query` frame on the live connection. Throws
    /// `TransportError.notConnected` when there is none -- the caller tells
    /// the wearer right away instead of waiting out a timeout.
    public func send(_ query: SceneQuery) async throws {
        guard let task else { throw TransportError.notConnected }
        try await task.send(.data(query.encoded()))
    }

    /// Asks the laptop to voice `request.text`; the reply arrives on
    /// `voices`. Throws `notConnected` with no link.
    public func send(_ request: SpeakRequest) async throws {
        guard let task else { throw TransportError.notConnected }
        try await task.send(.data(request.encoded()))
    }

    /// Best effort: the hand guide's live state for the laptop page.
    public func send(_ update: GuideUpdate) async throws {
        guard let task else { throw TransportError.notConnected }
        try await task.send(.data(update.encoded()))
    }

    /// Best effort: one black-box line to the laptop. Throws
    /// `notConnected` with no link; callers keep their own copy anyway.
    public func send(_ event: DiagnosticEvent) async throws {
        guard let task else { throw TransportError.notConnected }
        try await task.send(.data(event.encoded()))
    }

    /// True while a socket is registered (connecting or open).
    public var hasConnection: Bool { task != nil }

    /// Starts the connect/reconnect loop. Idempotent: a second call while
    /// already connecting/connected is a no-op. Safe to call without
    /// `await` from any context.
    public nonisolated func connect() {
        Task { await self.startIfNeeded() }
    }

    /// Cancels any in-flight connection or backoff wait. Idempotent. Does
    /// **not** finish `events` -- a later `connect()` resumes yielding on
    /// the same stream. Safe to call without `await` from any context.
    public nonisolated func disconnect() {
        Task { await self.stop() }
    }

    /// Callers should call `disconnect()` when finished with a client.
    /// Without it, dropping the last strong reference still deallocates
    /// the client: the reconnect loop only ever re-acquires a strong
    /// reference to `self` briefly, at the start and end of each
    /// connection attempt -- never across the (potentially long) blocking
    /// `receive()` wait or the backoff sleep between attempts. So the
    /// client deallocates at the next such boundary: after the current
    /// frame is received (or the current attempt errors) at the latest.
    deinit {
        runLoopTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        sinks.finish()
    }

    private func startIfNeeded() {
        guard runLoopTask == nil else { return }
        isUserDisconnected = false
        runLoopTask = Task { [weak self, url, session, now, logger, sinks] in
            await EventLinkClient.runReconnectLoop(
                url: url, session: session, now: now, logger: logger,
                sinks: sinks, weakClient: self
            )
        }
    }

    private func stop() {
        isUserDisconnected = true
        runLoopTask?.cancel()
        runLoopTask = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    func isStopRequested() -> Bool {
        isUserDisconnected
    }

    func register(_ webSocketTask: URLSessionWebSocketTask?) {
        task = webSocketTask
    }
}

/// Where decoded inbound frames go. Every stream outlives any one connection.
struct InboundSinks: Sendable {
    /// A steering command is only worth acting on if it is the newest one: an
    /// older one has been superseded and is inside 400 ms of being stale
    /// anyway (plan §3's `STEER_TTL_MS`). So the stream keeps exactly one, and
    /// a consumer that fell behind resumes on the current command instead of
    /// walking a queue of commands about a corridor it has already left.
    static let steerBuffer = 1

    let events: AsyncStream<AwarenessEvent>.Continuation
    let answers: AsyncStream<SceneAnswer>.Continuation
    let steers: AsyncStream<SteerCommand>.Continuation
    let grounds: AsyncStream<GroundCue>.Continuation
    let voices: AsyncStream<VoiceClip>.Continuation

    /// Routes one decoded frame to its stream. `ping` is not here: answering
    /// it needs the socket, so the run loop keeps that case. Anything with no
    /// stream (an unknown `type`) is ignored, which is the property plan §4.1
    /// requires of an old phone against a new laptop.
    func deliver(_ message: InboundMessage) {
        switch message {
        case .event(let event):
            events.yield(event)
        case .answer(let answer):
            answers.yield(answer)
        case .steer(let command):
            steers.yield(command)
        case .ground(let cue):
            grounds.yield(cue)
        case .voice(let clip):
            voices.yield(clip)
        case .ping, .unknown:
            break
        }
    }

    func finish() {
        events.finish()
        answers.finish()
        steers.finish()
        grounds.finish()
        voices.finish()
    }
}
