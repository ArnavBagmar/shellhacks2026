import CaneTransport
import Foundation
import os

/// Sends one `speak` frame. `EventLinkClient` is the real one.
public protocol VoiceLink: Sendable {
    func send(_ request: SpeakRequest) async throws
}

extension EventLinkClient: VoiceLink {}

/// The phone's half of the natural voice: text -> `speak` frame -> the
/// matching `voice` clip, cached by text so "Let me check." is fetched once
/// per walk. Returns nil fast when it cannot help (no link, the laptop has
/// no voice), and `NaturalSpeechRenderer` then uses the phone's own voice.
public actor LaptopVoice {
    /// A reply later than this is not coming (the renderer gave up sooner).
    public static let replyTimeoutMs = 4_000
    /// After an `unavailable` reply, stop asking for a while: the laptop has
    /// no key, or ElevenLabs is failing, and every ask would only add a
    /// round trip before the fallback voice.
    public static let unavailableBackoffMs: Int64 = 30_000
    public static let cacheEntries = 40

    private let link: VoiceLink
    private let clock: @Sendable () -> Int64
    private let makeId: @Sendable () -> String
    private var waiting: [String: (text: String, continuation: CheckedContinuation<Data?, Never>)] = [:]
    private var cache: [String: Data] = [:]
    private var cacheOrder: [String] = []
    private var backoffUntilMs: Int64 = 0
    private let logger = Logger(subsystem: "cane", category: "laptop-voice")

    public init(
        link: VoiceLink,
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        makeId: @escaping @Sendable () -> String = { "s-" + UUID().uuidString.prefix(8).lowercased() }
    ) {
        self.link = link
        self.clock = clock
        self.makeId = makeId
    }

    /// The clip for `text`, or nil.
    public func audio(for text: String) async -> Data? {
        let text = String(text.prefix(SpeakRequest.maxTextChars))
        if let hit = cache[text] {
            touch(text)
            return hit
        }
        guard clock() >= backoffUntilMs, let request = try? SpeakRequest(speakId: makeId(), text: text) else {
            return nil
        }
        let id = request.speakId
        return await withCheckedContinuation { (continuation: CheckedContinuation<Data?, Never>) in
            waiting[id] = (text, continuation)
            let link = link
            Task {
                do {
                    try await link.send(request)
                } catch {
                    self.resolve(id, with: nil)
                }
            }
            Task {
                try? await Task.sleep(for: .milliseconds(Self.replyTimeoutMs))
                self.resolve(id, with: nil)
            }
        }
    }

    /// Feed every `voice` frame from the link here.
    public func receive(_ clip: VoiceClip) {
        guard let entry = waiting[clip.speakId] else { return }
        if clip.status == .ok, let audio = clip.audio {
            store(audio, for: entry.text)
            resolve(clip.speakId, with: audio)
        } else {
            logger.info("laptop has no voice right now; using the phone's for \(Self.unavailableBackoffMs / 1000) s")
            backoffUntilMs = clock() + Self.unavailableBackoffMs
            resolve(clip.speakId, with: nil)
        }
    }

    private func resolve(_ id: String, with audio: Data?) {
        waiting.removeValue(forKey: id)?.continuation.resume(returning: audio)
    }

    private func store(_ audio: Data, for text: String) {
        cache[text] = audio
        touch(text)
        while cacheOrder.count > Self.cacheEntries {
            cache.removeValue(forKey: cacheOrder.removeFirst())
        }
    }

    private func touch(_ text: String) {
        cacheOrder.removeAll { $0 == text }
        cacheOrder.append(text)
    }
}
