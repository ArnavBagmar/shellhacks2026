import CaneContracts
import XCTest

@testable import CaneFeedback

/// Gates 3 and 4 (A1.3-4, A3-A5). Bearing + time proximity, never data
/// association: the phone and the X4 have no shared track ids and never will.
final class DedupGateTests: XCTestCase {
    private let t0 = Make.t0

    private func record(_ event: AwarenessEvent, arrivalMs: Int64? = nil) -> AcceptedRecord {
        AcceptedRecord(event: event, arrivalMs: arrivalMs ?? Int64(event.tCaptureMs))
    }

    // MARK: - Cross-source preference (A4)

    func testPhoneWithHealthyMetricRangeWinsWhateverTheArrivalOrder() throws {
        let phone = try Make.phone(trackId: "p1", tCaptureMs: t0, cls: .person, bearingDeg: 12.0, rangeM: 3.0)
        let x4 = try Make.x4(trackId: "x1", tCaptureMs: t0 + 100, cls: .person, bearingDeg: 17.0, rangeBand: .near)

        // X4 seen first, phone arrives second: the phone proceeds and evicts.
        XCTAssertEqual(
            DedupGate.decide(phone, recent: [record(x4)]),
            .keep(evicting: [FeedbackKey(x4)])
        )
        // Phone seen first, X4 arrives second: the X4 is the loser.
        XCTAssertEqual(
            DedupGate.decide(x4, recent: [record(phone)]),
            .drop(reason: .crossSource)
        )
    }

    func testPhoneWithoutTrustworthyRangeLosesToTheX4() throws {
        let phoneFar = try Make.event(
            source: .phone, trackId: "p2", tCaptureMs: t0, cls: .person,
            bearingDeg: 20.0, rangeM: 22.0, rangeBand: .far
        )
        let x4 = try Make.x4(trackId: "x2", tCaptureMs: t0 + 100, cls: .person, bearingDeg: 20.0, rangeBand: .far)

        XCTAssertEqual(DedupGate.decide(phoneFar, recent: [record(x4)]), .drop(reason: .crossSource))
        XCTAssertEqual(DedupGate.decide(x4, recent: [record(phoneFar)]), .keep(evicting: [FeedbackKey(phoneFar)]))
    }

    func testPhoneWithNoRangeAtAllLosesToTheX4() throws {
        let phoneBandOnly = try Make.event(
            source: .phone, trackId: "p3", tCaptureMs: t0, cls: .person,
            bearingDeg: 5.0, rangeM: nil, rangeBand: .mid
        )
        let x4 = try Make.x4(trackId: "x3", tCaptureMs: t0, cls: .person, bearingDeg: 5.0, rangeBand: .mid)
        XCTAssertEqual(DedupGate.decide(phoneBandOnly, recent: [record(x4)]), .drop(reason: .crossSource))
    }

    /// `x4_local` outranks `x4_cloud`: same picture, shorter path.
    func testLocalX4OutranksCloudX4() throws {
        let cloud = try Make.x4(source: .x4Cloud, trackId: "c1", tCaptureMs: t0, cls: .cyclist, bearingDeg: 100.0)
        let local = try Make.x4(source: .x4Local, trackId: "l1", tCaptureMs: t0 + 50, cls: .cyclist, bearingDeg: 103.0)
        XCTAssertEqual(DedupGate.decide(local, recent: [record(cloud)]), .keep(evicting: [FeedbackKey(cloud)]))
        XCTAssertEqual(DedupGate.decide(cloud, recent: [record(local)]), .drop(reason: .crossSource))
    }

    // MARK: - Candidate conditions (A3, A4)

    func testEventsTwelveDegreesApartAreBothKept() throws {
        let phone = try Make.phone(trackId: "p4", tCaptureMs: t0, cls: .person, bearingDeg: 0.0, rangeM: 3.0)
        let x4 = try Make.x4(trackId: "x4a", tCaptureMs: t0, cls: .person, bearingDeg: 12.0, rangeBand: .near)
        XCTAssertEqual(DedupGate.decide(x4, recent: [record(phone)]), .keep(evicting: []))
    }

