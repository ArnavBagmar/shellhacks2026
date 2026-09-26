import XCTest

@testable import CaneVoice

final class IntentParserTests: XCTestCase {
    func testFindPhrasings() {
        let cases: [(String, String)] = [
            ("is there a bathroom around?", "bathroom"),
            ("Is there a bathroom around here", "bathroom"),
            ("is there an elevator nearby", "elevator"),
            ("are there any benches near me", "benches"),
            ("where's the nearest restroom", "restroom"),
            ("Where is the exit?", "exit"),
            ("find me a water fountain please", "water fountain"),
            ("look for the stairs", "stairs"),
            ("I need a bathroom", "bathroom"),
            ("is there a bus stop around", "bus stop"),
            ("can you find the door", "door"),
            ("is there a bathroom around me", "bathroom"),
        ]
        for (utterance, target) in cases {
            XCTAssertEqual(IntentParser.parse(utterance), .find(target), utterance)
        }
    }

    func testTheOtherIntents() {
        XCTAssertEqual(IntentParser.parse("What's around me?"), .describeAround)
        XCTAssertEqual(IntentParser.parse("look around"), .describeAround)
        XCTAssertEqual(IntentParser.parse("what's in front of me"), .describeFront)
        XCTAssertEqual(IntentParser.parse("what is ahead of me"), .describeFront)
        XCTAssertEqual(IntentParser.parse("read that sign"), .readSign)
        XCTAssertEqual(IntentParser.parse("what does that sign say"), .readSign)
        XCTAssertEqual(IntentParser.parse("take me to the library"), .navigate("library"))
        XCTAssertEqual(IntentParser.parse("Never mind."), .cancel)
        XCTAssertEqual(IntentParser.parse("stop"), .cancel)
    }

    func testNaturalSentencesThatFailedLive() {
        let cases: [(String, VoiceIntent)] = [
            ("can you tell me if there's a bathroom around", .find("bathroom")),
            ("um is there a bathroom", .find("bathroom")),
            ("could you find the elevator for me", .find("elevator")),
            ("do you know where the restroom is", .find("restroom")),
            ("I'm looking for the exit", .find("exit")),
            ("is there a door in front of me", .find("door")),
            ("is there anything in front of me", .describeFront),
            ("is there anything around", .describeAround),
            ("tell me what's around me", .describeAround),
            ("can you read that sign", .readSign),
            ("please take me to the library", .navigate("library")),
        ]
        for (utterance, intent) in cases {
            XCTAssertEqual(IntentParser.parse(utterance), intent, utterance)
        }
    }

    func testArmingLetsTheWearerRepeatWithoutTheWakeWord() {
        var gate = WakeWordGate()
        gate.arm(nowMs: 0)
        XCTAssertEqual(gate.feed(text: "is there a bathroom", isFinal: true, nowMs: 2_000), .command("is there a bathroom"))
    }

    func testNonsenseIsNil() {
        XCTAssertNil(IntentParser.parse(""))
        XCTAssertNil(IntentParser.parse("the weather is nice"))
        XCTAssertNil(IntentParser.parse("is there"))
        XCTAssertNil(IntentParser.parse("ready when you are"))
    }
}

final class WakeWordGateTests: XCTestCase {
    func testAFinalResultWithTheWakeWordEmitsTheCommand() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "Hey Cane, is there a bathroom around?", isFinal: true, nowMs: 0),
                       .command("is there a bathroom around"))
    }

    func testSpeechWithoutTheWakeWordIsIgnored() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "is there a bathroom around", isFinal: true, nowMs: 0), .none)
        XCTAssertEqual(gate.tick(nowMs: 10_000), .none)
    }

    func testMisheardNamesStillWake() {
        for name in ["Kane", "Cain", "cane"] {
            var gate = WakeWordGate()
            XCTAssertEqual(gate.feed(text: "okay \(name) what's around me", isFinal: true, nowMs: 0),
                           .command("what is around me"), name)
        }
    }

    func testACompleteCommandActsAfterAShortPause() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "hey cane is there a", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.feed(text: "hey cane is there a bathroom", isFinal: false, nowMs: 400), .none)
        XCTAssertEqual(gate.tick(nowMs: 900), .none)
        XCTAssertEqual(gate.tick(nowMs: 1_150), .command("is there a bathroom"))
    }

    func testAnUnfinishedSentenceGetsTheLongPause() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "hey cane is there a", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.tick(nowMs: 1_000), .none)
        XCTAssertEqual(gate.feed(text: "hey cane is there a bathroom", isFinal: false, nowMs: 1_500), .none)
        XCTAssertEqual(gate.tick(nowMs: 2_250), .command("is there a bathroom"))
    }

    func testAStableVolatileResultEmitsOnceWithoutWaitingForFinal() {
        var gate = WakeWordGate(isComplete: { _ in false })
        XCTAssertEqual(gate.feed(text: "hey cane is there a", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.feed(text: "hey cane is there a bathroom", isFinal: false, nowMs: 400), .none)
        XCTAssertEqual(gate.tick(nowMs: 1_600), .none)
        XCTAssertEqual(gate.tick(nowMs: 2_200), .command("is there a bathroom"))
        // The same segment finalizing later must not fire a second time.
        XCTAssertEqual(gate.feed(text: "hey cane is there a bathroom", isFinal: true, nowMs: 2_500), .none)
        XCTAssertEqual(gate.tick(nowMs: 5_000), .none)
    }

    func testWakeWordAloneArmsTheNextUtterance() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "Hey Cane.", isFinal: true, nowMs: 0), .none)
        XCTAssertEqual(gate.tick(nowMs: 1_000), .none)
        XCTAssertEqual(gate.tick(nowMs: 1_300), .wakeOnly)
        XCTAssertEqual(gate.feed(text: "Where is the exit?", isFinal: true, nowMs: 3_000), .command("where is the exit"))
        // Disarmed after one command.
        XCTAssertEqual(gate.feed(text: "where is the exit", isFinal: true, nowMs: 4_000), .none)
    }

    func testAWakeWordFinalizedAloneDoesNotInterruptTheCommandThatFollows() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "Hey Cane", isFinal: true, nowMs: 0), .none)
        XCTAssertEqual(gate.feed(text: "is there a", isFinal: false, nowMs: 400), .none)
        XCTAssertEqual(gate.tick(nowMs: 1_300), .none)  // no "Yes?" over the user's sentence
        XCTAssertEqual(gate.feed(text: "is there a bathroom around", isFinal: true, nowMs: 1_900),
                       .command("is there a bathroom around"))
        XCTAssertEqual(gate.tick(nowMs: 5_000), .none)
    }

    func testTheArmExpires() {
        var gate = WakeWordGate()
        _ = gate.feed(text: "hey cane", isFinal: true, nowMs: 0)
        XCTAssertEqual(gate.feed(text: "where is the exit", isFinal: true, nowMs: 5_500), .none)
    }

    func testTheLastWakeWordWins() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "hey cane read hey cane what's in front of me", isFinal: true, nowMs: 0),
                       .command("what is in front of me"))
    }
}
