import XCTest

@testable import CaneNavigation

/// The chest-mount compass (FIU, 2026-09-27): an upright phone's compass
/// points out of its back, which on a chest faces the wearer.
final class HeadingCalibratorTests: XCTestCase {
    private func fix(_ t: Int64, course: Double?, speed: Double? = 1.3, accuracy: Double? = 10) -> LocationFix {
        LocationFix(latLng: LatLng(latitude: 25.7563, longitude: -80.3728), horizontalAccuracyM: 5, speedMps: speed,
                    timestampMs: t, courseDeg: course, courseAccuracyDeg: accuracy)
    }

    private func compass(_ degrees: Double, _ t: Int64 = 0) -> HeadingSample {
        HeadingSample(trueHeadingDeg: degrees, accuracyDeg: 10, timestampMs: t)
    }

    func testAChestMountedCompassIsLearnedAsHalfATurnOff() throws {
        var c = HeadingCalibrator()
        // Walking north; the compass says south (the phone's back faces the wearer).
        for t in 0..<HeadingCalibrator.samplesNeeded {
            c.observe(fix(Int64(t) * 1_000, course: 2), compass: compass(182))
        }
        XCTAssertEqual(abs(try XCTUnwrap(c.offsetDeg)), 180, accuracy: 1)
        // Standing still later, turned to face east: the corrected compass says east.
        let facing = try XCTUnwrap(c.heading(for: fix(9_000, course: nil, speed: 0), compass: compass(270)))
        XCTAssertEqual(facing.trueHeadingDeg, 90, accuracy: 1)
    }

    func testAPhoneInTheHandNeedsNoCorrection() throws {
        var c = HeadingCalibrator()
        for t in 0..<HeadingCalibrator.samplesNeeded { c.observe(fix(Int64(t), course: 45), compass: compass(47)) }
        XCTAssertEqual(try XCTUnwrap(c.offsetDeg), -2, accuracy: 1)
    }

    func testBeforeItIsLearnedOnlyAMovingGPSCourseCounts() {
        let c = HeadingCalibrator()
        XCTAssertEqual(c.heading(for: fix(0, course: 30), compass: compass(210))?.trueHeadingDeg, 30)
        XCTAssertNil(c.heading(for: fix(0, course: 30, speed: 0.2), compass: compass(210)), "standing still: no heading")
        XCTAssertNil(c.heading(for: fix(0, course: 30, accuracy: 90), compass: compass(210)), "a vague course is not one")
    }

    func testScatteredSamplesAreNotTrusted() {
        var c = HeadingCalibrator()
        for (t, diff) in [0.0, 120, 240, 60, 300, 180].enumerated() {
            c.observe(fix(Int64(t), course: 0), compass: compass(diff))
        }
        XCTAssertNil(c.offsetDeg)
    }
}
