import CaneContracts

/// Stage 8 (A11). Dequeue, **re-gate staleness with a fresh `now`**,
/// throttle, preempt, present. A pure function: it decides, the actor acts.
///
/// `MIN_SPEECH_GAP_MS` is one of the three demo knobs (H6).
public enum Pump {
    /// Back-to-back utterances are unusable; urgency 3 is exempt.
    public static let minSpeechGapMs: Int64 = 1_200

    public enum Decision: Equatable, Sendable {
        /// Nothing to do: empty, blocked by something louder, or throttled.
        case idle
        case present(Announcement)
        /// Cut the announcement with this id, then present the new one.
        case preempt(cancelling: UInt64, with: Announcement)

        /// A16: the same decision as a list of intents. The manager uses the
        /// typed case for preemption (it also has to await the cancelled
        /// task, which an intent cannot express) and this vocabulary is what
        /// step 10 and the logs read.
        public var effects: [Effect] {
            switch self {
            case .idle: return []
            case let .present(announcement): return [.present(announcement)]
            case let .preempt(_, announcement): return [.cancelCurrent, .present(announcement)]
            }
        }
    }

    public static func decide(_ state: FeedbackState, now: Int64) -> (FeedbackState, Decision) {
        var working = state
        working.queue = AnnouncementQueue.sorted(working.queue)

        while let head = working.queue.first {
            // Re-gate: an event can go stale while it waits behind an
            // utterance. It is dropped, not said late -- and this drop is
            // *not* a transport health signal.
            if StalenessGate.isExpired(expiresAtMs: head.expiresAtMs, now: now) {
                working.queue.removeFirst()
                working.counters.queueExpired += 1
                continue
            }
            if let inFlight = working.inFlight, head.urgency <= inFlight.urgency {
                return (working, .idle)
            }
            if head.urgency <= UrgencyLevel.actSoon,
               let lastSpeech = working.lastSpeechAtMs,
               now - lastSpeech < minSpeechGapMs {
                return (working, .idle)
            }
            working.queue.removeFirst()
            return start(head, in: working, now: now)
        }
        return (working, .idle)
    }

    /// Presentation start: the only place `TrackRecord`s and
    /// `recentAnnouncements` are written (A9, A11).
    private static func start(
        _ item: QueuedItem,
        in state: FeedbackState,
        now: Int64
    ) -> (FeedbackState, Decision) {
        var working = state
        let announcement = Announcement(
            id: working.nextAnnouncementId,
            level: item.level,
            event: item.event,
            decidedAtMs: now
        )
        working.nextAnnouncementId += 1
        working.tracks = TrackRecords.recordingPresentation(
            working.tracks,
            key: item.key,
            urgency: item.urgency,
            driver: item.driver,
            now: now
        )
        working.recentAnnouncements = RecentAnnouncements.appending(
            AnnouncementRecord(
                key: item.key,
                cls: item.event.cls,
                bearingDeg: item.event.bearingDeg,
                urgency: item.urgency,
                presentedAtMs: now
            ),
            to: working.recentAnnouncements,
            now: now
        )
        working.counters.announced += 1
        if announcement.speech != nil { working.lastSpeechAtMs = now }

        let previous = working.inFlight
        working.inFlight = FeedbackState.InFlight(
            id: announcement.id,
            urgency: item.urgency,
            key: item.key
        )
        guard let previous else { return (working, .present(announcement)) }
        // A preempted announcement is dropped: never resumed, never
        // re-queued, and its TrackRecord stands.
        working.counters.preempted += 1
        return (working, .preempt(cancelling: previous.id, with: announcement))
    }
}
