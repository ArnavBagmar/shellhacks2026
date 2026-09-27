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
        static let naturalVoice = "naturalVoice"
        static let forceIndoors = "forceIndoors"
    }

    private let defaults: UserDefaults

    /// On the iPhone's own hotspot the phone is 172.20.10.1 and the laptop
    /// gets 172.20.10.2 or later; the laptop prints its address at start.
    var laptopHost: String { didSet { defaults.set(laptopHost, forKey: Key.host) } }
    var laptopPort: Int { didSet { defaults.set(laptopPort, forKey: Key.port) } }
    var speechRate: Double { didSet { defaults.set(speechRate, forKey: Key.rate) } }
    var hapticGain: Double { didSet { defaults.set(hapticGain, forKey: Key.gain) } }
    /// Answers and prompts in the ElevenLabs voice (through the laptop).
    /// Hazards always use the phone's own voice.
    var naturalVoice: Bool { didSet { defaults.set(naturalVoice, forKey: Key.naturalVoice) } }
    /// Ignore GPS and always treat the walk as indoors.
    ///
    /// The escape hatch for a building that fools the estimator -- a glass
    /// lobby or an atrium can give a sky-quality fix indoors, which would send
    /// "take me to room 100" to a Maps engine that can only find the building
    /// the wearer is already standing in.
    var forceIndoors: Bool { didSet { defaults.set(forceIndoors, forKey: Key.forceIndoors) } }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        laptopHost = defaults.string(forKey: Key.host) ?? "172.20.10.2"
        laptopPort = defaults.object(forKey: Key.port) as? Int ?? 8765
        speechRate = defaults.object(forKey: Key.rate) as? Double ?? 0.55
        hapticGain = defaults.object(forKey: Key.gain) as? Double ?? 1.0
        naturalVoice = defaults.object(forKey: Key.naturalVoice) as? Bool ?? true
        forceIndoors = defaults.object(forKey: Key.forceIndoors) as? Bool ?? false
    }

    var laptopURL: URL? {
        Self.url(host: laptopHost, port: laptopPort)
    }

    /// `ws://host:port`. An IPv6 address (T-Mobile phone hotspots give no
    /// usable IPv4, 2026-09-26) must be bracketed in a URL; typed or pasted
    /// with or without the brackets, it works.
    static func url(host raw: String, port: Int) -> URL? {
        var host = raw.trimmingCharacters(in: .whitespaces)
        guard !host.isEmpty else { return nil }
        if host.contains(":"), !host.hasPrefix("[") {
            host = "[\(host)]"
        }
        return URL(string: "ws://\(host):\(port)")
    }

    /// A value snapshot the non-UI runtime can read from any thread.
    var snapshot: Snapshot {
        Snapshot(speechRate: Float(speechRate), hapticGain: Float(hapticGain), naturalVoice: naturalVoice,
                 forceIndoors: forceIndoors)
    }

    struct Snapshot: Sendable {
        let speechRate: Float
        let hapticGain: Float
        let naturalVoice: Bool
        let forceIndoors: Bool
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
