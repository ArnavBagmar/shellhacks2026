import XCTest

@testable import CaneFeedback

/// A2: every bearing comparison in the gauntlet goes through `AngularMath`.
/// The wrap at +/-180 is the difference between announcing an overtaking
/// cyclist once and announcing it twice.
final class AngularMathTests: XCTestCase {
    func testWrapAcrossTheAntimeridian() {
        XCTAssertEqual(AngularMath.delta(179.0, -178.0), -3.0, accuracy: 1e-9)
        XCTAssertEqual(AngularMath.delta(-178.0, 179.0), 3.0, accuracy: 1e-9)
        XCTAssertEqual(AngularMath.absDelta(179.0, -178.0), 3.0, accuracy: 1e-9)
    }

    func testSmallDifferencesAreUnchanged() {
        XCTAssertEqual(AngularMath.delta(10.0, 5.0), 5.0, accuracy: 1e-9)
        XCTAssertEqual(AngularMath.delta(5.0, 10.0), -5.0, accuracy: 1e-9)
        XCTAssertEqual(AngularMath.delta(0.0, 0.0), 0.0, accuracy: 1e-9)
    }

    /// Exact opposition is reported as +180, the inclusive end of the
    /// contract's `(-180, 180]` bearing interval, never as -180.
    func testExactOppositionIsPositive180() {
        XCTAssertEqual(AngularMath.delta(0.0, 180.0), 180.0, accuracy: 1e-9)
        XCTAssertEqual(AngularMath.delta(90.0, -90.0), 180.0, accuracy: 1e-9)
        XCTAssertEqual(AngularMath.delta(-90.0, 90.0), 180.0, accuracy: 1e-9)
    }

    /// Property: the result always lands in `(-180, 180]`, and the magnitude
    /// is symmetric, for every whole-degree pair in the contract's interval.
    func testResultAlwaysInsideTheContractInterval() {
        var bearingA = -179.0
        while bearingA <= 180.0 {
            var bearingB = -179.0
            while bearingB <= 180.0 {
                let d = AngularMath.delta(bearingA, bearingB)
                XCTAssertTrue(d > -180.0 && d <= 180.0, "delta(\(bearingA), \(bearingB)) = \(d)")
                XCTAssertEqual(
                    AngularMath.absDelta(bearingA, bearingB),
                    AngularMath.absDelta(bearingB, bearingA),
                    accuracy: 1e-9
                )
                bearingB += 1.0
            }
            bearingA += 1.0
        }
    }
}
