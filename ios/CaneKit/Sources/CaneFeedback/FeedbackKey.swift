import CaneContracts

/// The identity the gauntlet suppresses on: the pair `(source, track_id)`,
/// never `track_id` alone. Two detectors number their tracks independently,
/// so `x4_local#7` and `phone#7` are different objects; §4.7's "keyed on
/// track_id" is refined to this pair by the task-2 architect.
public struct FeedbackKey: Hashable, Sendable {
    public let source: Source
    public let trackId: String

    public init(source: Source, trackId: String) {
        self.source = source
        self.trackId = trackId
    }

    public init(_ event: AwarenessEvent) {
        self.init(source: event.source, trackId: event.trackId)
    }
}
