import Foundation

/// Owns a walk: resolve the destination, fetch the route, follow it
/// with the location and heading streams, and yield `TurnCue`s.
///
/// An `actor` because fixes, headings, the 1 Hz watchdog and the
/// caller's `start`/`stop` all touch the same follower and policy
/// state from different tasks; actor isolation serialises them instead
/// of a lock. It is the only actor in `CaneNavigation` -- everything
/// else is a pure value type.
///
/// **Boundary (build-plan F7).** This target depends on nothing from
/// `CaneContracts`/`CaneFeedback`, so a `CLLocation` cannot reach the
/// awareness path even by accident; bearings there stay torso-relative.
///
/// Failures never crash the actor: they become `NavigationStatus`
/// `.failed` plus, where a walker deserves to hear it, a cue.
public actor NavigationEngine {
    /// The watchdog cadence -- the rate at which "have the fixes
    /// stopped?" is asked. Cue evaluation itself happens per fix.
    public static let tickIntervalMs: Int64 = 1_000
    /// Brief F5: at most one reroute fetch per 15 s.
    public static let rerouteMinIntervalS: Double = 15

    /// The engine's per-fix loop lives in `NavigationEngineLoop.swift`,
    /// so the state below is `internal` rather than `private`: Swift's
    /// `private` is file-scoped, and splitting the actor keeps both
    /// files inside the 200-line budget.
    public nonisolated let cues: AsyncStream<TurnCue>
    nonisolated let cueContinuation: AsyncStream<TurnCue>.Continuation

    let resolver: any DestinationResolver
    let routes: RoutesClient
    let locations: any LocationSource
    let headings: any HeadingSource
    let clock: any NavigationClock

    public internal(set) var status: NavigationStatus = .idle

    var follower: RouteFollower?
    var followerState: FollowerState?
    var policyState = TurnCuePolicy.State()
    var destination: LatLng?
    var latestFix: LocationFix?
    var latestHeading: HeadingSample?
    var lastRerouteMs: Int64?
    var pendingFixes: [LocationFix] = []
    var isNavigating = false
    var locationTask: Task<Void, Never>?
    var headingTask: Task<Void, Never>?
    var watchdogTask: Task<Void, Never>?
    var firstFixWaiter: CheckedContinuation<LocationFix?, Never>?

    public init(
        resolver: any DestinationResolver,
        routes: RoutesClient,
        locations: any LocationSource,
        headings: any HeadingSource,
        clock: any NavigationClock = SystemNavigationClock()
    ) {
        self.resolver = resolver
        self.routes = routes
        self.locations = locations
        self.headings = headings
        self.clock = clock
        (cues, cueContinuation) = AsyncStream.makeStream(bufferingPolicy: .unbounded)
    }

    // MARK: - Starting

    /// Resolves spoken text ("the library"), then routes to it.
    public func start(to freeText: String) async {
        status = .resolving
        guard let origin = await firstFix() else { return fail(.noFix) }
        do {
            let place = try await resolver.resolve(freeText: freeText, near: origin.latLng)
            await route(from: origin, to: place.latLng)
        } catch let error as NavigationError {
            fail(error)
        } catch {
            fail(.transport(String(describing: error)))
        }
    }

    /// Routes straight to a coordinate; no resolver call.
    public func start(to destination: LatLng) async {
        status = .routing
        guard let origin = await firstFix() else { return fail(.noFix) }
        await route(from: origin, to: destination)
    }

    /// Cancels everything and finishes `cues`. Idempotent.
    public func stop() {
        shutdown()
        if case .failed = status { return }
        if status != .arrived { status = .idle }
    }

    private func route(from origin: LocationFix, to point: LatLng) async {
        status = .routing
        destination = point
        do {
            adopt(try await routes.computeWalkingRoute(origin: origin.latLng, destination: .latLng(point)))
        } catch let error as NavigationError {
            return fail(error)
        } catch {
            return fail(.transport(String(describing: error)))
        }
        startWatchdog()
        await drainPendingFixes()
    }

    func adopt(_ route: Route) {
        follower = RouteFollower(route: route)
        followerState = nil
        status = .navigating
    }

    private func fail(_ error: NavigationError) {
        status = .failed(error)
        shutdown()
    }

    func shutdown() {
        isNavigating = false
        locationTask?.cancel()
        headingTask?.cancel()
        watchdogTask?.cancel()
        locationTask = nil
        headingTask = nil
        watchdogTask = nil
        locations.stop()
        headings.stop()
        firstFixWaiter?.resume(returning: nil)
        firstFixWaiter = nil
        cueContinuation.finish()
    }

    // MARK: - The first fix

    /// Starts consuming both streams and waits (on the injected clock,
    /// not on real time) for a fix to anchor the route request.
    private func firstFix() async -> LocationFix? {
        startSources()
        if let latestFix { return latestFix }

        let clock = self.clock
        let timeout = Task { [weak self] in
            try? await clock.sleep(
                forMilliseconds: Int64(TurnCuePolicy.gpsLostAfterS * 1_000)
            )
            guard !Task.isCancelled else { return }
            await self?.abandonFirstFixWait()
        }
        let fix = await withCheckedContinuation { continuation in
            firstFixWaiter = continuation
        }
        timeout.cancel()
        return fix
    }

    private func abandonFirstFixWait() {
        firstFixWaiter?.resume(returning: nil)
        firstFixWaiter = nil
    }

    private func startSources() {
        guard locationTask == nil else { return }
        let locationStream = locations.locations()
        let headingStream = headings.headings()
        locationTask = Task { [weak self] in
            for await fix in locationStream {
                await self?.ingest(fix)
            }
        }
        headingTask = Task { [weak self] in
            for await heading in headingStream {
                await self?.ingest(heading)
            }
        }
    }
}
