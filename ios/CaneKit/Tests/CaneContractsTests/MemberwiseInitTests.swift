import XCTest

@testable import CaneContracts

/// The public validating initializer must run the *same* bound and
/// conditional checks as `init(from decoder:)` -- one shared implementation,
/// so an in-memory event can never be laxer than a decoded one.
final class MemberwiseInitTests: XCTestCase {
    func testValidMemberwiseInitRoundTripsThroughEncodedAndDecode() throws {
        let event = try AwarenessEvent(
            source: .phone,
            trackId: "phone-person-001",
            tCaptureMs: 1_790_000_000_000,
            ttlMs: 1500,
            cls: .person,
            confidence: 0.92,
            bearingDeg: 12.0,
            rangeM: 3.5,
            rangeBand: .near,
            closingMps: nil
        )

        XCTAssertEqual(event.schemaVersion, 1, "schema defaults to the only supported version")

        let decoded = try AwarenessEvent.decode(try event.encoded())
        XCTAssertEqual(decoded, event)
    }

    func testX4InitWithMetricRangeThrows() throws {
        XCTAssertThrowsError(
            try AwarenessEvent(
                source: .x4Local,
                trackId: "x4-cyclist-7",
                tCaptureMs: 1_790_000_000_000,
                ttlMs: 500,
                cls: .cyclist,
                confidence: 0.8,
                bearingDeg: 170.0,
                rangeM: 12.0,
                rangeBand: .mid,
                closingMps: 4.0
            )
        ) { error in
            XCTAssertEqual(error as? AwarenessEventError, .x4StatedMetres)
        }
    }

    /// Proves the bound checks are shared, not re-implemented: -180 is the
    /// disallowed exclusive minimum on both paths.
    func testMemberwiseInitRejectsBearingAtExclusiveMinimum() throws {
        XCTAssertThrowsError(
            try AwarenessEvent(
                source: .phone,
                trackId: "t1",
                tCaptureMs: 1_790_000_000_000,
                ttlMs: 500,
                cls: .pole,
                confidence: 0.5,
                bearingDeg: -180.0,
                rangeM: 2.0,
                rangeBand: .near,
                closingMps: nil
            )
        ) { error in
            XCTAssertEqual(error as? AwarenessEventError, .outOfRange(field: "bearing_deg"))
        }
    }
}
