import Foundation

/// The engine's only source of "now" and "later".
///
/// Injected so the 1 Hz watchdog can be driven by virtual time in
/// tests: the whole three-minute acceptance walk runs in milliseconds,
/// and a ten-second GPS outage is ten advances of a counter rather than
/// ten seconds of a test suite. Every other piece of navigation takes
/// time as an explicit `nowMs` parameter instead.
public protocol NavigationClock: Sendable {
    func nowMs() async -> Int64
    func sleep(forMilliseconds milliseconds: Int64) async throws
}

/// Wall clock plus `Task.sleep`, for the app.
public struct SystemNavigationClock: NavigationClock {
    public init() {}

    public func nowMs() async -> Int64 {
        Int64(Date().timeIntervalSince1970 * 1_000)
    }

    public func sleep(forMilliseconds milliseconds: Int64) async throws {
        try await Task.sleep(for: .milliseconds(milliseconds))
    }
}

/// Where navigation is in its lifecycle. `failed` carries the reason so
/// the UI can say something specific; nothing here ever traps.
public enum NavigationStatus: Sendable, Equatable {
    case idle
    case resolving
    case routing
    case navigating
    case arrived
    case failed(NavigationError)
}
