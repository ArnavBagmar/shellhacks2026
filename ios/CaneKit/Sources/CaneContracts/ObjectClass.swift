/// The closed 12-value class enum. Mirrors the `cls` enum in
/// `contracts/awareness_event.schema.json`.
///
/// A detector class outside these 12 must be mapped by the producer (see
/// `contracts/README.md`), never emitted as a new string.
public enum ObjectClass: String, Codable, CaseIterable, Sendable {
    case person
    case cyclist
    case scooter
    case car
    case bus
    case pole
    case bollard
    case wall
    case parkedCar = "parked_car"
    case door
    case overhang
    case unknownObstacle = "unknown_obstacle"
}
