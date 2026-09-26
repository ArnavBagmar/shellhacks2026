import Foundation

/// A2 -- the one place bearings are subtracted. `179` and `-178` are 3 apart,
/// not 357: an unwrapped subtraction at the antimeridian is the "announces
/// twice" bug in the reviewer checklist, and the X4's most interesting
/// objects (overtaking traffic) live exactly there.
public enum AngularMath {
    private static let fullTurnDeg = 360.0
    private static let halfTurnDeg = 180.0

    /// Signed difference `a - b`, normalised into the contract's bearing
    /// interval `(-180, 180]`. Exact opposition returns `+180` (the inclusive
    /// end), so no caller ever sees a value outside the interval the schema
    /// allows for `bearing_deg`.
    public static func delta(_ a: Double, _ b: Double) -> Double {
        let wrapped = (a - b).truncatingRemainder(dividingBy: fullTurnDeg)
        let shifted = (wrapped + fullTurnDeg + halfTurnDeg)
            .truncatingRemainder(dividingBy: fullTurnDeg) - halfTurnDeg
        return shifted == -halfTurnDeg ? halfTurnDeg : shifted
    }

    /// Unsigned angular separation in `[0, 180]` -- what every proximity gate
    /// in the gauntlet actually compares against its tolerance.
    public static func absDelta(_ a: Double, _ b: Double) -> Double {
        abs(delta(a, b))
    }

    /// `true` when two bearings are within `toleranceDeg` of each other the
    /// short way round.
    public static func isWithin(_ toleranceDeg: Double, _ a: Double, _ b: Double) -> Bool {
        absDelta(a, b) <= toleranceDeg
    }
}
