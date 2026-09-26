import Foundation

/// When to tell people the laptop link is down, and why.
///
/// Live 2026-09-26: Wi-Fi was off, the app sat on "CONNECTING" in small
/// letters, and every question went nowhere with nothing to say why. Now a
/// drop that lasts past `graceMs` is spoken (the wearer cannot see the
/// screen) and shown as a banner, repeated every `repeatMs` while it lasts,
/// with the likeliest cause; getting the link back is spoken once.
/// Pure and clock-injected.
public struct LinkAlert: Sendable {
    public enum Reason: Equatable, Sendable {
        /// The phone has no network at all.
        case noNetwork
        /// The phone has a network, but not Wi-Fi (the laptop is on Wi-Fi).
        case noWiFi
        /// The network is fine; the laptop is not answering.
        case laptopUnreachable
    }

    public enum Output: Equatable, Sendable {
        case none
        case lost(Reason)
        case restored
    }

    /// Reconnects faster than this are not worth interrupting anyone for
    /// (the client retries with backoff; a laptop restart takes ~3 s).
    public static let graceMs: Int64 = 5_000
    public static let repeatMs: Int64 = 60_000

    private var downSince: Int64?
    private var announced: Reason?
    private var lastAnnouncedMs: Int64 = 0

    public init() {}

    /// The problem to show on screen right now, or nil.
    public var showing: Reason? { announced }

    public mutating func update(connected: Bool, reason: Reason, nowMs: Int64) -> Output {
        if connected {
            downSince = nil
            guard announced != nil else { return .none }
            announced = nil
            return .restored
        }
        guard let since = downSince else {
            downSince = nowMs
            return .none
        }
        guard nowMs - since >= Self.graceMs else { return .none }
        if announced == nil || announced != reason || nowMs - lastAnnouncedMs >= Self.repeatMs {
            announced = reason
            lastAnnouncedMs = nowMs
            return .lost(reason)
        }
        return .none
    }

    public static func spoken(_ reason: Reason) -> String {
        switch reason {
        case .noNetwork: "I can't reach the laptop. This phone has no network."
        case .noWiFi: "I can't reach the laptop. Wi-Fi is off."
        case .laptopUnreachable: "I can't reach the laptop. Check that it's running and on the same Wi-Fi."
        }
    }

    public static let restoredPhrase = "Laptop connected."

    /// Banner text: a headline and one line of what to check.
    public static func banner(_ reason: Reason, address: String) -> (title: String, detail: String) {
        switch reason {
        case .noNetwork: ("No network", "Turn on Wi-Fi, then join the laptop's network")
        case .noWiFi: ("Wi-Fi is off", "Turn on Wi-Fi and join the laptop's network")
        case .laptopUnreachable: ("Laptop not answering", "Is ./startcane running at \(address), on this Wi-Fi?")
        }
    }

    /// The likeliest cause from what the phone can see of its network. On
    /// the phone's own hotspot (172.20.10.x) the phone is not a Wi-Fi client,
    /// so "Wi-Fi is off" would be wrong there.
    public static func reason(networkUp: Bool, onWiFi: Bool, laptopHost: String) -> Reason {
        if laptopHost.hasPrefix("172.20.10.") { return networkUp || onWiFi ? .laptopUnreachable : .noNetwork }
        if !networkUp { return onWiFi ? .laptopUnreachable : .noNetwork }
        return onWiFi ? .laptopUnreachable : .noWiFi
    }
}
