import Foundation

/// XCTest has no built-in per-assertion timeout for an arbitrary `async`
/// expression; races `operation` against a sleeping "timeout" task and
/// returns whichever finishes first. Shared by any `CaneTransportTests`
/// file that awaits something from a real socket/process and must not
/// hang the whole suite if it never arrives.
func withTimeout<T: Sendable>(
    seconds: Double,
    operation: @escaping @Sendable () async -> T
) async throws -> T {
    try await withThrowingTaskGroup(of: TimeoutRace<T>.self) { group in
        defer { group.cancelAll() }

        group.addTask {
            .value(await operation())
        }
        group.addTask {
            try await Task.sleep(for: .seconds(seconds))
            return .timedOut
        }

        guard let first = try await group.next() else {
            throw TestTimeoutError()
        }
        switch first {
        case .value(let value):
            return value
        case .timedOut:
            throw TestTimeoutError()
        }
    }
}

private enum TimeoutRace<T: Sendable>: Sendable {
    case value(T)
    case timedOut
}

struct TestTimeoutError: Error {}
