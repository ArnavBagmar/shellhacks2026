import CaneContracts
import CaneFeedback
import Foundation

/// What a replayed walk announced, as a value.
///
/// **It counts decisions, not seconds of audio.** The replay sink returns
/// immediately, so nothing is ever preempted or aged out of the queue the way
/// it would be on a device whose speaker takes ~1.2 s per phrase. That is
/// deliberate: a path-conflict gate changes what the gauntlet *decides*, and
/// measuring decisions isolates that change from renderer timing. Read the
/// totals as an upper bound on what the wearer would hear, and read `stops` as
/// exact -- urgency 3 preempts everything and always speaks, so its count does
/// not depend on the renderer at all.
///
/// Accumulators return a new value rather than mutating in place, so a tally
/// can be folded over a sequence without any stage owning shared state.
public struct ReplayTally: Equatable, Sendable {
    public private(set) var announcements: Int
    public private(set) var urgencies: [Int: Int]
    public private(set) var classes: [ObjectClass: Int]
    /// Urgency 2 and 3. Urgency 1 is haptic-only by design.
    public private(set) var spoken: Int
    public private(set) var hapticOnly: Int
    public private(set) var firstEmitMs: Int64?
    public private(set) var lastEmitMs: Int64?

    public init() {
        announcements = 0
        urgencies = [:]
        classes = [:]
        spoken = 0
        hapticOnly = 0
        firstEmitMs = nil
        lastEmitMs = nil
    }

    /// The number the baseline exists to move.
    public var stops: Int { count(urgency: AnnouncementLevel.immediate.rawValue) }

    public func count(urgency: Int) -> Int { urgencies[urgency] ?? 0 }

    public func count(cls: ObjectClass) -> Int { classes[cls] ?? 0 }

    /// Wall-clock span of the recording, from the first emit stamp to the last.
    public var spanMs: Int64 {
        guard let first = firstEmitMs, let last = lastEmitMs else { return 0 }
        return last - first
    }

    /// Zero for a zero span: one event is a count, not a rate, and dividing
    /// by it would put an infinity on a slide.
    public func perMinute(_ count: Int) -> Double {
        guard spanMs > 0 else { return 0 }
        return Double(count) * 60_000.0 / Double(spanMs)
    }

    public func adding(_ announcement: Announcement) -> ReplayTally {
        var next = self
        next.announcements += 1
        next.urgencies[announcement.urgency, default: 0] += 1
        next.classes[announcement.cls, default: 0] += 1
        if announcement.speech == nil {
            next.hapticOnly += 1
        } else {
            next.spoken += 1
        }
        return next
    }

    /// Widen the observed span. Called for every line read, announced or not:
    /// the denominator is how long the walk was, not how long it was talking.
    public func observing(emitMs: Int64) -> ReplayTally {
        var next = self
        next.firstEmitMs = Swift.min(firstEmitMs ?? emitMs, emitMs)
        next.lastEmitMs = Swift.max(lastEmitMs ?? emitMs, emitMs)
        return next
    }
}
