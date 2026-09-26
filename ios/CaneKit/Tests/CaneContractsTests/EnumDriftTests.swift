import XCTest

@testable import CaneContracts

/// Drift tests: the Swift enums and numeric bounds must never diverge from
/// the schema. The schema file is the only place a bound or an enum list
/// may be authored; every constant `AwarenessEventBounds` enforces is
/// asserted equal to it here.
final class EnumDriftTests: XCTestCase {
    private func loadSchema() throws -> [String: Any] {
        try ContractPaths.loadJSONObject(at: ContractPaths.schemaURL)
    }

    private func property(_ schema: [String: Any], _ name: String) throws -> [String: Any] {
        guard
            let properties = schema["properties"] as? [String: Any],
            let prop = properties[name] as? [String: Any]
        else {
            XCTFail("schema.properties.\(name) missing at \(ContractPaths.schemaURL.path)")
            return [:]
        }
        return prop
    }

    func testObjectClassMatchesSchemaEnum() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "cls")
        let schemaValues = prop["enum"] as? [String] ?? []
        XCTAssertEqual(ObjectClass.allCases.map(\.rawValue), schemaValues)
    }

    func testSourceMatchesSchemaEnum() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "source")
        let schemaValues = prop["enum"] as? [String] ?? []
        XCTAssertEqual(Source.allCases.map(\.rawValue), schemaValues)
    }

    func testRangeBandMatchesSchemaEnumMinusNull() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "range_band")
        let rawEnum = prop["enum"] as? [Any] ?? []
        let schemaValues = rawEnum.compactMap { $0 as? String }
        XCTAssertEqual(RangeBand.allCases.map(\.rawValue), schemaValues)
    }

    func testTrackIdLengthBoundsMatchSchema() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "track_id")
        XCTAssertEqual(AwarenessEventBounds.trackIdMinLength, prop["minLength"] as? Int)
        XCTAssertEqual(AwarenessEventBounds.trackIdMaxLength, prop["maxLength"] as? Int)
    }

    func testTCaptureMsBoundsMatchSchema() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "t_capture_ms")
        XCTAssertEqual(AwarenessEventBounds.tCaptureMsMin, prop["minimum"] as? Int)
        XCTAssertEqual(AwarenessEventBounds.tCaptureMsMax, prop["maximum"] as? Int)
    }

    func testTtlMsBoundsMatchSchema() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "ttl_ms")
        XCTAssertEqual(AwarenessEventBounds.ttlMsMin, prop["minimum"] as? Int)
        XCTAssertEqual(AwarenessEventBounds.ttlMsMax, prop["maximum"] as? Int)
    }

    func testConfidenceBoundsMatchSchema() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "confidence")
        XCTAssertEqual(AwarenessEventBounds.confidenceMin, prop["minimum"] as? Double)
        XCTAssertEqual(AwarenessEventBounds.confidenceMax, prop["maximum"] as? Double)
    }

    func testBearingDegBoundsMatchSchema() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "bearing_deg")
        XCTAssertEqual(AwarenessEventBounds.bearingDegMinExclusive, prop["exclusiveMinimum"] as? Double)
        XCTAssertEqual(AwarenessEventBounds.bearingDegMax, prop["maximum"] as? Double)
    }

    func testRangeMBoundsMatchSchema() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "range_m")
        XCTAssertEqual(AwarenessEventBounds.rangeMMin, prop["minimum"] as? Double)
        XCTAssertEqual(AwarenessEventBounds.rangeMMax, prop["maximum"] as? Double)
    }

    func testClosingMpsBoundsMatchSchema() throws {
        let schema = try loadSchema()
        let prop = try property(schema, "closing_mps")
        XCTAssertEqual(AwarenessEventBounds.closingMpsMin, prop["minimum"] as? Double)
        XCTAssertEqual(AwarenessEventBounds.closingMpsMax, prop["maximum"] as? Double)
    }
}
