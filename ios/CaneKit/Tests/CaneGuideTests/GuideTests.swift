import XCTest

@testable import CaneGuide

final class GuideGeometryTests: XCTestCase {
    func testThePhotoCentreIsStraightOutFromTheChest() {
        let p = GuideGeometry.point(NormPoint(x: 0.5, y: 0.5), depthM: 0.6, hfovDeg: 70, aspect: 4.0 / 3)
        XCTAssertEqual(p.x, 0, accuracy: 1e-9)
        XCTAssertEqual(p.y, 0, accuracy: 1e-9)
        XCTAssertEqual(p.z, 0.6, accuracy: 1e-9)
    }

    func testImageRightIsTheWearersRightAndImageDownIsDown() {
        let p = GuideGeometry.point(NormPoint(x: 1, y: 1), depthM: 1, hfovDeg: 90, aspect: 1)
        XCTAssertEqual(p.x, 1, accuracy: 1e-9) // tan(45) * 1 m
        XCTAssertEqual(p.y, 1, accuracy: 1e-9)
    }

    func testTheGapIsToTheSurfaceNotTheCentre() {
        // A 10 cm box straight ahead at 0.5 m; a fingertip 20 cm to its left.
        let hfov = 90.0, depth = 0.5
        let box = NormBox(xMin: 0.45, yMin: 0.45, xMax: 0.55, yMax: 0.55) // 0.1 * 2 * 0.5 m = 10 cm wide
        let hand = NormPoint(x: 0.3, y: 0.5) // (0.6 - 1) * 0.5 = -0.2 m... from the centre
        let (gap, towards) = GuideGeometry.gap(hand: hand, handDepthM: depth, object: box, objectDepthM: depth,
                                               hfovDeg: hfov, aspect: 1)
        XCTAssertEqual(towards.x, 0.2, accuracy: 1e-9)
        XCTAssertEqual(gap, 0.15, accuracy: 1e-9) // 20 cm to the centre minus a 5 cm half-side
        XCTAssertEqual(GuideGeometry.direction(towards), .right)
    }

    func testAHandShortOfTheThingIsToldForward() {
        let box = NormBox(xMin: 0.48, yMin: 0.48, xMax: 0.52, yMax: 0.52)
        let (_, towards) = GuideGeometry.gap(hand: NormPoint(x: 0.5, y: 0.5), handDepthM: 0.3, object: box,
                                             objectDepthM: 0.55, hfovDeg: 70, aspect: 4.0 / 3)
        XCTAssertEqual(GuideGeometry.direction(towards), .forward)
    }

    func testAThingBelowTheHandIsDown() {
        let box = NormBox(xMin: 0.45, yMin: 0.75, xMax: 0.55, yMax: 0.85)
        let (_, towards) = GuideGeometry.gap(hand: NormPoint(x: 0.5, y: 0.4), handDepthM: 0.5, object: box,
                                             objectDepthM: 0.5, hfovDeg: 70, aspect: 4.0 / 3)
        XCTAssertEqual(GuideGeometry.direction(towards), .down)
    }

    func testOnTargetHasNoDirection() {
        XCTAssertNil(GuideGeometry.direction(Point3(x: 0.01, y: -0.02, z: 0.0)))
    }
}

final class GuidePulseTests: XCTestCase {
    func testTapsGetFasterAsTheHandGetsCloser() {
        let far = GuidePulse.interval(gapM: 0.5)
        let mid = GuidePulse.interval(gapM: 0.2)
        let near = GuidePulse.interval(gapM: 0.05)
        XCTAssertGreaterThan(far, mid)
        XCTAssertGreaterThan(mid, near)
    }

    func testTheRhythmIsClampedAtBothEnds() {
        XCTAssertEqual(GuidePulse.interval(gapM: 5), GuidePulse.slowestS, accuracy: 1e-9)
        XCTAssertEqual(GuidePulse.interval(gapM: 0), GuidePulse.fastestS, accuracy: 1e-9)
    }

