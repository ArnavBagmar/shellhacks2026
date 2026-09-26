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
    let eventContinuation: AsyncStream<AwarenessEvent>.Continuation
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
        var continuation: AsyncStream<AwarenessEvent>.Continuation!
        self.events = AsyncStream { continuation = $0 }
        self.eventContinuation = continuation
    }

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
        eventContinuation.finish()
    }

    private func startIfNeeded() {
        guard runLoopTask == nil else { return }
        isUserDisconnected = false
        runLoopTask = Task { [weak self, url, session, now, logger, eventContinuation] in
            await EventLinkClient.runReconnectLoop(
                url: url, session: session, now: now, logger: logger,
                eventContinuation: eventContinuation, weakClient: self
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
