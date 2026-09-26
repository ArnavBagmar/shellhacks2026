import CaneContracts
import CaneFeedback
import XCTest

@testable import CaneOutput

/// The pure layer: sector thresholds and the sign anchor, phrasing, the
/// haptic alphabet's invariants, and panning.
final class BearingSectorTests: XCTestCase {
    func test_negativeBearingIsTheUsersLeft() throws {
        XCTAssertEqual(Sector(bearingDeg: -90), .left)
        XCTAssertEqual(Sector(bearingDeg: 90), .right)
        XCTAssertTrue(try HazardPhrasing.phrase(for: Make.announcement(bearingDeg: -90))!.text.contains("left"))
        XCTAssertLessThan(StereoPan.pan(bearingDeg: -90, stereoRoute: true), 0)
        XCTAssertEqual(HapticSymbol(sector: Sector(bearingDeg: -90)), .left)
    }

    func testMirrorProperty() {
        for b in 1...179 {
            let bearing = Double(b)
            XCTAssertEqual(Sector(bearingDeg: -bearing).side, Sector(bearingDeg: bearing).side.mirrored, "b=\(b)")
        }
    }

    func testBoundaries() {
        let cases: [(Double, Sector)] = [
            (0, .ahead), (14.9, .ahead), (15, .aheadRight), (-15, .aheadLeft), (59.9, .aheadRight),
            (60, .right), (119.9, .right), (120, .behindRight), (164.9, .behindRight),
            (165, .behind), (-165, .behind), (180, .behind), (-164.9, .behindLeft), (270, .left),
        ]
        for (bearing, expected) in cases {
            XCTAssertEqual(Sector(bearingDeg: bearing), expected, "bearing \(bearing)")
        }
    }
}

final class HazardPhrasingTests: XCTestCase {
    static let bearings: [Double] = [0, -30, 30, -90, 90, -140, 140, 180]

    func testEveryPhraseIsAtMostSixWordsDirectionFirstAndWithoutDecimals() throws {
        var checked = 0
        for bearing in Self.bearings {
            for cls in ObjectClass.allCases {
                for closing in [nil, 1.0, 5.0] as [Double?] {
                    var ranges: [(Double?, RangeBand?)] = (0...20).map { (Double($0) + 0.3, nil) }
                    ranges += RangeBand.allCases.map { (nil, $0) }
                    ranges.append((nil, nil))
                    for (metres, band) in ranges {
                        let a = try Make.announcement(cls: cls, bearingDeg: bearing, rangeM: metres, band: band, closingMps: closing)
                        let text = try XCTUnwrap(HazardPhrasing.phrase(for: a)).text
                        XCTAssertLessThanOrEqual(text.split(separator: " ").count, 6, text)
                        XCTAssertTrue(text.lowercased().hasPrefix(Sector(bearingDeg: bearing).words), text)
                        XCTAssertNil(text.range(of: #"\d\.\d"#, options: .regularExpression), text)
                        XCTAssertFalse(text.contains(" 0 meters"), text)
                        XCTAssertFalse(text.contains("closing"), text)
                        checked += 1
                    }
                }
            }
        }
        XCTAssertEqual(checked, 8 * 12 * 3 * 25)
    }

    func testUrgencyThreeWordsAreLocativeNeverSteering() throws {
        let expected: [Double: String] = [
            0: "Stop!", -30: "Stop!", 30: "Stop!", -90: "Left side!", 90: "Right side!",
            -140: "Behind you!", 140: "Behind you!", 180: "Behind you!",
        ]
        for (bearing, words) in expected {
            let phrase = try XCTUnwrap(HazardPhrasing.phrase(for: Make.announcement(level: .immediate, cls: .cyclist, bearingDeg: bearing)))
            XCTAssertEqual(phrase.text, words)
            XCTAssertEqual(phrase.pitchMultiplier, 1.15)
        }
    }

    func testUrgencyOneNeverSpeaksUnlessHapticsAreGone() throws {
        let a = try Make.announcement(level: .aware, bearingDeg: -90)
        XCTAssertNil(HazardPhrasing.phrase(for: a))
        XCTAssertEqual(HazardPhrasing.phrase(for: a, hapticsDegraded: true)?.text, "To your left, person")
    }

    func testRangeOnlyForAStaticClassStraightAhead() throws {
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .pole, bearingDeg: 0, rangeM: 4.2))?.text,
                       "Straight ahead, pole, about 4 meters")
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .person, bearingDeg: 0, rangeM: 4.2))?.text,
                       "Straight ahead, person")
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .pole, bearingDeg: 30, rangeM: 4.2))?.text,
                       "Ahead to the right, pole")
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .wall, bearingDeg: 0, rangeM: 0.4))?.text,
                       "Straight ahead, wall, close")
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .door, bearingDeg: 0, rangeM: 1.2))?.text,
                       "Straight ahead, door, about 1 meter")
    }

    func testFastOnlyAboveTheStepNineThresholdAndDroppedFirstWhenLong() throws {
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .cyclist, bearingDeg: 180, closingMps: 5))?.text,
                       "Directly behind you, cyclist fast")
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .cyclist, bearingDeg: 180, closingMps: 2))?.text,
                       "Directly behind you, cyclist")
        XCTAssertEqual(try HazardPhrasing.phrase(for: Make.announcement(cls: .cyclist, bearingDeg: -140, closingMps: 5))?.text,
                       "Behind you to the left, cyclist")
    }
}

