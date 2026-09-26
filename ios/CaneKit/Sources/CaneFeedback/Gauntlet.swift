import CaneContracts
import Foundation

/// Stages 1-7 of A1, composed. A pure function of `(state, event, now)`:
/// no clock, no sink, no `Task`. Everything the manager has to *do* comes
/// back as an outcome, never as a side effect.
public enum Gauntlet {
    public enum DropReason: Equatable, Sendable {
        case invalid(AwarenessEventError)
        case futureDated
        case stale
        case exactDuplicate
        case deduped
        case dedupedSeam
        case refractory
        case spatial
    }

    public enum Outcome: Equatable, Sendable {
        case dropped(DropReason)
        /// Urgency 0: log it and map it, never announce it.
        case ambient(AwarenessEvent, Int)
        case enqueued

        /// A16: what the manager must *do* about this outcome, as data.
        public var effects: [Effect] {
            switch self {
            case let .ambient(event, urgency): return [.ambient(event, urgency)]
            case .dropped, .enqueued: return []
            }
        }
    }

    /// Wire entry point.
    public static func ingest(
        _ data: Data,
        into state: FeedbackState,
        now: Int64
    ) -> (FeedbackState, Outcome) {
        var working = received(state, now: now)
        switch ValidationStage.validate(data, now: now) {
        case let .invalid(error):
            working.counters.invalidRejected += 1
            return (working, .dropped(.invalid(error)))
        case .futureDated:
            working.counters.futureDated += 1
            return (working, .dropped(.futureDated))
        case let .accepted(event):
            return admit(event, into: working, now: now)
        }
    }

    /// In-memory entry point (A16). Same gauntlet; the value has already
    /// satisfied every contract bound by construction.
    public static func ingest(
        _ event: AwarenessEvent,
        into state: FeedbackState,
        now: Int64
    ) -> (FeedbackState, Outcome) {
        var working = received(state, now: now)
        switch ValidationStage.validate(event, now: now) {
        case let .invalid(error):
            working.counters.invalidRejected += 1
            return (working, .dropped(.invalid(error)))
        case .futureDated:
            working.counters.futureDated += 1
            return (working, .dropped(.futureDated))
        case let .accepted(accepted):
            return admit(accepted, into: working, now: now)
        }
    }

    private static func received(_ state: FeedbackState, now: Int64) -> FeedbackState {
        var working = state
        working.counters.eventsReceived += 1
        working.rate = working.rate.recordingReceived(now: now)
        return working
    }

    // MARK: - Stages 2 to 7

    private static func admit(
        _ event: AwarenessEvent,
        into state: FeedbackState,
        now: Int64
    ) -> (FeedbackState, Outcome) {
        let (afterStaleness, staleOutcome) = staleness(event, in: state, now: now)
        if let staleOutcome { return (afterStaleness, staleOutcome) }
        let (afterDedup, dedupOutcome) = duplicates(event, in: afterStaleness, now: now)
        if let dedupOutcome { return (afterDedup, dedupOutcome) }
        return score(event, in: afterDedup, now: now)
    }

    /// Stage 2. A drop still scores, so the health signal knows what the
    /// wearer did not hear.
    private static func staleness(
        _ event: AwarenessEvent,
        in state: FeedbackState,
        now: Int64
    ) -> (FeedbackState, Outcome?) {
        guard StalenessGate.isStale(event, now: now) else { return (state, nil) }
        var working = state
        working.counters.staleDrops += 1
        working.counters.staleDropsByClass[event.cls, default: 0] += 1
        let wouldHaveBeen = UrgencyPolicy.decide(event).urgency
        working.counters.staleDropsByWouldBeUrgency[wouldHaveBeen, default: 0] += 1
        working.rate = working.rate.recordingStaleDrop(now: now)
        return (working, .dropped(.stale))
    }

    /// Stages 3 and 4: the exact-duplicate guard (transport reconnects
    /// replay) and the cross-source / seam dedup. On acceptance the event
    /// joins the dedup memory and refreshes its track's `lastSeenMs`.
    private static func duplicates(
        _ event: AwarenessEvent,
        in state: FeedbackState,
        now: Int64
    ) -> (FeedbackState, Outcome?) {
        var working = state
        working.recentAccepted = RecentAccepted.pruned(working.recentAccepted, now: now)
        if RecentAccepted.containsExactDuplicate(of: event, in: working.recentAccepted) {
            working.counters.duplicateExact += 1
            return (working, .dropped(.exactDuplicate))
        }
        switch DedupGate.decide(event, recent: working.recentAccepted) {
        case let .drop(reason):
            switch reason {
            case .crossSource:
                working.counters.deduped += 1
                return (working, .dropped(.deduped))
            case .seam:
                working.counters.dedupedSeam += 1
                return (working, .dropped(.dedupedSeam))
            }
        case let .keep(evicting):
            working.queue = AnnouncementQueue.removing(keys: evicting, from: working.queue)
        }
        working.recentAccepted = RecentAccepted.appending(
            AcceptedRecord(event: event, arrivalMs: now),
            to: working.recentAccepted,
            now: now
        )
        working.tracks = TrackRecords.touching(working.tracks, key: FeedbackKey(event), now: now)
        if event.rangeM == nil, event.rangeBand == nil {
            working.counters.bandUnknown += 1
        }
        return (working, nil)
    }

    /// Stages 5, 6 and 7: urgency with hysteresis, suppression, enqueue.
    private static func score(
        _ event: AwarenessEvent,
        in state: FeedbackState,
        now: Int64
    ) -> (FeedbackState, Outcome) {
        var working = state
        let key = FeedbackKey(event)
        let raw = UrgencyPolicy.decide(event)
        let smoothed = Hysteresis.apply(raw, record: working.tracks[key], now: now)
        if smoothed.held { working.counters.hysteresisHeld += 1 }
        let urgency = smoothed.decision.urgency

        // Ambient never reaches the refractory table: it is not competing
        // for the wearer's attention.
        guard let level = AnnouncementLevel(rawValue: urgency) else {
            working.counters.ambientLogged += 1
            return (working, .ambient(event, urgency))
        }

        switch RefractoryGate.check(
            event,
            urgency: urgency,
            record: working.tracks[key],
            announcements: working.recentAnnouncements,
            now: now
        ) {
        case .suppressedByKey:
            working.counters.refractorySuppressed += 1
            return (working, .dropped(.refractory))
        case .suppressedSpatially:
            working.counters.spatialSuppressed += 1
            return (working, .dropped(.spatial))
        case .pass:
            break
        }

        let item = QueuedItem(event: event, level: level, driver: smoothed.decision.driver)
        let inserted = AnnouncementQueue.inserting(item, into: working.queue)
        working.queue = inserted.queue
        if inserted.overflowed { working.counters.queueOverflow += 1 }
        return (working, .enqueued)
    }
}
