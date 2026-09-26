import XCTest

@testable import CaneNavigation

/// Free text to a point on the map (step-12 brief F2 / H3). Places Text
/// Search is preferred for spoken destinations ("the library");
/// Geocoding is the drop-in fallback for structured addresses and for
/// keys without Places.
final class DestinationResolverTests: XCTestCase {
    private let testKey = "test-key-not-a-real-credential"
    private let here = LatLng(latitude: 25.761_70, longitude: -80.191_80)

    private let placesReply = """
        {"places":[
          {"location":{"latitude":25.7634,"longitude":-80.1917},
           "displayName":{"text":"Main Library","languageCode":"en"},
           "formattedAddress":"101 W Flagler St, Miami, FL 33130, USA"},
          {"location":{"latitude":25.7700,"longitude":-80.1900},
           "displayName":{"text":"Another Library","languageCode":"en"},
           "formattedAddress":"somewhere else"}
        ]}
        """

    // MARK: - Places Text Search

    func testPlacesRequestMatchesTheDocumentedShape() async throws {
        let transport = FakeHTTPTransport.alwaysOK(json: placesReply)
        let resolver = PlacesTextSearchResolver(transport: transport, apiKey: { self.testKey })

        _ = try await resolver.resolve(freeText: "library", near: here)

        let request = await transport.requests[0]
        XCTAssertEqual(request.method, "POST")
        XCTAssertEqual(
            request.url.absoluteString, "https://places.googleapis.com/v1/places:searchText"
        )
        XCTAssertEqual(request.headers["X-Goog-Api-Key"], testKey)
        XCTAssertEqual(
            request.headers["X-Goog-FieldMask"],
            "places.location,places.displayName,places.formattedAddress"
        )

        let body = try await transport.requestBody(at: 0)
        XCTAssertEqual(body["textQuery"] as? String, "library")
        let circle = (body["locationBias"] as? [String: Any])?["circle"] as? [String: Any]
        let center = circle?["center"] as? [String: Any]
        XCTAssertEqual(center?["latitude"] as? Double, here.latitude)
        XCTAssertEqual(center?["longitude"] as? Double, here.longitude)
        XCTAssertEqual(circle?["radius"] as? Double, 2000)
    }

    func testPlacesRequestOmitsTheBiasWhenThereIsNoFixYet() async throws {
        let transport = FakeHTTPTransport.alwaysOK(json: placesReply)
        let resolver = PlacesTextSearchResolver(transport: transport, apiKey: { self.testKey })

        _ = try await resolver.resolve(freeText: "library", near: nil)

        let body = try await transport.requestBody(at: 0)
        XCTAssertNil(body["locationBias"])
    }

    func testPlacesTakesTheFirstResult() async throws {
        let resolver = PlacesTextSearchResolver(
            transport: FakeHTTPTransport.alwaysOK(json: placesReply), apiKey: { self.testKey }
        )

        let place = try await resolver.resolve(freeText: "library", near: here)

        XCTAssertEqual(place.name, "Main Library")
        XCTAssertEqual(place.latLng, LatLng(latitude: 25.7634, longitude: -80.1917))
        XCTAssertEqual(place.formattedAddress, "101 W Flagler St, Miami, FL 33130, USA")
    }

