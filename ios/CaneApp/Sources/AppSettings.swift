import Foundation
import Observation

/// What a sighted helper sets before the phone goes on the chest. Persisted:
/// speech rate and haptic gain must never be re-calibrated every launch
/// (step-10 design §3 Q7).
@MainActor
@Observable
final class AppSettings {
    private enum Key {
        static let host = "laptopHost"
        static let port = "laptopPort"
        static let rate = "speechRate"
        static let gain = "hapticGain"
    }

    private let defaults: UserDefaults

    /// On the iPhone's own hotspot the phone is 172.20.10.1 and the laptop
    /// gets 172.20.10.2 or later; the laptop prints its address at start.
    var laptopHost: String { didSet { defaults.set(laptopHost, forKey: Key.host) } }
    var laptopPort: Int { didSet { defaults.set(laptopPort, forKey: Key.port) } }
    var speechRate: Double { didSet { defaults.set(speechRate, forKey: Key.rate) } }
    var hapticGain: Double { didSet { defaults.set(hapticGain, forKey: Key.gain) } }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        laptopHost = defaults.string(forKey: Key.host) ?? "172.20.10.2"
        laptopPort = defaults.object(forKey: Key.port) as? Int ?? 8765
        speechRate = defaults.object(forKey: Key.rate) as? Double ?? 0.55
        hapticGain = defaults.object(forKey: Key.gain) as? Double ?? 1.0
    }

    var laptopURL: URL? {
        let host = laptopHost.trimmingCharacters(in: .whitespaces)
        guard !host.isEmpty else { return nil }
        return URL(string: "ws://\(host):\(laptopPort)")
    }

    /// A value snapshot the non-UI runtime can read from any thread.
    var snapshot: Snapshot {
        Snapshot(speechRate: Float(speechRate), hapticGain: Float(hapticGain))
    }

    struct Snapshot: Sendable {
        let speechRate: Float
        let hapticGain: Float
    }
}

/// Thread-safe holder so live setting changes reach the renderers.
final class SettingsBox: @unchecked Sendable {
    private let lock = NSLock()
    private var value: AppSettings.Snapshot

    init(_ value: AppSettings.Snapshot) { self.value = value }

    var current: AppSettings.Snapshot { lock.withLock { value } }
    func update(_ next: AppSettings.Snapshot) { lock.withLock { value = next } }
}
