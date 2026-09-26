#if os(iOS)
import AVFoundation
import Foundation
import os

/// The one audio-session configuration (step-10 design Amendment 5).
///
/// - Output: A2DP (AirPods at full quality) or the speaker; input: the
///   **iPhone's own microphone**. Never `.allowBluetooth` (HFP) and never
///   mode `.voiceChat`: both drop AirPods to call quality and re-route TTS.
/// - `.duckOthers`, not `.mixWithOthers`: a podcast must not mask a hazard.
///   The session stays active for the whole walk, so other audio is ducked
///   for the whole walk -- a disclosure, not a bug.
/// - If `.playAndRecord` cannot activate (usually: no microphone permission
///   yet) fall back to `.playback` -- speech is the safety channel and must
///   not be hostage to the microphone.
public enum AudioSessionConfig {
    public enum State: Equatable, Sendable {
        case playAndRecord
        /// Output only; voice input is off until `upgradeForRecording()`.
        case playbackOnly
        case failed
    }

    private static let logger = Logger(subsystem: "cane", category: "audio-session")

    @discardableResult
    public static func configure() -> State {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord, mode: .spokenAudio, policy: .default,
                options: [.duckOthers, .allowBluetoothA2DP, .allowAirPlay, .defaultToSpeaker]
            )
            try session.setActive(true)
            return .playAndRecord
        } catch {
            logger.warning("playAndRecord unavailable (\(String(describing: error), privacy: .public)); falling back to playback")
        }
        do {
            try session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers, .allowBluetoothA2DP, .allowAirPlay])
            try session.setActive(true)
            return .playbackOnly
        } catch {
            logger.error("audio session failed: \(String(describing: error), privacy: .public)")
            return .failed
        }
    }

    /// Call once microphone permission is granted. Same category string, so
    /// step 10's output routing is unchanged.
    @discardableResult
    public static func upgradeForRecording() -> State {
        configure()
    }

    /// Whether the current route is real stereo headphones (panning gate).
    public static func isStereoRoute() -> Bool {
        let outputs = AVAudioSession.sharedInstance().currentRoute.outputs.map {
            (portType: $0.portType.rawValue, channels: $0.channels?.count ?? 1)
        }
        return StereoPan.isStereoRoute(outputs: outputs)
    }

    /// Phone call / Siri / alarm. `began(true)` on start, `began(false)` on
    /// the end (after the session has been reactivated).
    public static func observeInterruptions(_ handler: @escaping @Sendable (_ began: Bool) -> Void) -> NSObjectProtocol {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: nil
        ) { note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: raw)
            else { return }
            switch type {
            case .began:
                handler(true)
            case .ended:
                _ = configure()
                handler(false)
            @unknown default:
                break
            }
        }
    }
}
#endif