    func testPlacesEmptyResultIsDestinationNotFound() async {
        let resolver = PlacesTextSearchResolver(
            transport: FakeHTTPTransport.alwaysOK(json: #"{"places":[]}"#), apiKey: { self.testKey }
        )

        await assertThrows(NavigationError.destinationNotFound("library")) {
            _ = try await resolver.resolve(freeText: "library", near: self.here)
        }
    }

    func testPlacesHTTPErrorSurfacesStatusAndMessage() async {
        let json = """
            {"error":{"code":403,"message":"Places API has not been used in project.","status":"PERMISSION_DENIED"}}
            """
        let resolver = PlacesTextSearchResolver(
            transport: FakeHTTPTransport.always(status: 403, json: json), apiKey: { self.testKey }
        )

        await assertThrows(
            NavigationError.routesAPI(status: 403, message: "Places API has not been used in project.")
        ) {
            _ = try await resolver.resolve(freeText: "library", near: self.here)
        }
    }

    func testPlacesMissingKeyMakesNoRequest() async {
        let transport = FakeHTTPTransport.alwaysOK(json: placesReply)
        let resolver = PlacesTextSearchResolver(
            transport: transport, apiKey: { throw NavigationError.missingAPIKey }
        )

        await assertThrows(NavigationError.missingAPIKey) {
            _ = try await resolver.resolve(freeText: "library", near: self.here)
        }
        let count = await transport.requests.count
        XCTAssertEqual(count, 0)
    }

    // MARK: - Geocoding fallback

    private let geocodingReply = """
        {"status":"OK","results":[
          {"formatted_address":"1200 Library St, Miami, FL 33130, USA",
           "geometry":{"location":{"lat":25.7617,"lng":-80.1918}}}
        ]}
        """

    func testGeocodingRequestIsAGetWithTheQueryAndKey() async throws {
        let transport = FakeHTTPTransport.alwaysOK(json: geocodingReply)
        let resolver = GeocodingResolver(transport: transport, apiKey: { self.testKey })

        _ = try await resolver.resolve(freeText: "1200 Library St & 1st Ave", near: nil)

        let request = await transport.requests[0]
        XCTAssertEqual(request.method, "GET")
        XCTAssertNil(request.body)
        let components = URLComponents(url: request.url, resolvingAgainstBaseURL: false)
        XCTAssertEqual(components?.host, "maps.googleapis.com")
        XCTAssertEqual(components?.path, "/maps/api/geocode/json")
        let items = Dictionary(
            uniqueKeysWithValues: (components?.queryItems ?? []).map { ($0.name, $0.value) }
        )
        XCTAssertEqual(items["address"], "1200 Library St & 1st Ave")
        XCTAssertEqual(items["key"], testKey)
        XCTAssertFalse(
            request.url.absoluteString.contains("&1st"), "the query must be percent-encoded"
        )
    }

    func testGeocodingOKTakesTheFirstResult() async throws {
        let resolver = GeocodingResolver(
            transport: FakeHTTPTransport.alwaysOK(json: geocodingReply), apiKey: { self.testKey }
        )

        let place = try await resolver.resolve(freeText: "1200 Library St", near: nil)

        XCTAssertEqual(place.latLng, LatLng(latitude: 25.7617, longitude: -80.1918))
        XCTAssertEqual(place.formattedAddress, "1200 Library St, Miami, FL 33130, USA")
        XCTAssertEqual(place.name, "1200 Library St, Miami, FL 33130, USA")
    }

    /// `ZERO_RESULTS` arrives as HTTP 200 (brief H3): the status field,
    /// not the HTTP code, decides.
    func testGeocodingZeroResultsIsDestinationNotFoundDespiteHTTP200() async {
        let resolver = GeocodingResolver(
            transport: FakeHTTPTransport.alwaysOK(json: #"{"status":"ZERO_RESULTS","results":[]}"#),
            apiKey: { self.testKey }
        )

        await assertThrows(NavigationError.destinationNotFound("the library")) {
            _ = try await resolver.resolve(freeText: "the library", near: nil)
        }
    }

    func testGeocodingRequestDeniedSurfacesTheStatus() async {
        let json = #"{"status":"REQUEST_DENIED","error_message":"The provided API key is invalid."}"#
        let resolver = GeocodingResolver(
            transport: FakeHTTPTransport.alwaysOK(json: json), apiKey: { self.testKey }
        )

        await assertThrowsNavigationError(subject: {
            _ = try await resolver.resolve(freeText: "library", near: nil)
        }) { error in
            guard case .routesAPI(let status, let message) = error else {
                return XCTFail("expected .routesAPI, got \(error)")
            }
            XCTAssertEqual(status, 200)
            XCTAssertTrue(message.contains("REQUEST_DENIED"), message)
            XCTAssertTrue(message.contains("The provided API key is invalid."), message)
        }
    }

    func testGeocodingMissingKeyMakesNoRequest() async {
        let transport = FakeHTTPTransport.alwaysOK(json: geocodingReply)
        let resolver = GeocodingResolver(
            transport: transport, apiKey: { throw NavigationError.missingAPIKey }
        )

        await assertThrows(NavigationError.missingAPIKey) {
            _ = try await resolver.resolve(freeText: "library", near: nil)
        }
        let count = await transport.requests.count
        XCTAssertEqual(count, 0)
    }
}
