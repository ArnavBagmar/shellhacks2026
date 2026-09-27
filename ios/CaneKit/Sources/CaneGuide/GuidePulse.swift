import Foundation

/// The watch's tap rhythm. The Taptic Engine on the wrist has no intensity
/// control, only a handful of fixed taps, so closeness is carried by *rate*
/// -- a parking sensor: slow when far, a fast patter when close.
public enum GuidePulse {
    public enum Tap: Sendable, Equatable {
        /// A light click.
        case light
        /// A firm tap: within `nearM`.
        case strong
    }

    /// At or beyond this, the slowest rhythm.
    public static let farM = 0.6
    /// At or under this, the fastest.
    public static let touchM = 0.03
    public static let nearM = 0.10
    public static let slowestS = 1.0
    /// Faster than this and the watch merges taps into a buzz.
    public static let fastestS = 0.15

    /// Seconds between taps for a fingertip `gapM` from the thing.
    public static func interval(gapM: Double) -> Double {
        let clamped = min(max(gapM, touchM), farM)
        let t = (clamped - touchM) / (farM - touchM)
        return fastestS + t * (slowestS - fastestS)
    }

    public static func tap(gapM: Double) -> Tap {
        gapM <= nearM ? .strong : .light
    }
}
