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

    func testGuideMyHandPhrasings() {
        let cases: [(String, String?)] = [
            ("guide my hand to the Rubik's cube", "rubik's cube"),
            ("can you guide my hand to the rubik's cube please", "rubik's cube"),
            ("help me grab the water bottle", "water bottle"),
            ("help me reach the cup on the table", "cup on the table"),
            ("help me pick up my keys", "my keys"),
            ("lead my hand towards the remote", "remote"),
            ("guide my hand", nil),
            ("help me grab it", nil),
            ("guide my hand to that", nil),
        ]
        for (utterance, target) in cases {
            XCTAssertEqual(IntentParser.parse(utterance), .guideHand(target), utterance)
        }
        // Not a route: "take my hand" is the hand, "take me to" is a walk.
        XCTAssertEqual(IntentParser.parse("take me to the cafe"), .navigate("cafe"))
    }

    /// Step 26 gave the voice layer the gap ring, so "which way is clear" is now a
    /// question the system can actually answer from the same number the steering cue
    /// is rendered from -- but the parser returned nil for it, so the wearer heard
    /// "Sorry, I didn't catch that". These are the phrasings a person reaches for
    /// when they want the ring, and they all route to `describeFront`.
    func testAskingWhichWayIsClearReachesTheFrontDescription() {
        let cases = [
            "which way is clear",
            "which way is clearer",
            "which way should I go",
            "is the way clear",
            "is it clear ahead",
            "where can I walk",
        ]
        for utterance in cases {
            XCTAssertEqual(
                IntentParser.parse(utterance), .describeFront,
                "\(utterance) should ask about the way ahead"
            )
        }
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

    func testTimeAndHelp() {
        for utterance in ["what time is it", "What's the time?", "tell me the time", "do you know what time it is"] {
            XCTAssertEqual(IntentParser.parse(utterance), .time, utterance)
        }
        for utterance in ["help", "what can you do", "what can I say", "what can I ask you"] {
            XCTAssertEqual(IntentParser.parse(utterance), .help, utterance)
        }
    }

    func testDoYouSeeIsAFind() {
        XCTAssertEqual(IntentParser.parse("do you see a bathroom nearby?"), .find("bathroom"))
        XCTAssertEqual(IntentParser.parse("can you see the door"), .find("door"))
        XCTAssertEqual(IntentParser.parse("do you see any stairs around here"), .find("stairs"))
        XCTAssertEqual(IntentParser.parse("what do you see"), .describeFront)
        XCTAssertEqual(IntentParser.parse("do you see anything"), .describeAround)
    }

    func testEverythingElseIsAQuestionForGemini() {
        XCTAssertEqual(IntentParser.route("how's the weather today"), .ask("how is the weather today"))
        XCTAssertEqual(IntentParser.route("is this line for the food"), .ask("is this line for the food"))
        XCTAssertEqual(IntentParser.route("is there a bathroom around"), .find("bathroom"))
        XCTAssertEqual(IntentParser.route("what time is it"), .time)
        // One stray word is a mishearing, not a question.
        XCTAssertNil(IntentParser.route("yeah"))
        XCTAssertNil(IntentParser.route(""))
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
        // "King" and "keen" are what the recognizer heard live (2026-09-26).
        for name in ["Kane", "Cain", "cane", "King", "keen", "Keane", "Kane's", "Cade", "Gane"] {
            var gate = WakeWordGate()
            XCTAssertEqual(gate.feed(text: "okay \(name) what's around me", isFinal: true, nowMs: 0),
                           .command("what is around me"), name)
        }
    }

    func testACompleteCommandWaitsOutAGapBetweenVolatileResults() {
        // Live: "sprite" and "can" arrived 0.7 s apart; 700 ms cut it off.
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "hey cane how far is the sprite", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.tick(nowMs: 800), .none)
        XCTAssertEqual(gate.feed(text: "hey cane how far is the sprite can", isFinal: false, nowMs: 900), .none)
        XCTAssertEqual(gate.feed(text: "Hey Cane, how far is the Sprite can?", isFinal: true, nowMs: 940),
                       .command("how far is the sprite can"))
    }

    func testWithoutAFinalACompleteCommandStillActs() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "hey cane is there a bathroom", isFinal: false, nowMs: 400), .none)
        XCTAssertEqual(gate.tick(nowMs: 2_800), .none)
        XCTAssertEqual(gate.tick(nowMs: 2_900), .command("is there a bathroom"))
    }

    func testAnUnfinishedSentenceGetsTheLongPause() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "hey cane what color are the walls", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.tick(nowMs: 4_500), .none)  // stalls of 2.8-4.5 s mid-sentence, heard live
        XCTAssertEqual(gate.tick(nowMs: 5_000), .command("what color are the walls"))
    }

    func testAFragmentWaitsForTheFinalHoweverLong() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "hey cane how far is", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.feed(text: "hey cane how", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.tick(nowMs: 20_000), .none)
        XCTAssertEqual(gate.feed(text: "Hey Cane, how far is the water bottle?", isFinal: true, nowMs: 21_000),
                       .command("how far is the water bottle"))
    }

    func testALateFinalThatAsksForMoreCorrectsTheEarlyCommand() {
        var gate = WakeWordGate()
        _ = gate.feed(text: "hey cane how far is the sprite", isFinal: false, nowMs: 0)
        XCTAssertEqual(gate.tick(nowMs: 2_500), .command("how far is the sprite"))
        XCTAssertEqual(gate.feed(text: "hey cane how far is the sprite can", isFinal: false, nowMs: 2_000), .none)
        XCTAssertEqual(gate.feed(text: "Hey Cane, how far is the Sprite can?", isFinal: true, nowMs: 7_000),
                       .command("how far is the sprite can"))
    }

    func testALateFinalThatAsksTheSameIsNotRepeated() {
        var gate = WakeWordGate()
        _ = gate.feed(text: "hey cane is there a bathroom", isFinal: false, nowMs: 0)
        XCTAssertEqual(gate.tick(nowMs: 2_500), .command("is there a bathroom"))
        XCTAssertEqual(gate.feed(text: "hey cane is there a bathroom around", isFinal: true, nowMs: 2_000), .none)
    }

    func testPausingAfterTheWakeWordDoesNotLoseTheCommand() {
        // Live: "Hey King" + a pause -> "Yes?", and the sentence's final
        // ("Hey, King, how far is the Sprite can?") was thrown away.
        var gate = WakeWordGate()
        _ = gate.feed(text: "Hey King", isFinal: false, nowMs: 0)
        XCTAssertEqual(gate.tick(nowMs: 2_200), .none)  // only a pause
        XCTAssertEqual(gate.tick(nowMs: 3_000), .wakeOnly)
        XCTAssertEqual(gate.feed(text: "Hey, King, how far is the Sprite can?", isFinal: true, nowMs: 3_000),
                       .command("how far is the sprite can"))
    }

    func testAGreetingAndAClearCommandWakeItWithoutTheName() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "Hey, how far is the Sprite can?", isFinal: true, nowMs: 0),
                       .command("how far is the sprite can"))
        XCTAssertEqual(gate.feed(text: "Hey team, how far is the Sprite can?", isFinal: true, nowMs: 1_000),
                       .command("how far is the sprite can"))
    }

    func testAGreetingAndChatterDoNot() {
        var gate = WakeWordGate()
        XCTAssertEqual(gate.feed(text: "Hey, what's up with you?", isFinal: true, nowMs: 0), .none)
        XCTAssertEqual(gate.feed(text: "Hey team, what are we doing for dinner?", isFinal: true, nowMs: 1_000), .none)
        XCTAssertEqual(gate.feed(text: "is there a bathroom around", isFinal: true, nowMs: 2_000), .none)
    }

    func testAStableVolatileResultEmitsOnceWithoutWaitingForFinal() {
        var gate = WakeWordGate(isComplete: { _ in false })
        XCTAssertEqual(gate.feed(text: "hey cane is there a", isFinal: false, nowMs: 0), .none)
        XCTAssertEqual(gate.feed(text: "hey cane is there a bathroom", isFinal: false, nowMs: 400), .none)
        XCTAssertEqual(gate.tick(nowMs: 5_300), .none)
        XCTAssertEqual(gate.tick(nowMs: 5_400), .command("is there a bathroom"))
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

final class HowFarIntentTests: XCTestCase {
    func testHowFarPhrasings() {
        let cases: [(String, String)] = [
            ("how far is the sprite can", "sprite can"),
            ("how far away is the door", "door"),
            ("how far is the chair from me", "chair"),
            ("how far am I from the wall", "wall"),
            ("how close is the table", "table"),
            ("what's the distance to the door", "door"),
            ("can you tell me how far the bench is", "bench"),
        ]
        for (text, target) in cases {
            XCTAssertEqual(IntentParser.parse(text), .howFar(target), text)
        }
    }

    func testHalfASentenceIsNotAThing() {
        XCTAssertNil(IntentParser.parse("how far is the"))
        XCTAssertNil(IntentParser.parse("is there a"))
    }

    func testTheThingKeepsItsDescription() {
        XCTAssertEqual(IntentParser.parse("how far is the sprite can on the table"), .howFar("sprite can on the table"))
    }

    func testFindsAreStillFinds() {
        XCTAssertEqual(IntentParser.parse("is there a door nearby"), .find("door"))
    }
}

final class NavigationCommandTests: XCTestCase {
    func testStopNavigationPhrasings() {
        for text in [
            "stop navigation", "please stop navigating", "cancel the route", "end directions", "stop routing",
            "can you cancel navigation", "stop taking me to the library", "exit navigation", "turn off navigation",
        ] {
            XCTAssertEqual(IntentParser.parse(text), .stopNavigation, text)
        }
    }

    func testPlainStopIsStillCancel() {
        for text in ["stop", "cancel", "never mind", "stop talking"] {
            XCTAssertEqual(IntentParser.parse(text), .cancel, text)
        }
    }

    func testRepeatAndStatus() {
        XCTAssertEqual(IntentParser.parse("repeat that"), .repeatDirection)
        XCTAssertEqual(IntentParser.parse("say that again"), .repeatDirection)
        XCTAssertEqual(IntentParser.parse("can you repeat that please"), .repeatDirection)
        XCTAssertEqual(IntentParser.parse("what was that"), .repeatDirection)
        XCTAssertEqual(IntentParser.parse("where am I going"), .navigationStatus)
        XCTAssertEqual(IntentParser.parse("are we there yet"), .navigationStatus)
        XCTAssertEqual(IntentParser.parse("what's my destination"), .navigationStatus)
    }

    func testStartingARouteStillWorks() {
        XCTAssertEqual(IntentParser.parse("take me to the library"), .navigate("library"))
        // A question that merely starts like a repeat goes to Gemini.
        XCTAssertNil(IntentParser.parse("what was that sign about"))
    }
}