    func testCloseTapsAreFirm() {
        XCTAssertEqual(GuidePulse.tap(gapM: 0.3), .light)
        XCTAssertEqual(GuidePulse.tap(gapM: 0.05), .strong)
    }
}

final class GuideMessageTests: XCTestCase {
    func testAMessageSurvivesTheWatchConnectivityDictionary() {
        let m = GuideMessage(state: .guiding, gapM: 0.123, metric: true, target: "rubik's cube", hint: "Left.",
                             sentMs: 1_790_000_000_000)
        XCTAssertEqual(GuideMessage(dictionary: m.dictionary), m)
        // Every value is a property-list type.
        XCTAssertNoThrow(try PropertyListSerialization.data(fromPropertyList: m.dictionary, format: .binary, options: 0))
    }

    func testForeignDictionariesAreIgnored() {
        XCTAssertNil(GuideMessage(dictionary: ["kind": "other"]))
        XCTAssertNil(GuideMessage(dictionary: ["kind": "guide", "v": 99, "state": "guiding", "target": "x", "t": 1]))
    }

    func testTheWatchShowsANumberOnlyWhenItWasMeasured() {
        XCTAssertEqual(GuideMessage(state: .guiding, gapM: 0.123, target: "x", sentMs: 0).distanceText, "12 cm")
        XCTAssertEqual(GuideMessage(state: .guiding, gapM: 1.24, target: "x", sentMs: 0).distanceText, "1.2 m")
        XCTAssertNil(GuideMessage(state: .guiding, gapM: 0.123, metric: false, target: "x", sentMs: 0).distanceText)
    }
}

final class GuideSessionTests: XCTestCase {
    private let cube = NormBox(xMin: 0.6, yMin: 0.6, xMax: 0.7, yMax: 0.7)

    private func frame(_ t: Int64, object: NormBox?, hand: NormPoint? = nil, handDepth: Double? = 0.5,
                       objectDepth: Double? = 0.5) -> GuideFrame {
        GuideFrame(tMs: t, object: object, objectDepthM: objectDepth, hand: hand, handDepthM: handDepth,
                   hfovDeg: 70, aspect: 4.0 / 3)
    }

    private func says(_ out: [GuideOutput]) -> [String] {
        out.compactMap { if case .say(let s) = $0 { s } else { nil } }
    }

    private func messages(_ out: [GuideOutput]) -> [GuideMessage] {
        out.compactMap { if case .watch(let m) = $0 { m } else { nil } }
    }

    func testNoHandMeansNoTapsAndAReminderToReachOut() {
        var s = GuideSession(target: "rubik's cube", startMs: 0)
        let first = s.update(frame(100, object: cube))
        XCTAssertEqual(messages(first).first?.state, .findHand)
        XCTAssertNil(messages(first).first?.gapM)
        XCTAssertEqual(says(first), [])
        XCTAssertEqual(says(s.update(frame(6_100, object: cube))), [GuideSession.noHandPrompt])
    }

    func testAHandSendsTheGapToTheWatchAboutTenTimesASecond() {
        var s = GuideSession(target: "cube", startMs: 0)
        let hand = NormPoint(x: 0.3, y: 0.65)
        let a = messages(s.update(frame(1_000, object: cube, hand: hand)))
        let b = messages(s.update(frame(1_050, object: cube, hand: hand)))
        let c = messages(s.update(frame(1_100, object: cube, hand: hand)))
        XCTAssertEqual(a.first?.state, .guiding)
        XCTAssertTrue(a.first?.metric ?? false)
        XCTAssertGreaterThan(a.first?.gapM ?? 0, 0.1)
        XCTAssertTrue(b.isEmpty, "throttled")
        XCTAssertEqual(c.count, 1)
    }

    func testTheVoiceSaysWhichWayEverySecondOrSo() {
        var s = GuideSession(target: "cube", startMs: 0)
        let hand = NormPoint(x: 0.2, y: 0.65) // the cube is to the right
        XCTAssertEqual(says(s.update(frame(1_200, object: cube, hand: hand))), ["Right."])
        XCTAssertEqual(says(s.update(frame(2_000, object: cube, hand: hand))), [])
        XCTAssertEqual(says(s.update(frame(2_400, object: cube, hand: hand))), ["Right."], "still moving right: said again")
        XCTAssertEqual(s.hintText, "Right.")
    }