final class HapticScriptTests: XCTestCase {
    func testTheWholeAlphabetPassesTheUrgencyInvariants() {
        for gain in [0.6, 1.0, 1.4] as [Float] {
            for symbol in HapticSymbol.allCases {
                for urgency in 1...3 {
                    let script = HapticScript.hazard(symbol: symbol, urgency: urgency, userGain: gain)
                    XCTAssertNil(script.violation(userGain: gain), "\(symbol) u\(urgency) g\(gain)")
                }
            }
            for side in [Side.left, .right, .center] {
                XCTAssertNil(HapticScript.navigation(side: side, userGain: gain)?.violation(userGain: gain))
            }
        }
    }

    func testUrgencyThreeIsAlwaysTheFullStrengthAlarm() {
        for symbol in HapticSymbol.allCases {
            let script = HapticScript.hazard(symbol: symbol, urgency: 3, userGain: 0.6)
            XCTAssertEqual(script.symbol, .alarm)
            XCTAssertEqual(script.totalMs, 900)
            XCTAssertTrue(script.elements.allSatisfy { $0.intensity == 1.0 })
        }
    }

    func testOnlyTheAlarmOutlastsFourHundredTwentyMilliseconds() {
        for symbol in HapticSymbol.allCases where symbol != .alarm {
            XCTAssertLessThanOrEqual(HapticScript.hazard(symbol: symbol, urgency: 2).totalMs, 420)
        }
    }

    func testNoTwoSymbolsShareAShape() {
        let shapes = HapticSymbol.allCases.map { HapticScript.hazard(symbol: $0, urgency: 2).elements.map(\.kind) }
        XCTAssertEqual(Set(shapes.map { "\($0)" }).count, shapes.count)
    }

    func testLeftAndRightDifferByAMiddleClickNotByMirroring() {
        let left = HapticScript.hazard(symbol: .left, urgency: 2).elements.map(\.onsetMs)
        let right = HapticScript.hazard(symbol: .right, urgency: 2).elements.map(\.onsetMs)
        XCTAssertEqual(left, [0, 200])
        XCTAssertEqual(right, [0, 100, 200])
    }

    func testTheIntensityFloorKeepsUrgencyOneFeelable() {
        let script = HapticScript.hazard(symbol: .ahead, urgency: 1, userGain: 0.6)
        XCTAssertGreaterThanOrEqual(script.elements[0].intensity, 0.35)
    }

    func testNavigationIsSofterThanAnyHazard() {
        let nav = HapticScript.navigation(side: .left)!.elements.map(\.intensity).max()!
        let hazard = HapticScript.hazard(symbol: .left, urgency: 1).elements.map(\.intensity).min()!
        XCTAssertLessThan(nav, hazard)
    }
}

final class StereoPanTests: XCTestCase {
    func testPanIsCappedSineAndZeroFrontAndBack() {
        XCTAssertEqual(StereoPan.pan(bearingDeg: 0, stereoRoute: true), 0, accuracy: 1e-9)
        XCTAssertEqual(StereoPan.pan(bearingDeg: 90, stereoRoute: true), 0.4, accuracy: 1e-9)
        XCTAssertEqual(StereoPan.pan(bearingDeg: -90, stereoRoute: true), -0.4, accuracy: 1e-9)
        XCTAssertEqual(StereoPan.pan(bearingDeg: 180, stereoRoute: true), 0, accuracy: 1e-9)
    }

    func testNoRouteButRealStereoHeadphonesIsEverPanned() {
        XCTAssertEqual(StereoPan.pan(bearingDeg: 90, stereoRoute: false), 0)
        XCTAssertFalse(StereoPan.isStereoRoute(outputs: [("Speaker", 2)]))
        XCTAssertFalse(StereoPan.isStereoRoute(outputs: [("Receiver", 1)]))
        XCTAssertFalse(StereoPan.isStereoRoute(outputs: [("BluetoothA2DPOutput", 1)]))
        XCTAssertFalse(StereoPan.isStereoRoute(outputs: [("Headphones", 2), ("Speaker", 2)]))
        XCTAssertTrue(StereoPan.isStereoRoute(outputs: [("BluetoothA2DPOutput", 2)]))
    }

    func testOneBudInLosesAtMostFourDecibels() {
        for b in stride(from: -180.0, through: 180.0, by: 5) {
            let gains = StereoPan.channelGains(pan: StereoPan.pan(bearingDeg: b, stereoRoute: true))
            XCTAssertGreaterThanOrEqual(min(gains.left, gains.right), StereoPan.minChannelGain - 1e-9)
        }
    }
}
