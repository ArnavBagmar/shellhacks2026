import Foundation

/// Panning is a bonus, never load-bearing (step-10 design Amendment 4):
/// haptics and the spoken sector words carry direction unconditionally. Pan
/// is capped so a listener with one bud in -- either side -- loses at most
/// ~4.4 dB, is zero on any route that is not real stereo headphones, and is
/// applied to a 40 ms earcon, never to speech.
public enum StereoPan {
    public static let panCap = 0.4
    public static let minChannelGain = 0.6

    /// `sin(bearing) x 0.4`: -0.4 hard-ish left, +0.4 right, 0 ahead and
    /// directly behind (front/back is the haptic's and the words' job).
    public static func pan(bearingDeg: Double, stereoRoute: Bool) -> Double {
        guard stereoRoute, bearingDeg.isFinite else { return 0 }
        let raw = sin(bearingDeg * .pi / 180)
        return min(max(raw, -1), 1) * panCap
    }

    /// Linear pan law: the far channel is attenuated to `1 - |pan|`.
    public static func channelGains(pan: Double) -> (left: Double, right: Double) {
        (left: 1 - max(pan, 0), right: 1 + min(pan, 0))
    }

    /// Port types that are real stereo to the ears. The built-in speaker is
    /// excluded on purpose: a phone upside-down in the strap swaps L and R.
    public static let stereoPortTypes: Set<String> = [
        "Headphones", "BluetoothA2DPOutput", "BluetoothLE", "USBAudio", "AirPlay",
    ]

    /// Pan only when there is exactly one output, of a stereo type, with at
    /// least two channels. Recompute on every route change.
    public static func isStereoRoute(outputs: [(portType: String, channels: Int)]) -> Bool {
        guard outputs.count == 1, let only = outputs.first else { return false }
        return stereoPortTypes.contains(only.portType) && only.channels >= 2
    }
}
