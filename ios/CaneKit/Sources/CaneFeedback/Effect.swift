import CaneContracts

/// A16 -- stages return intents; only `FeedbackManager` performs them. No
/// stage calls a sink, which is what keeps every stage a pure function and
/// every test synchronous.
public enum Effect: Equatable, Sendable {
    case cancelCurrent
    case present(Announcement)
    case ambient(AwarenessEvent, Int)
}
