import CaneContracts
import XCTest

@testable import CaneFeedback

/// Bearing sweep, from the records the gauntlet already keeps. No new state,
/// and no gyro: there is no yaw source on this device (§ milestone-2 plan).
final class BearingRateTests: XCTestCase {
    private let t0 = Make.t0

    private func record(
        trackId: String, bearingDeg: Double, atOffsetMs offset: Int64
    ) throws -> AcceptedRecord {
        AcceptedRecord(
            event: try Make.event(
                source: .x4Cloud,
                trackId: trackId,
                tCaptureMs: t0 + offset,
                cls: .car,
                bearingDeg: bearingDeg,
                rangeBand: .near
            ),
            arrivalMs: t0 + offset
        )
    }

    private func key(_ trackId: String) throws -> FeedbackKey {
        FeedbackKey(try Make.event(source: .x4Cloud, trackId: trackId, cls: .car))
    }

    // MARK: - One track

    func testTwoSamplesGiveDegreesPerSecond() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 10, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 20, atOffsetMs: 500),
        ]

        let rate = try XCTUnwrap(BearingRate.forTrack(try key("a"), in: records))

        XCTAssertEqual(rate, 20.0, accuracy: 0.001)
    }

    /// The X4's most interesting objects live at the antimeridian, so an
    /// unwrapped subtraction here would report -700 deg/s for a car drifting
    /// 10 degrees across the back.
    func testTheSweepWrapsAtTheAntimeridian() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 175, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: -175, atOffsetMs: 500),
        ]

        let rate = try XCTUnwrap(BearingRate.forTrack(try key("a"), in: records))

        XCTAssertEqual(rate, 20.0, accuracy: 0.001)
    }

    func testASweepToTheLeftIsNegative() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 20, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 10, atOffsetMs: 500),
        ]

        let rate = try XCTUnwrap(BearingRate.forTrack(try key("a"), in: records))

        XCTAssertEqual(rate, -20.0, accuracy: 0.001)
    }

    /// One sighting is a position, not a rate. Reject rather than guess --
    /// the same rule `motion.py` applies to range.
    func testASingleSampleHasNoRate() throws {
        let records = [try record(trackId: "a", bearingDeg: 10, atOffsetMs: 0)]

        XCTAssertNil(BearingRate.forTrack(try key("a"), in: records))
    }

    func testTwoSamplesAtTheSameCaptureStampHaveNoRate() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 10, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 40, atOffsetMs: 0),
        ]

        XCTAssertNil(BearingRate.forTrack(try key("a"), in: records))
    }

    /// One degree of box-centre jitter across 20 ms reads as 50 deg/s, which
    /// is over the gate's threshold. Nothing sweeps meaningfully inside one
    /// frame, so an interval this short is noise and must not produce a rate.
    func testAnIntervalTooShortToTrustHasNoRate() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 170, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 171, atOffsetMs: 20),
        ]

        XCTAssertNil(BearingRate.forTrack(try key("a"), in: records))
    }

    func testTheShortestTrustworthyIntervalStillGivesARate() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(
                trackId: "a", bearingDeg: 10,
                atOffsetMs: Int64(BearingRate.minIntervalMs)
            ),
        ]

        XCTAssertNotNil(BearingRate.forTrack(try key("a"), in: records))
    }

    /// After a dropout the two surviving samples can straddle a long gap, and
    /// their average says nothing about what the object is doing now.
    func testAnIntervalLongerThanRetentionHasNoRate() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(
                trackId: "a", bearingDeg: 90,
                atOffsetMs: Int64(BearingRate.maxIntervalMs) + 1
            ),
        ]

        XCTAssertNil(BearingRate.forTrack(try key("a"), in: records))
    }

    func testOnlyTheTwoMostRecentSamplesAreUsed() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 90, atOffsetMs: 500),
            try record(trackId: "a", bearingDeg: 100, atOffsetMs: 1_000),
        ]

        let rate = try XCTUnwrap(BearingRate.forTrack(try key("a"), in: records))

        XCTAssertEqual(rate, 20.0, accuracy: 0.001)
    }

    func testOtherTracksDoNotContributeToThisTracksRate() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 10, atOffsetMs: 0),
            try record(trackId: "b", bearingDeg: 170, atOffsetMs: 250),
            try record(trackId: "a", bearingDeg: 20, atOffsetMs: 500),
        ]

        let rate = try XCTUnwrap(BearingRate.forTrack(try key("a"), in: records))

        XCTAssertEqual(rate, 20.0, accuracy: 0.001)
    }

    // MARK: - Common mode (the stand-in for a gyro)

    func testCommonModeIsTheMedianOfTheOtherTracks() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 60, atOffsetMs: 500),
            try record(trackId: "b", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "b", bearingDeg: 5, atOffsetMs: 500),   // 10 deg/s
            try record(trackId: "c", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "c", bearingDeg: 10, atOffsetMs: 500),  // 20 deg/s
        ]

        let common = try XCTUnwrap(BearingRate.commonMode(excluding: try key("a"), in: records))

        XCTAssertEqual(common, 15.0, accuracy: 0.001)
    }

    /// One other track might itself be a moving car. A median needs two.
    func testCommonModeNeedsTwoOtherTracks() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 60, atOffsetMs: 500),
            try record(trackId: "b", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "b", bearingDeg: 5, atOffsetMs: 500),
        ]

        XCTAssertNil(BearingRate.commonMode(excluding: try key("a"), in: records))
    }

    // MARK: - Corrected

    /// A torso turn moves every bearing together. Subtracting the median
    /// cancels it exactly, which is the whole point of having no gyro.
    func testCorrectedSubtractsTheCommonMode() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 60, atOffsetMs: 500),   // 120 deg/s raw
            try record(trackId: "b", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "b", bearingDeg: 50, atOffsetMs: 500),   // 100
            try record(trackId: "c", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "c", bearingDeg: 50, atOffsetMs: 500),   // 100
        ]

        let corrected = try XCTUnwrap(BearingRate.corrected(try key("a"), in: records))

        XCTAssertEqual(corrected, 20.0, accuracy: 0.001)
    }

    /// A lone car on an empty street is the common case, and it must still
    /// get a rate -- just an uncorrected one, judged against a threshold set
    /// high enough to absorb walking.
    func testCorrectedFallsBackToTheRawRateWithNoCommonMode() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "a", bearingDeg: 60, atOffsetMs: 500),
        ]

        let corrected = try XCTUnwrap(BearingRate.corrected(try key("a"), in: records))

        XCTAssertEqual(corrected, 120.0, accuracy: 0.001)
    }

    func testCorrectedIsNilWhenTheTrackItselfHasNoRate() throws {
        let records = [
            try record(trackId: "a", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "b", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "b", bearingDeg: 50, atOffsetMs: 500),
            try record(trackId: "c", bearingDeg: 0, atOffsetMs: 0),
            try record(trackId: "c", bearingDeg: 50, atOffsetMs: 500),
        ]

        XCTAssertNil(BearingRate.corrected(try key("a"), in: records))
    }
}
