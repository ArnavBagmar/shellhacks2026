import Foundation

/// The Routes API v2 walking-route client (step-12 brief H1).
///
/// The field mask is **required**: omitting or mistyping it is an HTTP
/// 400 INVALID_ARGUMENT, which is why it is a constant here and asserted
/// verbatim in the tests rather than assembled at the call site.
public struct RoutesClient: Sendable {
    public static let endpointURLString = "https://routes.googleapis.com/directions/v2:computeRoutes"
    public static let fieldMask = """
        routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,\
        routes.legs.steps.navigationInstruction,routes.legs.steps.polyline,\
        routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration,\
        routes.legs.steps.startLocation,routes.legs.steps.endLocation,\
        routes.legs.steps.travelMode
        """
    public static let apiKeyHeader = "X-Goog-Api-Key"
    public static let fieldMaskHeader = "X-Goog-FieldMask"

    /// Force-unwrapped once, on a compile-time constant that the
    /// `testHeadersCarryTheKeyAndTheExactFieldMask` test pins: there is
    /// no runtime input here that could make it fail.
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

    /// Fetches a walking route. The key is read first, so a keyless
    /// build never builds or sends a request.
    public func computeWalkingRoute(origin: LatLng, destination: Destination) async throws -> Route {
        let request = try makeRequest(origin: origin, destination: destination, key: apiKey())
        let response = try await send(request)
        return try RoutesClient.route(from: response)
    }

    private func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        do {
            return try await transport.send(request)
        } catch let error as NavigationError {
            throw error
        } catch {
            throw NavigationError.transport(String(describing: error))
        }
    }

    func makeRequest(origin: LatLng, destination: Destination, key: String) throws -> HTTPRequest {
        let body = ComputeRoutesRequest(
            origin: WaypointDTO(origin),
            destination: WaypointDTO(destination),
            travelMode: "WALK",
            polylineQuality: "HIGH_QUALITY",
            polylineEncoding: "ENCODED_POLYLINE",
            languageCode: "en-US",
            units: "METRIC",
            computeAlternativeRoutes: false
        )
        return HTTPRequest(
            url: RoutesClient.endpoint,
            method: "POST",
            headers: [
                "Content-Type": "application/json",
                RoutesClient.apiKeyHeader: key,
                RoutesClient.fieldMaskHeader: RoutesClient.fieldMask,
            ],
            body: try JSONEncoder().encode(body)
        )
    }

    static func route(from response: HTTPResponse) throws -> Route {
        guard (200..<300).contains(response.statusCode) else {
            throw routesError(from: response)
        }
        let decoded: ComputeRoutesResponse
        do {
            decoded = try JSONDecoder().decode(ComputeRoutesResponse.self, from: response.body)
        } catch {
            throw NavigationError.decoding("computeRoutes body: \(error)")
        }
        guard let first = decoded.routes?.first else {
            throw NavigationError.noRoute
        }
        return try first.toRoute()
    }

    /// Turns a non-2xx reply into `.routesAPI`, preferring the
    /// `google.rpc.Status` message when the body carries one.
    private static func routesError(from response: HTTPResponse) -> NavigationError {
        let envelope = try? JSONDecoder().decode(GoogleRPCStatusEnvelope.self, from: response.body)
        let message = envelope?.error.message
            ?? String(data: response.body, encoding: .utf8)
            ?? "no message"
        return .routesAPI(status: response.statusCode, message: message)
    }
}