    func testEventsFourHundredMillisecondsApartAreBothKept() throws {
        let phone = try Make.phone(trackId: "p5", tCaptureMs: t0, cls: .person, bearingDeg: 0.0, rangeM: 3.0)
        let x4 = try Make.x4(trackId: "x5", tCaptureMs: t0 + 400, cls: .person, bearingDeg: 2.0, rangeBand: .near)
        XCTAssertEqual(DedupGate.decide(x4, recent: [record(phone)]), .keep(evicting: []))
    }

    /// A3: proximity is `t_capture` to `t_capture`. The X4's version of an
    /// object arrives hundreds of milliseconds after the phone's, so matching
    /// on arrival time would never fire.
    func testProximityUsesCaptureTimeNotArrivalTime() throws {
        let phone = try Make.phone(trackId: "p6", tCaptureMs: t0, cls: .person, bearingDeg: 8.0, rangeM: 4.0)
        let x4 = try Make.x4(trackId: "x6", tCaptureMs: t0 + 120, cls: .person, bearingDeg: 9.0, rangeBand: .near)
        let lateArrival = record(phone, arrivalMs: t0)
        XCTAssertEqual(
            DedupGate.decide(x4, recent: [lateArrival]),
            .drop(reason: .crossSource),
            "captures 120 ms apart are duplicates even though the arrivals are 450 ms apart"
        )
        XCTAssertTrue(
            RecentAccepted.pruned([lateArrival], now: t0 + 450).contains(lateArrival),
            "the phone's record must still be retained 450 ms later"
        )
    }

    /// A2 + A17: the antimeridian. 179 and -178 are 3 apart.
    func testBearingWrapIsHandledInDedup() throws {
        let cloud = try Make.x4(source: .x4Cloud, trackId: "c2", tCaptureMs: t0, cls: .cyclist, bearingDeg: 179.0)
        let local = try Make.x4(source: .x4Local, trackId: "l2", tCaptureMs: t0 + 80, cls: .cyclist, bearingDeg: -178.0)
        XCTAssertEqual(DedupGate.decide(local, recent: [record(cloud)]), .keep(evicting: [FeedbackKey(cloud)]))
    }

    func testDifferentClassesAreNotDuplicates() throws {
        let phone = try Make.phone(trackId: "p7", tCaptureMs: t0, cls: .person, bearingDeg: 0.0, rangeM: 3.0)
        let x4 = try Make.x4(trackId: "x7", tCaptureMs: t0, cls: .cyclist, bearingDeg: 1.0, rangeBand: .near)
        XCTAssertEqual(DedupGate.decide(x4, recent: [record(phone)]), .keep(evicting: []))
    }

    /// A4's directional exception: `unknown_obstacle` may lose, never win.
    func testClassifiedEventBeatsUnknownObstacleRegardlessOfSourceRank() throws {
        let phoneUnknown = try Make.phone(
            trackId: "p8", tCaptureMs: t0, cls: .unknownObstacle, bearingDeg: 4.0, rangeM: 3.0
        )
        let x4Classified = try Make.x4(trackId: "x8", tCaptureMs: t0, cls: .cyclist, bearingDeg: 4.0, rangeBand: .near)

        XCTAssertEqual(
            DedupGate.decide(phoneUnknown, recent: [record(x4Classified)]),
            .drop(reason: .crossSource),
            "a trusted phone unknown_obstacle still loses to a classified X4 event"
        )
        XCTAssertEqual(
            DedupGate.decide(x4Classified, recent: [record(phoneUnknown)]),
            .keep(evicting: [FeedbackKey(phoneUnknown)])
        )
    }

    // MARK: - Same-source seam rule (A5)

