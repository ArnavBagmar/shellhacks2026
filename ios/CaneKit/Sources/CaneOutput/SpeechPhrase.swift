import Foundation
import CaneContracts
import CaneFeedback

/// Amendment 2 -- every phrase the device ever speaks.
///
/// **Direction first, at every urgency level.** Four reasons: (i) urgency 3
/// preempts urgency 2 by design, so any phrase can be truncated mid-word --
/// direction-first means the user has already received the part that
/// determines the action; (ii) the haptic plays first and encodes direction,
/// so the word confirms what the body just felt instead of contradicting the
/// order; (iii) it makes urgency 2 and urgency 3 one consistent vocabulary
/// (WCAG 3.2.4 Consistent Identification); (iv) class-first puts the alarming
/// word ("cyclist") first and users act on it before the direction arrives.
public enum SpeechPhrase {
    /// Enforced by dropping in priority order: range, then "fast". The
    /// sector words and the class word are never dropped.
    public static let maxSpokenWords = 6

    /// Reuses step 9's threshold rather than introducing a second knob.
    public static let fastClosingMps = UrgencyPolicy.fastClosingMps

    // MARK: - Urgency 3 (Amendment 2's H2 table)

    /// Every entry is **locative or a self-contained imperative, never a
    /// steering command**. The system does not model free space, so it must
    /// never tell the user which way to move: "Left side!" states where the
    /// hazard is, "Left!" does not. The one extra word costs ~180 ms and
    /// removes a coin-flip wrong-action risk.
    ///
    /// "Stop!" is correct for the whole ahead family because A7 caps static
    /// classes at urgency 2, so urgency 3 ahead is always a moving class
    /// closing head-on with TTC < 2 s.
    public static func immediateWords(sector: Sector) -> String {
        switch sector {
        case .ahead, .aheadQuarter:
            return "Stop!"
        case .lateral(.left):
            return "Left side!"
        case .lateral:
            return "Right side!"
        case .behindQuarter, .behind:
            return "Behind you!"
        }
    }

    // MARK: - Range words

    /// A spoken zero reads as impact, so `n < 1` renders as "close" and
    /// never "about 0 meters". "about 1 meter" stays singular. Never a
    /// decimal.
    ///
    /// Step 9 owns the rounding into `.aboutMetres` and it must be **floor**,
    /// so the spoken distance is never further than the truth.
    public static func rangeWords(_ range: RangeHint) -> String? {
        switch range {
        case .aboutMetres(let n) where n < 1:
            return "close"
        case .aboutMetres(1):
            return "about 1 meter"
        case .aboutMetres(let n):
            return "about \(n) meters"
        case .band(.near):
            return "close"
        case .band(.mid):
            return "nearby"
        case .band(.far):
            return "far"
        case .unknown:
            return nil
        }
    }

    /// Spoken form of each class. One word wherever the language allows it,
    /// because every word competes with the 1.2 s budget.
    ///
    /// NOT verbatim from the spec: Amendment 2 fixes the sector and range
    /// vocabulary but leaves the class words open. Ratify before the
    /// blindfolded-listener protocol runs, since changing them invalidates
    /// its transcript.
    public static func classWord(_ cls: ObjectClass) -> String {
        switch cls {
        case .person: return "person"
        case .cyclist: return "cyclist"
        case .scooter: return "scooter"
        case .car: return "car"
        case .bus: return "bus"
        case .pole: return "pole"
        case .bollard: return "bollard"
        case .wall: return "wall"
        case .parkedCar: return "parked car"
        case .door: return "door"
        case .overhang: return "overhang"
        case .unknownObstacle: return "obstacle"
        }
    }

    // MARK: - The template

    public static func wordCount(_ phrase: String) -> Int {
        phrase.split(whereSeparator: { $0 == " " }).count
    }

    /// `"{sectorWords}, {classWord}"`, then `" fast"` iff
    /// `closingMps > 3.0`, then `", {rangeWords}"` iff the sector is `.ahead`
    /// **and** the class is static.
    ///
    /// The word "closing" is never spoken: it costs ~250 ms of masking to
    /// say what the urgency level already said.
    public static func actSoonWords(bearingDeg: Double, cls: ObjectClass,
                                    range: RangeHint, closingMps: Double?) -> String {
        let sector = BearingSector.sector(bearingDeg)
        let head = "\(BearingSector.phrase(bearingDeg)), \(classWord(cls))"

        let fast = (closingMps ?? 0) > fastClosingMps
        let wantsRange = sector == .ahead && UrgencyPolicy.isStatic(cls)
        let range = wantsRange ? rangeWords(range) : nil

        let withFast = fast ? "\(head) fast" : head
        let withRange = range.map { "\(withFast), \($0)" } ?? withFast

        // Drop in priority order until the phrase fits: range, then "fast".
        for candidate in [withRange, withFast, head] where wordCount(candidate) <= maxSpokenWords {
            return candidate
        }
        // The head alone can exceed the budget ("behind you to the left,
        // parked car" is 7). Sector and class are never dropped, so this is
        // the floor: Amendment 3's escape ladder (raise rate, then lower
        // maxSpokenWords) is what absorbs it, never a shorter vocabulary.
        return head
    }

    /// The single entry point the renderer calls.
    public static func words(for announcement: Announcement) -> String? {
        guard let verbosity = announcement.speech else { return nil }
        let sector = BearingSector.sector(announcement.bearingDeg)
        switch verbosity {
        case .oneWord:
            // "<= 2 words, <= 450 ms", not literally one word.
            return immediateWords(sector: sector)
        case .brief:
            return actSoonWords(bearingDeg: announcement.bearingDeg,
                                cls: announcement.cls,
                                range: announcement.range,
                                closingMps: announcement.closingMps)
        }
    }
}
