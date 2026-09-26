import Foundation
import XCTest

@testable import CaneTransport

/// `PhoneClockResponder` is the only clock-sync code Swift has (D1): it
/// answers a laptop `ping` with a `pong`, and does nothing else.
final class PhoneClockResponderTests: XCTestCase {
    private func pingData(t1: Int64) -> Data {
        Data("{\"type\": \"ping\", \"t1\": \(t1)}".utf8)
    }

    private func decodePong(_ data: Data) throws -> [String: Any] {
        let object = try JSONSerialization.jsonObject(with: data)
        guard let dict = object as? [String: Any] else {
            XCTFail("pong was not a JSON object")
            return [:]
        }
        return dict
    }

    func testPongEchoesT1() throws {
        let data = try PhoneClockResponder.pong(forPing: pingData(t1: 1_790_000_000_000), now: { 1_790_000_000_500 })
        let pong = try decodePong(data)
        XCTAssertEqual(pong["type"] as? String, "pong")
        XCTAssertEqual((pong["t1"] as? NSNumber)?.int64Value, 1_790_000_000_000)
    }

    func testT2IsLessThanOrEqualToT3() throws {
        var clock: Int64 = 1_790_000_000_000
        let data = try PhoneClockResponder.pong(forPing: pingData(t1: 1_790_000_000_000)) {
            defer { clock += 1 }
            return clock
        }
        let pong = try decodePong(data)
        let t2 = try XCTUnwrap((pong["t2"] as? NSNumber)?.int64Value)
        let t3 = try XCTUnwrap((pong["t3"] as? NSNumber)?.int64Value)
        XCTAssertLessThanOrEqual(t2, t3)
    }

    func testTimestampsAreIntegerMilliseconds() throws {
        let data = try PhoneClockResponder.pong(forPing: pingData(t1: 1_790_000_000_000), now: { 1_790_000_000_777 })
        let pong = try decodePong(data)
        XCTAssertEqual((pong["t2"] as? NSNumber)?.int64Value, 1_790_000_000_777)
        XCTAssertEqual((pong["t3"] as? NSNumber)?.int64Value, 1_790_000_000_777)
    }

    func testMalformedPingThrowsTransportErrorMalformed() {
        let notJSON = Data("not json at all {".utf8)
        XCTAssertThrowsError(try PhoneClockResponder.pong(forPing: notJSON, now: { 0 })) { error in
            XCTAssertEqual(error as? TransportError, .malformed)
        }
    }

    func testWrongTypeThrowsTransportErrorMalformed() {
        let notAPing = Data("{\"type\": \"pong\", \"t1\": 1, \"t2\": 2, \"t3\": 3}".utf8)
        XCTAssertThrowsError(try PhoneClockResponder.pong(forPing: notAPing, now: { 0 })) { error in
            XCTAssertEqual(error as? TransportError, .malformed)
        }
    }

    func testMissingT1ThrowsTransportErrorMalformed() {
        let missingT1 = Data("{\"type\": \"ping\"}".utf8)
        XCTAssertThrowsError(try PhoneClockResponder.pong(forPing: missingT1, now: { 0 })) { error in
            XCTAssertEqual(error as? TransportError, .malformed)
        }
    }

    func testNonIntegerT1ThrowsTransportErrorMalformed() {
        let fractionalT1 = Data("{\"type\": \"ping\", \"t1\": 1.5}".utf8)
        XCTAssertThrowsError(try PhoneClockResponder.pong(forPing: fractionalT1, now: { 0 })) { error in
            XCTAssertEqual(error as? TransportError, .malformed)
        }
    }
}
