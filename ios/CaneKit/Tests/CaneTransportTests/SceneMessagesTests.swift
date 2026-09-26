import Foundation
import XCTest

@testable import CaneTransport

/// Step 20's `query`/`answer` frames against the same golden fixture the
/// Python suite runs: every valid case decodes, every invalid one throws.
final class SceneMessagesTests: XCTestCase {
    func testEveryFixtureCaseDecodesOrIsRejectedLikePython() throws {
        let cases = try FixturePaths.queryAnswerCases()
        XCTAssertGreaterThan(cases.count, 20)
        for item in cases {
            let data = try JSONSerialization.data(withJSONObject: item.frame)
            let type = item.frame["type"] as? String
            do {
                if type == "query" {
                    _ = try SceneQuery.decode(data)
                } else if type == "speak" {
                    _ = try SpeakRequest.decode(data)
                } else if type == "voice" {
                    guard case .voice = try InboundMessage.decode(data) else {
                        XCTFail("\(item.name): decoded to something other than .voice")
                        continue
                    }
                } else {
                    guard case .answer = try InboundMessage.decode(data) else {
                        XCTFail("\(item.name): decoded to something other than .answer")
                        continue
                    }
                }
                XCTAssertTrue(item.valid, "\(item.name) should have been rejected")
            } catch {
                XCTAssertFalse(item.valid, "\(item.name) should decode, threw \(error)")
            }
        }
    }

