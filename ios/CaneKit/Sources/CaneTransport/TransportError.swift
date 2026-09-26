import CaneContracts

/// Errors the transport layer throws. Mirrors the "rejected, not crashed"
/// pattern `CaneContracts.AwarenessEventError` establishes: every failure
/// mode below is a value, never a trap.
public enum TransportError: Error, Equatable, Sendable {
    /// The frame was not valid JSON, or not the shape expected for the
    /// call site -- e.g. `PhoneClockResponder.pong(forPing:)` given
    /// anything but a well-formed `ping`.
    case malformed
    /// The frame decoded as an `event` envelope, but its nested
    /// `AwarenessEvent` payload failed contract validation.
    case invalidEvent(AwarenessEventError)
}
