import XCTest
import CaneOutput

final class StereoPanTests: XCTestCase {

    /// The invariant that keeps a hazard announcement audible with one bud
    /// in, on either side. iOS downmixes to mono for a single AirPod and the
    /// route API gives no way to detect it, which is why the cap exists.
    func test_noBearingCanAttenuateAChannelBelowTheFloor() {
        for bearing in stride(from: -180.0, through: 180.0, by: 0.5) {
            let pan = StereoPan.pan(bearingDeg: bearing)
            XCTAssertGreaterThanOrEqual(StereoPan.minChannelGain(pan),
                                        StereoPan.minChannelGainFloor - 1e-9,
                                        "bearing \(bearing)")
            XCTAssertLessThanOrEqual(abs(pan), StereoPan.panCap + 1e-9)
        }
    }

    /// Negative is the user's left, matching `BearingSector`.
    func test_panSignMatchesTheBearingConvention() {
        XCTAssertLessThan(StereoPan.pan(bearingDeg: -90), 0)
        XCTAssertGreaterThan(StereoPan.pan(bearingDeg: 90), 0)
        XCTAssertEqual(StereoPan.pan(bearingDeg: -90), -StereoPan.panCap, accuracy: 1e-9)
        XCTAssertEqual(StereoPan.pan(bearingDeg: 90), StereoPan.panCap, accuracy: 1e-9)
    }

    /// Front/back collapse is accepted: the haptic and the words separate
    /// them.
    func test_frontAndBackBothCollapseToCentre() {
        XCTAssertEqual(StereoPan.pan(bearingDeg: 0), 0)
        XCTAssertEqual(StereoPan.pan(bearingDeg: 180), 0)
        XCTAssertEqual(StereoPan.pan(bearingDeg: -180), 0)
    }

    func test_panMirrorsAcrossTheMidline() {
        for degrees in 1...179 {
            let bearing = Double(degrees)
            XCTAssertEqual(StereoPan.pan(bearingDeg: -bearing),
                           -StereoPan.pan(bearingDeg: bearing), accuracy: 1e-12)
        }
    }

    /// The built-in speakers are ~15 cm apart at 30 cm from the chest, and a
    /// phone mounted upside-down in the strap swaps L and R.
    func test_routeGateSilencesPanningOnTheBuiltInSpeakers() {
        XCTAssertEqual(StereoPan.pan(bearingDeg: -90, route: .builtInSpeaker, channelCount: 2), 0)
        XCTAssertEqual(StereoPan.pan(bearingDeg: -90, route: .builtInReceiver, channelCount: 2), 0)
        XCTAssertEqual(StereoPan.pan(bearingDeg: -90, route: .other, channelCount: 2), 0)
    }

    func test_routeGateRequiresTwoChannels() {
        XCTAssertEqual(StereoPan.pan(bearingDeg: -90, route: .bluetoothA2DP, channelCount: 1), 0)
        XCTAssertLessThan(StereoPan.pan(bearingDeg: -90, route: .bluetoothA2DP, channelCount: 2), 0)
    }

    func test_everyAllowedRoutePans() {
        for route: PanRoute in [.headphones, .bluetoothA2DP, .bluetoothLE, .usbAudio, .airPlay] {
            XCTAssertNotEqual(StereoPan.pan(bearingDeg: 90, route: route, channelCount: 2), 0, "\(route)")
        }
    }
}
