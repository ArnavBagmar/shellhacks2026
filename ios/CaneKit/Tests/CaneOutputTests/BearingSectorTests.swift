import XCTest
import CaneOutput

final class BearingSectorTests: XCTestCase {

    /// Amendment 6 names this test. A sign inversion mirrors every
    /// announcement the product will ever make and passes every magnitude
    /// test, so this is the anchor.
    func test_negativeBearingIsTheUsersLeft() {
        XCTAssertEqual(BearingSector.sector(-90).side, .left)
        XCTAssertEqual(BearingSector.sector(90).side, .right)
        XCTAssertTrue(BearingSector.phrase(-90).contains("left"))
        XCTAssertTrue(BearingSector.phrase(90).contains("right"))
        XCTAssertLessThan(StereoPan.pan(bearingDeg: -90), 0)
    }

    /// Amendment 6 names this one too.
    func test_mirrorProperty() {
        for degrees in 1...179 {
            let bearing = Double(degrees)
            XCTAssertEqual(BearingSector.sector(-bearing).side,
                           BearingSector.sector(bearing).side.mirrored,
                           "bearing \(bearing) does not mirror")
        }
    }

    /// E6's boundaries: necessary but not sufficient -- they are all
    /// magnitude tests and cannot catch a left/right swap.
    func test_boundaries() {
        XCTAssertEqual(BearingSector.sector(14.9), .ahead)
        XCTAssertEqual(BearingSector.sector(15), .aheadQuarter(.right))
        XCTAssertEqual(BearingSector.sector(60), .lateral(.right))
        XCTAssertEqual(BearingSector.sector(120), .behindQuarter(.right))
        XCTAssertEqual(BearingSector.sector(165), .behindQuarter(.right))
        XCTAssertEqual(BearingSector.sector(165.1), .behind)
        XCTAssertEqual(BearingSector.sector(-165), .behindQuarter(.left))
        XCTAssertEqual(BearingSector.sector(180), .behind)
    }

    func test_centreSectorsCarryNoSide() {
        XCTAssertEqual(BearingSector.sector(0), .ahead)
        XCTAssertEqual(BearingSector.sector(0).side, .center)
        XCTAssertEqual(BearingSector.sector(180).side, .center)
        XCTAssertEqual(BearingSector.sector(-0.0).side, .center)
    }

    func test_everySectorHasWords() {
        for bearing in stride(from: -180.0, through: 180.0, by: 0.5) {
            XCTAssertFalse(BearingSector.phrase(bearing).isEmpty)
        }
    }
}
