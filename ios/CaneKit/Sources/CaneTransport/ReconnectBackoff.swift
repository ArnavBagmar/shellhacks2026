/// Capped exponential backoff for `EventLinkClient`'s reconnect loop:
/// 0.5 s, 1 s, 2 s, 4 s, 8 s, 8 s, ... (D5).
struct ReconnectBackoff {
    static let initialSeconds: Double = 0.5
    static let capSeconds: Double = 8.0

    private var currentSeconds = ReconnectBackoff.initialSeconds

    /// The delay to sleep before the next reconnect attempt; doubles
    /// (capped at `capSeconds`) for the attempt after that.
    mutating func next() -> Duration {
        let seconds = currentSeconds
        currentSeconds = Swift.min(currentSeconds * 2, ReconnectBackoff.capSeconds)
        return .seconds(seconds)
    }

    /// Called once a connection proves live (at least one frame
    /// received), so a later drop starts backing off from the beginning
    /// again instead of continuing to escalate from where a previous,
    /// unrelated failure streak left off.
    mutating func reset() {
        currentSeconds = ReconnectBackoff.initialSeconds
    }
}
