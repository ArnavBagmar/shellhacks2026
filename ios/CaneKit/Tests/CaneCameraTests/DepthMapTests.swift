import XCTest

@testable import CaneCamera

final class DepthMapTests: XCTestCase {
    /// 4 wide, 2 high: values are 1...8 row by row.
    private let small = DepthMap(values: [1, 2, 3, 4, 5, 6, 7, 8], width: 4, height: 2,
                                 accuracy: .absolute, timestampMs: 0)!

    /// A wall at `wall` metres with a `can`-metre object filling the given
    /// fractions of the frame.
    private func scene(width: Int = 100, height: Int = 80, wall: Float = 2.5, can: Float = 1.0,
                       x: ClosedRange<Double>, y: ClosedRange<Double>) -> DepthMap {
        var values = [Float](repeating: wall, count: width * height)
        for row in 0..<height {
            for col in 0..<width {
                let fx = (Double(col) + 0.5) / Double(width), fy = (Double(row) + 0.5) / Double(height)
                if x.contains(fx), y.contains(fy) { values[row * width + col] = can }
            }
        }
        return DepthMap(values: values, width: width, height: height, accuracy: .absolute, timestampMs: 0)!
    }

    func test_aMismatchedMapIsRefused() {
        XCTAssertNil(DepthMap(values: [1, 2, 3], width: 2, height: 2, accuracy: .absolute, timestampMs: 0))
    }

    func test_aQuarterTurnClockwise() {
        // 1 2 3 4        5 1
        // 5 6 7 8   ->   6 2
        //                7 3
        //                8 4
        let turned = small.rotatedClockwise(90)
        XCTAssertEqual(turned.width, 2)
        XCTAssertEqual(turned.height, 4)
        XCTAssertEqual(turned.values, [5, 1, 6, 2, 7, 3, 8, 4])
    }

    func test_halfAndThreeQuarterTurns() {
        XCTAssertEqual(small.rotatedClockwise(180).values, [8, 7, 6, 5, 4, 3, 2, 1])
        let turned = small.rotatedClockwise(270)
        XCTAssertEqual(turned.width, 2)
        XCTAssertEqual(turned.values, [4, 8, 3, 7, 2, 6, 1, 5])
        XCTAssertEqual(small.rotatedClockwise(-90), turned)
    }

    func test_fourQuarterTurnsAreTheIdentity() {
        var map = small
        for _ in 0..<4 { map = map.rotatedClockwise(90) }
        XCTAssertEqual(map, small)
        XCTAssertEqual(small.rotatedClockwise(0), small)
    }

    func test_theThingInTheBoxIsMeasuredNotTheWallBehindIt() throws {
        let map = scene(x: 0.30...0.40, y: 0.40...0.60)
        let sample = try XCTUnwrap(map.distance(inBox: 0.30, 0.40, 0.40, 0.60))
        XCTAssertEqual(sample.metres, 1.0, accuracy: 0.001)
        XCTAssertEqual(sample.coverage, 1.0, accuracy: 0.001)
        XCTAssertTrue(sample.isTrustworthy())
    }

    func test_aLooseBoxStillLeansToTheNearerThing() throws {
        // Gemini's box is twice as wide as the can: part of the inner
        // region is wall, and the 30th percentile still lands on the can.
        let map = scene(x: 0.40...0.50, y: 0.30...0.70)
        let sample = try XCTUnwrap(map.distance(inBox: 0.35, 0.30, 0.55, 0.70))
        XCTAssertEqual(sample.metres, 1.0, accuracy: 0.001)
        XCTAssertEqual(sample.nearestMetres, 1.0, accuracy: 0.001)
    }

    func test_theEdgesOfTheBoxAreIgnored() throws {
        // A near rim around the box (a table edge in the corner) is outside
        // the inner 60% and does not count.
        var values = [Float](repeating: 2.0, count: 100 * 100)
        for i in 0..<100 { values[i] = 0.5; values[i * 100] = 0.5 }
        let map = DepthMap(values: values, width: 100, height: 100, accuracy: .absolute, timestampMs: 0)!
        let sample = try XCTUnwrap(map.distance(inBox: 0, 0, 1, 1))
        XCTAssertEqual(sample.nearestMetres, 2.0, accuracy: 0.001)
    }

    func test_holesLowerCoverageAndSunlightIsRefused() throws {
        var values = [Float](repeating: .nan, count: 10 * 10)
        values[55] = 1.2
        let map = DepthMap(values: values, width: 10, height: 10, accuracy: .absolute, timestampMs: 0)!
        let sample = try XCTUnwrap(map.distance(inBox: 0, 0, 1, 1))
        XCTAssertLessThan(sample.coverage, DepthProbe.minCoverage)
        XCTAssertFalse(sample.isTrustworthy())
    }

    func test_aTinyBoxStillReadsOnePixel() throws {
        let sample = try XCTUnwrap(scene(x: 0...1, y: 0...1).distance(inBox: 0.5, 0.5, 0.5001, 0.5001))
        XCTAssertEqual(sample.metres, 1.0, accuracy: 0.001)
    }

    func test_anEmptyBoxIsNil() {
        XCTAssertNil(small.distance(inBox: 0.5, 0.5, 0.5, 0.9))
    }

    func test_aBoxOnTheUprightPhotoFindsTheThingInATurnedMap() throws {
        // The sensor's map is landscape; the photo was turned 90 degrees
        // clockwise to stand upright. An object in the map's top-left lands
        // top-right in the photo, and a box drawn there must find it.
        let sensor = scene(width: 80, height: 60, x: 0.05...0.25, y: 0.05...0.30)
        let upright = sensor.rotatedClockwise(90)
        let sample = try XCTUnwrap(upright.distance(inBox: 0.72, 0.07, 0.93, 0.23))
        XCTAssertEqual(sample.metres, 1.0, accuracy: 0.001)
        // And the same box on the unturned map misses it.
        XCTAssertEqual(try XCTUnwrap(sensor.distance(inBox: 0.72, 0.07, 0.93, 0.23)).metres, 2.5, accuracy: 0.001)
    }
}
