#if os(iOS)
import AVFoundation
import Foundation
import os
import Speech

/// The open microphone: the iPhone's own mic -> iOS 26 `SpeechAnalyzer` +
/// `SpeechTranscriber`, fully on-device and long-form (no one-minute task
/// limit). Every result, volatile and final, goes to `onTranscript`; the
/// `WakeWordGate` decides what is addressed to the phone.
///
/// Muted while the phone itself is speaking (step-10 design H-11b: without
/// echo cancellation the mic would hear "cyclist" and act on it). Uses the
/// session `AudioSessionConfig` set up -- never reconfigures the category,
/// never `.voiceChat` (H-11a).
@available(iOS 26, *)
public final class SpeechListener: @unchecked Sendable {
    public typealias TranscriptHandler = @Sendable (_ text: String, _ isFinal: Bool) -> Void
    /// Microphone loudness in 0...1, ~20 times a second (drives the orb).
    public typealias LevelHandler = @Sendable (_ level: Double) -> Void
    static let levelIntervalS = 0.05

    /// How long the mic stays muted after the phone stops speaking: the tail
    /// of our own voice is still in the room.
    public static let unmuteTailMs = 300

    private let locale: Locale
    private let engine = AVAudioEngine()
    private let lock = NSLock()
    private var muted = false
    private var unmuteTask: Task<Void, Never>?
    private var analyzer: SpeechAnalyzer?
    private var inputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "cane", category: "voice")

    public init(locale: Locale = Locale(identifier: "en-US")) {
        self.locale = locale
    }

    /// Microphone permission (speech recognition permission too, for older
    /// paths that still ask). Returns whether the mic may be used.
    public static func requestPermissions() async -> Bool {
        let mic = await AVAudioApplication.requestRecordPermission()
        _ = await withCheckedContinuation { (c: CheckedContinuation<SFSpeechRecognizerAuthorizationStatus, Never>) in
            SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) }
        }
        return mic
    }

    public func start(onTranscript: @escaping TranscriptHandler, onLevel: LevelHandler? = nil) async throws {
        stop()
        guard SpeechTranscriber.isAvailable else { throw ListenerError.transcriberUnavailable }
        let supported = await SpeechTranscriber.supportedLocale(equivalentTo: locale) ?? locale
        let transcriber = SpeechTranscriber(
            locale: supported, transcriptionOptions: [],
            reportingOptions: [.volatileResults, .fastResults], attributeOptions: []
        )
        if let install = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            logger.info("downloading the on-device speech model")
            try await install.downloadAndInstall()
        }
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let context = AnalysisContext()
        context.contextualStrings[.general] = ["Hey Cane", "Cane", "bathroom", "restroom"]
        try? await analyzer.setContext(context)

        guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw ListenerError.noAudioFormat
        }
        let (inputs, continuation) = AsyncStream<AnalyzerInput>.makeStream(bufferingPolicy: .bufferingNewest(64))
        try await analyzer.start(inputSequence: inputs)

        let input = engine.inputNode
        let micFormat = input.outputFormat(forBus: 0)
        guard let converter = AVAudioConverter(from: micFormat, to: analyzerFormat) else {
            throw ListenerError.noAudioFormat
        }
        input.removeTap(onBus: 0)
        var lastLevelAt = 0.0
        input.installTap(onBus: 0, bufferSize: 2_048, format: micFormat) { [weak self] buffer, _ in
            guard let self, !self.isMuted else { return }
            if let onLevel {
                let now = CFAbsoluteTimeGetCurrent()
                if now - lastLevelAt >= Self.levelIntervalS {
                    lastLevelAt = now
                    onLevel(Self.level(of: buffer))
                }
            }
            if let converted = Self.convert(buffer, with: converter, to: analyzerFormat) {
                continuation.yield(AnalyzerInput(buffer: converted))
            }
        }
        engine.prepare()
        try engine.start()

        let results = Task { [logger] in
            do {
                for try await result in transcriber.results {
                    onTranscript(String(result.text.characters), result.isFinal)
                }
            } catch {
                logger.error("transcriber stopped: \(String(describing: error), privacy: .public)")
            }
        }
        lock.withLock {
            self.analyzer = analyzer
            self.inputContinuation = continuation
            self.resultsTask = results
        }
        logger.info("listening (\(supported.identifier, privacy: .public))")
    }

    public func stop() {
        let (analyzer, continuation, results) = lock.withLock { () -> (SpeechAnalyzer?, AsyncStream<AnalyzerInput>.Continuation?, Task<Void, Never>?) in
            defer {
                self.analyzer = nil
                self.inputContinuation = nil
                self.resultsTask = nil
            }
            return (self.analyzer, self.inputContinuation, self.resultsTask)
        }
        engine.inputNode.removeTap(onBus: 0)
        if engine.isRunning { engine.stop() }
        continuation?.finish()
        results?.cancel()
        if let analyzer {
            Task { await analyzer.cancelAndFinishNow() }
        }
    }

    /// Wire to `OutputArbiter.observeSpeaking`. Unmutes after a short tail.
    public func setPhoneSpeaking(_ speaking: Bool) {
        lock.withLock {
            unmuteTask?.cancel()
            if speaking {
                muted = true
                return
            }
            unmuteTask = Task { [weak self] in
                try? await Task.sleep(for: .milliseconds(Self.unmuteTailMs))
                guard !Task.isCancelled else { return }
                self?.lock.withLock { self?.muted = false }
            }
        }
    }

    private var isMuted: Bool { lock.withLock { muted } }

    /// RMS in dBFS mapped from -55...-10 dB onto 0...1.
    static func level(of buffer: AVAudioPCMBuffer) -> Double {
        guard let samples = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return 0 }
        let count = Int(buffer.frameLength)
        var sum: Float = 0
        for i in 0..<count { sum += samples[i] * samples[i] }
        let rms = (sum / Float(count)).squareRoot()
        let db = 20 * log10(max(Double(rms), 1e-7))
        return min(max((db + 55) / 45, 0), 1)
    }

    /// Mic format (e.g. 48 kHz float) -> the analyzer's (e.g. 16 kHz int16).
    private static func convert(
        _ buffer: AVAudioPCMBuffer, with converter: AVAudioConverter, to format: AVAudioFormat
    ) -> AVAudioPCMBuffer? {
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 16
        guard let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }
        var consumed = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            if consumed {
                inputStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            inputStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, error == nil, output.frameLength > 0 else { return nil }
        return output
    }

    public enum ListenerError: Error {
        case transcriberUnavailable
        case noAudioFormat
    }
}
#endif
