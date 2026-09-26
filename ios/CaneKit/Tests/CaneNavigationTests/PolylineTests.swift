import XCTest

@testable import CaneNavigation

/// Google's encoded polyline algorithm at precision 1e5 (step-12 brief
/// H2). `encode` exists so the test fixtures carry real encoded
/// geometry rather than hand-typed strings, so it is held to
/// byte-for-byte agreement with Google's documented example.
final class PolylineTests: XCTestCase {
    /// The example from the polyline-algorithm page.
    private let googleExample = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    private let googlePoints = [
        LatLng(latitude: 38.5, longitude: -120.2),
        LatLng(latitude: 40.7, longitude: -120.95),
        LatLng(latitude: 43.252, longitude: -126.453),
    ]

    func testDecodesGooglesDocumentedExample() throws {
        let decoded = try Polyline.decode(googleExample)

        XCTAssertEqual(decoded.count, 3)
        for (actual, expected) in zip(decoded, googlePoints) {
            XCTAssertEqual(actual.latitude, expected.latitude, accuracy: 1e-9)
            XCTAssertEqual(actual.longitude, expected.longitude, accuracy: 1e-9)
        }
    }

    func testEncodeReproducesGooglesDocumentedExample() {
        XCTAssertEqual(Polyline.encode(googlePoints), googleExample)
    }

    func testEncodeOfDecodeIsTheIdentityOnTheDocumentedExample() throws {
        XCTAssertEqual(Polyline.encode(try Polyline.decode(googleExample)), googleExample)
    }

    func testRoundTripPreservesCoordinatesToFiveDecimalPlaces() throws {
        let points = [
            LatLng(latitude: 25.761_70, longitude: -80.191_80),
            LatLng(latitude: 25.762_60, longitude: -80.191_80),
            LatLng(latitude: 25.762_60, longitude: -80.192_60),
            LatLng(latitude: 25.763_23, longitude: -80.192_60),
        ]

        let decoded = try Polyline.decode(Polyline.encode(points))

        XCTAssertEqual(decoded.count, points.count)
        for (actual, expected) in zip(decoded, points) {
            XCTAssertEqual(actual.latitude, expected.latitude, accuracy: 1e-5)
            XCTAssertEqual(actual.longitude, expected.longitude, accuracy: 1e-5)
        }
    }

    func testZeroDeltasRoundTrip() throws {
        let point = LatLng(latitude: 25.763_23, longitude: -80.192_60)
        let repeated = [point, point]

        let encoded = Polyline.encode(repeated)
        let decoded = try Polyline.decode(encoded)

        XCTAssertTrue(encoded.hasSuffix("??"), "a zero delta encodes as '?', got \(encoded)")
        XCTAssertEqual(decoded.count, 2)
        XCTAssertEqual(decoded[0], decoded[1])
    }

    func testNegativeDeltasRoundTrip() throws {
        let points = [
            LatLng(latitude: 10.0, longitude: 20.0),
            LatLng(latitude: 9.999_00, longitude: 19.998_00),
            LatLng(latitude: 9.997_00, longitude: 19.999_00),
        ]

        let decoded = try Polyline.decode(Polyline.encode(points))

        for (actual, expected) in zip(decoded, points) {
            XCTAssertEqual(actual.latitude, expected.latitude, accuracy: 1e-9)
            XCTAssertEqual(actual.longitude, expected.longitude, accuracy: 1e-9)
        }
    }

    func testEmptyStringDecodesToNoPoints() throws {
        XCTAssertEqual(try Polyline.decode("").count, 0)
    }

    func testEmptyPointListEncodesToEmptyString() {
        XCTAssertEqual(Polyline.encode([]), "")
    }

    func testTruncatedStringThrowsDecoding() {
        // The last character of the first longitude has been dropped, so
        // its continuation bit is set with nothing following it.
        let truncated = String(googleExample.prefix(9))

        XCTAssertThrowsError(try Polyline.decode(truncated)) { error in
            guard case NavigationError.decoding = error else {
                return XCTFail("expected .decoding, got \(error)")
            }
        }
    }

    func testLatitudeWithoutLongitudeThrowsDecoding() {
        XCTAssertThrowsError(try Polyline.decode("_p~iF")) { error in
            guard case NavigationError.decoding = error else {
                return XCTFail("expected .decoding, got \(error)")
            }
        }
    }
}
