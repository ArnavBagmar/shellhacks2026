import CaneContracts
import Foundation

/// Gate 1 of the §4.7 gauntlet (A1.1). Validation *is*
/// `AwarenessEvent.decode` -- schema version, enum membership, bearing in
/// `(-180, 180]`, `ttl > 0` and the rest are the contract's job and are not
/// re-implemented here. This stage adds exactly one rule the contract cannot
/// know: an event stamped in the future.
///
/// A future-dated event is *not* a stale-drop. A bad clock offset is not
/// fixed by switching detectors, so it feeds `futureDated`, never the §3.4
/// fallback signal.
public enum ValidationStage {
    /// Clock offsets up to this are normal (the §2 sync is good to ~20 ms;
    /// this is a generous margin). Strictly beyond it, the producer's clock
    /// is wrong and its `t_capture_ms` cannot be reasoned about.
    public static let futureToleranceMs: Int64 = 250

    public enum Outcome: Equatable, Sendable {
        case accepted(AwarenessEvent)
        /// Rejected by the contract; carries the reason for the log.
        case invalid(AwarenessEventError)
        /// Stamped more than `futureToleranceMs` ahead of the phone clock.
        case futureDated
    }

    /// Wire entry point: decodes, then future-dates. Never throws, never
    /// traps -- a malformed payload is a return value.
    public static func validate(_ data: Data, now: Int64) -> Outcome {
        let event: AwarenessEvent
        do {
            event = try AwarenessEvent.decode(data)
        } catch let error as AwarenessEventError {
            return .invalid(error)
        } catch {
            // `AwarenessEvent.decode` is documented to throw only
            // `AwarenessEventError`; this arm keeps the promise unconditional.
            return .invalid(.malformedJSON)
        }
        return validate(event, now: now)
    }

    /// In-memory entry point: the value already satisfies every contract
    /// bound (both initializers enforce them), so only future-dating remains.
    public static func validate(_ event: AwarenessEvent, now: Int64) -> Outcome {
        guard Int64(event.tCaptureMs) <= now + futureToleranceMs else {
            return .futureDated
        }
        return .accepted(event)
    }
}
