import XCTest
import CaneCamera

final class DepthProbeTests: XCTestCase {

    /// A flat wall two metres away reads as two metres.
    func test_medianOverTheProbeRegion() {
        let values = [Float](repeating: 2.0, count: 100 * 100)
        let sample = DepthProbe.sample(values: values, width: 100, height: 100,
                                       accuracy: .absolute, timestampMs: 0)
        XCTAssertEqual(sample?.metres ?? 0, 2.0, accuracy: 1e-6)
        XCTAssertEqual(sample?.coverage ?? 0, 1.0, accuracy: 1e-6)
        XCTAssertTrue(sample?.isTrustworthy() ?? false)
    }

    /// Only the central 10% x 10% counts: a close object at the edge of frame
    /// must not be reported as what is straight ahead.
    func test_onlyTheCentreCounts() {
        var values = [Float](repeating: 2.5, count: 100 * 100)
        for y in 0..<100 where y < 20 {
            for x in 0..<20 { values[y * 100 + x] = 0.3 }  // something close, top-left
        }
        let sample = DepthProbe.sample(values: values, width: 100, height: 100,
                                       accuracy: .absolute, timestampMs: 0)
        XCTAssertEqual(sample?.metres ?? 0, 2.5, accuracy: 1e-6)
    }

    /// Pixels the sensor could not resolve are ignored, and coverage says how
    /// much of the region actually came back.
    func test_holesAreIgnoredAndCounted() {
        var values = [Float](repeating: Float.nan, count: 100 * 100)
        let box = DepthProbe.centreRegion(width: 100, height: 100)
        var filled = 0
        for y in box.y0..<box.y1 {
            for x in box.x0..<box.x1 where (x + y) % 2 == 0 {
                values[y * 100 + x] = 1.5
                filled += 1
            }
        }
        let sample = DepthProbe.sample(values: values, width: 100, height: 100,
                                       accuracy: .absolute, timestampMs: 0)
        XCTAssertEqual(sample?.metres ?? 0, 1.5, accuracy: 1e-6)
        XCTAssertEqual(sample?.coverage ?? 0, 0.5, accuracy: 0.05)
        XCTAssertTrue(sample?.isTrustworthy() ?? false)
    }

    /// Bright sun washes out the infrared pattern: the failure is coverage
    /// collapsing, not values going wrong, so the gate must catch it.
    func test_sunlightLooksLikeLostCoverageAndIsRefused() {
        var values = [Float](repeating: Float.nan, count: 100 * 100)
        let box = DepthProbe.centreRegion(width: 100, height: 100)
        values[box.y0 * 100 + box.x0] = 1.2  // a single surviving pixel
        let sample = DepthProbe.sample(values: values, width: 100, height: 100,
                                       accuracy: .absolute, timestampMs: 0)
        XCTAssertEqual(sample?.metres ?? 0, 1.2, accuracy: 1e-6)
        XCTAssertLessThan(sample?.coverage ?? 1, DepthProbe.minCoverage)
        XCTAssertFalse(sample?.isTrustworthy() ?? true, "a near-empty region is not a distance")
    }

    /// `.relative` means shape without scale. It must never be spoken as
    /// metres, the same rule the X4 path and Depth Anything obey.
    func test_relativeAccuracyIsNeverADistance() {
        let values = [Float](repeating: 1.5, count: 100 * 100)
        let sample = DepthProbe.sample(values: values, width: 100, height: 100,
                                       accuracy: .relative, timestampMs: 0)
        XCTAssertEqual(sample?.accuracy, .relative)
        XCTAssertFalse(sample?.isTrustworthy() ?? true)
    }

    /// TrueDepth is built for arm's length. Outside its working range the
    /// reading exists but is not trustworthy.
    func test_outsideWorkingRangeIsRefused() {
        for metres in [Float(0.05), Float(8.0)] {
            let values = [Float](repeating: metres, count: 100 * 100)
            let sample = DepthProbe.sample(values: values, width: 100, height: 100,
                                           accuracy: .absolute, timestampMs: 0)
            XCTAssertFalse(sample?.isTrustworthy() ?? true, "\(metres) m should not be trusted")
        }
        // Inside the range it is.
        let ok = DepthProbe.sample(values: [Float](repeating: 1.0, count: 100 * 100),
                                   width: 100, height: 100, accuracy: .absolute, timestampMs: 0)
        XCTAssertTrue(ok?.isTrustworthy() ?? false)
    }

    func test_nearestIsReportedAlongsideTheMedian() {
        var values = [Float](repeating: 2.0, count: 100 * 100)
        let box = DepthProbe.centreRegion(width: 100, height: 100)
        values[box.y0 * 100 + box.x0] = 0.8  // one much closer pixel
        let sample = DepthProbe.sample(values: values, width: 100, height: 100,
                                       accuracy: .absolute, timestampMs: 0)
        XCTAssertEqual(sample?.metres ?? 0, 2.0, accuracy: 1e-6, "one pixel must not move the median")
        XCTAssertEqual(sample?.nearestMetres ?? 0, 0.8, accuracy: 1e-6)
    }

    func test_emptyAndMismatchedInputsReturnNil() {
        XCTAssertNil(DepthProbe.sample(values: [], width: 0, height: 0, accuracy: .absolute, timestampMs: 0))
        XCTAssertNil(DepthProbe.sample(values: [1.0], width: 10, height: 10, accuracy: .absolute, timestampMs: 0))
    }

    /// The probe matches the desktop preview's centre box, so "ahead" means
    /// the same thing in both tools.
    func test_centreRegionMatchesTheDesktopPreview() {
        let box = DepthProbe.centreRegion(width: 1920, height: 1080)
        XCTAssertEqual(box.x0, 864)
        XCTAssertEqual(box.x1, 1056)
        XCTAssertEqual(box.y0, 486)
        XCTAssertEqual(box.y1, 594)
        let tiny = DepthProbe.centreRegion(width: 1, height: 1)
        XCTAssertEqual(tiny.x1, 1)
        XCTAssertEqual(tiny.y1, 1)
    }
}
