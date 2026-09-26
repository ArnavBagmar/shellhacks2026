/// A9 -- "urgency thresholds use bands with hysteresis so a noisy estimate
/// cannot flip urgency back and forth."
///
/// Upgrades are immediate: nothing here can delay a Stop. Only a *downgrade*
/// has to earn its way past the margin, and three kinds of downgrade never
/// do: a receding object, an object behind you, and a stale one (the
/// staleness gate drops those before this stage runs).
public enum Hysteresis {
    /// A TTC estimate has to clear its threshold by half a second.
    public static let ttcHysteresisS = 0.5
    /// A static range estimate has to clear it by half a metre.
    public static let rangeHysteresisM = 0.5
    /// When the driving metric changes kind there is no comparable margin,
    /// so the old level simply has to have stood for this long.
    public static let dwellMs: Int64 = 1_000

    public struct Result: Equatable, Sendable {
        public let decision: UrgencyDecision
        /// True when the raw downgrade was refused and the announced level
        /// was held (counted as `hysteresisHeld`).
        public let held: Bool
    }

    public static func apply(_ raw: UrgencyDecision, record: TrackRecord?, now: Int64) -> Result {
        guard !raw.isHardZero else { return Result(decision: raw, held: false) }
        guard let record, raw.urgency < record.lastUrgency else {
            return Result(decision: raw, held: false)
        }
        guard !downgradeIsEarned(raw: raw, record: record, now: now) else {
            return Result(decision: raw, held: false)
        }
        let held = UrgencyDecision(
            urgency: record.lastUrgency,
            driver: raw.driver,
            isHardZero: false
        )
        return Result(decision: held, held: true)
    }

    private static func downgradeIsEarned(raw: UrgencyDecision, record: TrackRecord, now: Int64) -> Bool {
        switch (raw.driver, record.lastDriver) {
        case let (.ttc(new), .ttc):
            return new >= ttcThreshold(forLevel: record.lastUrgency) + ttcHysteresisS
        case let (.staticRange(new), .staticRange):
            return new >= rangeThreshold(forLevel: record.lastUrgency) + rangeHysteresisM
        case let (.band(new), .band(previous)):
            // Bands are a full step apart by construction: the estimate has
            // to have left the band it was announced in.
            return new != previous
        default:
            // The driver changed kind; there is no margin to compare, so the
            // announced level has to have stood for the dwell.
            return now - record.presentedAtMs >= dwellMs
        }
    }

    /// The TTC boundary the announced level sat on.
    private static func ttcThreshold(forLevel level: Int) -> Double {
        level == UrgencyLevel.immediate ? UrgencyPolicy.ttcStopS : UrgencyPolicy.ttcActS
    }

    /// The static-range boundary the announced level sat on.
    private static func rangeThreshold(forLevel level: Int) -> Double {
        level == UrgencyLevel.actSoon ? UrgencyPolicy.staticActM : UrgencyPolicy.staticAwareM
    }
}
