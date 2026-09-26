import Foundation

/// One whole TrueDepth map, copied out at the moment a question's photo is
/// taken and turned to the photo's orientation, so a box Gemini draws on
/// the photo lands on the same pixels here.
///
/// "How far is the Sprite can?": the laptop answers with the can's box in
/// the photo; `distance(inBox:)` measures inside it. The map is the one from
/// the photo's moment, not a live one -- the answer comes back ~1.5 s later
/// and the wearer may have turned.
public struct DepthMap: Sendable, Equatable {
    /// Row-major metres; NaN or <= 0 is "no return".
    public let values: [Float]
    public let width: Int
    public let height: Int
    public let accuracy: DepthAccuracy
    public let timestampMs: Int64

    /// Only the middle of a box is measured: a box around a can also holds
    /// a strip of table and wall at its edges.
    public static let boxInnerFraction = 0.6
    /// Which reading inside the box stands for the thing. A box around a
    /// thin or round object is still partly background, which is farther;
    /// the 30th percentile leans to the nearer object without trusting one
    /// stray near pixel the way a minimum would.
    public static let boxPercentile = 0.3

    public init?(values: [Float], width: Int, height: Int, accuracy: DepthAccuracy, timestampMs: Int64) {
        guard width > 0, height > 0, values.count == width * height else { return nil }
        self.values = values
        self.width = width
        self.height = height
        self.accuracy = accuracy
        self.timestampMs = timestampMs
    }

    /// The map turned clockwise by a multiple of 90 degrees (anything else
    /// is rounded to the nearest quarter turn).
    public func rotatedClockwise(_ degrees: Int) -> DepthMap {
        let quarter = ((Int((Double(degrees) / 90).rounded()) % 4) + 4) % 4
        guard quarter != 0 else { return self }
        let (w, h) = (width, height)
        var out = [Float](repeating: .nan, count: values.count)
        switch quarter {
        case 1:
            // old (x, y) -> new (h-1-y, x); the new map is h wide.
            for y in 0..<h { for x in 0..<w { out[x * h + (h - 1 - y)] = values[y * w + x] } }
        case 2:
            for y in 0..<h { for x in 0..<w { out[(h - 1 - y) * w + (w - 1 - x)] = values[y * w + x] } }
        default:
            // old (x, y) -> new (y, w-1-x); the new map is h wide.
            for y in 0..<h { for x in 0..<w { out[(w - 1 - x) * h + y] = values[y * w + x] } }
        }
        let odd = quarter % 2 == 1
        return DepthMap(values: out, width: odd ? h : w, height: odd ? w : h,
                        accuracy: accuracy, timestampMs: timestampMs)!
    }

    /// The distance to whatever fills a box given as fractions of the photo
    /// (0...1 from the top-left), or nil for an empty box. Check
    /// `isTrustworthy()` before speaking it: coverage and accuracy are
    /// carried through exactly as for the centre probe.
    public func distance(inBox xMin: Double, _ yMin: Double, _ xMax: Double, _ yMax: Double) -> DepthSample? {
        let inset = (1 - Self.boxInnerFraction) / 2
        let boxW = xMax - xMin, boxH = yMax - yMin
        guard boxW > 0, boxH > 0 else { return nil }
        let fx0 = xMin + boxW * inset, fx1 = xMax - boxW * inset
        let fy0 = yMin + boxH * inset, fy1 = yMax - boxH * inset
        let x0 = clamp(Int((fx0 * Double(width)).rounded(.down)), 0, width - 1)
        let y0 = clamp(Int((fy0 * Double(height)).rounded(.down)), 0, height - 1)
        // At least one pixel, however small the box.
        let x1 = clamp(Int((fx1 * Double(width)).rounded(.up)), x0 + 1, width)
        let y1 = clamp(Int((fy1 * Double(height)).rounded(.up)), y0 + 1, height)

        var valid: [Double] = []
        valid.reserveCapacity((x1 - x0) * (y1 - y0))
        for y in y0..<y1 {
            for x in x0..<x1 {
                let value = Double(values[y * width + x])
                if value.isFinite, value > 0 { valid.append(value) }
            }
        }
        let total = (x1 - x0) * (y1 - y0)
        guard !valid.isEmpty else {
            return DepthSample(metres: 0, nearestMetres: 0, coverage: 0, accuracy: accuracy, timestampMs: timestampMs)
        }
        valid.sort()
        let index = min(valid.count - 1, Int(Double(valid.count - 1) * Self.boxPercentile))
        return DepthSample(metres: valid[index], nearestMetres: valid[0],
                           coverage: Double(valid.count) / Double(total),
                           accuracy: accuracy, timestampMs: timestampMs)
    }

    private func clamp(_ value: Int, _ low: Int, _ high: Int) -> Int {
        min(max(value, low), high)
    }
}
