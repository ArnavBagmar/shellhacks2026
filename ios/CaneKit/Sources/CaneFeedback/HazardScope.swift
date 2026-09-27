import CaneContracts

/// Which detections may become a hazard announcement at all.
///
/// The wearer asked (2026-09-26) for hazards to be *vehicles coming at
/// them*, not every car in sight and not people, chairs or poles. Under
/// `.approachingVehicles` everything else is still validated, deduped and
/// logged as ambient (the map and the counters keep seeing it) -- it just
/// never competes for the wearer's attention. Steering around obstacles is a
/// separate path (`Steering`) and is not affected.
public enum HazardScope: Equatable, Sendable {
    /// Every class, scored by `UrgencyPolicy` (the step-9 behaviour).
    case everything
    /// Only a car or bus within ~15 m (`near` or `mid`) closing at
    /// `approachMps` or faster.
    case approachingVehicles

    /// "Coming at you". `closing_mps` is the *line-of-sight* rate, so it
    /// includes the wearer's own walking (1-1.4 m/s): a parked car walked
    /// towards closes at about that. The bar sits just above it, so a car has
    /// to be moving itself (the wearer tried 3 m/s on 2026-09-27 and went
    /// back to 1.5; "only if close" below is what keeps it quiet). The laptop
    /// has already filtered jitter (0.5 m/s deadband, two agreeing samples).
    public static let approachMps = 1.5
    // "Only alarm if close" (2026-09-27). The X4 path gives bands, not
    // metres: `near` <= 5 m, `mid` 5-15 m, `far` beyond. Near-only (with mid
    // needing 8 m/s) warned too late -- a car was already within 5 m -- so
    // both near and mid alarm at `approachMps`; `far` never does.

    /// Exhaustive, no `default`: a new class must be placed on purpose.
    public static func isVehicle(_ cls: ObjectClass) -> Bool {
        switch cls {
        case .car, .bus:
            return true
        case .person, .cyclist, .scooter, .pole, .bollard, .wall, .parkedCar, .door, .overhang, .unknownObstacle:
            return false
        }
    }

    /// Whether `event` may be announced under this scope.
    public func admits(_ event: AwarenessEvent) -> Bool {
        switch self {
        case .everything:
            return true
        case .approachingVehicles:
            guard Self.isVehicleComing(cls: event.cls, closingMps: event.closingMps),
                  let closing = event.closingMps else { return false }
            return Self.isClose(band: UrgencyPolicy.effectiveBand(event), closingMps: closing)
        }
    }

    /// Near or mid at `approachMps`, far never.
    public static func isClose(band: RangeBand, closingMps: Double) -> Bool {
        switch band {
        case .near, .mid: return closingMps >= approachMps
        case .far: return false
        }
    }

    /// `UrgencyPolicy.decide` for `.everything`. For `.approachingVehicles`:
    /// ambient for anything kept quiet, and a car coming at the wearer is
    /// always the top level ("we are about to cross a crosswalk and a car is
    /// coming: STOP, CAR IS COMING"). Confidence still gates it -- a shaky
    /// box is demoted one level, as for every other alarm (A8).
    public func decide(_ event: AwarenessEvent) -> UrgencyDecision {
        guard admits(event) else {
            return UrgencyDecision(
                urgency: UrgencyLevel.ambient, driver: .band(UrgencyPolicy.effectiveBand(event)), isHardZero: true
            )
        }
        guard self == .approachingVehicles else { return UrgencyPolicy.decide(event) }
        let driver = UrgencyPolicy.ladder(event).driver
        return UrgencyPolicy.confidenceGated(
            UrgencyDecision(urgency: UrgencyLevel.immediate, driver: driver), confidence: event.confidence
        )
    }

    /// A vehicle closing fast enough to be "coming", from any source.
    public static func isVehicleComing(cls: ObjectClass, closingMps: Double?) -> Bool {
        guard isVehicle(cls), let closingMps else { return false }
        return closingMps >= approachMps
    }
}
