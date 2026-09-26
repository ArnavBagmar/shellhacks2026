import CaneCamera
import CaneTransport
import Foundation

/// What the TrueDepth map says about a found thing's box: a distance, or
/// why there is none. Only `measured` is ever spoken as a number.
public enum DistanceOutcome: Equatable, Sendable {
    case measured(DepthSample)
    /// Found in a 360 view, not in the phone photo: no depth there.
    case outOfView
    /// No depth map with the photo (no TrueDepth, or no photo at all).
    case noSensor
    /// A clean reading, but past TrueDepth's honest range.
    case tooFar(DepthSample)
    /// Too little came back (sunlight, glass, too close) or no scale.
    case unreadable(DepthSample?)

    public static func evaluate(box: PhoneBox?, depth: DepthMap?) -> DistanceOutcome {
        guard let box else { return .outOfView }
        guard let depth else { return .noSensor }
        guard let sample = depth.distance(inBox: box.xMin, box.yMin, box.xMax, box.yMax) else {
            return .unreadable(nil)
        }
        if sample.isTrustworthy() { return .measured(sample) }
        if sample.accuracy == .absolute, sample.coverage >= DepthProbe.minCoverage,
           sample.metres > DepthProbe.maxRangeM {
            return .tooFar(sample)
        }
        return .unreadable(sample)
    }

    public var sample: DepthSample? {
        switch self {
        case .measured(let s), .tooFar(let s): s
        case .unreadable(let s): s
        case .outOfView, .noSensor: nil
        }
    }

    /// Short name for the black box.
    public var label: String {
        switch self {
        case .measured: "measured"
        case .outOfView: "out_of_view"
        case .noSensor: "no_sensor"
        case .tooFar: "too_far"
        case .unreadable: "unreadable"
        }
    }
}

/// Distances in the wearer's own units, rounded to what a person can use.
public enum DistanceWords {
    /// "about 4 feet away" / "about 1.2 metres away".
    public static func away(_ metres: Double, locale: Locale = .current) -> String {
        if usesFeet(locale) {
            let feet = metres * 3.28084
            if feet < 1 { return "less than a foot away" }
            let whole = Int(feet.rounded())
            return whole == 1 ? "about a foot away" : "about \(whole) feet away"
        }
        if metres < 1 {
            let centimetres = max(10, Int((metres * 10).rounded()) * 10)
            return "about \(centimetres) centimetres away"
        }
        let tenths = (metres * 10).rounded() / 10
        let text = tenths == tenths.rounded() ? String(Int(tenths)) : String(format: "%.1f", tenths)
        return text == "1" ? "about 1 metre away" : "about \(text) metres away"
    }

    /// "more than 10 feet away" for a reading past the sensor's range.
    public static func beyondRange(locale: Locale = .current) -> String {
        usesFeet(locale) ? "more than 10 feet away" : "more than 3 metres away"
    }

    static func usesFeet(_ locale: Locale) -> Bool {
        locale.measurementSystem == .us || locale.measurementSystem == .uk
    }
}
