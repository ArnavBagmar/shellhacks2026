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
