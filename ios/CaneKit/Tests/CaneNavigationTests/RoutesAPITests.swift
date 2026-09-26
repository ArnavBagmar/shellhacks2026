import XCTest

@testable import CaneNavigation

/// `RoutesClient` against a fake transport: the request must match
/// step-12 brief H1 field for field (a wrong field is an HTTP 400 an
/// hour before the demo), and every documented failure shape must come
/// back as a `NavigationError` value.
final class RoutesAPITests: XCTestCase {
    private let testKey = "test-key-not-a-real-credential"
    private let expectedFieldMask = """
        routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,\
        routes.legs.steps.navigationInstruction,routes.legs.steps.polyline,\
        routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration,\
        routes.legs.steps.startLocation,routes.legs.steps.endLocation,\
        routes.legs.steps.travelMode
        """

    private func client(_ transport: FakeHTTPTransport) -> RoutesClient {
        RoutesClient(transport: transport, apiKey: { self.testKey })
    }

    // MARK: - Request shape

    func testWalkingRequestMatchesTheDocumentedBody() async throws {
        let transport = FakeHTTPTransport.alwaysOK(data: try NavigationFixtures.twoTurnsData())

        _ = try await client(transport).computeWalkingRoute(
            origin: NavigationFixtures.origin,
            destination: .address("1200 Library St, Miami, FL")
        )

        let body = try await transport.requestBody(at: 0)
        XCTAssertEqual(body["travelMode"] as? String, "WALK")
        XCTAssertEqual(body["polylineEncoding"] as? String, "ENCODED_POLYLINE")
        XCTAssertEqual(body["polylineQuality"] as? String, "HIGH_QUALITY")
        XCTAssertEqual(body["units"] as? String, "METRIC")
        XCTAssertEqual(body["languageCode"] as? String, "en-US")
        XCTAssertEqual(body["computeAlternativeRoutes"] as? Bool, false)

        let origin = body["origin"] as? [String: Any]
        let latLng = (origin?["location"] as? [String: Any])?["latLng"] as? [String: Any]
        XCTAssertEqual(latLng?["latitude"] as? Double, NavigationFixtures.origin.latitude)
        XCTAssertEqual(latLng?["longitude"] as? Double, NavigationFixtures.origin.longitude)

        let destination = body["destination"] as? [String: Any]
        XCTAssertEqual(destination?["address"] as? String, "1200 Library St, Miami, FL")
        XCTAssertNil(destination?["location"])
    }

    /// H1: "Do not send for WALK: routingPreference, trafficModel,
    /// transitPreferences, arrivalTime".
    func testWalkingRequestOmitsDrivingOnlyFields() async throws {
        let transport = FakeHTTPTransport.alwaysOK(data: try NavigationFixtures.twoTurnsData())

        _ = try await client(transport).computeWalkingRoute(
            origin: NavigationFixtures.origin, destination: .latLng(NavigationFixtures.destination)
        )

        let body = try await transport.requestBody(at: 0)
        for forbidden in ["routingPreference", "trafficModel", "transitPreferences", "arrivalTime", "departureTime"] {
            XCTAssertNil(body[forbidden], "\(forbidden) must not be sent for a WALK route")
        }
    }

    func testLatLngDestinationIsSentAsAWaypointLocation() async throws {
        let transport = FakeHTTPTransport.alwaysOK(data: try NavigationFixtures.twoTurnsData())

        _ = try await client(transport).computeWalkingRoute(
            origin: NavigationFixtures.origin, destination: .latLng(NavigationFixtures.destination)
        )

        let body = try await transport.requestBody(at: 0)
        let destination = body["destination"] as? [String: Any]
        let latLng = (destination?["location"] as? [String: Any])?["latLng"] as? [String: Any]
        XCTAssertEqual(latLng?["latitude"] as? Double, NavigationFixtures.destination.latitude)
        XCTAssertNil(destination?["address"])
    }

    func testHeadersCarryTheKeyAndTheExactFieldMask() async throws {
        let transport = FakeHTTPTransport.alwaysOK(data: try NavigationFixtures.twoTurnsData())

        _ = try await client(transport).computeWalkingRoute(
            origin: NavigationFixtures.origin, destination: .address("library")
        )

        let request = await transport.requests[0]
        XCTAssertEqual(request.method, "POST")
        XCTAssertEqual(
            request.url.absoluteString, "https://routes.googleapis.com/directions/v2:computeRoutes"
        )
        XCTAssertEqual(request.headers["X-Goog-Api-Key"], testKey)
        XCTAssertEqual(request.headers["X-Goog-FieldMask"], expectedFieldMask)
        XCTAssertEqual(request.headers["Content-Type"], "application/json")
    }

    // MARK: - Decoding the recorded two-turn route

