import XCTest

@testable import CaneTransport

final class GuideUpdateTests: XCTestCase {
    func testTheWireFrameHasEveryKeyWithExplicitNulls() throws {
        let update = try GuideUpdate(tPhoneMs: 1_790_000_000_000, state: "find_hand", target: "rubik's cube",
                                     box: .init(xMin: 0.6, yMin: 0.5, xMax: 0.7, yMax: 0.62), hand: nil,
                                     gapM: nil, metric: false, hint: nil, frame: nil)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: update.encoded()) as? [String: Any])
        XCTAssertEqual(Set(object.keys), ["type", "t_phone_ms", "state", "target", "box", "hand", "gap_m", "metric",
                                          "hint", "frame"])
        XCTAssertEqual(object["type"] as? String, "guide")
        XCTAssertTrue(object["hand"] is NSNull)
        XCTAssertEqual((object["box"] as? [String: Double])?["x_max"], 0.7)
    }

    func testAFrameAndAHandTravelToo() throws {
        let frame = try PhoneFrame(jpeg: Data([0xFF, 0xD8, 0xFF, 0xE0]), hfovDeg: 70, width: 480, height: 360)
        let update = try GuideUpdate(tPhoneMs: 1, state: "guiding", target: "cup", box: nil, hand: (0.25, 0.5),
                                     gapM: 0.12, metric: true, hint: "Right.", frame: frame)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: update.encoded()) as? [String: Any])
        XCTAssertEqual((object["hand"] as? [String: Double])?["x"], 0.25)
        XCTAssertEqual((object["frame"] as? [String: Any])?["width"] as? Int, 480)
    }

    func testTheIdleCameraCarriesNoTarget() throws {
        let update = try GuideUpdate(tPhoneMs: 1, state: "idle", target: nil, box: nil, hand: nil, gapM: nil,
                                     metric: false, hint: nil, frame: nil)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: update.encoded()) as? [String: Any])
        XCTAssertTrue(object["target"] is NSNull)
        XCTAssertThrowsError(try GuideUpdate(tPhoneMs: 1, state: "guiding", target: nil, box: nil, hand: nil,
                                             gapM: nil, metric: false, hint: nil, frame: nil))
        XCTAssertThrowsError(try GuideUpdate(tPhoneMs: 1, state: "idle", target: "cup", box: nil, hand: nil,
                                             gapM: nil, metric: false, hint: nil, frame: nil))
    }

    func testNonsenseIsRefusedBeforeItIsSent() {
        XCTAssertThrowsError(try GuideUpdate(tPhoneMs: 1, state: "dancing", target: "cup", box: nil, hand: nil,
                                             gapM: nil, metric: false, hint: nil, frame: nil))
        XCTAssertThrowsError(try GuideUpdate(tPhoneMs: 1, state: "guiding", target: "cup", box: nil, hand: (1.5, 0.5),
                                             gapM: nil, metric: false, hint: nil, frame: nil))
    }
}
