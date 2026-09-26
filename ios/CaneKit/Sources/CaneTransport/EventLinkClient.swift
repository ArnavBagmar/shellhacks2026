import CaneContracts
import Foundation
import os

/// Wraps a `URLSessionWebSocketTask` connected to the laptop's event link
/// server. Auto-answers `ping` with `pong` (`PhoneClockResponder`); yields
/// decoded `AwarenessEvent`s on `events`; reconnects with capped
/// exponential backoff (`ReconnectBackoff`) when the socket drops. The
/// phone never estimates an offset (D1) -- there is no clock-sync math
/// here beyond answering PONG.
public final class EventLinkClient: @unchecked Sendable {
    private let url: URL
    private let session: URLSession
    private let now: () -> Int64
    private let logger = Logger(subsystem: "cane", category: "transport")

    private let eventContinuation: AsyncStream<AwarenessEvent>.Continuation
    public let events: AsyncStream<AwarenessEvent>

    private var task: URLSessionWebSocketTask?
    private var runLoopTask: Task<Void, Never>?
    private var isUserDisconnected = false

    public init(
        url: URL,
        session: URLSession = .shared,
        now: @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.url = url
        self.session = session
        self.now = now
        var continuation: AsyncStream<AwarenessEvent>.Continuation!
        self.events = AsyncStream { continuation = $0 }
        self.eventContinuation = continuation
    }

    /// Starts the connect/reconnect loop. Idempotent: a second call while
    /// already connecting/connected is a no-op.
    public func connect() {
        guard runLoopTask == nil else { return }
        isUserDisconnected = false
        runLoopTask = Task { [weak self] in
            await self?.runReconnectLoop()
        }
    }

    /// Cancels any in-flight connection or backoff wait and finishes
    /// `events`. Idempotent.
    public func disconnect() {
        isUserDisconnected = true
        runLoopTask?.cancel()
        runLoopTask = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    deinit {
        eventContinuation.finish()
    }

    private func runReconnectLoop() async {
        var backoff = ReconnectBackoff()
        while !Task.isCancelled && !isUserDisconnected {
            let webSocketTask = session.webSocketTask(with: url)
            task = webSocketTask
            webSocketTask.resume()

            let (receivedAnyFrame, connectionError) = await runConnection(webSocketTask)
            if receivedAnyFrame {
                backoff.reset()
            }
            if let connectionError, !isUserDisconnected, !(connectionError is CancellationError) {
                logger.warning("connection dropped: \(String(describing: connectionError), privacy: .public)")
            }
            task = nil

            if isUserDisconnected || Task.isCancelled { break }
            do {
                try await Task.sleep(for: backoff.next())
            } catch {
                break // cancelled while backing off
            }
        }
        eventContinuation.finish()
    }

    /// Reads frames until the socket errors or the task is cancelled.
    /// Returns whether at least one frame was successfully received
    /// (proof the connection was genuinely live) alongside the error that
    /// ended the loop, rather than throwing, so the caller can decide
    /// whether to reset backoff without needing a separate closure.
    private func runConnection(_ webSocketTask: URLSessionWebSocketTask) async -> (receivedAnyFrame: Bool, error: Error?) {
        var receivedAnyFrame = false
        while true {
            if Task.isCancelled {
                return (receivedAnyFrame, CancellationError())
            }
            do {
                let message = try await webSocketTask.receive()
                receivedAnyFrame = true
                await handle(Self.data(for: message), on: webSocketTask)
            } catch {
                return (receivedAnyFrame, error)
            }
        }
    }

    private func handle(_ data: Data, on webSocketTask: URLSessionWebSocketTask) async {
        do {
            switch try InboundMessage.decode(data) {
            case .ping:
                await respondToPing(data, on: webSocketTask)
            case .event(let event):
                eventContinuation.yield(event)
            case .unknown(let type):
                logger.debug("ignoring unknown frame type: \(type, privacy: .public)")
            }
        } catch {
            logger.warning("dropping malformed frame: \(String(describing: error), privacy: .public)")
        }
    }

    private func respondToPing(_ pingData: Data, on webSocketTask: URLSessionWebSocketTask) async {
        do {
            let pongData = try PhoneClockResponder.pong(forPing: pingData, now: now)
            try await webSocketTask.send(.data(pongData))
        } catch {
            logger.warning("failed to answer ping: \(String(describing: error), privacy: .public)")
        }
    }

    private static func data(for message: URLSessionWebSocketTask.Message) -> Data {
        switch message {
        case .data(let data):
            return data
        case .string(let text):
            return Data(text.utf8)
        @unknown default:
            return Data()
        }
    }
}
