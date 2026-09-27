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

    func testTheTimeIsSpokenInTheWearersClockStyle() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        let date = calendar.date(from: DateComponents(year: 2026, month: 9, day: 26, hour: 15, minute: 7))!
        XCTAssertEqual(AnswerPhrasing.time(date, locale: Locale(identifier: "en_US"), timeZone: calendar.timeZone),
                       "It's 3:07\u{202F}PM.")
    }

    func testNotUnderstoodSaysWhatWasHeardAndWhatWorks() {
        let phrase = AnswerPhrasing.notUnderstood(heard: "how is the weather today")
        XCTAssertTrue(phrase.hasPrefix("I heard: how is the weather today."), phrase)
        XCTAssertTrue(phrase.contains("find"), phrase)
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
        return QueryCoordinator(link: link, speaker: speaker, snapshot: { FrontCapture(frame: frame) }, clock: { 1_790_000_000_000 },
                                makeId: { "q-1" }, timeoutMs: timeoutMs, choosePhrase: { _ in 0 })
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

    func testAnyOtherQuestionIsAskedWithThePhotoAndNoHoldStill() async {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)

        await c.handle(.ask("how is the weather today"))

        XCTAssertEqual(link.sent.first?.kind, .ask)
        XCTAssertNil(link.sent.first?.target)
        XCTAssertEqual(link.sent.first?.utterance, "how is the weather today")
        XCTAssertNotNil(link.sent.first?.phoneFrame)
        XCTAssertEqual(speaker.said.map(\.0), [AnswerPhrasing.checking])
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

    // MARK: Guide my hand

    private final class GuideBox: @unchecked Sendable {
        private let lock = NSLock()
        private var _requests: [GuideRequest] = []
        var requests: [GuideRequest] { lock.withLock { _requests } }
        func add(_ r: GuideRequest) { lock.withLock { _requests.append(r) } }
    }

    private func guideCoordinator(_ link: FakeLink, _ speaker: FakeSpeaker, _ box: GuideBox) -> QueryCoordinator {
        let frame = try? PhoneFrame(jpeg: Data([0xFF, 0xD8, 0xFF, 0xE0]), hfovDeg: 70, width: 1280, height: 720)
        return QueryCoordinator(link: link, speaker: speaker, snapshot: { FrontCapture(frame: frame) },
                                clock: { 1_790_000_000_000 }, makeId: { "q-1" }, onGuide: { box.add($0); return "Reach out slowly." },
                                choosePhrase: { _ in 0 })
    }

    func testGuideMyHandFindsTheThingThenHandsItsPhotoBoxToTheGuide() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), guides = GuideBox()
        let c = guideCoordinator(link, speaker, guides)
        await c.handle(.guideHand("rubik's cube"))
        XCTAssertEqual(link.sent.first?.kind, .find)
        XCTAssertEqual(link.sent.first?.target, "rubik's cube")

        let box = try PhoneBox(xMin: 0.6, yMin: 0.5, xMax: 0.7, yMax: 0.62)
        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "Rubik's cube", bearingDeg: 12,
                                        detail: "on the table", confidence: .high, latencyMs: 1_500, phoneBox: box))

        XCTAssertEqual(speaker.said.last?.0, "Straight ahead, Rubik's cube, on the table. Reach out slowly.")
        XCTAssertEqual(guides.requests, [GuideRequest(target: "rubik's cube", box: box)])
    }

    func testGuideForAThingOnlyThe360SawAsksTheWearerToFaceIt() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), guides = GuideBox()
        let c = guideCoordinator(link, speaker, guides)
        await c.handle(.guideHand("cup"))
        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "cup", bearingDeg: 90,
                                        detail: nil, confidence: .high, latencyMs: 1_500))

        XCTAssertEqual(speaker.said.last?.0, "To your right, cup. " + AnswerPhrasing.faceItToGuide)
        XCTAssertTrue(guides.requests.isEmpty)
    }

    func testAPlainFindNeverStartsAGuide() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), guides = GuideBox()
        let c = guideCoordinator(link, speaker, guides)
        await c.handle(.find("cup"))
        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "cup", bearingDeg: 0, detail: nil,
                                        confidence: .high, latencyMs: 1_500,
                                        phoneBox: try PhoneBox(xMin: 0.4, yMin: 0.4, xMax: 0.5, yMax: 0.5)))
        XCTAssertTrue(guides.requests.isEmpty)
    }

    func testAFindWithinReachOffersTheHandGuideOnce() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), guides = GuideBox()
        let c = guideCoordinator(link, speaker, guides)
        let box = try PhoneBox(xMin: 0.4, yMin: 0.5, xMax: 0.5, yMax: 0.6)
        for _ in 0..<2 {
            await c.handle(.find("rubik's cube"))
            await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "Rubik's cube", bearingDeg: 2,
                                            detail: "on the table", confidence: .high, latencyMs: 900, phoneBox: box))
        }
        let answers = speaker.said.filter { $0.1 == .answer }.map(\.0)
        XCTAssertEqual(answers.first, "Straight ahead, Rubik's cube, on the table. " + AnswerPhrasing.offerGuide)
        XCTAssertEqual(answers.last, "Straight ahead, Rubik's cube, on the table.", "offered once, not every time")
    }

    func testHelpMeGrabItMeansTheLastThingFound() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), guides = GuideBox()
        let c = guideCoordinator(link, speaker, guides)

        await c.handle(.guideHand(nil))
        XCTAssertEqual(speaker.said.last?.0, AnswerPhrasing.guideWhat)
        XCTAssertTrue(link.sent.isEmpty)

        await c.handle(.find("rubik's cube"))
        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "Rubik's cube", bearingDeg: 60,
                                        detail: nil, confidence: .high, latencyMs: 1_500))
        await c.handle(.guideHand(nil))
        XCTAssertEqual(link.sent.last?.target, "rubik's cube")
    }

    func testARefindIsSilentAndReturnsTheNewBox() async throws {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)
        let box = try PhoneBox(xMin: 0.5, yMin: 0.5, xMax: 0.6, yMax: 0.6)
        async let found = c.refind("rubik's cube")
        for _ in 0..<200 where link.sent.isEmpty { try await Task.sleep(for: .milliseconds(5)) }
        XCTAssertEqual(link.sent.first?.target, "rubik's cube")
        XCTAssertNotNil(link.sent.first?.phoneFrame, "a fresh photo, or the box would be old again")
        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "Rubik's cube", bearingDeg: 5,
                                        detail: nil, confidence: .high, latencyMs: 900, phoneBox: box))
        let result = await found
        XCTAssertEqual(result, box)
        XCTAssertTrue(speaker.said.isEmpty, "no Hold still, no spoken answer")
        let state = await c.state
        XCTAssertEqual(state, .idle)
    }

    func testARefindThatOnlyThe360SeesIsNil() async throws {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)
        async let found = c.refind("cup")
        for _ in 0..<200 where link.sent.isEmpty { try await Task.sleep(for: .milliseconds(5)) }
        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "cup", bearingDeg: 90, detail: nil,
                                        confidence: .high, latencyMs: 900))
        let result = await found
        XCTAssertNil(result)
    }

    func testARealQuestionInFlightWinsOverARefind() async {
        let link = FakeLink(), speaker = FakeSpeaker()
        let c = coordinator(link, speaker)
        await c.handle(.find("exit"))
        let result = await c.refind("cup")
        XCTAssertNil(result)
        XCTAssertEqual(link.sent.count, 1)
    }

    func testNavigationAndSignReadingAreNotItsJob() async {
        let c = coordinator(FakeLink(), FakeSpeaker())
        let nav = await c.handle(.navigate("library"))
        let sign = await c.handle(.readSign)
        let time = await c.handle(.time)
        let help = await c.handle(.help)
        XCTAssertFalse(nav)
        XCTAssertFalse(sign)
        XCTAssertFalse(time)
        XCTAssertFalse(help)
    }
}