    func testSameSourceSeamDuplicateKeepsTheEarlierEvent() throws {
        let first = try Make.x4(trackId: "seam-a", tCaptureMs: t0, cls: .person, bearingDeg: 88.0)
        let second = try Make.x4(trackId: "seam-b", tCaptureMs: t0 + 60, cls: .person, bearingDeg: 93.0)
        XCTAssertEqual(DedupGate.decide(second, recent: [record(first)]), .drop(reason: .seam))
        XCTAssertEqual(DedupGate.decide(first, recent: [record(second)]), .keep(evicting: [FeedbackKey(second)]))
    }

    func testSameSourceSeamTieBreaksOnTrackId() throws {
        let a = try Make.x4(trackId: "aaa", tCaptureMs: t0, cls: .person, bearingDeg: 29.0)
        let b = try Make.x4(trackId: "bbb", tCaptureMs: t0, cls: .person, bearingDeg: 31.0)
        XCTAssertEqual(DedupGate.decide(b, recent: [record(a)]), .drop(reason: .seam))
        XCTAssertEqual(DedupGate.decide(a, recent: [record(b)]), .keep(evicting: [FeedbackKey(b)]))
    }

    /// Away from a crop seam the same source reporting two tracks means two
    /// objects, not one object twice.
    func testSameSourceAwayFromASeamKeepsBoth() throws {
        let first = try Make.x4(trackId: "mid-a", tCaptureMs: t0, cls: .person, bearingDeg: 60.0)
        let second = try Make.x4(trackId: "mid-b", tCaptureMs: t0 + 60, cls: .person, bearingDeg: 62.0)
        XCTAssertEqual(DedupGate.decide(second, recent: [record(first)]), .keep(evicting: []))
    }

    /// The same track reporting again is refractory's problem, not dedup's.
    func testSameKeyIsNeverADedupCandidate() throws {
        let first = try Make.x4(trackId: "same", tCaptureMs: t0, cls: .person, bearingDeg: 90.0)
        let again = try Make.x4(trackId: "same", tCaptureMs: t0 + 100, cls: .person, bearingDeg: 91.0)
        XCTAssertEqual(DedupGate.decide(again, recent: [record(first)]), .keep(evicting: []))
    }

    // MARK: - Retention and the exact-duplicate guard (A1.3, A3, A15)

    func testRecordsAreRetainedForTheRetentionWindowThenDropped() throws {
        let event = try Make.x4(trackId: "r1", tCaptureMs: t0, cls: .person, bearingDeg: 90.0)
        let entry = record(event, arrivalMs: t0)
        XCTAssertEqual(RecentAccepted.pruned([entry], now: t0 + RecentAccepted.retentionMs).count, 1)
        XCTAssertTrue(RecentAccepted.pruned([entry], now: t0 + RecentAccepted.retentionMs + 1).isEmpty)
    }

    func testExactDuplicateTripleIsRecognised() throws {
        let event = try Make.x4(trackId: "r2", tCaptureMs: t0, cls: .person, bearingDeg: 90.0)
        let replay = event
        let moved = try Make.x4(trackId: "r2", tCaptureMs: t0 + 1, cls: .person, bearingDeg: 90.0)
        let recent = [record(event)]
        XCTAssertTrue(RecentAccepted.containsExactDuplicate(of: replay, in: recent))
        XCTAssertFalse(RecentAccepted.containsExactDuplicate(of: moved, in: recent))
    }

    func testRecentAcceptedIsBounded() throws {
        var recent: [AcceptedRecord] = []
        for index in 0..<(RecentAccepted.maxRecords * 3) {
            let event = try Make.x4(
                trackId: "bulk-\(index)", tCaptureMs: t0 + Int64(index), cls: .person, bearingDeg: 90.0
            )
            recent = RecentAccepted.appending(record(event), to: recent, now: t0 + Int64(index))
        }
        XCTAssertLessThanOrEqual(recent.count, RecentAccepted.maxRecords)
    }
}
