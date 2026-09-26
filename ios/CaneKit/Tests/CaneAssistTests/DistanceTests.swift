import CaneCamera
import CaneTransport
import XCTest

@testable import CaneAssist

private let us = Locale(identifier: "en_US")
private let metric = Locale(identifier: "en_DE")

/// A wall at 2.5 m with a can at `can` metres filling x 0.30-0.40, y 0.40-0.60.
private func canOnTable(can: Float = 1.2, hole: Bool = false, accuracy: DepthAccuracy = .absolute) -> DepthMap {
    let width = 100, height = 80
    var values = [Float](repeating: 2.5, count: width * height)
    for row in 0..<height {
        for col in 0..<width {
            let fx = (Double(col) + 0.5) / Double(width), fy = (Double(row) + 0.5) / Double(height)
            if (0.30...0.40).contains(fx), (0.40...0.60).contains(fy) { values[row * width + col] = hole ? .nan : can }
        }
    }
    return DepthMap(values: values, width: width, height: height, accuracy: accuracy, timestampMs: 0)!
}

private let canBox = try! PhoneBox(xMin: 0.30, yMin: 0.40, xMax: 0.40, yMax: 0.60)

final class DistanceOutcomeTests: XCTestCase {
    func testAClearReadingIsMeasured() {
        guard case .measured(let sample) = DistanceOutcome.evaluate(box: canBox, depth: canOnTable()) else {
            return XCTFail("not measured")
        }
        XCTAssertEqual(sample.metres, 1.2, accuracy: 0.001)
    }

    func testNoBoxMeansItWasNotInThePhonePhoto() {
        XCTAssertEqual(DistanceOutcome.evaluate(box: nil, depth: canOnTable()), .outOfView)
    }

    func testNoMapMeansNoSensor() {
        XCTAssertEqual(DistanceOutcome.evaluate(box: canBox, depth: nil), .noSensor)
    }

    func testPastTheRangeIsTooFar() {
        guard case .tooFar = DistanceOutcome.evaluate(box: canBox, depth: canOnTable(can: 4.5)) else {
            return XCTFail("not too far")
        }
    }

    func testHolesAndRelativeScaleAreUnreadable() {
        guard case .unreadable = DistanceOutcome.evaluate(box: canBox, depth: canOnTable(hole: true)) else {
            return XCTFail("holes read as a distance")
        }
        guard case .unreadable = DistanceOutcome.evaluate(box: canBox, depth: canOnTable(accuracy: .relative)) else {
            return XCTFail("relative depth read as a distance")
        }
    }
}

final class DistanceWordsTests: XCTestCase {
    func testFeetInTheUS() {
        XCTAssertEqual(DistanceWords.away(1.2, locale: us), "about 4 feet away")
        XCTAssertEqual(DistanceWords.away(0.35, locale: us), "about a foot away")
        XCTAssertEqual(DistanceWords.away(0.25, locale: us), "less than a foot away")
        XCTAssertEqual(DistanceWords.beyondRange(locale: us), "more than 10 feet away")
    }

    func testMetresElsewhere() {
        XCTAssertEqual(DistanceWords.away(1.24, locale: metric), "about 1.2 metres away")
        XCTAssertEqual(DistanceWords.away(2.0, locale: metric), "about 2 metres away")
        XCTAssertEqual(DistanceWords.away(1.02, locale: metric), "about 1 metre away")
        XCTAssertEqual(DistanceWords.away(0.64, locale: metric), "about 60 centimetres away")
    }
}

final class DistancePhrasingTests: XCTestCase {
    private func found(bearing: Double? = 0, detail: String? = nil, box: PhoneBox? = canBox) throws -> SceneAnswer {
        try SceneAnswer(queryId: "q-1", status: .found, subject: "sprite can", bearingDeg: bearing,
                        detail: detail, confidence: .high, latencyMs: 1_500, phoneBox: box)
    }

    private func say(_ answer: SceneAnswer, _ outcome: DistanceOutcome?, asked: Bool) -> String {
        AnswerPhrasing.phrase(for: answer, target: "sprite can", distance: outcome, asked: asked, locale: us)
    }

    func testTheDistanceFollowsTheThing() throws {
        let outcome = DistanceOutcome.evaluate(box: canBox, depth: canOnTable())
        let text = say(try found(detail: "on the table"), outcome, asked: true)
        XCTAssertTrue(text.hasSuffix(", sprite can, about 4 feet away, on the table."), text)
    }

    func testAMeasuredDistanceIsAddedEvenUnasked() throws {
        let outcome = DistanceOutcome.evaluate(box: canBox, depth: canOnTable())
        XCTAssertTrue(say(try found(), outcome, asked: false).hasSuffix("sprite can, about 4 feet away."))
    }

