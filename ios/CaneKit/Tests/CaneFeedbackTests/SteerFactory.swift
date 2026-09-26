import CaneTransport
import Foundation

/// Builds `SteerCommand`s through the contract's validating initializer
/// (step 21), the same discipline `Make.event` applies to `AwarenessEvent`:
/// no test may assert on a steering command the wire would have rejected.
enum MakeSteer {
    /// Same phone-clock anchor every other feedback test uses.
    static let t0: Int64 = Make.t0

    static func command(
        tCaptureMs: Int64 = t0,
        ttlMs: Int = SteerLimits.ttlMs,
        steer: Double = 0.0,
        proximity: Double = 0.0,
        gapBearingDeg: Double = 0.0,
        blocked: Bool = false,
        proximityAnchored: Bool = true
    ) throws -> SteerCommand {
        try SteerCommand(
            tCaptureMs: tCaptureMs,
            ttlMs: ttlMs,
            steer: steer,
            proximity: proximity,
            gapBearingDeg: gapBearingDeg,
            blocked: blocked,
            proximityAnchored: proximityAnchored,
            source: .x4Cloud
        )
    }
}
