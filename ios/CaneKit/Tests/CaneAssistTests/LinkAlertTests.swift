import XCTest

@testable import CaneAssist

final class LinkAlertTests: XCTestCase {
    func testAShortDropIsNotAnnounced() {
        var alert = LinkAlert()
        XCTAssertEqual(alert.update(connected: false, reason: .laptopUnreachable, nowMs: 0), .none)
        XCTAssertEqual(alert.update(connected: false, reason: .laptopUnreachable, nowMs: 4_000), .none)
        XCTAssertEqual(alert.update(connected: true, reason: .laptopUnreachable, nowMs: 4_500), .none)
        XCTAssertNil(alert.showing)
    }

    func testALongDropIsAnnouncedOnceThenRepeatedAMinuteLater() {
        var alert = LinkAlert()
        _ = alert.update(connected: false, reason: .noWiFi, nowMs: 0)
        XCTAssertEqual(alert.update(connected: false, reason: .noWiFi, nowMs: 5_000), .lost(.noWiFi))
        XCTAssertEqual(alert.showing, .noWiFi)
        XCTAssertEqual(alert.update(connected: false, reason: .noWiFi, nowMs: 30_000), .none)
        XCTAssertEqual(alert.update(connected: false, reason: .noWiFi, nowMs: 65_000), .lost(.noWiFi))
    }

    func testANewCauseIsSaidAtOnce() {
        // Wi-Fi turned back on, but the laptop is still not answering.
        var alert = LinkAlert()
        _ = alert.update(connected: false, reason: .noWiFi, nowMs: 0)
        _ = alert.update(connected: false, reason: .noWiFi, nowMs: 5_000)
        XCTAssertEqual(alert.update(connected: false, reason: .laptopUnreachable, nowMs: 8_000), .lost(.laptopUnreachable))
    }

    func testReconnectingAfterAnAnnouncedDropIsSaid() {
        var alert = LinkAlert()
        _ = alert.update(connected: false, reason: .noWiFi, nowMs: 0)
        _ = alert.update(connected: false, reason: .noWiFi, nowMs: 5_000)
        XCTAssertEqual(alert.update(connected: true, reason: .laptopUnreachable, nowMs: 9_000), .restored)
        XCTAssertNil(alert.showing)
        XCTAssertEqual(alert.update(connected: true, reason: .laptopUnreachable, nowMs: 10_000), .none)
    }

    func testTheCause() {
        XCTAssertEqual(LinkAlert.reason(networkUp: true, onWiFi: false, laptopHost: "10.108.43.185"), .noWiFi)
        XCTAssertEqual(LinkAlert.reason(networkUp: false, onWiFi: false, laptopHost: "10.108.43.185"), .noNetwork)
        XCTAssertEqual(LinkAlert.reason(networkUp: true, onWiFi: true, laptopHost: "10.108.43.185"), .laptopUnreachable)
        // On the phone's own hotspot the phone is not a Wi-Fi client.
        XCTAssertEqual(LinkAlert.reason(networkUp: true, onWiFi: false, laptopHost: "172.20.10.2"), .laptopUnreachable)
    }
}
