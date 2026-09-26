import CaneFeedback
import Foundation
import os

/// Step 10's half of the A13 seam: an `Announcement` becomes a haptic
/// symbol, maybe words, maybe a panned tick, and goes to the arbiter's
/// hazard channel. Adds **no** throttle and never drops or shortens what it
/// was handed (Amendment 6) -- step 9 has already stamped its refractory
/// bookkeeping, so a silent drop here would suppress the next warning too.
public final class OutputSink: AnnouncementSink, @unchecked Sendable {
    private let arbiter: OutputArbiter
    private let userGain: @Sendable () -> Float
    private let stereoRoute: @Sendable () -> Bool
    private let clock: @Sendable () -> Int64
    private let logger = Logger(subsystem: "cane", category: "output")

    public init(
        arbiter: OutputArbiter,
        userGain: @escaping @Sendable () -> Float = { 1.0 },
        stereoRoute: @escaping @Sendable () -> Bool = { false },
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.arbiter = arbiter
        self.userGain = userGain
        self.stereoRoute = stereoRoute
        self.clock = clock
    }

    public func present(_ a: Announcement) async {
        guard !Task.isCancelled else { return }
        // Belt and braces: step 9 already re-gates at dequeue.
        guard clock() < a.expiresAtMs else { return }
        let plan = Self.plan(for: a, hapticsAvailable: arbiter.haptics.isAvailable,
                             userGain: userGain(), stereoRoute: stereoRoute())
        logger.info("""
            present id=\(a.id) u=\(a.urgency) \(a.cls.rawValue, privacy: .public) \
            b=\(a.bearingDeg, format: .fixed(precision: 0)) \
            "\(plan.phrase?.text ?? "-", privacy: .public)" \
            capture->decide=\(a.decidedAtMs - a.tCaptureMs)ms
            """)
        await arbiter.presentHazard(haptic: plan.haptic, phrase: plan.phrase, pan: plan.pan)
    }

    public func cancelCurrent() {
        arbiter.cancelHazard()
    }

    /// Everything `present` decides, as a value (so it is unit-tested).
    public struct Plan: Equatable, Sendable {
        public let haptic: HapticScript?
        public let phrase: SpokenPhrase?
        public let pan: Double
    }

    public static func plan(
        for a: Announcement, hapticsAvailable: Bool, userGain: Float, stereoRoute: Bool
    ) -> Plan {
        let sector = Sector(bearingDeg: a.bearingDeg)
        let symbol: HapticSymbol = a.haptic == .burst ? .alarm : HapticSymbol(sector: sector)
        let haptic = hapticsAvailable
            ? HapticScript.hazard(symbol: symbol, urgency: a.urgency, userGain: userGain)
            : nil
        return Plan(
            haptic: haptic,
            phrase: HazardPhrasing.phrase(for: a, hapticsDegraded: !hapticsAvailable),
            pan: haptic == nil ? 0 : StereoPan.pan(bearingDeg: a.bearingDeg, stereoRoute: stereoRoute)
        )
    }
}