    func testAskedButOutOfViewSaysHowToGetOne() throws {
        let text = say(try found(bearing: 120, box: nil), .outOfView, asked: true)
        XCTAssertTrue(text.hasSuffix("sprite can. " + AnswerPhrasing.faceItToMeasure), text)
    }

    func testUnaskedFailuresAddNothing() throws {
        XCTAssertFalse(say(try found(box: nil), .outOfView, asked: false).contains("measure"))
        XCTAssertFalse(say(try found(), .unreadable(nil), asked: false).contains("distance"))
    }

    func testTooFarIsSaidAsARangeNotANumber() throws {
        let outcome = DistanceOutcome.evaluate(box: canBox, depth: canOnTable(can: 4.5))
        XCTAssertTrue(say(try found(), outcome, asked: true).hasSuffix("sprite can, more than 10 feet away."))
    }

    func testNoSensorAndNoCleanReadingSaySo() throws {
        XCTAssertTrue(say(try found(), .noSensor, asked: true).hasSuffix(AnswerPhrasing.noDepthSensor))
        XCTAssertTrue(say(try found(), .unreadable(nil), asked: true).hasSuffix(AnswerPhrasing.noCleanReading))
    }

    func testGeminisDetailBecomesAClause() {
        XCTAssertEqual(AnswerPhrasing.clause("On the table in front of you."), "on the table in front of you")
        XCTAssertEqual(AnswerPhrasing.clause("Starbucks sign."), "Starbucks sign")
        XCTAssertEqual(AnswerPhrasing.clause("ATM by the door"), "ATM by the door")
    }

    func testWithoutABearingTheDistanceStillComes() throws {
        let outcome = DistanceOutcome.evaluate(box: canBox, depth: canOnTable())
        XCTAssertEqual(say(try found(bearing: nil), outcome, asked: true),
                       "Sprite can nearby, about 4 feet away, direction unclear.")
    }
}

final class HowFarCoordinatorTests: XCTestCase {
    private func coordinator(_ link: FakeLink, _ speaker: FakeSpeaker, depth: DepthMap?,
                             outcomes: Box<[DistanceOutcome]>) -> QueryCoordinator {
        let frame = try? PhoneFrame(jpeg: Data([0xFF, 0xD8, 0xFF, 0xE0]), hfovDeg: 70, width: 1280, height: 960)
        return QueryCoordinator(link: link, speaker: speaker, snapshot: { FrontCapture(frame: frame, depth: depth) },
                                clock: { 1_790_000_000_000 }, makeId: { "q-1" }, timeoutMs: 10_000, locale: us,
                                onDistance: { outcomes.append($0) })
    }

    func testHowFarSendsAFindAndSpeaksTheMeasuredDistance() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), outcomes = Box<[DistanceOutcome]>([])
        let c = coordinator(link, speaker, depth: canOnTable(), outcomes: outcomes)

        await c.handle(.howFar("sprite can"), utterance: "how far is the sprite can")
        XCTAssertEqual(link.sent.first?.kind, .find)
        XCTAssertEqual(link.sent.first?.target, "sprite can")

        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "sprite can", bearingDeg: -5,
                                        detail: nil, confidence: .high, latencyMs: 1_500, phoneBox: canBox))

        XCTAssertTrue(speaker.said.last?.0.hasSuffix("sprite can, about 4 feet away.") ?? false, "\(speaker.said)")
        guard case .measured = outcomes.value.first else { return XCTFail("\(outcomes.value)") }
    }

    func testAPhoneWithoutDepthSaysItCannotMeasure() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), outcomes = Box<[DistanceOutcome]>([])
        let c = coordinator(link, speaker, depth: nil, outcomes: outcomes)
        await c.handle(.howFar("sprite can"))

        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "sprite can", bearingDeg: 0,
                                        detail: nil, confidence: .high, latencyMs: 1_500, phoneBox: canBox))

        XCTAssertTrue(speaker.said.last?.0.hasSuffix(AnswerPhrasing.noDepthSensor) ?? false, "\(speaker.said)")
        XCTAssertEqual(outcomes.value, [.noSensor])
    }

    func testAPlainFindThatIsNotInViewSaysNothingAboutDistance() async throws {
        let link = FakeLink(), speaker = FakeSpeaker(), outcomes = Box<[DistanceOutcome]>([])
        let c = coordinator(link, speaker, depth: canOnTable(), outcomes: outcomes)
        await c.handle(.find("bathroom"))

        await c.receive(try SceneAnswer(queryId: "q-1", status: .found, subject: "restroom sign", bearingDeg: 80,
                                        detail: nil, confidence: .high, latencyMs: 1_500))

        XCTAssertEqual(speaker.said.last?.0, "To your right, restroom sign.")
    }
}

final class Box<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var _value: Value
    init(_ value: Value) { _value = value }
    var value: Value { lock.withLock { _value } }
    func append<Element>(_ element: Element) where Value == [Element] { lock.withLock { _value.append(element) } }
}
