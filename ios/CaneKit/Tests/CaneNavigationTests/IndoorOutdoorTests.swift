import XCTest

@testable import CaneNavigation

/// Deciding indoors from outdoors with nothing but GPS accuracy.
///
/// The cases that matter are the ones that must *not* flip the mode: a glass
/// lobby where accuracy hovers, a single bad reading under a canopy, and a
/// covered walkway. A mode that oscillates would hand the wearer between two
/// navigation voices mid-walk, which is worse than being wrong consistently.
final class IndoorOutdoorTests: XCTestCase {
    private let start: Int64 = 1_790_000_000_000

    private func fix(accuracyM: Double, atMs: Int64) -> LocationFix {
        LocationFix(
            latLng: LatLng(latitude: 25.7617, longitude: -80.1918),
            horizontalAccuracyM: accuracyM,
            speedMps: 1.2,
            timestampMs: atMs
        )
    }

    // MARK: - Before anything is known

    func testNothingIsClaimedBeforeTheFirstReading() {
        XCTAssertNil(IndoorOutdoorEstimator().setting)
    }

    func testTheFirstSharpFixIsAdoptedAtOnceWithNoDwell() {
        let e = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))

        XCTAssertEqual(e.setting, .outdoors)
    }

    func testTheFirstVagueFixIsAlsoAdoptedAtOnce() {
        let e = IndoorOutdoorEstimator().seeing(fix(accuracyM: 80, atMs: start))

        XCTAssertEqual(e.setting, .indoors)
    }

    func testNoFixAtAllReadsAsIndoors() {
        // Location denied, or the walk started inside. Either way the safe
        // reading is the engine that does not need a fix.
        let e = IndoorOutdoorEstimator().seeingNothing(atMs: start)

        XCTAssertEqual(e.setting, .indoors)
    }

    // MARK: - The dwell

    func testGoingInsideDoesNotFlipUntilTheVerdictHolds() {
        var e = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))
        XCTAssertEqual(e.setting, .outdoors)

        e = e.seeing(fix(accuracyM: 90, atMs: start + 1_000))
        XCTAssertEqual(e.setting, .outdoors, "one bad reading must not flip the mode")

        e = e.seeing(fix(accuracyM: 90, atMs: start + 4_000))
        XCTAssertEqual(e.setting, .outdoors, "still inside the dwell")

        e = e.seeing(fix(accuracyM: 90, atMs: start + 9_000))
        XCTAssertEqual(e.setting, .indoors, "the verdict held past the dwell")
    }

    func testAHalfFormedChangeIsForgottenWhenTheVerdictGoesBack() {
        var e = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))
        e = e.seeing(fix(accuracyM: 90, atMs: start + 1_000))  // starts to look indoors
        e = e.seeing(fix(accuracyM: 6, atMs: start + 2_000))   // ...no, still outside
        e = e.seeing(fix(accuracyM: 90, atMs: start + 3_000))  // begins again from here

        e = e.seeing(fix(accuracyM: 90, atMs: start + 9_000))
        XCTAssertEqual(e.setting, .outdoors, "the dwell restarted, so 6 s is not enough")

        e = e.seeing(fix(accuracyM: 90, atMs: start + 11_500))
        XCTAssertEqual(e.setting, .indoors)
    }

    func testComingBackOutsideFlipsBackOnTheSameTerms() {
        var e = IndoorOutdoorEstimator(setting: .indoors)

        e = e.seeing(fix(accuracyM: 4, atMs: start))
        XCTAssertEqual(e.setting, .indoors, "not yet -- one sharp fix is a doorway, not the street")

        e = e.seeing(fix(accuracyM: 4, atMs: start + 8_500))
        XCTAssertEqual(e.setting, .outdoors)
    }

    // MARK: - The dead band

    func testAFixInTheDeadBandChangesNothing() {
        let outside = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))

        // 27 m is neither a sky view nor clearly inferred: a covered walkway.
        var e = outside.seeing(fix(accuracyM: 27, atMs: start + 1_000))
        e = e.seeing(fix(accuracyM: 27, atMs: start + 20_000))

        XCTAssertEqual(e.setting, .outdoors, "the dead band must never flip anything")
    }

    func testTheDeadBandAlsoCancelsAPendingChange() {
        var e = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))
        e = e.seeing(fix(accuracyM: 90, atMs: start + 1_000))  // starts to look indoors
        e = e.seeing(fix(accuracyM: 27, atMs: start + 2_000))  // undecided: forget it
        e = e.seeing(fix(accuracyM: 90, atMs: start + 8_000))

        XCTAssertEqual(e.setting, .outdoors, "the undecided reading should have restarted the dwell")
    }

    // MARK: - Silence

    func testGpsFallingSilentEventuallyReadsAsIndoors() {
        var e = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))

        e = e.seeingNothing(atMs: start + 5_000)
        XCTAssertEqual(e.setting, .outdoors, "a 5 s gap is just a slow fix")

        e = e.seeingNothing(atMs: start + 16_000)
        XCTAssertEqual(e.setting, .outdoors, "silence still has to clear the dwell")

        e = e.seeingNothing(atMs: start + 25_000)
        XCTAssertEqual(e.setting, .indoors)
    }

    func testAFreshFixKeepsSilenceFromCountingAgainstIt() {
        var e = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))
        for step in stride(from: Int64(2_000), through: 40_000, by: 2_000) {
            e = e.seeingNothing(atMs: start + step)
            e = e.seeing(fix(accuracyM: 5, atMs: start + step))
        }

        XCTAssertEqual(e.setting, .outdoors)
    }

    // MARK: - Policy

    func testThePolicyThresholdsAreHonoured() {
        let policy = IndoorOutdoorEstimator.Policy(
            outdoorAccuracyM: 10, indoorAccuracyM: 12, dwellMs: 1_000, staleFixMs: 1_000
        )
        var e = IndoorOutdoorEstimator(policy: policy).seeing(fix(accuracyM: 9, atMs: start))
        XCTAssertEqual(e.setting, .outdoors)

        e = e.seeing(fix(accuracyM: 13, atMs: start + 500))
        XCTAssertEqual(e.setting, .outdoors)
        e = e.seeing(fix(accuracyM: 13, atMs: start + 1_600))
        XCTAssertEqual(e.setting, .indoors)
    }

    func testReadingAFixNeverMutatesTheEstimatorItWasReadFrom() {
        let outside = IndoorOutdoorEstimator().seeing(fix(accuracyM: 5, atMs: start))

        _ = outside.seeing(fix(accuracyM: 90, atMs: start + 1_000))
        _ = outside.seeing(fix(accuracyM: 90, atMs: start + 20_000))

        XCTAssertEqual(outside.setting, .outdoors)
    }
}