    func testASpeakRequestEncodesItsThreeKeys() throws {
        let data = try SpeakRequest(speakId: "s-1", text: "Let me check.").encoded()
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object as NSDictionary, ["type": "speak", "speak_id": "s-1", "text": "Let me check."])
        XCTAssertEqual(try SpeakRequest.decode(data).text, "Let me check.")
    }

    func testAVoiceClipDecodesItsAudio() throws {
        let frame: [String: Any] = [
            "type": "voice", "speak_id": "s-1", "status": "ok", "format": "mp3_44100_64",
            "audio_b64": Data("ID3 audio".utf8).base64EncodedString(), "latency_ms": 260,
        ]
        guard case .voice(let clip) = try InboundMessage.decode(JSONSerialization.data(withJSONObject: frame)) else {
            return XCTFail("not a voice frame")
        }
        XCTAssertEqual(clip.audio, Data("ID3 audio".utf8))
        XCTAssertEqual(clip.latencyMs, 260)
    }

    func testAFoundAnswerDecodesEveryField() throws {
        let frame = try XCTUnwrap(FixturePaths.queryAnswerCases().first { $0.name == "answer_found" }).frame
        guard case .answer(let answer) = try InboundMessage.decode(JSONSerialization.data(withJSONObject: frame)) else {
            return XCTFail("expected .answer")
        }
        XCTAssertEqual(answer.queryId, "q-0001")
        XCTAssertEqual(answer.status, .found)
        XCTAssertEqual(answer.subject, "restroom sign")
        XCTAssertEqual(answer.bearingDeg, 72.5)
        XCTAssertEqual(answer.detail, "past the elevators")
        XCTAssertEqual(answer.confidence, .medium)
        XCTAssertEqual(answer.latencyMs, 2140)
    }

    func testAQueryEncodesAllSevenKeysWithExplicitNulls() throws {
        let query = try SceneQuery(
            queryId: "q-1", kind: .describeAround, target: nil,
            utterance: "what's around me", tPhoneMs: 1_790_000_000_000, phoneFrame: nil
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: query.encoded()) as? [String: Any])

        XCTAssertEqual(Set(object.keys), ["type", "query_id", "kind", "target", "utterance", "t_phone_ms", "phone_frame"])
        XCTAssertEqual(object["type"] as? String, "query")
        XCTAssertEqual(object["kind"] as? String, "describe_around")
        XCTAssertTrue(object["target"] is NSNull)
        XCTAssertTrue(object["phone_frame"] is NSNull)
        XCTAssertEqual((object["t_phone_ms"] as? NSNumber)?.int64Value, 1_790_000_000_000)
    }

    func testAQueryRoundTripsThroughItsOwnDecoder() throws {
        let frame = try PhoneFrame(jpeg: Data([0xFF, 0xD8, 0xFF, 0xE0, 0x00]), hfovDeg: 68, width: 1280, height: 960)
        let query = try SceneQuery(
            queryId: "q-2", kind: .find, target: "bathroom",
            utterance: "is there a bathroom around", tPhoneMs: 1_790_000_000_001, phoneFrame: frame
        )

        XCTAssertEqual(try SceneQuery.decode(query.encoded()), query)
    }

    func testFindWithoutATargetCannotBeBuilt() {
        XCTAssertThrowsError(try SceneQuery(
            queryId: "q-3", kind: .find, target: nil, utterance: "", tPhoneMs: 0, phoneFrame: nil
        )) { error in
            XCTAssertEqual(error as? TransportError, .invalidFrame("target"))
        }
    }

    func testAPhoneBoxDecodesAndItsAbsenceIsNil() throws {
        let boxed = try XCTUnwrap(FixturePaths.queryAnswerCases().first { $0.name == "answer_found_with_phone_box" }).frame
        guard case .answer(let answer) = try InboundMessage.decode(JSONSerialization.data(withJSONObject: boxed)) else {
            return XCTFail("not an answer")
        }
        XCTAssertEqual(answer.phoneBox, try PhoneBox(xMin: 0.31, yMin: 0.42, xMax: 0.39, yMax: 0.61))

        var plain = boxed
        plain.removeValue(forKey: "phone_box")
        guard case .answer(let without) = try InboundMessage.decode(JSONSerialization.data(withJSONObject: plain)) else {
            return XCTFail("not an answer")
        }
        XCTAssertNil(without.phoneBox)
    }

    func testAnInvertedPhoneBoxIsRejected() throws {
        // The schema cannot say x_min < x_max; both decoders do.
        var frame = try XCTUnwrap(FixturePaths.queryAnswerCases().first { $0.name == "answer_found_with_phone_box" }).frame
        frame["phone_box"] = ["x_min": 0.5, "y_min": 0.1, "x_max": 0.4, "y_max": 0.2]
        XCTAssertThrowsError(try InboundMessage.decode(JSONSerialization.data(withJSONObject: frame)))
    }

    func testABoolIsNotAnIntegerTimestamp() throws {
        var frame = try XCTUnwrap(FixturePaths.queryAnswerCases().first { $0.name == "query_find_no_frame" }).frame
        frame["t_phone_ms"] = true
        XCTAssertThrowsError(try SceneQuery.decode(JSONSerialization.data(withJSONObject: frame)))
    }
}

final class DiagnosticEventTests: XCTestCase {
    func testADiagFrameHasTheShapeTheLaptopValidates() throws {
        let event = DiagnosticEvent(tPhoneMs: 1_790_000_000_000, kind: "transcript",
                                    data: ["text": .text("hey cane"), "final": .flag(false), "level": .number(0.4)])
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: event.encoded()) as? [String: Any])
        XCTAssertEqual(Set(object.keys), ["type", "t_phone_ms", "kind", "data"])
        XCTAssertEqual(object["type"] as? String, "diag")
        let data = try XCTUnwrap(object["data"] as? [String: Any])
        XCTAssertEqual(data["text"] as? String, "hey cane")
        XCTAssertEqual(data["final"] as? Bool, false)
    }

    func testLongTextIsCappedAtTheLaptopsLimit() throws {
        let event = DiagnosticEvent(tPhoneMs: 0, kind: "note", data: ["text": .text(String(repeating: "x", count: 900))])
        let data = try XCTUnwrap(event.jsonObject["data"] as? [String: Any])
        XCTAssertEqual((data["text"] as? String)?.count, 500)
    }
}