// MARK: - Laptop voice

final class FakeVoiceLink: VoiceLink, @unchecked Sendable {
    private let lock = NSLock()
    private var _sent: [SpeakRequest] = []
    let connected: Bool
    var onSend: (@Sendable (SpeakRequest) -> Void)?

    init(connected: Bool = true) { self.connected = connected }

    var sent: [SpeakRequest] { lock.withLock { _sent } }

    func send(_ request: SpeakRequest) async throws {
        guard connected else { throw TransportError.notConnected }
        lock.withLock { _sent.append(request) }
        onSend?(request)
    }
}

final class LaptopVoiceTests: XCTestCase {
    private let mp3 = Data("ID3 mp3".utf8)

    private func replying(_ status: VoiceClipStatus, audio: Data?) -> (FakeVoiceLink, LaptopVoice) {
        let link = FakeVoiceLink()
        let voice = LaptopVoice(link: link)
        link.onSend = { request in
            Task {
                let clip = try VoiceClip(speakId: request.speakId, status: status,
                                         format: audio == nil ? nil : "mp3_44100_64", audio: audio, latencyMs: 250)
                await voice.receive(clip)
            }
        }
        return (link, voice)
    }

    func testTheReplyForTheRequestIsReturned() async {
        let (link, voice) = replying(.ok, audio: mp3)
        let audio = await voice.audio(for: "Paris.")
        XCTAssertEqual(audio, mp3)
        XCTAssertEqual(link.sent.map(\.text), ["Paris."])
    }

    func testTheSameWordsAreFetchedOncePerWalk() async {
        let (link, voice) = replying(.ok, audio: mp3)
        _ = await voice.audio(for: "Let me check.")
        let again = await voice.audio(for: "Let me check.")
        XCTAssertEqual(again, mp3)
        XCTAssertEqual(link.sent.count, 1)
    }

    func testNoLinkIsNilAtOnce() async {
        let voice = LaptopVoice(link: FakeVoiceLink(connected: false))
        let ms = await elapsedMs { _ = await voice.audio(for: "Paris.") }
        XCTAssertLessThan(ms, 100)
    }

    func testAnUnavailableLaptopIsNotAskedAgainForAWhile() async {
        let (link, voice) = replying(.unavailable, audio: nil)
        let first = await voice.audio(for: "Paris.")
        let second = await voice.audio(for: "Rome.")
        XCTAssertNil(first)
        XCTAssertNil(second)
        XCTAssertEqual(link.sent.count, 1)
    }
}

private func elapsedMs(_ body: () async -> Void) async -> Double {
    let start = ContinuousClock.now
    await body()
    let d = ContinuousClock.now - start
    return Double(d.components.seconds) * 1000 + Double(d.components.attoseconds) / 1e15
}
