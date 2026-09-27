#if os(iOS)
import AVFoundation
import CaneGuide
import CoreHaptics
import Foundation
import os

/// The phone's own parking sensor for "guide my hand": a buzz on the chest
/// and a short beep, faster and higher as the fingertip closes on the thing
/// (`GuidePulse` sets the rhythm the watch uses too). The beep is panned
/// toward the way the hand should move, which earbuds make audible.
///
/// Runs beside the output arbiter rather than through it: a rhythm that
/// waits its turn behind speech is not a rhythm. It still yields --
/// nothing at all during a hazard, and no beep (the buzz goes on) while a
/// word is being spoken, so "Left." is never masked.
public final class GuideTicker: @unchecked Sendable {
    /// A reading older than this is a stalled camera: go quiet.
    static let staleMs: Int64 = 500
    static let beepS = 0.035
    static let beepAmplitude: Float = 0.35
    /// Beep pitch from far to touching.
    static let lowHz = 600.0
    static let highHz = 1_500.0
    static let pitchSteps = 8

    private let hazardActive: @Sendable () -> Bool
    private let speaking: @Sendable () -> Bool
    private let clock: @Sendable () -> Int64
    private let lock = NSLock()
    private var reading: (gapM: Double, pan: Double, atMs: Int64)?
    private var loop: Task<Void, Never>?
    private var haptics: CHHapticEngine?
    private let audio = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private var beeps: [AVAudioPCMBuffer] = []
    private let logger = Logger(subsystem: "cane", category: "guide-ticker")

    public init(
        hazardActive: @escaping @Sendable () -> Bool,
        speaking: @escaping @Sendable () -> Bool,
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.hazardActive = hazardActive
        self.speaking = speaking
        self.clock = clock
        if let format = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 1) {
            audio.attach(node)
            audio.connect(node, to: audio.mainMixerNode, format: format)
            beeps = (0..<Self.pitchSteps).compactMap { step in
                let t = Double(step) / Double(Self.pitchSteps - 1)
                return Self.beep(format: format, hz: Self.lowHz + t * (Self.highHz - Self.lowHz))
            }
        }
        if CHHapticEngine.capabilitiesForHardware().supportsHaptics {
            haptics = try? CHHapticEngine()
            haptics?.resetHandler = { [weak self] in try? self?.haptics?.start() }
        }
    }

    /// The newest gap (nil: no hand, stay quiet) and which way to lean the
    /// beep (-1 left ... 1 right).
    public func update(gapM: Double?, pan: Double) {
        let now = clock()
        lock.withLock { reading = gapM.map { ($0, max(-1, min(1, pan)), now) } }
    }

    public func start() {
        lock.withLock {
            guard loop == nil else { return }
            try? haptics?.start()
            loop = Task.detached(priority: .userInitiated) { [weak self] in await self?.run() }
        }
    }

    /// Stops the rhythm; `reached` adds a quick triple buzz.
    public func stop(reached: Bool = false) {
        let task = lock.withLock { () -> Task<Void, Never>? in
            defer { loop = nil; reading = nil }
            return loop
        }
        task?.cancel()
        if reached, !hazardActive() {
            buzz(intensity: 1, sharpness: 1, count: 3)
        }
    }

    // MARK: - Loop

    private func run() async {
        while !Task.isCancelled {
            let now = clock()
            guard let r = lock.withLock({ reading }), now - r.atMs < Self.staleMs, !hazardActive() else {
                try? await Task.sleep(for: .milliseconds(50))
                continue
            }
            // 0 far ... 1 touching.
            let close = 1 - min(max((r.gapM - GuidePulse.touchM) / (GuidePulse.farM - GuidePulse.touchM), 0), 1)
            buzz(intensity: 0.55 + 0.45 * close, sharpness: 0.4 + 0.5 * close, count: 1)
            if !speaking() { beep(close: close, pan: r.pan) }
            try? await Task.sleep(for: .seconds(GuidePulse.interval(gapM: r.gapM)))
        }
    }

    private func buzz(intensity: Double, sharpness: Double, count: Int) {
        guard let haptics else { return }
        let events = (0..<count).map { i in
            CHHapticEvent(eventType: .hapticTransient, parameters: [
                CHHapticEventParameter(parameterID: .hapticIntensity, value: Float(intensity)),
                CHHapticEventParameter(parameterID: .hapticSharpness, value: Float(sharpness)),
            ], relativeTime: Double(i) * 0.09)
        }
        do {
            try haptics.makePlayer(with: CHHapticPattern(events: events, parameters: [])).start(atTime: CHHapticTimeImmediate)
        } catch {
            try? haptics.start()
        }
    }

    private func beep(close: Double, pan: Double) {
        guard !beeps.isEmpty else { return }
        let buffer = beeps[min(beeps.count - 1, Int((close * Double(beeps.count - 1)).rounded()))]
        do {
            if !audio.isRunning { try audio.start() }
            node.pan = Float(pan * 0.8)
            node.scheduleBuffer(buffer, at: nil, options: .interrupts)
            if !node.isPlaying { node.play() }
        } catch {
            logger.warning("guide beep skipped: \(String(describing: error), privacy: .public)")
        }
    }

    static func beep(format: AVAudioFormat, hz: Double) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(format.sampleRate * beepS)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let samples = buffer.floatChannelData?[0]
        else { return nil }
        buffer.frameLength = frames
        for i in 0..<Int(frames) {
            let envelope = 0.5 * (1 - cos(2 * .pi * Double(i) / Double(frames - 1)))
            samples[i] = beepAmplitude * Float(envelope * sin(2 * .pi * hz * Double(i) / format.sampleRate))
        }
        return buffer
    }
}
#endif
