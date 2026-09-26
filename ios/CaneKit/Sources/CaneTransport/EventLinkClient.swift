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
        self.events = events
        self.answers = answers
        self.sinks = InboundSinks(events: eventContinuation, answers: answerContinuation)
    }

    /// Sends one `query` frame on the live connection. Throws
    /// `TransportError.notConnected` when there is none -- the caller tells
    /// the wearer right away instead of waiting out a timeout.
    public func send(_ query: SceneQuery) async throws {
        guard let task else { throw TransportError.notConnected }
        try await task.send(.data(query.encoded()))
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

/// Where decoded inbound frames go. Both streams outlive any one connection.
struct InboundSinks: Sendable {
    let events: AsyncStream<AwarenessEvent>.Continuation
    let answers: AsyncStream<SceneAnswer>.Continuation

    func finish() {
        events.finish()
        answers.finish()
    }
}
