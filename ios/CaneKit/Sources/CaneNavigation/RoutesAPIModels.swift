import Foundation

/// Wire models for `routes.googleapis.com/directions/v2:computeRoutes`,
/// mirroring step-12 brief H1 exactly: the request carries only the
/// fields documented for WALK, and the response models only the fields
/// the field mask asks for.
struct ComputeRoutesRequest: Encodable {
    let origin: WaypointDTO
    let destination: WaypointDTO
    let travelMode: String
    let polylineQuality: String
    let polylineEncoding: String
    let languageCode: String
    let units: String
    let computeAlternativeRoutes: Bool
}

/// A Routes API `Waypoint`: exactly one of `location` / `address` is
/// set, and `JSONEncoder` drops the other.
struct WaypointDTO: Encodable {
    let location: LocationDTO?
    let address: String?

    init(_ destination: Destination) {
        switch destination {
        case .latLng(let point):
            self.location = LocationDTO(latLng: point)
            self.address = nil
        case .address(let text):
            self.location = nil
            self.address = text
        }
    }

    init(_ point: LatLng) {
        self.location = LocationDTO(latLng: point)
        self.address = nil
    }
}

struct LocationDTO: Codable {
    let latLng: LatLng
}

struct ComputeRoutesResponse: Decodable {
    let routes: [RouteDTO]?
}

struct RouteDTO: Decodable {
    let distanceMeters: Int?
    let duration: String?
    let polyline: PolylineDTO?
    let legs: [LegDTO]?

    /// Maps the wire shape onto the domain `Route`, decoding polylines
    /// and parsing the `"631s"` duration strings.
    func toRoute() throws -> Route {
        guard let encoded = polyline?.encodedPolyline else {
            throw NavigationError.decoding("routes[0].polyline.encodedPolyline missing")
        }
        return Route(
            distanceMeters: distanceMeters ?? 0,
            durationS: try RouteDTO.seconds(from: duration),
            points: try Polyline.decode(encoded),
            legs: try (legs ?? []).map { leg in
                RouteLeg(steps: try (leg.steps ?? []).map { try $0.toStep() })
            }
        )
    }

    /// `duration` / `staticDuration` are strings like `"631s"` -- plain
    /// seconds with a trailing `s`, not ISO-8601 (brief H1).
    static func seconds(from text: String?) throws -> Double {
        guard let text, !text.isEmpty else { return 0 }
        guard text.hasSuffix("s"), let value = Double(text.dropLast()) else {
            throw NavigationError.decoding("duration \"\(text)\" is not the documented \"631s\" form")
        }
        return value
    }
}

struct PolylineDTO: Decodable {
    let encodedPolyline: String?
}

struct LegDTO: Decodable {
    let steps: [StepDTO]?
}

struct StepDTO: Decodable {
    let distanceMeters: Int?
    let staticDuration: String?
    let polyline: PolylineDTO?
    let startLocation: LocationDTO?
    let endLocation: LocationDTO?
    let navigationInstruction: NavigationInstructionDTO?
    let travelMode: String?

    func toStep() throws -> RouteStep {
        let points = try Polyline.decode(polyline?.encodedPolyline ?? "")
        guard let start = startLocation?.latLng ?? points.first,
            let end = endLocation?.latLng ?? points.last
        else {
            throw NavigationError.decoding("a step carried neither endpoints nor a polyline")
        }
        return RouteStep(
            distanceMeters: distanceMeters ?? 0,
            staticDurationS: try RouteDTO.seconds(from: staticDuration),
            points: points,
            start: start,
            end: end,
            maneuver: navigationInstruction?.maneuver ?? .unspecified,
            instruction: navigationInstruction?.instructions ?? ""
        )
    }
}

struct NavigationInstructionDTO: Decodable {
    let maneuver: Maneuver?
    let instructions: String?
}

/// `google.rpc.Status` -- the error body every Google API returns
/// (brief H1).
struct GoogleRPCStatusEnvelope: Decodable {
    let error: GoogleRPCStatus
}

struct GoogleRPCStatus: Decodable {
    let code: Int?
    let message: String?
    let status: String?
}
