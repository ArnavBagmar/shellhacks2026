import Foundation

extension TurnCuePolicy {
    /// What the policy remembers between updates: which maneuvers have
    /// been announced, when the last nag was, and how long the walker
    /// has been off route. A value -- `evaluate` returns a new one and
    /// never edits the caller's.
    public struct State: Sendable, Equatable {
        var preparedBoundary: Int?
        var announcedBoundary: Int?
        var lastCorrectHeadingMs: Int64?
        var lastContinueStraightMs: Int64?
        var straightReminderArmed = false
        var offRouteSinceMs: Int64?
        var offRouteAnnounced = false
        var arrivedAnnounced = false
        var gpsLostAnnounced = false

        public init() {}
    }
}
