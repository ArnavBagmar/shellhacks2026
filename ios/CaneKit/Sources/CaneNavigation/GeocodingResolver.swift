import Foundation

/// Geocoding API fallback (step-12 brief F2/H3) for structured
/// addresses and for keys that do not have Places enabled. Drop-in for
/// `PlacesTextSearchResolver`.
///
/// Two traps this handles: the key goes in the **query string** (there
/// is no `X-Goog-Api-Key` header on this endpoint), and `ZERO_RESULTS`
/// arrives as HTTP 200 -- the `status` field decides, not the HTTP code.
public struct GeocodingResolver: DestinationResolver {
    public static let endpointURLString = "https://maps.googleapis.com/maps/api/geocode/json"

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
        let request = try makeRequest(freeText: freeText, key: apiKey())
        let response = try await GoogleHTTP.send(request, through: transport)
        guard (200..<300).contains(response.statusCode) else {
            throw GoogleHTTP.statusError(from: response)
        }

        let decoded: GeocodingResponse
        do {
            decoded = try JSONDecoder().decode(GeocodingResponse.self, from: response.body)
        } catch {
            throw NavigationError.decoding("geocode/json body: \(error)")
        }
        return try place(from: decoded, query: freeText, statusCode: response.statusCode)
    }

    private func place(
        from decoded: GeocodingResponse, query: String, statusCode: Int
    ) throws -> ResolvedPlace {
        switch decoded.status {
        case "OK":
            guard let first = decoded.results?.first, let location = first.geometry?.location else {
                throw NavigationError.destinationNotFound(query)
            }
            let address = first.formatted_address ?? query
            return ResolvedPlace(
                name: address,
                latLng: LatLng(latitude: location.lat, longitude: location.lng),
                formattedAddress: address
            )
        case "ZERO_RESULTS":
            throw NavigationError.destinationNotFound(query)
        default:
            let status = decoded.status ?? "no status"
            let detail = decoded.error_message.map { ": \($0)" } ?? ""
            throw NavigationError.routesAPI(status: statusCode, message: "\(status)\(detail)")
        }
    }

    private func makeRequest(freeText: String, key: String) throws -> HTTPRequest {
        var components = URLComponents(string: GeocodingResolver.endpointURLString)
        components?.queryItems = [
            URLQueryItem(name: "address", value: freeText),
            URLQueryItem(name: "key", value: key),
        ]
        guard let url = components?.url else {
            throw NavigationError.decoding("could not build a geocoding URL for \"\(freeText)\"")
        }
        return HTTPRequest(url: url, method: "GET", headers: [:], body: nil)
    }
}

// MARK: - Wire models (snake_case, unlike the newer APIs)

private struct GeocodingResponse: Decodable {
    let status: String?
    let error_message: String?
    let results: [GeocodingResult]?
}

private struct GeocodingResult: Decodable {
    let formatted_address: String?
    let geometry: GeocodingGeometry?
}

private struct GeocodingGeometry: Decodable {
    let location: GeocodingLocation?
}

private struct GeocodingLocation: Decodable {
    let lat: Double
    let lng: Double
}
