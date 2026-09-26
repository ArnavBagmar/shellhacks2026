import XCTest

@testable import CaneContracts

/// Every case in `contracts/fixtures/awareness_events.golden.json` runs
/// through `AwarenessEvent.decode`; a disagreement between the fixture's
/// expectation and Swift's decoder fails loudly, with the case name in the
/// failure message (XCTest has no parametrize).
final class GoldenFixtureTests: XCTestCase {
    private struct Case {
        let name: String
        let expect: String
        let hasReason: Bool
        let eventData: Data
    }

    private func loadFixture() throws -> [String: Any] {
        try ContractPaths.loadJSONObject(at: ContractPaths.fixtureURL)
    }

    private func loadCases() throws -> [Case] {
        let fixture = try loadFixture()
        guard let rawCases = fixture["cases"] as? [[String: Any]] else {
            XCTFail("fixture.cases missing or malformed at \(ContractPaths.fixtureURL.path)")
            return []
        }
        return try rawCases.map { raw in
            guard
                let name = raw["name"] as? String,
                let expect = raw["expect"] as? String,
                let event = raw["event"] as? [String: Any]
            else {
                XCTFail("malformed case entry: \(raw)")
                return Case(name: "malformed", expect: "invalid", hasReason: false, eventData: Data())
            }
            let eventData = try JSONSerialization.data(withJSONObject: event)
            return Case(name: name, expect: expect, hasReason: raw["reason"] != nil, eventData: eventData)
        }
    }

    func testFixtureEnvelopeIsSane() throws {
        let fixture = try loadFixture()
        XCTAssertEqual(fixture["fixture_version"] as? Int, 1)

        let cases = try loadCases()
        let names = cases.map(\.name)
        XCTAssertEqual(Set(names).count, names.count, "case names must be unique")

        var validCount = 0
        var invalidCount = 0
        for c in cases {
            XCTAssertTrue(
                c.expect == "valid" || c.expect == "invalid",
                "\(c.name): expect must be exactly 'valid' or 'invalid'"
            )
            if c.expect == "invalid" {
                XCTAssertTrue(c.hasReason, "\(c.name): invalid case must carry a reason")
                invalidCount += 1
            } else {
                XCTAssertFalse(c.hasReason, "\(c.name): valid case must not carry a reason")
                validCount += 1
            }
        }
        XCTAssertGreaterThanOrEqual(validCount, 8, "fixture must have at least 8 valid cases")
        XCTAssertGreaterThanOrEqual(invalidCount, 10, "fixture must have at least 10 invalid cases")
    }

    func testEveryFixtureCaseMatchesExpectation() throws {
        let cases = try loadCases()
        XCTAssertFalse(cases.isEmpty, "fixture produced no cases")

        for c in cases {
            switch c.expect {
            case "valid":
                XCTAssertNoThrow(
                    try AwarenessEvent.decode(c.eventData),
                    "\(c.name): expected to decode as valid"
                )
            case "invalid":
                XCTAssertThrowsError(
                    try AwarenessEvent.decode(c.eventData),
                    "\(c.name): expected to be rejected"
                ) { error in
                    XCTAssertTrue(
                        error is AwarenessEventError,
                        "\(c.name): expected AwarenessEventError, got \(error)"
                    )
                }
            default:
                XCTFail("\(c.name): unknown expect value '\(c.expect)'")
            }
        }
    }

    func testMalformedPayloadsAreRejectedNotCrashed() throws {
        let fixture = try loadFixture()
        guard let payloads = fixture["malformed_payloads"] as? [String] else {
            XCTFail("fixture.malformed_payloads missing at \(ContractPaths.fixtureURL.path)")
            return
        }
        XCTAssertFalse(payloads.isEmpty)

        for payload in payloads {
            let data = Data(payload.utf8)
            XCTAssertThrowsError(
                try AwarenessEvent.decode(data),
                "malformed payload \(payload.debugDescription) should be rejected, not crashed"
            ) { error in
                XCTAssertTrue(error is AwarenessEventError)
            }
        }
    }

    func testRoundTripDecodeEncodeProducesExactly11Keys() throws {
        let cases = try loadCases()
        guard let validCase = cases.first(where: { $0.expect == "valid" }) else {
            XCTFail("no valid case in fixture")
            return
        }

        let event = try AwarenessEvent.decode(validCase.eventData)
        let encoded = try event.encoded()
        let decoded = try AwarenessEvent.decode(encoded)
        XCTAssertEqual(decoded, event)

        let object = try JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        XCTAssertEqual(object?.keys.count, 11, "encoded event must carry exactly the 11 wire keys")
    }

    func testExplicitNullRangeMDecodes() throws {
        let json = """
        {"schema": 1, "source": "x4_local", "track_id": "t1", \
        "t_capture_ms": 1700000000000, "ttl_ms": 500, "cls": "scooter", \
        "confidence": 0.5, "bearing_deg": 10.0, "range_m": null, \
        "range_band": "near", "closing_mps": null}
        """
        XCTAssertNoThrow(try AwarenessEvent.decode(Data(json.utf8)))
    }

    func testAbsentRangeMKeyThrows() throws {
        let json = """
        {"schema": 1, "source": "x4_local", "track_id": "t1", \
        "t_capture_ms": 1700000000000, "ttl_ms": 500, "cls": "scooter", \
        "confidence": 0.5, "bearing_deg": 10.0, \
        "range_band": "near", "closing_mps": null}
        """
        XCTAssertThrowsError(try AwarenessEvent.decode(Data(json.utf8))) { error in
            XCTAssertTrue(error is AwarenessEventError)
        }
    }
}
