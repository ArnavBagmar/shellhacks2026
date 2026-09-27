import Foundation

/// Places API (New) Text Search (step-12 brief H3), the preferred
/// resolver for spoken destinations like "the library": Geocoding often
/// answers `ZERO_RESULTS` for a point-of-interest phrase.
///
/// The field mask header is required here too, and the search is biased
/// to a 2 km circle around the walker's current fix so "the library"
/// means the one they can walk to.
public struct PlacesTextSearchResolver: DestinationResolver {
    public static let endpointURLString = "https://places.googleapis.com/v1/places:searchText"
    public static let fieldMask = "places.location,places.displayName,places.formattedAddress"
    public static let locationBiasRadiusM: Double = 2000
    /// A walking destination farther than this is a mishearing, not a
    /// place: the bias only *prefers* nearby results, and "trition" came
    /// back as an office in the Netherlands (2026-09-27).
    public static let maxWalkDistanceM: Double = 5_000
    /// Candidates asked for, so a far first hit can give way to a near one.
    public static let pageSize = 5

    /// A compile-time constant URL; see `RoutesClient.endpoint`.
    private static let endpoint = URL(string: endpointURLString)!

    private let transport: any HTTPTransport
    private let apiKey: @Sendable () throws -> String

    public init(
        transport: any HTTPTransport,
        apiKey: @escaping @Sendable () throws -> String = { try NavigationSecrets.apiKey() }
    ) {
        self.transport = transport
        self.apiKey = apiKey
    }

    public func resolve(freeText: String, near: LatLng?) async throws -> ResolvedPlace {
        let request = try makeRequest(freeText: freeText, near: near, key: apiKey())
        let response = try await GoogleHTTP.send(request, through: transport)
        guard (200..<300).contains(response.statusCode) else {
            throw GoogleHTTP.statusError(from: response)
        }

        let decoded: SearchTextResponse
        do {
            decoded = try JSONDecoder().decode(SearchTextResponse.self, from: response.body)
        } catch {
            throw NavigationError.decoding("places:searchText body: \(error)")
        }
        let located = (decoded.places ?? []).compactMap { place in place.location.map { (place, $0) } }
        let walkable = near.map { here in
            located.filter { Geo.haversineMeters(here, $0.1) <= PlacesTextSearchResolver.maxWalkDistanceM }
        } ?? located
        guard let (first, location) = walkable.first else {
            throw NavigationError.destinationNotFound(freeText)
        }
        return ResolvedPlace(
            name: first.displayName?.text ?? freeText,
            latLng: location,
            formattedAddress: first.formattedAddress ?? ""
        )
    }

    private func makeRequest(freeText: String, near: LatLng?, key: String) throws -> HTTPRequest {
        let body = SearchTextRequest(
            textQuery: freeText,
            pageSize: PlacesTextSearchResolver.pageSize,
            locationBias: near.map {
                LocationBiasDTO(
                    circle: CircleDTO(center: $0, radius: PlacesTextSearchResolver.locationBiasRadiusM)
                )
            }
        )
        return HTTPRequest(
            url: PlacesTextSearchResolver.endpoint,
            method: "POST",
            headers: [
                "Content-Type": "application/json",
                RoutesClient.apiKeyHeader: key,
                RoutesClient.fieldMaskHeader: PlacesTextSearchResolver.fieldMask,
            ],
            body: try JSONEncoder().encode(body)
        )
    }
}

// MARK: - Wire models

private struct SearchTextRequest: Encodable {
    let textQuery: String
    let pageSize: Int
    let locationBias: LocationBiasDTO?
}

private struct LocationBiasDTO: Encodable {
    let circle: CircleDTO
}

private struct CircleDTO: Encodable {
    let center: LatLng
    let radius: Double
}

private struct SearchTextResponse: Decodable {
    let places: [PlaceDTO]?
}

private struct PlaceDTO: Decodable {
    let location: LatLng?
    let displayName: DisplayNameDTO?
    let formattedAddress: String?
}

private struct DisplayNameDTO: Decodable {
    let text: String?
    let languageCode: String?
}
