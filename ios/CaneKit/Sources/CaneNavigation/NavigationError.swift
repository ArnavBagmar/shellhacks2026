import Foundation

/// Every way navigation can fail, as a value (step-12 brief F1). Nothing
/// in this module traps: a bad response, a missing key or a silent GPS
/// becomes one of these and surfaces as `NavigationStatus.failed` or a
/// spoken cue, never a crash in the walker's hand.
public enum NavigationError: Error, Equatable, Sendable, CustomStringConvertible {
    /// No `GOOGLE_MAPS_API_KEY` in the environment and no
    /// `GoogleMapsAPIKey` in the Info.plist. Checked *before* any network
    /// call is built, so a keyless build makes zero requests.
    case missingAPIKey
    /// The resolver found no place for the spoken text.
    case destinationNotFound(String)
    /// A non-2xx reply; `message` is the `google.rpc.Status` message when
    /// the body carried one.
    case routesAPI(status: Int, message: String)
    /// A 200 reply with an empty `routes` array.
    case noRoute
    /// No location fix arrived in time to anchor the route request.
    case noFix
    /// A malformed body or polyline; the string says which field.
    case decoding(String)
    /// The transport itself failed (DNS, offline, TLS). Carries
    /// `String(describing:)` of the underlying error rather than the
    /// error itself so `NavigationError` can stay `Equatable` and
    /// `Sendable`, which `NavigationStatus` needs.
    case transport(String)

    public var description: String {
        switch self {
        case .missingAPIKey:
            return "missing Google Maps API key (env GOOGLE_MAPS_API_KEY or Info.plist GoogleMapsAPIKey)"
        case .destinationNotFound(let query):
            return "no place found for \"\(query)\""
        case .routesAPI(let status, let message):
            // Shared by Routes, Places (New) and Geocoding HTTP failures, so the
            // label stays source-neutral.
            return "Google Maps API HTTP \(status): \(message)"
        case .noRoute:
            return "the Routes API returned no route"
        case .noFix:
            return "no location fix available to start navigation"
        case .decoding(let detail):
            return "could not decode response: \(detail)"
        case .transport(let detail):
            return "transport failed: \(detail)"
        }
    }
}