    func testDecodesTheTwoTurnFixture() async throws {
        let transport = FakeHTTPTransport.alwaysOK(data: try NavigationFixtures.twoTurnsData())

        let route = try await client(transport).computeWalkingRoute(
            origin: NavigationFixtures.origin, destination: .address("library")
        )

        XCTAssertEqual(route.distanceMeters, 250)
        XCTAssertEqual(route.durationS, 179, accuracy: 0.001)
        XCTAssertEqual(route.points, [
            NavigationFixtures.origin, NavigationFixtures.firstCorner,
            NavigationFixtures.secondCorner, NavigationFixtures.destination,
        ])
        XCTAssertEqual(route.legs.count, 1)
        XCTAssertEqual(route.steps.count, 4)
        XCTAssertEqual(
            route.steps.map(\.maneuver), [.depart, .turnLeft, .turnRight, .unspecified]
        )
        XCTAssertEqual(route.steps.map(\.distanceMeters), [100, 80, 70, 0])
        XCTAssertEqual(route.steps[0].staticDurationS, 72, accuracy: 0.001)
        XCTAssertEqual(route.steps[1].instruction, "Turn left onto Library Walk")
        XCTAssertEqual(route.steps[1].points, [NavigationFixtures.firstCorner, NavigationFixtures.secondCorner])
        XCTAssertEqual(route.steps[1].start, NavigationFixtures.firstCorner)
        XCTAssertEqual(route.steps[1].end, NavigationFixtures.secondCorner)
        XCTAssertEqual(route.steps[3].instruction, "Your destination is on the left")
    }

    func testUnknownManeuverStringDecodesWithoutThrowing() async throws {
        let json = """
            {"routes":[{"distanceMeters":12,"duration":"9s",
            "polyline":{"encodedPolyline":"sqf|CvmmhNsD?"},
            "legs":[{"steps":[{"distanceMeters":12,"staticDuration":"9s",
            "polyline":{"encodedPolyline":"sqf|CvmmhNsD?"},
            "startLocation":{"latLng":{"latitude":25.7617,"longitude":-80.1918}},
            "endLocation":{"latLng":{"latitude":25.7626,"longitude":-80.1918}},
            "navigationInstruction":{"maneuver":"TELEPORT_LEFT","instructions":"Blink"},
            "travelMode":"WALK"}]}]}]}
            """

        let route = try await client(.alwaysOK(json: json)).computeWalkingRoute(
            origin: NavigationFixtures.origin, destination: .address("library")
        )

        XCTAssertEqual(route.steps[0].maneuver, .unknown)
        XCTAssertFalse(route.steps[0].maneuver.isTurn)
    }

    // MARK: - Failures

    func testPermissionDeniedBodyBecomesRoutesAPIError() async {
        let json = """
            {"error":{"code":403,"message":"Requests to this API are blocked.","status":"PERMISSION_DENIED"}}
            """
        let subject = client(.always(status: 403, json: json))

        await assertThrows(NavigationError.routesAPI(status: 403, message: "Requests to this API are blocked.")) {
            _ = try await subject.computeWalkingRoute(
                origin: NavigationFixtures.origin, destination: .address("library")
            )
        }
    }

    func testBadFieldMaskBodyBecomesRoutesAPIError() async {
        let json = """
            {"error":{"code":400,"message":"Request contains an invalid argument.","status":"INVALID_ARGUMENT"}}
            """
        let subject = client(.always(status: 400, json: json))

        await assertThrows(
            NavigationError.routesAPI(status: 400, message: "Request contains an invalid argument.")
        ) {
            _ = try await subject.computeWalkingRoute(
                origin: NavigationFixtures.origin, destination: .address("library")
            )
        }
    }

    func testEmptyRouteListBecomesNoRoute() async {
        let subject = client(.alwaysOK(json: #"{"routes":[]}"#))

        await assertThrows(NavigationError.noRoute) {
            _ = try await subject.computeWalkingRoute(
                origin: NavigationFixtures.origin, destination: .address("library")
            )
        }
    }

    func testGarbageBodyBecomesDecodingError() async {
        let subject = client(.alwaysOK(json: "not json at all"))

        await assertThrowsNavigationError(subject: {
            _ = try await subject.computeWalkingRoute(
                origin: NavigationFixtures.origin, destination: .address("library")
            )
        }) { error in
            guard case .decoding = error else { return XCTFail("expected .decoding, got \(error)") }
        }
    }

    func testTransportFailureBecomesTransportError() async {
        let subject = client(.alwaysFailing(with: FakeTransportExhausted()))

        await assertThrowsNavigationError(subject: {
            _ = try await subject.computeWalkingRoute(
                origin: NavigationFixtures.origin, destination: .address("library")
            )
        }) { error in
            guard case .transport = error else { return XCTFail("expected .transport, got \(error)") }
        }
    }

    /// The key is checked before the request is built, so a keyless
    /// build makes no network calls at all (brief F1).
    func testMissingKeyThrowsBeforeAnyTransportCall() async throws {
        let transport = FakeHTTPTransport.alwaysOK(data: try NavigationFixtures.twoTurnsData())
        let subject = RoutesClient(
            transport: transport, apiKey: { throw NavigationError.missingAPIKey }
        )

        await assertThrows(NavigationError.missingAPIKey) {
            _ = try await subject.computeWalkingRoute(
                origin: NavigationFixtures.origin, destination: .address("library")
            )
        }
        let count = await transport.requests.count
        XCTAssertEqual(count, 0)
    }
}
