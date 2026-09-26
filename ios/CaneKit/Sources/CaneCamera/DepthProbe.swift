import Foundation

/// How far the TrueDepth sensor's own reading can be trusted.
///
/// `AVDepthData` reports this per frame, and it is the honesty gate for the
/// whole feature: only `.absolute` is a measurement in metres. `.relative`
/// means the sensor is producing a consistent *shape* but an unknown scale,
/// which must never be spoken as a distance -- the same rule that stops the
/// X4 path and Depth Anything from stating metres.
public enum DepthAccuracy: String, Sendable, Equatable {
    case absolute
    case relative
}

/// One reading from the forward depth sensor, in metres.
public struct DepthSample: Sendable, Equatable {
    /// Median of the valid pixels inside the probe region.
    public let metres: Double
    public let nearestMetres: Double
    /// Fraction of the probe region that returned a usable value, 0...1.
    /// TrueDepth returns NaN wherever its infrared pattern did not come back
    /// -- too far, too dark, too shiny, or washed out by sunlight.
    public let coverage: Double
    public let accuracy: DepthAccuracy
    public let timestampMs: Int64

    public init(metres: Double, nearestMetres: Double, coverage: Double,
                accuracy: DepthAccuracy, timestampMs: Int64) {
        self.metres = metres
        self.nearestMetres = nearestMetres
        self.coverage = coverage
        self.accuracy = accuracy
        self.timestampMs = timestampMs
    }

    /// Whether this reading may be spoken as a distance at all.
    ///
    /// Three ways a reading is not a distance: the sensor said so
    /// (`.relative`), too little of the probe came back, or the value sits
    /// outside TrueDepth's honest working range. Sunlight typically shows up
    /// as collapsing coverage rather than as wrong numbers.
    public func isTrustworthy(minCoverage: Double = DepthProbe.minCoverage) -> Bool {
        accuracy == .absolute
            && coverage >= minCoverage
            && metres >= DepthProbe.minRangeM
            && metres <= DepthProbe.maxRangeM
    }
}

/// Sampling maths for a depth map, kept free of AVFoundation so it runs and
/// is tested on any platform.
public enum DepthProbe {
    /// TrueDepth is an infrared dot projector built for face unlock at arm's
    /// length. Below this it saturates; beyond it the dots are too sparse to
    /// trust. These are working limits for a walking aid, deliberately
    /// tighter than the sensor's advertised maximum.
    public static let minRangeM = 0.2
    public static let maxRangeM = 3.0

    /// Below this much valid coverage the region is not a reading. Bright sun
    /// washes the pattern out, and the failure looks like coverage falling
    /// rather than values going wrong.
    public static let minCoverage = 0.35

    /// The central 10%-wide, 10%-high rectangle, matching the centre probe the
    /// desktop preview draws, so the two tools mean the same thing by "ahead".
    public static func centreRegion(width: Int, height: Int) -> (x0: Int, x1: Int, y0: Int, y1: Int) {
        (max(0, Int(Double(width) * 0.45)), max(1, Int(Double(width) * 0.55)),
         max(0, Int(Double(height) * 0.45)), max(1, Int(Double(height) * 0.55)))
    }

    /// Median depth over a region, ignoring the pixels the sensor could not
    /// resolve. Returns nil when the region is empty or nothing came back.
    ///
    /// `values` is row-major, `width * height` long, in metres, with NaN or a
    /// non-positive value meaning "no return".
    public static func sample(values: [Float], width: Int, height: Int,
                              region: (x0: Int, x1: Int, y0: Int, y1: Int)? = nil,
                              accuracy: DepthAccuracy, timestampMs: Int64) -> DepthSample? {
        guard width > 0, height > 0, values.count == width * height else { return nil }
        let box = region ?? centreRegion(width: width, height: height)
        let x0 = min(max(box.x0, 0), width), x1 = min(max(box.x1, x0), width)
        let y0 = min(max(box.y0, 0), height), y1 = min(max(box.y1, y0), height)
        let total = (x1 - x0) * (y1 - y0)
        guard total > 0 else { return nil }

        var valid: [Double] = []
        valid.reserveCapacity(total)
        for y in y0..<y1 {
            let row = y * width
            for x in x0..<x1 {
                let value = Double(values[row + x])
                if value.isFinite, value > 0 { valid.append(value) }
            }
        }
        guard !valid.isEmpty else {
            return DepthSample(metres: 0, nearestMetres: 0, coverage: 0,
                               accuracy: accuracy, timestampMs: timestampMs)
        }
        valid.sort()
        let middle = valid.count / 2
        let median = valid.count % 2 == 1 ? valid[middle] : (valid[middle - 1] + valid[middle]) / 2
        return DepthSample(metres: median, nearestMetres: valid[0],
                           coverage: Double(valid.count) / Double(total),
                           accuracy: accuracy, timestampMs: timestampMs)
    }
}
