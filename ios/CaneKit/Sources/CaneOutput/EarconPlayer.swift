#if os(iOS)
import AVFoundation
import Foundation
import os

/// The 40 ms panned tick (step-10 design Amendment 4): 1.2 kHz, raised-cosine
/// envelope, amplitude 0.5, rendered in code -- **mono**, because
/// `AVAudioMixing.pan` is a no-op on a multi-channel source. The first thing
/// to cut if time runs short; nothing depends on it.
public final class EarconPlayer: EarconRenderer, @unchecked Sendable {
    static let frequencyHz = 1_200.0
    static let durationS = 0.040
    static let amplitude: Float = 0.5

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let buffer: AVAudioPCMBuffer?
    private let lock = NSLock()
    private let logger = Logger(subsystem: "cane", category: "earcon")

    public init() {
        let format = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 1)
        buffer = format.flatMap(Self.tick)
        guard let format else { return }
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
    }

    public func play(pan: Double) {
        guard let buffer else { return }
        lock.withLock {
            do {
                if !engine.isRunning { try engine.start() }
                node.pan = Float(pan)
                node.scheduleBuffer(buffer, at: nil, options: .interrupts)
                if !node.isPlaying { node.play() }
            } catch {
                logger.warning("earcon skipped: \(String(describing: error), privacy: .public)")
            }
        }
    }

    static func tick(format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(format.sampleRate * durationS)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let samples = buffer.floatChannelData?[0]
        else { return nil }
        buffer.frameLength = frames
        for i in 0..<Int(frames) {
            let t = Double(i) / format.sampleRate
            let envelope = 0.5 * (1 - cos(2 * .pi * Double(i) / Double(frames - 1)))
            samples[i] = amplitude * Float(envelope * sin(2 * .pi * frequencyHz * t))
        }
        return buffer
    }
}
#endif
