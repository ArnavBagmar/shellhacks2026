import CaneOutput
import CaneTransport
import CaneVoice
import XCTest

@testable import CaneAssist

final class AnswerPhrasingTests: XCTestCase {
    private func answer(
        _ status: SceneAnswerStatus, subject: String? = nil, bearing: Double? = nil,
        detail: String? = nil, confidence: SceneConfidence? = nil
    ) throws -> SceneAnswer {
        try SceneAnswer(queryId: "q-1", status: status, subject: subject, bearingDeg: bearing,
                        detail: detail, confidence: confidence, latencyMs: 1_000)
    }

    func testAFoundAnswerLeadsWithTheSameDirectionWordsAsHazards() throws {
        let a = try answer(.found, subject: "restroom sign", bearing: 72, detail: "past the elevators", confidence: .medium)
        XCTAssertEqual(AnswerPhrasing.phrase(for: a, target: "bathroom"), "To your right, restroom sign, past the elevators.")
        XCTAssertEqual(AnswerPhrasing.side(for: a), .right)
    }

    func testNegativeBearingIsSpokenAsLeft() throws {
        let a = try answer(.found, subject: "door", bearing: -30)
        XCTAssertEqual(AnswerPhrasing.phrase(for: a, target: "door"), "Ahead to the left, door.")
        XCTAssertEqual(AnswerPhrasing.side(for: a), .left)
    }

    func testLowConfidenceSaysMaybe() throws {
        let a = try answer(.found, subject: "restroom sign", bearing: 175, confidence: .low)
        XCTAssertEqual(AnswerPhrasing.phrase(for: a, target: nil), "Maybe directly behind you, restroom sign.")
    }

    func testFoundWithoutABearingSaysSo() throws {
        let a = try answer(.found, subject: "elevator")
        XCTAssertEqual(AnswerPhrasing.phrase(for: a, target: nil), "Elevator nearby, direction unclear.")
        XCTAssertNil(AnswerPhrasing.side(for: a))
    }

    func testNotFoundUsesTheRightArticle() throws {
        let a = try answer(.notFound)
        XCTAssertEqual(AnswerPhrasing.phrase(for: a, target: "bathroom"), "I don't see a bathroom nearby.")
        XCTAssertEqual(AnswerPhrasing.phrase(for: a, target: "elevator"), "I don't see an elevator nearby.")
        XCTAssertEqual(AnswerPhrasing.phrase(for: a, target: "stairs"), "I don't see any stairs nearby.")
    }

    func testDescribedAndUnavailable() throws {
        XCTAssertEqual(AnswerPhrasing.phrase(for: try answer(.described, detail: "A hallway."), target: nil), "A hallway.")
        XCTAssertEqual(AnswerPhrasing.phrase(for: try answer(.unavailable), target: nil),
                       "Looking around isn't available right now.")
    }
}

// MARK: - Coordinator

final class FakeLink: QueryLink, @unchecked Sendable {
    private let lock = NSLock()
    private var _sent: [SceneQuery] = []
    var failing = false
    var sent: [SceneQuery] { lock.withLock { _sent } }
    func send(_ query: SceneQuery) async throws {
        if failing { throw TransportError.notConnected }
        lock.withLock { _sent.append(query) }
    }
}

final class FakeSpeaker: AssistSpeaker, @unchecked Sendable {
    private let lock = NSLock()
    private var _said: [(String, OutputChannel)] = []
    private var _haptics: [HapticScript?] = []
    var said: [(String, OutputChannel)] { lock.withLock { _said } }
    var haptics: [HapticScript?] { lock.withLock { _haptics } }
    func say(_ phrase: SpokenPhrase?, on channel: OutputChannel, haptic: HapticScript?, pan: Double, maxWaitMs: Int) async -> Bool {
        lock.withLock {
            _said.append((phrase?.text ?? "", channel))
            _haptics.append(haptic)
        }
        return true
    }
    func cancel(upTo channel: OutputChannel) {}
}

