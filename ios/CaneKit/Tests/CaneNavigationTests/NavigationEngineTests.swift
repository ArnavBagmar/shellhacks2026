import XCTest

@testable import CaneNavigation

/// The actor end to end, on fakes and virtual time: the same acceptance
/// cue sequence the policy tests assert, but delivered through
/// `start` -> streams -> `cues` (step-12 brief F5/F6). No network, no
/// CoreLocation, no real sleeping.
final class NavigationEngineTests: XCTestCase {
    private let library = ResolvedPlace(
        name: "Main Library",
        latLng: NavigationFixtures.destination,
        formattedAddress: "101 W Flagler St, Miami, FL"
    )

    private struct Harness {
        let engine: NavigationEngine
        let locations: FakeLocationSource
        let headings: FakeHeadingSource
        let clock: ManualClock
    }

    private func makeHarness(
        transport: FakeHTTPTransport,
        resolver: FakeDestinationResolver? = nil
    ) -> Harness {
        let locations = FakeLocationSource()
        let headings = FakeHeadingSource()
        let clock = ManualClock()
        let engine = NavigationEngine(
            resolver: resolver ?? .returning(library),
            routes: RoutesClient(transport: transport, apiKey: { "test-key-not-a-real-credential" }),
            locations: locations,
            headings: headings,
            clock: clock
        )
        return Harness(engine: engine, locations: locations, headings: headings, clock: clock)
    }

    private func fixtureTransport() throws -> FakeHTTPTransport {
        FakeHTTPTransport.alwaysOK(data: try NavigationFixtures.twoTurnsData())
    }

    /// Drains the cue stream until it finishes.
    private func collect(_ engine: NavigationEngine) -> Task<[TurnCue], Never> {
        Task {
            var cues: [TurnCue] = []
            for await cue in engine.cues { cues.append(cue) }
            return cues
        }
    }

    // MARK: - The acceptance walk

    func testStartToALibraryPlaysTheAcceptanceCueSequence() async throws {
        let harness = makeHarness(transport: try fixtureTransport())
        let route = try NavigationFixtures.twoTurnsRoute()
        let samples = RouteWalkSimulator(route: route).samples()
        let collected = collect(harness.engine)

        harness.locations.emit(samples)
        await harness.engine.start(to: "library")
        harness.locations.finish()

        let cues = try await withTestTimeout { await collected.value }
        XCTAssertEqual(
            cues.map(\.phrase),
            ["Left in 30 metres", "Left now", "Right in 30 metres", "Right now", "Arrived"]
        )
        let status = await harness.engine.status
        XCTAssertEqual(status, .arrived)
        await harness.clock.releaseAll()
    }

    func testThePlaceIsAnnouncedBeforeAnyCueAndNoCueIsLost() async throws {
        let harness = makeHarness(transport: try fixtureTransport())
        let route = try NavigationFixtures.twoTurnsRoute()
        let samples = RouteWalkSimulator(route: route).samples()
        let collected = collect(harness.engine)
        final class Box: @unchecked Sendable { var announced: (String, Int)? }
        let box = Box()

        harness.locations.emit(samples)
        await harness.engine.start(to: "library") { place, route in
            box.announced = (place.name, route.distanceMeters)
        }
        harness.locations.finish()

        let cues = try await withTestTimeout { await collected.value }
        XCTAssertEqual(box.announced?.0, library.name)
        XCTAssertEqual(box.announced?.1, route.distanceMeters)
        XCTAssertEqual(cues.map(\.phrase).first, "Left in 30 metres", "fixes during the announcement are kept")
        let resolved = await harness.engine.resolved
        XCTAssertEqual(resolved, library)
        await harness.clock.releaseAll()
    }

    func testTheRouteRequestUsesTheResolvedPlaceAndTheFirstFix() async throws {
        let transport = try fixtureTransport()
        let harness = makeHarness(transport: transport)
        let route = try NavigationFixtures.twoTurnsRoute()
        let samples = RouteWalkSimulator(route: route).samples()

        harness.locations.emit(Array(samples.prefix(1)))
        await harness.engine.start(to: "library")
        await harness.engine.stop()

        let body = try await transport.requestBody(at: 0)
        let originLatLng =
            ((body["origin"] as? [String: Any])?["location"] as? [String: Any])?["latLng"]
            as? [String: Any]
        let destinationLatLng =
            ((body["destination"] as? [String: Any])?["location"] as? [String: Any])?["latLng"]
            as? [String: Any]
        XCTAssertEqual(originLatLng?["latitude"] as? Double, NavigationFixtures.origin.latitude)
        XCTAssertEqual(
            destinationLatLng?["latitude"] as? Double, NavigationFixtures.destination.latitude
        )
        await harness.clock.releaseAll()
    }

    func testStartToACoordinateSkipsTheResolver() async throws {
        let harness = makeHarness(
            transport: try fixtureTransport(), resolver: .failing(.destinationNotFound("never asked"))
        )
        let route = try NavigationFixtures.twoTurnsRoute()

        harness.locations.emit(Array(RouteWalkSimulator(route: route).samples().prefix(1)))
        await harness.engine.start(to: NavigationFixtures.destination)

        let status = await harness.engine.status
        XCTAssertEqual(status, .navigating)
        await harness.engine.stop()
        await harness.clock.releaseAll()
    }

