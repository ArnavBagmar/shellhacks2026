import Foundation

/// Where the Google Maps Platform key comes from. Never a literal in
/// source, never in a fixture: the environment variable
/// `GOOGLE_MAPS_API_KEY` first (that is what `nav-cli` uses), then the
/// app's Info.plist `GoogleMapsAPIKey` (what a device build uses).
///
/// Callers resolve the key *before* building a request, so a build
/// without one makes zero network calls.
public enum NavigationSecrets {
    public static let environmentVariableName = "GOOGLE_MAPS_API_KEY"
    public static let infoPlistKey = "GoogleMapsAPIKey"

    /// Reads the process environment and the main bundle.
    /// - Throws: `NavigationError.missingAPIKey` when neither carries a
    ///   non-blank value.
    public static func apiKey() throws -> String {
        try apiKey(
            environment: ProcessInfo.processInfo.environment,
            infoPlistValue: Bundle.main.object(forInfoDictionaryKey: infoPlistKey) as? String
        )
    }

    /// The pure form both the tests and `apiKey()` use.
    static func apiKey(environment: [String: String], infoPlistValue: String?) throws -> String {
        let candidates = [environment[environmentVariableName], infoPlistValue]
        for candidate in candidates {
            let trimmed = candidate?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !trimmed.isEmpty { return trimmed }
        }
        throw NavigationError.missingAPIKey
    }
}
