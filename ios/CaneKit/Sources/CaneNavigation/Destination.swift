import Foundation

/// Where the walker asked to go. Both forms are valid Routes API
/// `Waypoint`s (brief H1), so a resolved place and a raw coordinate take
/// the same path through `RoutesClient`.
public enum Destination: Sendable, Equatable {
    case latLng(LatLng)
    case address(String)
}

/// What a destination lookup returns.
public struct ResolvedPlace: Sendable, Equatable {
    public let name: String
    public let latLng: LatLng
    public let formattedAddress: String

    public init(name: String, latLng: LatLng, formattedAddress: String) {
        self.name = name
        self.latLng = latLng
        self.formattedAddress = formattedAddress
    }
}

/// Free text ("navigate me to the library") to a point on the map. A
/// protocol so the engine's tests inject a fake and never reach the
/// network, and so Places can be swapped for Geocoding on a key that
/// lacks Places (brief F2).
public protocol DestinationResolver: Sendable {
    /// - Parameter near: the walker's current fix, used to bias the
    ///   search; `nil` when there is no fix yet.
    func resolve(freeText: String, near: LatLng?) async throws -> ResolvedPlace
}
