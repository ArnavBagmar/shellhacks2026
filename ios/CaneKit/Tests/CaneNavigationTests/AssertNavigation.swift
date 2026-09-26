import XCTest

@testable import CaneNavigation

/// `XCTAssertThrowsError` has no `async` form, so these two wrap the
/// do/catch every navigation failure test would otherwise repeat.
func assertThrows(
    _ expected: NavigationError,
    file: StaticString = #filePath,
    line: UInt = #line,
    _ operation: () async throws -> Void
) async {
    do {
        try await operation()
        XCTFail("expected \(expected), but nothing was thrown", file: file, line: line)
    } catch let error as NavigationError {
        XCTAssertEqual(error, expected, file: file, line: line)
    } catch {
        XCTFail("expected \(expected), got \(error)", file: file, line: line)
    }
}

func assertThrowsNavigationError(
    file: StaticString = #filePath,
    line: UInt = #line,
    subject operation: () async throws -> Void,
    _ check: (NavigationError) -> Void
) async {
    do {
        try await operation()
        XCTFail("expected a NavigationError, but nothing was thrown", file: file, line: line)
    } catch let error as NavigationError {
        check(error)
    } catch {
        XCTFail("expected a NavigationError, got \(error)", file: file, line: line)
    }
}

/// Races an async expression against a real (short) deadline so a
/// broken engine fails the suite instead of hanging it. The engine's
/// own time is virtual; this is only a safety net.
func withTestTimeout<T: Sendable>(
    seconds: Double = 5,
    _ operation: @escaping @Sendable () async -> T
) async throws -> T {
    try await withThrowingTaskGroup(of: T?.self) { group in
        defer { group.cancelAll() }
        group.addTask { await operation() }
        group.addTask {
            try await Task.sleep(for: .seconds(seconds))
            return nil
        }
        guard let first = try await group.next(), let value = first else {
            throw TestTimeoutExceeded()
        }
        return value
    }
}

struct TestTimeoutExceeded: Error {}
