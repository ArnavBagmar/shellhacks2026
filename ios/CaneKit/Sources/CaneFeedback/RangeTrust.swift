import CaneContracts

/// §4.5: the phone's monocular range stays trustworthy to roughly 15 m; past
/// that, boxes get small and depth gets soft.
///
/// This constant is deliberately *not* duplicated at the top of a stage file:
/// two stages (dedup's source preference in A4 and the urgency ladder's
/// `trusted` in A7) must agree on it exactly, and a second copy is a silent
/// way for them to disagree.
public enum RangeTrust {
    public static let trustRangeM = 15.0

    /// Metric range this far out, from any producer, is treated as real
    /// metres. The contract already guarantees only the phone may state
    /// `range_m` at all.
    public static func isTrusted(rangeM: Double?) -> Bool {
        guard let rangeM else { return false }
        return rangeM <= trustRangeM
    }

    public static func isTrusted(_ event: AwarenessEvent) -> Bool {
        isTrusted(rangeM: event.rangeM)
    }
}
