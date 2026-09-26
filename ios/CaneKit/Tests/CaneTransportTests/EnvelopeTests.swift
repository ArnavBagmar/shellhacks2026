import CaneContracts
import Foundation
import XCTest

@testable import CaneTransport

/// `InboundMessage.decode` is the phone's read side of the envelope (D2):
/// `ping`/`event` decode to their cases, an unrecognised `type` decodes to
/// `.unknown` rather than throwing, and a contract-invalid `event` payload
/// throws `TransportError.invalidEvent`.
final class EnvelopeTests: XCTestCase {
    func testDecodesEventFrameFromFixtureFirstValidCase() throws {
        let event = try FixturePaths.firstValidEventObject()
        let frame: [String: Any] = ["type": "event", "event": event]
        let data = try JSONSerialization.data(withJSONObject: frame)

        let expectedEvent = try AwarenessEvent.decode(JSONSerialization.data(withJSONObject: event))

        switch try InboundMessage.decode(data) {
        case .event(let decoded):
            XCTAssertEqual(decoded, expectedEvent)
        default:
            XCTFail("expected .event")
        }
    }

    func testPingFrameDecodes() throws {
        let data = Data("{\"type\": \"ping\", \"t1\": 1790000000000}".utf8)
        switch try InboundMessage.decode(data) {
        case .ping(let t1):
            XCTAssertEqual(t1, 1_790_000_000_000)
        default:
            XCTFail("expected .ping")
        }
    }

    func testUnknownTypeDecodesToUnknownCase() throws {
        let data = Data("{\"type\": \"health\", \"battery_pct\": 80}".utf8)
        switch try InboundMessage.decode(data) {
        case .unknown(let type):
            XCTAssertEqual(type, "health")
        default:
            XCTFail("expected .unknown")
        }
    }

    func testReservedLocationTypeAlsoDecodesToUnknown() throws {
        let data = Data("{\"type\": \"location\"}".utf8)
        switch try InboundMessage.decode(data) {
        case .unknown(let type):
            XCTAssertEqual(type, "location")
        default:
            XCTFail("expected .unknown")
        }
    }

    func testInvalidEventPayloadThrowsTransportErrorInvalidEvent() throws {
        var event = try FixturePaths.firstValidEventObject()
        event["ttl_ms"] = -1 // out of range: ttl_ms >= 1 (task-3-brief.md D6 mirrors the Python test)
        let frame: [String: Any] = ["type": "event", "event": event]
        let data = try JSONSerialization.data(withJSONObject: frame)

        XCTAssertThrowsError(try InboundMessage.decode(data)) { error in
            guard case .invalidEvent(.outOfRange(let field)) = error as? TransportError else {
                XCTFail("expected .invalidEvent(.outOfRange), got \(error)")
                return
            }
            XCTAssertEqual(field, "ttl_ms")
        }
    }

    func testMalformedJSONThrowsTransportErrorMalformed() {
        let data = Data("not json at all {".utf8)
        XCTAssertThrowsError(try InboundMessage.decode(data)) { error in
            XCTAssertEqual(error as? TransportError, .malformed)
        }
    }
}
