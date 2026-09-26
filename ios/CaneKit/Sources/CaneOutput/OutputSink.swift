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
    private let panRoute: @Sendable () -> (route: PanRoute, channels: Int)
    private let clock: @Sendable () -> Int64
    private let logger = Logger(subsystem: "cane", category: "output")

    public init(
        arbiter: OutputArbiter,
        userGain: @escaping @Sendable () -> Float = { 1.0 },
        panRoute: @escaping @Sendable () -> (route: PanRoute, channels: Int) = { (.builtInSpeaker, 1) },
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.arbiter = arbiter
        self.userGain = userGain
        self.panRoute = panRoute
        self.clock = clock
    }

    public func present(_ a: Announcement) async {
        guard !Task.isCancelled else { return }
        // Belt and braces: step 9 already re-gates at dequeue.
        guard clock() < a.expiresAtMs else { return }
        let route = panRoute()
        let plan = Self.plan(for: a, hapticsAvailable: arbiter.haptics.isAvailable,
                             userGain: userGain(), route: route.route, channels: route.channels)
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
        for a: Announcement, hapticsAvailable: Bool, userGain: Float,
        route: PanRoute, channels: Int
    ) -> Plan {
        let level = AnnouncementLevel(rawValue: a.urgency) ?? .aware
        let symbol = a.haptic == .burst
            ? HapticSymbol.alarm
            : HapticSymbol.symbol(level: level, sector: BearingSector.sector(a.bearingDeg))
        let haptic = hapticsAvailable
            ? HapticScript.hazard(symbol, level: level, userGain: Double(userGain))
            : nil
        return Plan(
            haptic: haptic,
            phrase: HazardPhrasing.phrase(for: a, hapticsDegraded: !hapticsAvailable),
            pan: haptic == nil ? 0 : StereoPan.pan(bearingDeg: a.bearingDeg, route: route, channelCount: channels)
        )
    }
}
