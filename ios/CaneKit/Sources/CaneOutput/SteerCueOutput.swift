import CaneFeedback
import Foundation
import os

/// Step 24's half of the steering seam, the mirror of `OutputSink`: a
/// `SteerCue` becomes a pulse of the steering pattern, panned to the side the
/// wearer should move toward, and goes to the arbiter's lowest channel.
///
/// Adds no policy of its own -- no threshold, no rate, no gate. `CaneFeedback`
/// decided all of that on the tick; this file decides only *how* it sounds and
/// feels.
public final class SteerCueOutput: SteerCueSink, @unchecked Sendable {
    /// Full-scale steering (`|steer| == 1`) points at the edge of the forward
    /// ring the bridge profiles: plan §3's `RING_COLUMNS` spans -90...+90, so
    /// "all the way over" is 90 degrees. Going through a bearing rather than
    /// panning `lateral` directly means `StereoPan`'s cap, route gate and sign
    /// convention are reused exactly as a hazard uses them, instead of a second
    /// derivation that could disagree about which side is left.
    public static let ringEdgeDeg = 90.0

    private let arbiter: OutputArbiter
    private let userGain: @Sendable () -> Float
    private let panRoute: @Sendable () -> (route: PanRoute, channels: Int)
    private let logger = Logger(subsystem: "cane", category: "steering")

    public init(
        arbiter: OutputArbiter,
        userGain: @escaping @Sendable () -> Float = { 1.0 },
        panRoute: @escaping @Sendable () -> (route: PanRoute, channels: Int) = { (.builtInSpeaker, 1) }
    ) {
        self.arbiter = arbiter
        self.userGain = userGain
        self.panRoute = panRoute
    }

    /// Fire and forget, because `SteerCueSink` is synchronous by contract: the
    /// feedback manager's tick must not wait on the Taptic Engine, or it stops
    /// integrating. A pulse that cannot start is dropped by the arbiter, which
    /// is the wanted behaviour and needs no answer here.
    public func pulse(_ cue: SteerCue) {
        let route = panRoute()
        let plan = Self.plan(
            for: cue, hapticsAvailable: arbiter.haptics.isAvailable,
            userGain: userGain(), route: route.route, channels: route.channels
        )
        guard plan.isPlayable else { return }
        let arbiter = self.arbiter
        Task { await arbiter.steerPulse(haptic: plan.haptic, pan: plan.pan) }
    }

    public func silence() {
        arbiter.cancelSteering()
    }

    /// Everything `pulse` decides, as a value (so it is unit-tested).
    public struct Plan: Equatable, Sendable {
        public let haptic: HapticScript?
        public let pan: Double

        /// No Taptic Engine, no steering cue. It is deliberately **not** read
        /// out instead: the degraded-hazard path speaks because a warning must
        /// arrive somehow, whereas a continuous cue rendered as speech is the
        /// chatty third channel plan risk 3 says ruins the demo. The wearer
        /// keeps hazards and turn cues; they lose the steering gradient.
        public var isPlayable: Bool { haptic != nil }
    }

    public static func plan(
        for cue: SteerCue, hapticsAvailable: Bool, userGain: Float,
        route: PanRoute, channels: Int
    ) -> Plan {
        guard hapticsAvailable else { return Plan(haptic: nil, pan: 0) }
        return Plan(
            haptic: HapticScript.steering(userGain: Double(userGain)),
            pan: StereoPan.pan(
                bearingDeg: bearingDeg(forLateral: cue.lateral), route: route, channelCount: channels
            )
        )
    }

    /// The virtual bearing a steering command points at.
    public static func bearingDeg(forLateral lateral: Double) -> Double {
        min(max(lateral, -1), 1) * ringEdgeDeg
    }
}
