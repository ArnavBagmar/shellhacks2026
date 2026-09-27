import XCTest

@testable import CaneAssist

final class PromptPhrasesTests: XCTestCase {
    func testALineIsNeverRepeatedBackToBack() {
        var picker = PromptPicker(choose: { _ in 0 })
        for moment in PromptMoment.allCases {
            var previous = picker.next(moment)
            for _ in 0..<20 {
                let line = picker.next(moment)
                XCTAssertNotEqual(line, previous, "\(moment)")
                previous = line
            }
        }
    }

    func testRandomPicksCoverThePool() {
        var picker = PromptPicker()
        let heard = Set((0..<200).map { _ in picker.next(.lookAround) })
        XCTAssertEqual(heard, Set(PromptPhrases.pool(.lookAround)))
    }

    func testEveryMomentHasSeveralShortLines() {
        for moment in PromptMoment.allCases {
            let pool = PromptPhrases.pool(moment)
            XCTAssertGreaterThanOrEqual(pool.count, 4, "\(moment)")
            XCTAssertEqual(Set(pool).count, pool.count, "\(moment) has a duplicate")
            for line in pool { XCTAssertLessThanOrEqual(line.split(separator: " ").count, 7, line) }
        }
    }

    func testTheLaptopPreVoicesExactlyTheseLines() throws {
        // backend/src/cane/scene/voice.py COMMON_PHRASES must match, or the
        // natural voice waits on ElevenLabs for the line it did not pre-voice.
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("backend/src/cane/scene/voice.py")
        let source = try String(contentsOf: url, encoding: .utf8)
        for line in PromptPhrases.all {
            XCTAssertTrue(source.contains("\"\(line)\""), "voice.py COMMON_PHRASES is missing \(line)")
        }
    }

    func testHowFarGetsAMeasuringPrompt() async {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = QueryCoordinator(link: link, speaker: speaker, makeId: { "q-1" }, choosePhrase: { _ in 0 })
        await c.handle(.howFar("sprite can"))
        XCTAssertEqual(speaker.said.first?.0, PromptPhrases.pool(.measure)[0])
    }
}
