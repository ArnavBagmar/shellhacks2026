import XCTest

@testable import CaneCamera

final class CameraGeometryTests: XCTestCase {
    func test_anObjectOnTheWearersLeftHasANegativeBearing() {
        for mount in MountOrientation.allCases {
            let hfov = CameraGeometry.imageHorizontalFOV(sensorLongSideFOVDeg: 70, sensorAspect: 16.0 / 9, mount: mount)
            XCTAssertLessThan(CameraGeometry.bearingDeg(normalizedX: 0.2, hfovDeg: hfov), 0, "\(mount)")
            XCTAssertGreaterThan(CameraGeometry.bearingDeg(normalizedX: 0.8, hfovDeg: hfov), 0, "\(mount)")
            XCTAssertEqual(CameraGeometry.bearingDeg(normalizedX: 0.5, hfovDeg: hfov), 0, accuracy: 1e-9)
        }
    }

    func testTheImageEdgesAreHalfTheFieldOfView() {
        XCTAssertEqual(CameraGeometry.bearingDeg(normalizedX: 0, hfovDeg: 70), -35, accuracy: 1e-9)
        XCTAssertEqual(CameraGeometry.bearingDeg(normalizedX: 1, hfovDeg: 70), 35, accuracy: 1e-9)
        XCTAssertEqual(CameraGeometry.bearingDeg(normalizedX: 1.5, hfovDeg: 70), 35, accuracy: 1e-9)
    }

    func testPortraitNarrowsTheViewToTheSensorsShortSide() {
        let portrait = CameraGeometry.imageHorizontalFOV(sensorLongSideFOVDeg: 70, sensorAspect: 16.0 / 9, mount: .portrait)
        XCTAssertEqual(portrait, 2 * atan(tan(35 * .pi / 180) * 9 / 16) * 180 / .pi, accuracy: 1e-9)
        XCTAssertLessThan(portrait, 70)
        XCTAssertEqual(CameraGeometry.imageHorizontalFOV(sensorLongSideFOVDeg: 70, sensorAspect: 16.0 / 9, mount: .landscape), 70)
    }

    func testIntrinsicsGiveTheFieldOfView() {
        // cx = fx means 45 degrees each side.
        XCTAssertEqual(CameraGeometry.horizontalFOV(fx: 640, cx: 640)!, 90, accuracy: 1e-9)
        XCTAssertNil(CameraGeometry.horizontalFOV(fx: 0, cx: 640))
    }
}
