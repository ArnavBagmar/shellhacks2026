import XCTest

@testable import CaneNavigation

/// Failures are values, and each one has to be able to say what went
/// wrong: `nav-cli` prints these straight to stderr and the app shows
/// them, so an empty or generic description is a defect.
final class NavigationErrorTests: XCTestCase {
    private let all: [NavigationError] = [
        .missingAPIKey,
        .destinationNotFound("the library"),
        .routesAPI(status: 403, message: "Requests to this API are blocked."),
        .noRoute,
        .noFix,
        .decoding("routes[0].polyline.encodedPolyline missing"),
        .transport("The Internet connection appears to be offline."),
    ]

    func testEveryErrorDescribesItself() {
        for error in all {
            XCTAssertFalse(error.description.isEmpty, "\(error) has no description")
            XCTAssertGreaterThan(error.description.count, 10, error.description)
        }
    }

    func testDescriptionsCarryTheirDetail() {
        XCTAssertTrue(NavigationError.missingAPIKey.description.contains("GOOGLE_MAPS_API_KEY"))
        XCTAssertTrue(
            NavigationError.destinationNotFound("the library").description.contains("the library")
        )
        XCTAssertTrue(
            NavigationError.routesAPI(status: 403, message: "blocked").description.contains("403")
        )
        XCTAssertTrue(NavigationError.decoding("bad polyline").description.contains("bad polyline"))
        XCTAssertTrue(NavigationError.transport("offline").description.contains("offline"))
        XCTAssertTrue(NavigationError.noFix.description.contains("fix"))
        XCTAssertTrue(NavigationError.noRoute.description.contains("route"))
    }

    func testErrorsCompareByValue() {
        XCTAssertEqual(NavigationError.destinationNotFound("a"), .destinationNotFound("a"))
        XCTAssertNotEqual(NavigationError.destinationNotFound("a"), .destinationNotFound("b"))
        XCTAssertNotEqual(NavigationError.noRoute, .noFix)
        XCTAssertEqual(
            NavigationStatus.failed(.noRoute), NavigationStatus.failed(.noRoute)
        )
    }

    /// The real clock: the app's `NavigationEngine` default. Tested
    /// with a tiny sleep so the suite stays fast.
    func testSystemClockMovesForwardAndSleeps() async throws {
        let clock = SystemNavigationClock()

        let before = await clock.nowMs()
        try await clock.sleep(forMilliseconds: 5)
        let after = await clock.nowMs()

        XCTAssertGreaterThan(before, 1_700_000_000_000, "milliseconds since the epoch")
        XCTAssertGreaterThanOrEqual(after, before + 4)
    }
}