    func testTheLiveGapIsExposedForTheTicksAndThePage() {
        var s = GuideSession(target: "cube", startMs: 0)
        _ = s.update(frame(100, object: cube, hand: NormPoint(x: 0.2, y: 0.65)))
        XCTAssertGreaterThan(s.gapM ?? 0, 0.1)
        XCTAssertTrue(s.metric)
        XCTAssertGreaterThan(s.towards?.x ?? 0, 0, "the cube is to the right of the hand")
        _ = s.update(frame(200, object: cube))
        XCTAssertNil(s.gapM, "no hand, no gap")
    }

    func testTouchingForAMomentEndsWithYouveGotIt() {
        var s = GuideSession(target: "cube", startMs: 0)
        let onIt = NormPoint(x: 0.65, y: 0.65)
        XCTAssertFalse(s.update(frame(1_000, object: cube, hand: onIt)).contains(.end(.reached)))
        let out = s.update(frame(1_350, object: cube, hand: onIt))
        XCTAssertTrue(out.contains(.end(.reached)))
        XCTAssertEqual(says(out), [GuideSession.reachedPhrase])
        XCTAssertEqual(messages(out).last?.state, .reached)
        XCTAssertTrue(s.update(frame(1_500, object: cube, hand: onIt)).isEmpty, "finished stays finished")
    }

    func testABrushPastIsNotAReach() {
        var s = GuideSession(target: "cube", startMs: 0)
        _ = s.update(frame(1_000, object: cube, hand: NormPoint(x: 0.65, y: 0.65)))
        _ = s.update(frame(1_100, object: cube, hand: NormPoint(x: 0.2, y: 0.2)))
        let out = s.update(frame(1_400, object: cube, hand: NormPoint(x: 0.65, y: 0.65)))
        XCTAssertFalse(out.contains(.end(.reached)))
    }

    func testLosingTheThingForTooLongEndsTheGuide() {
        var s = GuideSession(target: "cube", startMs: 0)
        _ = s.update(frame(100, object: cube))
        XCTAssertTrue(s.update(frame(2_000, object: nil)).isEmpty)
        let out = s.update(frame(2_700, object: nil))
        XCTAssertTrue(out.contains(.end(.lost)))
        XCTAssertEqual(says(out), [GuideSession.lostPhrase("cube")])
    }

    func testItGivesUpAfterAMinute() {
        var s = GuideSession(target: "cube", startMs: 0)
        XCTAssertTrue(s.update(frame(60_000, object: cube)).contains(.end(.timedOut)))
    }

    func testWithoutADepthReadingNothingIsShownAsANumber() {
        var s = GuideSession(target: "cube", startMs: 0)
        let out = messages(s.update(frame(1_000, object: cube, hand: NormPoint(x: 0.3, y: 0.65), handDepth: nil,
                                          objectDepth: nil)))
        XCTAssertEqual(out.first?.metric, false)
        XCTAssertNil(out.first?.distanceText)
        XCTAssertNotNil(out.first?.gapM, "the taps still use the estimate")
    }

    func testNoWordsInTheLastFewCentimetres() {
        var s = GuideSession(target: "cube", startMs: 0)
        // 7.9 cm from the cube's surface: the beeps carry it from here.
        XCTAssertEqual(says(s.update(frame(1_300, object: cube, hand: NormPoint(x: 0.5, y: 0.65)))), [])
    }

    func testStopEndsQuietly() {
        var s = GuideSession(target: "cube", startMs: 0)
        let out = s.stop(nowMs: 500)
        XCTAssertEqual(says(out), [])
        XCTAssertEqual(messages(out).first?.state, .stopped)
        XCTAssertTrue(out.contains(.end(.stopped)))
    }
}
