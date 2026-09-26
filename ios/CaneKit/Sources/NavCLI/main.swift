import CaneNavigation
import Foundation

// nav-cli -- the live check for step 12 (brief F7). Resolves both
// endpoints, fetches a real walking route, prints its steps, then
// replays a simulated 1.4 m/s walk through the same follower and
// policy the app uses and prints the cue sequence.
//
//   GOOGLE_MAPS_API_KEY=... swift run nav-cli "<origin>" "<destination>"
//
// Either argument may be "lat,lng" instead of text. Exit 2 on any
// failure, including a missing key (which costs zero network calls).

let exitUsage: Int32 = 2

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(exitUsage)
}

func parseLatLng(_ text: String) -> LatLng? {
    let parts = text.split(separator: ",").map {
        Double($0.trimmingCharacters(in: .whitespaces))
    }
    guard parts.count == 2, let latitude = parts[0], let longitude = parts[1] else { return nil }
    return LatLng(latitude: latitude, longitude: longitude)
}

func describe(_ point: LatLng) -> String {
    String(format: "%.5f,%.5f", point.latitude, point.longitude)
}

func resolve(
    _ text: String, near bias: LatLng?, with resolver: PlacesTextSearchResolver
) async throws -> (point: LatLng, label: String) {
    if let parsed = parseLatLng(text) { return (parsed, describe(parsed)) }
    let place = try await resolver.resolve(freeText: text, near: bias)
    return (place.latLng, "\(place.name) (\(place.formattedAddress))")
}

func printSteps(_ route: Route) {
    print("route: \(route.distanceMeters) m, \(Int(route.durationS)) s, \(route.steps.count) steps")
    for (index, step) in route.steps.enumerated() {
        let maneuver = step.maneuver.rawValue.padding(toLength: 20, withPad: " ", startingAt: 0)
        let distance = String(format: "%5d m", step.distanceMeters)
        print("  \(index). \(maneuver) \(distance)  \(step.instruction)")
    }
}

/// Replays the route at walking pace and prints every cue, with the
/// second of the walk it lands on.
func printCues(_ route: Route) {
    print("cues (simulated \(RouteWalkSimulator.defaultSpeedMps) m/s walk):")
    let follower = RouteFollower(route: route)
    var followerState = follower.initialState
    var policyState = TurnCuePolicy.State()

    for sample in RouteWalkSimulator(route: route).samples() {
        followerState = follower.advance(
            from: followerState, fix: sample.fix, heading: sample.heading
        )
        let (next, cue) = TurnCuePolicy.evaluate(
            state: policyState, follower: followerState, nowMs: sample.fix.timestampMs
        )
        policyState = next
        guard let cue else { continue }
        let seconds = sample.fix.timestampMs / 1_000
        print(
            "  t=\(seconds)s  \"\(cue.phrase)\""
                + "  [\(cue.kind.rawValue) \(cue.direction.rawValue) haptic=\(cue.hapticHint.rawValue)]"
        )
    }
}

func run(originText: String, destinationText: String) async throws {
    // Read the key first: without one, nav-cli must not touch the
    // network at all.
    let key = try NavigationSecrets.apiKey()
    let transport = URLSessionTransport()
    let resolver = PlacesTextSearchResolver(transport: transport, apiKey: { key })
    let routes = RoutesClient(transport: transport, apiKey: { key })

    let origin = try await resolve(originText, near: nil, with: resolver)
    print("origin:      \(origin.label)")
    let destination = try await resolve(destinationText, near: origin.point, with: resolver)
    print("destination: \(destination.label)")

    let route = try await routes.computeWalkingRoute(
        origin: origin.point, destination: .latLng(destination.point)
    )
    printSteps(route)
    printCues(route)
}

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    fail("usage: GOOGLE_MAPS_API_KEY=... nav-cli \"<origin address or lat,lng>\" \"<destination>\"")
}

do {
    try await run(originText: arguments[1], destinationText: arguments[2])
} catch {
    fail("nav-cli: \(error)")
}
