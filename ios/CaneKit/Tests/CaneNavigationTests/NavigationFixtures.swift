import Foundation

@testable import CaneNavigation

/// Resolves the recorded Routes API replies that live next to this file.
/// Mirrors `CaneTransportTests/FixturePaths.swift`; duplicated rather
/// than shared because test targets are separate modules.
enum NavigationFixtures {
    /// `ios/CaneKit/Tests/Fixtures/` -- one level *above* this test
    /// target's source directory on purpose: `Package.swift` (owned by
    /// the controller, not editable here) declares no `resources` for
    /// `CaneNavigationTests`, and a JSON file inside the target
    /// directory would make every build warn "found 1 file(s) which are
    /// unhandled". Read from the source tree, not a bundle.
    private static var fixturesDirectory: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // NavigationFixtures.swift -> CaneNavigationTests/
            .deletingLastPathComponent()  // CaneNavigationTests/ -> Tests/
            .appendingPathComponent("Fixtures")
    }

    /// The ~250 m DEPART -> TURN_LEFT -> TURN_RIGHT -> arrive walk.
    static func twoTurnsData() throws -> Data {
        try Data(contentsOf: fixturesDirectory.appendingPathComponent("routes_two_turns.json"))
    }

    /// The fixture decoded through the real client path, so the tests
    /// that walk it exercise the same decoding the device will.
    static func twoTurnsRoute() throws -> Route {
        try RoutesClient.route(from: HTTPResponse(statusCode: 200, body: try twoTurnsData()))
    }

    /// The four geometry corners of that fixture, as hand-checked
    /// constants: 100 m north, 80 m west, 70 m north.
    static let origin = LatLng(latitude: 25.761_70, longitude: -80.191_80)
    static let firstCorner = LatLng(latitude: 25.762_60, longitude: -80.191_80)
    static let secondCorner = LatLng(latitude: 25.762_60, longitude: -80.192_60)
    static let destination = LatLng(latitude: 25.763_23, longitude: -80.192_60)

    /// Leg lengths in metres, as `Geo.haversineMeters` measures them.
    static let firstLegM = 100.0756
    static let secondLegM = 80.1141
    static let thirdLegM = 70.0529
    static let totalM = firstLegM + secondLegM + thirdLegM
}
