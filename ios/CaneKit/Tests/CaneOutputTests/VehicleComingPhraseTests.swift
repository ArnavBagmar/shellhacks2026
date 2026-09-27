import CaneContracts
import CaneFeedback
import XCTest

@testable import CaneOutput

/// "We are about to cross a crosswalk and a car is coming: STOP, CAR IS
/// COMING" (2026-09-26).
final class VehicleComingPhraseTests: XCTestCase {
    private func said(_ cls: ObjectClass, bearing: Double, closing: Double?) throws -> String? {
        let a = try Make.announcement(level: .immediate, cls: cls, bearingDeg: bearing, band: .mid, closingMps: closing)
        return HazardPhrasing.phrase(for: a)?.text
    }

    func testTheWholeWarningFromEachSide() throws {
        XCTAssertEqual(try said(.car, bearing: 0, closing: 5), "Stop! Car coming ahead.")
        XCTAssertEqual(try said(.car, bearing: -80, closing: 5), "Stop! Car coming from your left.")
        XCTAssertEqual(try said(.car, bearing: 80, closing: 5), "Stop! Car coming from your right.")
        XCTAssertEqual(try said(.bus, bearing: 175, closing: 5), "Bus coming behind you!")
    }

    func testItFitsTheSixWordBudget() throws {
        for bearing in stride(from: -165.0, through: 180.0, by: 15) {
            let words = try XCTUnwrap(try said(.car, bearing: bearing, closing: 3))
            XCTAssertLessThanOrEqual(SpeechPhrase.wordCount(words), SpeechPhrase.maxSpokenWords, words)
        }
    }

    func testANonApproachingCarKeepsTheOrdinaryWords() throws {
        XCTAssertEqual(try said(.car, bearing: 0, closing: 0), "Stop!")
    }

    func testAComingCarIsSpokenHigherPitched() throws {
        let a = try Make.announcement(level: .immediate, cls: .car, bearingDeg: -80, band: .near, closingMps: 4)
        let calm = try Make.announcement(level: .actSoon, cls: .car, bearingDeg: -80, band: .near, closingMps: 4)
        XCTAssertGreaterThan(try XCTUnwrap(HazardPhrasing.phrase(for: a)).pitchMultiplier,
                             try XCTUnwrap(HazardPhrasing.phrase(for: calm)).pitchMultiplier)
    }
}
