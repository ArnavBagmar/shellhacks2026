import Foundation

/// A point in the phone photo, as fractions from the top-left (0...1).
public struct NormPoint: Sendable, Equatable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

/// A box in the phone photo, as fractions from the top-left (0...1) --
/// the same convention as the laptop's `phone_box`.
public struct NormBox: Sendable, Equatable {
    public var xMin: Double
    public var yMin: Double
    public var xMax: Double
    public var yMax: Double

    public init(xMin: Double, yMin: Double, xMax: Double, yMax: Double) {
        self.xMin = xMin
        self.yMin = yMin
        self.xMax = xMax
        self.yMax = yMax
    }

    public var width: Double { xMax - xMin }
    public var height: Double { yMax - yMin }
    public var centre: NormPoint { NormPoint(x: (xMin + xMax) / 2, y: (yMin + yMax) / 2) }

    /// Grown by `fraction` of its own size on every side.
    public func expanded(by fraction: Double) -> NormBox {
        let dx = width * fraction, dy = height * fraction
        return NormBox(xMin: xMin - dx, yMin: yMin - dy, xMax: xMax + dx, yMax: yMax + dy)
    }

    public func contains(_ p: NormPoint) -> Bool {
        (xMin...xMax).contains(p.x) && (yMin...yMax).contains(p.y)
    }
}

/// Metres in the front camera's frame: x to the wearer's right, y down,
/// z straight out from the chest. The photo is upright and unmirrored
/// (`FrontCamera`), so image-right is the wearer's right.
public struct Point3: Sendable, Equatable {
    public var x: Double
    public var y: Double
    public var z: Double

    public init(x: Double, y: Double, z: Double) {
        self.x = x
        self.y = y
        self.z = z
    }

    public static func - (a: Point3, b: Point3) -> Point3 { Point3(x: a.x - b.x, y: a.y - b.y, z: a.z - b.z) }
    public var length: Double { (x * x + y * y + z * z).squareRoot() }
}

/// Which way the hand has to move, in the wearer's words.
public enum GuideDirection: String, Sendable, Equatable {
    case left, right, up, down, forward, back

    /// Short on purpose: it is said again every couple of seconds.
    public var spoken: String {
        switch self {
        case .left: "Left."
        case .right: "Right."
        case .up: "Up."
        case .down: "Down."
        case .forward: "Forward."
        case .back: "Back toward you."
        }
    }
}

/// Pinhole maths from photo fractions + TrueDepth metres to the gap between
/// a fingertip and the thing it is reaching for.
public enum GuideGeometry {
    /// A photo point at `depthM` (TrueDepth's planar depth) in camera metres.
    /// `aspect` is the photo's width / height.
    public static func point(_ p: NormPoint, depthM: Double, hfovDeg: Double, aspect: Double) -> Point3 {
        let tanX = tan(hfovDeg * .pi / 360)
        let tanY = tanX / max(aspect, 0.01)
        return Point3(x: (2 * p.x - 1) * tanX * depthM, y: (2 * p.y - 1) * tanY * depthM, z: depthM)
    }

    /// Half the object's smaller side, in metres: the hand is "there" when
    /// it reaches the object's surface, not its centre.
    public static func radius(of box: NormBox, depthM: Double, hfovDeg: Double, aspect: Double) -> Double {
        let tanX = tan(hfovDeg * .pi / 360)
        let tanY = tanX / max(aspect, 0.01)
        let w = box.width * 2 * tanX * depthM
        let h = box.height * 2 * tanY * depthM
        return max(0, min(w, h) / 2)
    }

    /// Metres from the fingertip to the object's surface (0 when touching),
    /// and the vector from hand to object.
    public static func gap(
        hand: NormPoint, handDepthM: Double, object: NormBox, objectDepthM: Double,
        hfovDeg: Double, aspect: Double
    ) -> (metres: Double, towards: Point3) {
        let h = point(hand, depthM: handDepthM, hfovDeg: hfovDeg, aspect: aspect)
        let o = point(object.centre, depthM: objectDepthM, hfovDeg: hfovDeg, aspect: aspect)
        let towards = o - h
        let r = radius(of: object, depthM: objectDepthM, hfovDeg: hfovDeg, aspect: aspect)
        return (max(0, towards.length - r), towards)
    }

    /// The one axis that matters most, or nil when the hand is on target.
    public static func direction(_ towards: Point3, deadbandM: Double = 0.03) -> GuideDirection? {
        let ax = abs(towards.x), ay = abs(towards.y), az = abs(towards.z)
        guard max(ax, ay, az) > deadbandM else { return nil }
        if ax >= ay, ax >= az { return towards.x > 0 ? .right : .left }
        if ay >= az { return towards.y > 0 ? .down : .up }
        return towards.z > 0 ? .forward : .back
    }
}
