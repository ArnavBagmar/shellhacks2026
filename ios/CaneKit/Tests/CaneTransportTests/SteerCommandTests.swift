import Foundation
import XCTest

@testable import CaneTransport

/// Step 21's `steer` frame against the same golden fixture the Python suite
/// runs: every valid case decodes to the same nine values, every invalid one
/// throws, and an unknown `type` is still ignored rather than thrown.
/// Nothing here acts on a steering value -- that is step 24.
final class SteerCommandTests: XCTestCase {
    func testEveryFixtureCaseDecodesOrIsRejectedLikePython() throws {
        let cases = try FixturePaths.steerCases()
        XCTAssertGreaterThan(cases.count, 15)
        for item in cases {
            let data = try JSONSerialization.data(withJSONObject: item.frame)
            do {
                guard case .steer = try InboundMessage.decode(data) else {
                    XCTFail("\(item.name): decoded to something other than .steer")
                    continue
                }
                XCTAssertTrue(item.valid, "\(item.name) should have been rejected")
            } catch {
                XCTAssertFalse(item.valid, "\(item.name) should decode, threw \(error)")
            }
        }
    }

    /// The fixture is the wire, not a paraphrase of it: what Swift read back
    /// out must be the same object Python re-encodes byte-identically.
    func testEveryValidCaseReEncodesToTheFixtureFrame() throws {
        for item in try FixturePaths.steerCases() where item.valid {
            let command = try SteerCommand.decode(JSONSerialization.data(withJSONObject: item.frame))
            let reencoded = try XCTUnwrap(
                JSONSerialization.jsonObject(with: try command.encoded()) as? [String: Any]
            )
            XCTAssertEqual(
                NSDictionary(dictionary: reencoded),
                NSDictionary(dictionary: item.frame),
                "\(item.name) did not re-encode to its own fixture frame"
            )
        }
    }

    func testTheCanonicalFrameDecodesEveryField() throws {
        let frame = try XCTUnwrap(FixturePaths.steerCases().first { $0.name == "steer_right_anchored" }).frame
        guard case .steer(let command) = try InboundMessage.decode(JSONSerialization.data(withJSONObject: frame)) else {
            return XCTFail("expected .steer")
        }
        XCTAssertEqual(command.tCaptureMs, 1_790_000_000_000)
        XCTAssertEqual(command.ttlMs, 400)
        XCTAssertEqual(command.steer, 0.42)
        XCTAssertEqual(command.proximity, 0.61)
        XCTAssertEqual(command.gapBearingDeg, 37.5)
        XCTAssertFalse(command.blocked)
        XCTAssertTrue(command.proximityAnchored)
        XCTAssertEqual(command.source, .x4Cloud)
    }

    /// Sec 3 pins the failsafe window at 400 ms; step 24 gates staleness on
    /// this constant, so both languages must name the same number.
    func testTheFailsafeTTLMatchesThePlan() {
        XCTAssertEqual(SteerLimits.ttlMs, 400)
    }

    func testOutOfRangeValuesCannotBeBuilt() {
        let bad: [(String, () throws -> SteerCommand)] = [
            ("steer", { try Self.command(steer: 1.5) }),
            ("steer", { try Self.command(steer: -1.5) }),
            ("steer", { try Self.command(steer: .nan) }),
            ("proximity", { try Self.command(proximity: -0.01) }),
            ("proximity", { try Self.command(proximity: 1.01) }),
            ("gap_bearing_deg", { try Self.command(gapBearingDeg: -180) }),
            ("gap_bearing_deg", { try Self.command(gapBearingDeg: 180.1) }),
            ("gap_bearing_deg", { try Self.command(gapBearingDeg: .infinity) }),
            ("ttl_ms", { try Self.command(ttlMs: 0) }),
            ("ttl_ms", { try Self.command(ttlMs: 15_001) }),
            ("t_capture_ms", { try Self.command(tCaptureMs: 1_000_000_000_000) }),
        ]
        for (field, build) in bad {
            XCTAssertThrowsError(try build(), "\(field) should have been rejected") { error in
                XCTAssertEqual(error as? TransportError, .invalidFrame(field))
            }
        }
    }

    func testTheBoundsAreInclusiveWhereTheContractSaysTheyAre() throws {
        XCTAssertEqual(try Self.command(steer: 1.0).steer, 1.0)
        XCTAssertEqual(try Self.command(steer: -1.0).steer, -1.0)
        XCTAssertEqual(try Self.command(proximity: 0.0).proximity, 0.0)
        XCTAssertEqual(try Self.command(proximity: 1.0).proximity, 1.0)
        XCTAssertEqual(try Self.command(gapBearingDeg: 180).gapBearingDeg, 180)
    }

    func testABoolIsNotANumber() throws {
        for key in ["ttl_ms", "steer", "proximity", "gap_bearing_deg"] {
            var frame = try XCTUnwrap(FixturePaths.steerCases().first { $0.name == "steer_right_anchored" }).frame
            frame[key] = true
            XCTAssertThrowsError(
                try SteerCommand.decode(JSONSerialization.data(withJSONObject: frame)),
                "\(key) accepted a bool"
            )
        }
    }

    func testANumberIsNotABool() throws {
        for key in ["blocked", "proximity_anchored"] {
            var frame = try XCTUnwrap(FixturePaths.steerCases().first { $0.name == "steer_right_anchored" }).frame
            frame[key] = 1
            XCTAssertThrowsError(
                try SteerCommand.decode(JSONSerialization.data(withJSONObject: frame)),
                "\(key) accepted a number"
            )
        }
    }

    /// Plan Sec 4.1: this is what lets an old phone survive a new laptop.
    /// `steer` is exactly the frame that property was written for, so pin it
    /// with a type this build has never heard of.
    func testAnUnknownFrameTypeIsStillIgnoredNotThrown() throws {
        let data = Data("{\"type\": \"telemetry\", \"cpu_pct\": 41, \"steer\": 1.5}".utf8)
        switch try InboundMessage.decode(data) {
        case .unknown(let type):
            XCTAssertEqual(type, "telemetry")
        default:
            XCTFail("expected .unknown")
        }
    }

    private static func command(
        tCaptureMs: Int64 = 1_790_000_000_000,
        ttlMs: Int = 400,
        steer: Double = 0.0,
        proximity: Double = 0.5,
        gapBearingDeg: Double = 0.0,
        blocked: Bool = false,
        proximityAnchored: Bool = true,
        source: SteerSource = .x4Cloud
    ) throws -> SteerCommand {
        try SteerCommand(
            tCaptureMs: tCaptureMs,
            ttlMs: ttlMs,
            steer: steer,
            proximity: proximity,
            gapBearingDeg: gapBearingDeg,
            blocked: blocked,
            proximityAnchored: proximityAnchored,
            source: source
        )
    }
}
