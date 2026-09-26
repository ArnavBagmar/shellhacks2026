#if os(iOS)
import AVFoundation
import CoreHaptics
import Foundation
import os

/// `HapticScript` -> `CHHapticPattern`, one player at a time.
///
/// Engine rules (step-10 design Amendment 1): `playsHapticsOnly` so it does
/// not contend with the speech session, auto-shutdown off while walking (it
/// costs tens of ms on the first play after silence -- every hazard), and a
/// restart on every reset/stop reason including a phone call.
public final class CoreHapticsRenderer: HapticRenderer, @unchecked Sendable {
    private let lock = NSLock()
    private var engine: CHHapticEngine?
    private var player: CHHapticPatternPlayer?
    private var finish: CheckedContinuation<Void, Never>?
    private let logger = Logger(subsystem: "cane", category: "haptics")
    public let isAvailable: Bool

    public init() {
        isAvailable = CHHapticEngine.capabilitiesForHardware().supportsHaptics
        guard isAvailable else { return }
        startEngine()
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: nil
        ) { [weak self] _ in self?.startEngine() }
    }

    private func startEngine() {
        do {
            let engine = try CHHapticEngine()
            engine.playsHapticsOnly = true
            engine.isAutoShutdownEnabled = false
            engine.resetHandler = { [weak self] in self?.restart(reason: "reset") }
            engine.stoppedHandler = { [weak self] reason in self?.restart(reason: "stopped \(reason.rawValue)") }
            try engine.start()
            lock.withLock { self.engine = engine }
        } catch {
            logger.error("haptic engine failed to start: \(String(describing: error), privacy: .public)")
        }
    }

    private func restart(reason: String) {
        logger.warning("haptic engine \(reason, privacy: .public); restarting")
        let engine = lock.withLock { self.engine }
        do {
            try engine?.start()
        } catch {
            startEngine()
        }
    }

    public func play(_ script: HapticScript) async {
        stop()
        guard !Task.isCancelled, let engine = lock.withLock({ self.engine }) else { return }
        do {
            let pattern = try CHHapticPattern(events: script.elements.map(Self.event), parameters: [])
            let player = try engine.makePlayer(with: pattern)
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                lock.withLock {
                    self.player = player
                    self.finish = continuation
                }
                do {
                    try player.start(atTime: CHHapticTimeImmediate)
                } catch {
                    logger.error("haptic play failed: \(String(describing: error), privacy: .public)")
                    resumeFinish()
                    return
                }
                let totalMs = script.totalMs
                Task { [weak self] in
                    try? await Task.sleep(for: .milliseconds(totalMs))
                    self?.resumeFinish(ifPlayer: player)
                }
            }
        } catch {
            logger.error("bad haptic pattern: \(String(describing: error), privacy: .public)")
        }
    }

    public func stop() {
        let player = lock.withLock { () -> CHHapticPatternPlayer? in
            defer { self.player = nil }
            return self.player
        }
        try? player?.stop(atTime: CHHapticTimeImmediate)
        resumeFinish()
    }

    private func resumeFinish(ifPlayer expected: CHHapticPatternPlayer? = nil) {
        let continuation = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            if let expected, self.player !== expected as AnyObject? { return nil }
            defer { self.finish = nil }
            return self.finish
        }
        continuation?.resume()
    }

    private static func event(_ element: HapticElement) -> CHHapticEvent {
        let seconds = TimeInterval(element.onsetMs) / 1000
        var parameters = [
            CHHapticEventParameter(parameterID: .hapticIntensity, value: element.intensity),
            CHHapticEventParameter(parameterID: .hapticSharpness, value: element.sharpness),
        ]
        switch element.kind {
        case .tap:
            return CHHapticEvent(eventType: .hapticTransient, parameters: parameters, relativeTime: seconds)
        case .buzz(let durationMs):
            parameters.append(CHHapticEventParameter(parameterID: .attackTime, value: element.attack))
            parameters.append(CHHapticEventParameter(parameterID: .releaseTime, value: element.release))
            return CHHapticEvent(
                eventType: .hapticContinuous, parameters: parameters,
                relativeTime: seconds, duration: TimeInterval(durationMs) / 1000
            )
        }
    }
}
#endif
