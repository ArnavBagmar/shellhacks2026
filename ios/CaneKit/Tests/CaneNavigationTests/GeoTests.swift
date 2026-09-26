import XCTest

@testable import CaneNavigation

/// `Geo` is the only place in `CaneNavigation` that knows spherical
/// trigonometry; every distance, bearing and projection the follower and
/// the policy use comes from here, so the sign conventions are pinned
/// down by these tests (step-12 brief F6 · GeoTests).
final class GeoTests: XCTestCase {
    private let origin = LatLng(latitude: 25.761_70, longitude: -80.191_80)

    // MARK: - Distance

    func testOneDegreeOfLatitudeIsAboutOneHundredElevenThousandMetres() {
        let north = LatLng(latitude: origin.latitude + 1, longitude: origin.longitude)
        XCTAssertEqual(Geo.haversineMeters(origin, north), 111_195, accuracy: 50)
    }

    func testDistanceToSelfIsZero() {
        XCTAssertEqual(Geo.haversineMeters(origin, origin), 0, accuracy: 1e-6)
    }

    func testDistanceIsSymmetric() {
        let other = LatLng(latitude: 25.763_00, longitude: -80.193_00)
        XCTAssertEqual(
            Geo.haversineMeters(origin, other),
            Geo.haversineMeters(other, origin),
            accuracy: 1e-6
        )
    }

    // MARK: - Bearing

    func testBearingsForTheFourCardinalDirections() {
        let north = LatLng(latitude: origin.latitude + 0.001, longitude: origin.longitude)
        let south = LatLng(latitude: origin.latitude - 0.001, longitude: origin.longitude)
        let east = LatLng(latitude: origin.latitude, longitude: origin.longitude + 0.001)
        let west = LatLng(latitude: origin.latitude, longitude: origin.longitude - 0.001)

        XCTAssertEqual(Geo.initialBearingDeg(from: origin, to: north), 0, accuracy: 0.01)
        XCTAssertEqual(Geo.initialBearingDeg(from: origin, to: east), 90, accuracy: 0.01)
        XCTAssertEqual(Geo.initialBearingDeg(from: origin, to: south), 180, accuracy: 0.01)
        XCTAssertEqual(Geo.initialBearingDeg(from: origin, to: west), 270, accuracy: 0.01)
    }

    func testBearingIsNormalisedIntoZeroToThreeSixty() {
        let west = LatLng(latitude: origin.latitude, longitude: origin.longitude - 0.001)
        let bearing = Geo.initialBearingDeg(from: origin, to: west)
        XCTAssertGreaterThanOrEqual(bearing, 0)
        XCTAssertLessThan(bearing, 360)
    }

    // MARK: - destinationPoint

    func testDestinationPointRoundTripsThroughDistanceAndBearing() {
        let moved = Geo.destinationPoint(from: origin, bearingDeg: 45, distanceMeters: 250)
        XCTAssertEqual(Geo.haversineMeters(origin, moved), 250, accuracy: 0.05)
        XCTAssertEqual(Geo.initialBearingDeg(from: origin, to: moved), 45, accuracy: 0.05)
    }

    func testDestinationPointDueNorthKeepsLongitude() {
        let moved = Geo.destinationPoint(from: origin, bearingDeg: 0, distanceMeters: 100)
        XCTAssertEqual(moved.longitude, origin.longitude, accuracy: 1e-9)
        XCTAssertGreaterThan(moved.latitude, origin.latitude)
    }

    // MARK: - Cross-track / along-track

    /// A 100 m northbound segment; a point 50 m along it and 10 m to the
    /// *left* (west). Left of the path is negative by convention -- the
    /// one definition the follower's `offRouteM` and the policy's turn
    /// direction both lean on.
    func testCrossTrackIsNegativeToTheLeftAndPositiveToTheRight() {
        let start = origin
        let end = Geo.destinationPoint(from: origin, bearingDeg: 0, distanceMeters: 100)
        let onPath = Geo.destinationPoint(from: origin, bearingDeg: 0, distanceMeters: 50)
        let left = Geo.destinationPoint(from: onPath, bearingDeg: 270, distanceMeters: 10)
        let right = Geo.destinationPoint(from: onPath, bearingDeg: 90, distanceMeters: 10)

        XCTAssertEqual(Geo.crossTrackMeters(point: left, start: start, end: end), -10, accuracy: 0.1)
        XCTAssertEqual(Geo.crossTrackMeters(point: right, start: start, end: end), 10, accuracy: 0.1)
        XCTAssertEqual(Geo.crossTrackMeters(point: onPath, start: start, end: end), 0, accuracy: 0.01)
    }

    func testAlongTrackMeasuresProgressAndGoesNegativeBeforeTheStart() {
        let start = origin
        let end = Geo.destinationPoint(from: origin, bearingDeg: 0, distanceMeters: 100)
        let midway = Geo.destinationPoint(from: origin, bearingDeg: 0, distanceMeters: 40)
        let before = Geo.destinationPoint(from: origin, bearingDeg: 180, distanceMeters: 15)

        XCTAssertEqual(Geo.alongTrackMeters(point: midway, start: start, end: end), 40, accuracy: 0.1)
        XCTAssertEqual(Geo.alongTrackMeters(point: before, start: start, end: end), -15, accuracy: 0.1)
    }

    func testDistanceToSegmentClampsToTheEndpoints() {
        let start = origin
        let end = Geo.destinationPoint(from: origin, bearingDeg: 0, distanceMeters: 100)
        let beyond = Geo.destinationPoint(from: end, bearingDeg: 0, distanceMeters: 30)
        let besideMiddle = Geo.destinationPoint(
            from: Geo.destinationPoint(from: origin, bearingDeg: 0, distanceMeters: 50),
            bearingDeg: 90,
            distanceMeters: 12
        )

        XCTAssertEqual(Geo.distanceToSegmentMeters(point: beyond, start: start, end: end), 30, accuracy: 0.1)
        XCTAssertEqual(
            Geo.distanceToSegmentMeters(point: besideMiddle, start: start, end: end), 12, accuracy: 0.1
        )
    }

    func testDegenerateSegmentFallsBackToPointDistance() {
        let point = Geo.destinationPoint(from: origin, bearingDeg: 90, distanceMeters: 20)
        XCTAssertEqual(
            Geo.distanceToSegmentMeters(point: point, start: origin, end: origin), 20, accuracy: 0.1
        )
        XCTAssertEqual(Geo.crossTrackMeters(point: point, start: origin, end: origin), 0, accuracy: 1e-9)
    }

    // MARK: - Heading-error normalisation

    func testHeadingErrorNormalisesIntoMinusOneEightyExclusiveToOneEightyInclusive() {
        XCTAssertEqual(Geo.normalizedAngleDeg(350 - 10), -20, accuracy: 1e-9)
        XCTAssertEqual(Geo.normalizedAngleDeg(10 - 350), 20, accuracy: 1e-9)
        XCTAssertEqual(Geo.normalizedAngleDeg(180), 180, accuracy: 1e-9)
        XCTAssertEqual(Geo.normalizedAngleDeg(-180), 180, accuracy: 1e-9)
        XCTAssertEqual(Geo.normalizedAngleDeg(540), 180, accuracy: 1e-9)
        XCTAssertEqual(Geo.normalizedAngleDeg(-190), 170, accuracy: 1e-9)
        XCTAssertEqual(Geo.normalizedAngleDeg(0), 0, accuracy: 1e-9)
    }
}
