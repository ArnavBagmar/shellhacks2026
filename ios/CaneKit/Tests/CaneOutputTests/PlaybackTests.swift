import CaneContracts
import CaneFeedback
import XCTest

@testable import CaneOutput

/// What the playback layer adds on top of the step-10 policy files
/// (BearingSector, HapticAlphabet, SpeechPhrase, StereoPan -- tested in
/// their own suites): played intensities, the navigation tier, the degraded
/// speech path, and the sink's plan.
final class HapticPlaybackTests: XCTestCase {
    func testPlayedIntensitiesFollowTheAlphabetsGainRules() {
        for gain in [0.6, 1.0, 1.4] {
            for symbol in HapticSymbol.allCases where symbol != .alarm {
                for level in [AnnouncementLevel.aware, .actSoon] {
                    let played = HapticScript.hazard(symbol, level: level, userGain: gain)
                    XCTAssertEqual(played.elements.map(\.onsetMs), HapticAlphabet.script(symbol).elements.map(\.onsetMs))
                    for element in played.elements {
                        XCTAssertEqual(element.baseIntensity,
                                       HapticAlphabet.intensity(base: 1.0, level: level, userGain: gain), accuracy: 1e-9)
                    }
                }
            }
        }
    }

    func testTheAlarmIsAlwaysFullStrength() {
        let alarm = HapticScript.hazard(.alarm, level: .aware, userGain: 0.6)
        XCTAssertEqual(alarm.totalMs, HapticAlphabet.alarmMs)
        XCTAssertTrue(alarm.elements.allSatisfy { $0.baseIntensity == 1.0 })
    }

    func testNavigationIsSofterThanAnyHazardAndKeepsTheSideShapes() throws {
        // At every user setting, a turn cue is softer than the softest hazard.
        for gain in [0.6, 1.0, 1.4] {
            let nav = try XCTUnwrap(HapticScript.navigation(side: .left, userGain: gain))
            let weakestHazard = HapticScript.hazard(.left, level: .aware, userGain: gain).elements.map(\.baseIntensity).min()!
            XCTAssertLessThan(nav.elements.map(\.baseIntensity).max()!, weakestHazard, "gain \(gain)")
        }
        XCTAssertEqual(HapticScript.navigation(side: .left)?.symbol, .left)
        XCTAssertEqual(HapticScript.navigation(side: .right)?.symbol, .right)
        XCTAssertEqual(HapticScript.navigation(side: .center)?.symbol, .ahead)
        XCTAssertNil(HapticScript.navigation(side: nil))
    }
}

final class HazardPhrasingTests: XCTestCase {
    func testUrgencyOneIsSilentUnlessHapticsAreGone() throws {
        let a = try Make.announcement(level: .aware, bearingDeg: -90)
        XCTAssertNil(HazardPhrasing.phrase(for: a))
        XCTAssertEqual(HazardPhrasing.phrase(for: a, hapticsDegraded: true)?.text, "To your left, person")
    }

    func testUrgencyThreeIsTheLocativeWordAtRaisedPitch() throws {
        let phrase = try XCTUnwrap(HazardPhrasing.phrase(for: Make.announcement(level: .immediate, cls: .cyclist, bearingDeg: 178)))
        XCTAssertEqual(phrase.text, "Behind you!")
        XCTAssertEqual(phrase.pitchMultiplier, SpeechSettings.pitchMultiplier(.immediate))
    }

    func testUrgencyTwoIsDirectionFirstAndCapitalised() throws {
        let phrase = try XCTUnwrap(HazardPhrasing.phrase(for: Make.announcement(cls: .pole, bearingDeg: 0, rangeM: 4.2)))
        XCTAssertEqual(phrase.text, "Straight ahead, pole, about 4 meters")
        XCTAssertEqual(phrase.pitchMultiplier, 1.0)
    }
}
