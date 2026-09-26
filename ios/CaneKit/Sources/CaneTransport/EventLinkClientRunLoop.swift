import CaneContracts
import Foundation
import os

/// `EventLinkClient`'s reconnect loop, factored into its own file because
/// every function here is deliberately `static` and takes an
/// `EventLinkClient?` weakly rather than being an instance method: an
/// instance method call keeps its receiver alive for the call's entire
/// duration, and `runConnection` below blocks on `receive()` for as long
/// as the socket stays open. Writing that as `self.runConnection(...)`
/// would retain `self` for the connection's whole lifetime -- exactly the
/// reference cycle (`self` -> `runLoopTask` -> a call that retains `self`)
/// `EventLinkClient`'s `deinit` doc comment promises does not happen.
/// `self` is only re-acquired, briefly, inside `beginAttempt`/`endAttempt`
/// -- each its own call frame, so that strong reference is released
/// deterministically when it returns, not held across the long wait.
extension EventLinkClient {
    static func runReconnectLoop(
        url: URL,
        session: URLSession,
        now: @escaping @Sendable () -> Int64,
        logger: Logger,
        sinks: InboundSinks,
        weakClient: EventLinkClient?
    ) async {
        var backoff = ReconnectBackoff()
        while !Task.isCancelled {
            guard let webSocketTask = await beginAttempt(weakClient: weakClient, url: url, session: session) else {
                return
            }

            let (receivedAnyFrame, connectionError) = await runConnection(
                webSocketTask, now: now, logger: logger, sinks: sinks
            )
            if receivedAnyFrame {
                backoff.reset()
            }

            guard await endAttempt(weakClient: weakClient, connectionError: connectionError, logger: logger) else {
                return
            }

            do {
                try await Task.sleep(for: backoff.next())
            } catch {
                return // cancelled while backing off
            }
        }
    }

    /// Registers a freshly-created, resumed `URLSessionWebSocketTask` with
    /// the client and returns it, or `nil` if the client is gone or
    /// `disconnect()` was called -- either way, the loop must stop.
    private static func beginAttempt(
        weakClient: EventLinkClient?, url: URL, session: URLSession
    ) async -> URLSessionWebSocketTask? {
        guard let client = weakClient else { return nil }
        guard await !client.isStopRequested() else { return nil }
        let webSocketTask = session.webSocketTask(with: url)
        await client.register(webSocketTask)
        webSocketTask.resume()
        return webSocketTask
    }

    /// Clears the client's registered task and logs a dropped connection
    /// (unless the drop was requested); returns whether the loop should
    /// keep going.
    private static func endAttempt(
        weakClient: EventLinkClient?, connectionError: Error?, logger: Logger
    ) async -> Bool {
        guard let client = weakClient else { return false }
        await client.register(nil)
        let stopRequested = await client.isStopRequested()
        if let connectionError, !stopRequested, !(connectionError is CancellationError) {
            logger.warning("connection dropped: \(String(describing: connectionError), privacy: .public)")
        }
        return !stopRequested
    }

    /// Reads frames until the socket errors or the task is cancelled.
    /// Returns whether at least one frame was successfully received
    /// (proof the connection was genuinely live) alongside the error that
    /// ended the loop. Takes no reference to the client at all: this is
    /// the (potentially long) blocking wait `self` must not be retained
    /// across.
    private static func runConnection(
        _ webSocketTask: URLSessionWebSocketTask,
        now: @escaping @Sendable () -> Int64,
        logger: Logger,
        sinks: InboundSinks
    ) async -> (receivedAnyFrame: Bool, error: Error?) {
        var receivedAnyFrame = false
        while true {
            if Task.isCancelled {
                return (receivedAnyFrame, CancellationError())
            }
            do {
                let message = try await webSocketTask.receive()
                receivedAnyFrame = true
                await handle(
                    data(for: message), on: webSocketTask, now: now, logger: logger, sinks: sinks
                )
            } catch {
                return (receivedAnyFrame, error)
            }
        }
    }

    private static func handle(
        _ data: Data,
        on webSocketTask: URLSessionWebSocketTask,
        now: @escaping @Sendable () -> Int64,
        logger: Logger,
        sinks: InboundSinks
    ) async {
        do {
            switch try InboundMessage.decode(data) {
            case .ping:
                await respondToPing(data, on: webSocketTask, now: now, logger: logger)
            case .event(let event):
                sinks.events.yield(event)
            case .answer(let answer):
                sinks.answers.yield(answer)
            case .unknown(let type):
                logger.debug("ignoring unknown frame type: \(type, privacy: .public)")
            }
        } catch {
            logger.warning("dropping malformed frame: \(String(describing: error), privacy: .public)")
        }
    }

    private static func respondToPing(
        _ pingData: Data, on webSocketTask: URLSessionWebSocketTask, now: @escaping @Sendable () -> Int64, logger: Logger
    ) async {
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
