import Foundation
import XCTest

@testable import CaneTransport

/// Step 29's frame. A bad one from the laptop is rejected, never trapped: the
/// phone must not go down mid-walk because the cloud sent nonsense.
final class GroundCueTests: XCTestCase {
    private func json(_ overrides: [String: Any] = [:]) -> Data {
        var object: [String: Any] = [
            "type": "ground",
            "t_capture_ms": 1_790_000_000_000,
            "ttl_ms": 1000,
            "bearing_deg": -11.25,
            "span_deg": 3.75,
            "drop_ratio": 2.4,
            "confidence": 0.64,
            "source": "x4_cloud",
        ]
        for (key, value) in overrides { object[key] = value }
        return try! JSONSerialization.data(withJSONObject: object)
    }

    func testAWellFormedFrameDecodes() throws {
        let cue = try GroundCue.decode(json())

        XCTAssertEqual(cue.bearingDeg, -11.25)
        XCTAssertEqual(cue.spanDeg, 3.75)
        XCTAssertEqual(cue.dropRatio, 2.4)
        XCTAssertEqual(cue.source, .x4Cloud)
    }

    /// The ratio is the only magnitude on the frame, and it is not a depth.
    func testTheFrameCarriesNoDepth() throws {
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: json()) as? [String: Any]
        )

        XCTAssertNil(object["depth_m"])
        XCTAssertNil(object["drop_m"])
    }

    func testAnUnknownKeyIsRejected() {
        XCTAssertThrowsError(try GroundCue.decode(json(["drop_m": 0.15])))
    }

    /// atan2 returns -180 for directly behind; a producer that forgets to
    /// normalize must be caught here, not silently pointed at the one bearing
    /// the contract excludes.
    func testExactlyMinusOneEightyIsRejected() {
        XCTAssertThrowsError(try GroundCue.decode(json(["bearing_deg": -180.0])))
    }

    func testARatioAtOrBelowOneIsRejected() {
        XCTAssertThrowsError(try GroundCue.decode(json(["drop_ratio": 1.0])))
        XCTAssertThrowsError(try GroundCue.decode(json(["drop_ratio": 0.5])))
    }

    func testAConfidenceOutsideTheUnitIntervalIsRejected() {
        XCTAssertThrowsError(try GroundCue.decode(json(["confidence": 1.5])))
    }

    func testANonPositiveSpanIsRejected() {
        XCTAssertThrowsError(try GroundCue.decode(json(["span_deg": 0.0])))
    }

    func testAnUnknownSourceIsRejected() {
        XCTAssertThrowsError(try GroundCue.decode(json(["source": "phone"])))
    }

    func testTheEnvelopeRoutesAGroundFrame() throws {
        let message = try InboundMessage.decode(json())

        guard case .ground(let cue) = message else {
            return XCTFail("expected .ground, got \(message)")
        }
        XCTAssertEqual(cue.dropRatio, 2.4)
    }
}
