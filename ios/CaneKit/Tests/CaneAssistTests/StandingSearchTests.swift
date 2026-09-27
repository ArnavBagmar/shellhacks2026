import CaneOutput
import CaneTransport
import CaneVoice
import XCTest

@testable import CaneAssist

/// Indoors, "I don't see a bathroom nearby" is a dead end the wearer can walk
/// out of. These tests pin the alternative: the promise is made once, the
/// re-asks are silent, and it ends -- out loud -- rather than hanging.
final class StandingSearchTests: XCTestCase {
    /// A clock the test advances, so the reassurance and give-up windows can be
    /// crossed without waiting them out.
    private final class Clock: @unchecked Sendable {
        private let lock = NSLock()
        private var ms: Int64 = 1_790_000_000_000
        var now: Int64 { lock.withLock { ms } }
        func advance(ms delta: Int64) { lock.withLock { ms += delta } }
    }

    private final class Ids: @unchecked Sendable {
        private let lock = NSLock()
        private var n = 0
        func next() -> String { lock.withLock { n += 1; return "q-\(n)" } }
    }

    private func coordinator(
        _ link: FakeLink, _ speaker: FakeSpeaker, clock: Clock, ids: Ids, indoors: Bool
    ) -> QueryCoordinator {
        QueryCoordinator(
            link: link, speaker: speaker,
            clock: { clock.now },
            makeId: { ids.next() },
            timeoutMs: 10_000,
            keepSearching: { indoors },
            standingRetryMs: 20,
            choosePhrase: { _ in 0 }
        )
    }

    private func notFound(_ queryId: String) -> SceneAnswer {
        try! SceneAnswer(queryId: queryId, status: .notFound, subject: nil, bearingDeg: nil,
                         detail: nil, confidence: nil, latencyMs: 900)
    }

    private func found(_ queryId: String) -> SceneAnswer {
        try! SceneAnswer(queryId: queryId, status: .found, subject: "restroom sign", bearingDeg: 72.5,
                         detail: nil, confidence: .medium, latencyMs: 900)
    }

    /// Give the silent re-ask time to land.
    private func waitForSends(_ link: FakeLink, atLeast count: Int) async {
        for _ in 0..<100 where link.sent.count < count {
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    // MARK: - Indoors

    func testIndoorsANotFoundPromisesToKeepLookingInsteadOfGivingUp() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))

        let spoken = speaker.said.map(\.0)
        XCTAssertTrue(
            spoken.contains("Not yet. Keep walking and I'll tell you when I see a bathroom."),
            "\(spoken)"
        )
        XCTAssertFalse(spoken.contains("I don't see a bathroom nearby."), "\(spoken)")
    }

    func testThePromiseIsSpokenOnTheAnswerChannelSoItIsNotDropped() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))

        let promise = speaker.said.first { $0.0.hasPrefix("Not yet.") }
        XCTAssertEqual(promise?.1, .answer)
    }

    func testTheSearchAsksAgainWhileTheWearerWalks() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))
        await waitForSends(link, atLeast: 2)

        XCTAssertGreaterThanOrEqual(link.sent.count, 2)
        XCTAssertEqual(link.sent.last?.target, "bathroom")
        XCTAssertEqual(link.sent.last?.kind, .find)
    }

    func testTheReAsksAreSilentSoTheWearerIsNotToldToHoldStillEveryFewSeconds() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        let afterFirstAsk = speaker.said.count
        await c.receive(notFound("q-1"))
        await waitForSends(link, atLeast: 2)
        let afterPromise = speaker.said.count

        // The promise itself is spoken; the re-ask that follows adds nothing.
        XCTAssertGreaterThan(afterPromise, afterFirstAsk)
        await waitForSends(link, atLeast: 3)
        XCTAssertEqual(speaker.said.count, afterPromise, "\(speaker.said.map(\.0))")
    }

    func testFindingItLaterSpeaksTheRealAnswerAndEndsTheSearch() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))
        await waitForSends(link, atLeast: 2)
        await c.receive(found("q-2"))

        XCTAssertTrue(speaker.said.map(\.0).contains("To your right, restroom sign."),
                      "\(speaker.said.map(\.0))")
        let sendsAtAnswer = link.sent.count
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(link.sent.count, sendsAtAnswer, "the search kept polling after it was answered")
    }

    func testTheSearchSaysSoWhenItGivesUp() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))
        await waitForSends(link, atLeast: 2)
        clock.advance(ms: Int64(QueryCoordinator.standingSearchMaxMs) + 1)
        await c.receive(notFound("q-2"))

        let spoken = speaker.said.map(\.0)
        XCTAssertTrue(
            spoken.contains { $0.hasPrefix("I still haven't seen a bathroom.") },
            "\(spoken)"
        )
        let sends = link.sent.count
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(link.sent.count, sends, "it kept polling after giving up")
    }

    func testAskingSomethingElseRetiresTheSearch() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))
        await c.handle(.describeAround, utterance: "what is around me")
        let sends = link.sent.count

        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(link.sent.count, sends, "the retired search kept polling")
    }

    func testCancelRetiresTheSearch() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: true)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))
        await c.handle(.cancel)
        let sends = link.sent.count

        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(link.sent.count, sends)
    }

    // MARK: - Outdoors

    func testOutdoorsANotFoundIsJustAnAnswer() async {
        let link = FakeLink(), speaker = FakeSpeaker(), clock = Clock(), ids = Ids()
        let c = coordinator(link, speaker, clock: clock, ids: ids, indoors: false)

        await c.handle(.find("bathroom"), utterance: "is there a bathroom around")
        await c.receive(notFound("q-1"))

        XCTAssertTrue(speaker.said.map(\.0).contains("I don't see a bathroom nearby."),
                      "\(speaker.said.map(\.0))")
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(link.sent.count, 1, "it kept looking outdoors, where Maps owns navigation")
    }
}