    // MARK: - Losing GPS

    func testFixesDryingUpYieldsOneGPSLostAndLeavesTheActorAlive() async throws {
        let harness = makeHarness(transport: try fixtureTransport())
        let route = try NavigationFixtures.twoTurnsRoute()
        let first = RouteWalkSimulator(route: route).samples()[0]

        harness.locations.emit(first.fix)
        await harness.engine.start(to: "library")

        let firstCue = Task { () -> TurnCue? in
            for await cue in harness.engine.cues { return cue }
            return nil
        }
        for _ in 0..<25 {
            await harness.clock.advance(byMs: 1_000)
            await Task.yield()
        }

        let cue = try await withTestTimeout { await firstCue.value }
        XCTAssertEqual(cue?.kind, .gpsLost)
        XCTAssertEqual(cue?.phrase, "GPS lost, navigation paused")
        let status = await harness.engine.status
        XCTAssertEqual(status, .navigating, "the actor survives a silent GPS")
        await harness.engine.stop()
        await harness.clock.releaseAll()
    }

    // MARK: - Failures

    func testATransportFailureFailsTheStatusAndFinishesTheStream() async throws {
        let harness = makeHarness(transport: .alwaysFailing(with: FakeTransportExhausted()))
        let route = try NavigationFixtures.twoTurnsRoute()
        let collected = collect(harness.engine)

        harness.locations.emit(Array(RouteWalkSimulator(route: route).samples().prefix(1)))
        await harness.engine.start(to: "library")

        let cues = try await withTestTimeout { await collected.value }
        XCTAssertEqual(cues.count, 0)
        let status = await harness.engine.status
        guard case .failed(.transport) = status else {
            return XCTFail("expected .failed(.transport), got \(status)")
        }
        await harness.clock.releaseAll()
    }

    func testAnUnresolvableDestinationFails() async throws {
        let harness = makeHarness(
            transport: try fixtureTransport(), resolver: .failing(.destinationNotFound("atlantis"))
        )
        let route = try NavigationFixtures.twoTurnsRoute()

        harness.locations.emit(Array(RouteWalkSimulator(route: route).samples().prefix(1)))
        await harness.engine.start(to: "atlantis")

        let status = await harness.engine.status
        XCTAssertEqual(status, .failed(.destinationNotFound("atlantis")))
        await harness.clock.releaseAll()
    }

    func testNoFixWithinTwentySecondsFailsWithNoFix() async throws {
        let transport = try fixtureTransport()
        let harness = makeHarness(transport: transport)

        let starting = Task { await harness.engine.start(to: "library") }
        for _ in 0..<40 {
            await harness.clock.advance(byMs: 1_000)
            await Task.yield()
        }
        await starting.value

        let status = await harness.engine.status
        XCTAssertEqual(status, .failed(.noFix))
        let requests = await transport.requests.count
        XCTAssertEqual(requests, 0, "no route is fetched without an origin")
        await harness.clock.releaseAll()
    }

    // MARK: - Rerouting

    func testWalkingOffRouteAnnouncesAndReroutes() async throws {
        let harness = makeHarness(transport: try fixtureTransport())
        let route = try NavigationFixtures.twoTurnsRoute()
        let samples = RouteWalkSimulator(route: route).samples()

        harness.locations.emit(samples[0].fix)
        await harness.engine.start(to: "library")

        let collected = Task { () -> [TurnCue] in
            var cues: [TurnCue] = []
            for await cue in harness.engine.cues {
                cues.append(cue)
                if cues.count == 2 { break }
            }
            return cues
        }
        for sample in samples[1...8] {
            harness.locations.emit(
                LocationFix(
                    latLng: Geo.destinationPoint(
                        from: sample.fix.latLng, bearingDeg: 90, distanceMeters: 40
                    ),
                    horizontalAccuracyM: 5,
                    speedMps: 1.4,
                    timestampMs: sample.fix.timestampMs
                )
            )
        }

        let cues = try await withTestTimeout { await collected.value }
        XCTAssertEqual(cues.map(\.kind), [.offRoute, .rerouting])
        let status = await harness.engine.status
        XCTAssertEqual(status, .navigating)
        await harness.engine.stop()
        await harness.clock.releaseAll()
    }

    // MARK: - Lifecycle

    func testStopFinishesTheStream() async throws {
        let harness = makeHarness(transport: try fixtureTransport())
        let route = try NavigationFixtures.twoTurnsRoute()
        let collected = collect(harness.engine)

        harness.locations.emit(Array(RouteWalkSimulator(route: route).samples().prefix(3)))
        await harness.engine.start(to: "library")
        await harness.engine.stop()

        let cues = try await withTestTimeout { await collected.value }
        XCTAssertTrue(cues.isEmpty, "three metres of walking says nothing yet")
        let status = await harness.engine.status
        XCTAssertEqual(status, .idle)
        await harness.clock.releaseAll()
    }

    func testStoppingTwiceIsHarmless() async throws {
        let harness = makeHarness(transport: try fixtureTransport())

        await harness.engine.stop()
        await harness.engine.stop()

        let status = await harness.engine.status
        XCTAssertEqual(status, .idle)
        await harness.clock.releaseAll()
    }
}
