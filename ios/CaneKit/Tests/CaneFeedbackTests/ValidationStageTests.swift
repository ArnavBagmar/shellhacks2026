import CaneContracts
import XCTest

@testable import CaneFeedback

/// Gate 1 (A1.1): decode + future-dating. Validation *is*
/// `AwarenessEvent.decode` -- there is no second validator -- so the golden
/// fixture drives this stage directly, malformed payloads included.
final class ValidationStageTests: XCTestCase {
    /// Well past every fixture timestamp, so nothing here is future-dated.
    private let now = Make.t0 + 60_000

    func testEveryValidFixtureCasePassesGateOne() throws {
        let (cases, _) = try FeedbackFixturePaths.load()
        XCTAssertFalse(cases.isEmpty)
        for goldenCase in cases where goldenCase.expectValid {
            switch ValidationStage.validate(goldenCase.json, now: now) {
            case .accepted:
                break
            case let other:
                XCTFail("\(goldenCase.name): expected acceptance, got \(other)")
            }
        }
    }

    func testEveryInvalidFixtureCaseIsRejectedNotCrashed() throws {
        let (cases, _) = try FeedbackFixturePaths.load()
        for goldenCase in cases where !goldenCase.expectValid {
            switch ValidationStage.validate(goldenCase.json, now: now) {
            case .invalid:
                break
            case let other:
                XCTFail("\(goldenCase.name): expected rejection, got \(other)")
            }
        }
    }

    func testEveryMalformedPayloadIsRejectedNotCrashed() throws {
        let (_, malformed) = try FeedbackFixturePaths.load()
        XCTAssertFalse(malformed.isEmpty)
        for payload in malformed {
            switch ValidationStage.validate(payload, now: now) {
            case .invalid:
                break
            case let other:
                XCTFail("malformed payload should be rejected, got \(other)")
            }
        }
    }

    func testFutureDatedBeyondToleranceIsItsOwnOutcome() throws {
        let event = try Make.event(tCaptureMs: Make.t0)
        let tooEarly = Make.t0 - ValidationStage.futureToleranceMs - 1
        XCTAssertEqual(ValidationStage.validate(event, now: tooEarly), .futureDated)
    }

    /// Strict `>`: exactly at the tolerance the event is still accepted, so a
    /// 250 ms clock offset does not silence the phone.
    func testFutureDatedExactlyAtToleranceIsAccepted() throws {
        let event = try Make.event(tCaptureMs: Make.t0)
        let atTolerance = Make.t0 - ValidationStage.futureToleranceMs
        XCTAssertEqual(ValidationStage.validate(event, now: atTolerance), .accepted(event))
    }

    func testFutureDatingAlsoAppliesToTheDataEntryPoint() throws {
        let event = try Make.event(tCaptureMs: Make.t0)
        let data = try event.encoded()
        let tooEarly = Make.t0 - ValidationStage.futureToleranceMs - 1
        XCTAssertEqual(ValidationStage.validate(data, now: tooEarly), .futureDated)
    }
}