final class QueryCoordinatorTests: XCTestCase {
    private func coordinator(_ link: FakeLink, _ speaker: FakeSpeaker, timeoutMs: Int = 10_000) -> QueryCoordinator {
        let frame = try? PhoneFrame(jpeg: Data([0xFF, 0xD8, 0xFF, 0xE0]), hfovDeg: 70, width: 1280, height: 720)
        return QueryCoordinator(link: link, speaker: speaker, snapshot: { frame }, clock: { 1_790_000_000_000 },
                                makeId: { "q-1" }, timeoutMs: timeoutMs)
    }

    func testAFindSendsTheQueryWithAPhotoThenSaysHoldStill() async {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")

        XCTAssertEqual(link.sent.count, 1)
        XCTAssertEqual(link.sent.first?.kind, .find)
        XCTAssertEqual(link.sent.first?.target, "bathroom")
        XCTAssertNotNil(link.sent.first?.phoneFrame)
        XCTAssertEqual(speaker.said.map(\.0), ["Hold still, looking around."])
        XCTAssertEqual(speaker.said.map(\.1), [.prompt])
        let state = await c.state
        XCTAssertEqual(state, .looking(queryId: "q-1"))
    }

    func testTheMatchingAnswerIsSpokenOnTheAnswerChannelWithASideBuzz() async throws {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)
        await c.handle(.find("bathroom"))

        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "restroom sign", bearingDeg: 80,
                                        detail: nil, confidence: .high, latencyMs: 1_800))

        XCTAssertEqual(speaker.said.last?.0, "To your right, restroom sign.")
        XCTAssertEqual(speaker.said.last?.1, .answer)
        XCTAssertEqual(speaker.haptics.last??.symbol, .right)
        let state = await c.state
        XCTAssertEqual(state, .idle)
    }

    func testAStaleAnswerIsIgnored() async throws {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)
        await c.handle(.find("bathroom"))

        await c.receive(try SceneAnswer(queryId: "q-old", status: .notFound, subject: nil, bearingDeg: nil,
                                        detail: nil, confidence: nil, latencyMs: 10))

        XCTAssertEqual(speaker.said.count, 1)
    }

    func testNoLinkIsSaidAtOnceInsteadOfWaiting() async {
        let link = FakeLink(), speaker = FakeSpeaker()
        link.failing = true
        let c = coordinator(link, speaker)

        await c.handle(.describeAround)

        XCTAssertEqual(speaker.said.map(\.0), [AnswerPhrasing.noLink])
        let state = await c.state
        XCTAssertEqual(state, .idle)
    }

    func testNoAnswerTimesOutWithAnApology() async throws {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker, timeoutMs: 100)
        await c.handle(.find("exit"))

        try await Task.sleep(for: .milliseconds(300))

        XCTAssertEqual(speaker.said.last?.0, AnswerPhrasing.timedOut)
        let state = await c.state
        XCTAssertEqual(state, .idle)
    }

    func testCancelDropsTheQuestionInFlight() async throws {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)
        await c.handle(.find("exit"))
        await c.handle(.cancel)

        await c.receive(try SceneAnswer(queryId: "q-1", status: .notFound, subject: nil, bearingDeg: nil,
                                        detail: nil, confidence: nil, latencyMs: 10))

        XCTAssertEqual(speaker.said.map(\.0), ["Hold still, looking around.", "Okay."])
    }

    func testAnAnswerFasterThanTheSendIsStillHeard() async throws {
        final class InstantLink: QueryLink, @unchecked Sendable {
            weak var coordinator: QueryCoordinator?
            func send(_ query: SceneQuery) async throws {
                let answer = try SceneAnswer(queryId: query.queryId, status: .notFound, subject: nil, bearingDeg: nil,
                                             detail: nil, confidence: nil, latencyMs: 1)
                await coordinator?.receive(answer)
            }
        }
        let link = InstantLink(), speaker = FakeSpeaker()
        let c = QueryCoordinator(link: link, speaker: speaker, makeId: { "q-9" })
        link.coordinator = c

        await c.handle(.find("bathroom"))

        XCTAssertTrue(speaker.said.map(\.0).contains("I don't see a bathroom nearby."))
    }

    func testNavigationAndSignReadingAreNotItsJob() async {
        let c = coordinator(FakeLink(), FakeSpeaker())
        let nav = await c.handle(.navigate("library"))
        let sign = await c.handle(.readSign)
        XCTAssertFalse(nav)
        XCTAssertFalse(sign)
    }
}
