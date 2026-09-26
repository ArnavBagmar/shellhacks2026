import Foundation

/// The output routes on which panning is allowed to be non-zero.
///
/// `.builtInSpeaker` and `.builtInReceiver` are absent on purpose: the
/// speakers are ~15 cm apart at 30 cm from the chest, and a phone mounted
/// upside-down in the strap **swaps L and R**, which would point the cue at
/// the wrong side.
public enum PanRoute: Equatable, Sendable {
    case headphones
    case bluetoothA2DP
    case bluetoothLE
    case usbAudio
    case airPlay
    case builtInSpeaker
    case builtInReceiver
    case other

    public var allowsPanning: Bool {
        switch self {
        case .headphones, .bluetoothA2DP, .bluetoothLE, .usbAudio, .airPlay:
            return true
        case .builtInSpeaker, .builtInReceiver, .other:
            return false
        }
    }
}

/// Amendment 4 -- panning is a bonus that must never be load-bearing and
/// must never reduce audibility.
///
/// Haptics carry direction unconditionally. The spoken sector words carry
/// direction unconditionally. With one AirPod in, a hard pan is an 8-20 dB
/// attenuator or, on a wired bud, silence -- so an uncapped pan can make a
/// hazard announcement *inaudible*, or point the spatial cue opposite the
/// words.
///
/// Pure and fully tested either way the controller rules on Amendment 4's
/// open question (panned earcon, the shipping default, versus panned
/// speech): nothing here touches AVFoundation.
public enum StereoPan {
    /// Caps the pan so the quieter channel keeps at least `minChannelGain`.
    public static let panCap = 0.4

    /// Linear pan law: the quieter channel's gain is `1 - |pan|`, so the cap
    /// above guarantees >= 0.6 (about -4.4 dB).
    public static let minChannelGainFloor = 0.6

    /// `clamp(sin(bearing), -1, 1) * PAN_CAP`. Negative is the user's left,
    /// matching `BearingSector`'s convention.
    ///
    /// `pan(0) == pan(180) == 0`: the front/back collapse is accepted, and
    /// the haptic and the words separate them.
    public static func pan(bearingDeg: Double) -> Double {
        let radians = bearingDeg * .pi / 180
        let value = min(max(sin(radians), -1.0), 1.0) * panCap
        // sin(180 degrees) is ~1.2e-16, not 0, in binary floating point.
        return abs(value) < 1e-9 ? 0 : value
    }

    /// Applies the route gate. Must be recomputed on every route change and
    /// never cached across one, because buds go in and out mid-walk.
    ///
    /// iOS downmixes to mono for a single AirPod, and the route API gives no
    /// way to detect that -- which is exactly why the cap exists.
    public static func pan(bearingDeg: Double, route: PanRoute, channelCount: Int) -> Double {
        guard route.allowsPanning, channelCount >= 2 else { return 0 }
        return pan(bearingDeg: bearingDeg)
    }

    /// Gain of the quieter channel. The invariant asserted in tests is
    /// `minChannelGain(pan(b)) >= 0.6` for every bearing, so a listener with
    /// one bud in -- on either side -- loses at most 4.4 dB of the panned
    /// component.
    public static func minChannelGain(_ pan: Double) -> Double {
        1 - abs(pan)
    }
}
