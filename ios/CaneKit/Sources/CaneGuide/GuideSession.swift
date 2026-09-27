import Foundation

/// What the camera saw in one frame, already reduced to fractions and metres.
public struct GuideFrame: Sendable, Equatable {
    public var tMs: Int64
    /// Where the tracker has the thing; nil when it lost it this frame.
    public var object: NormBox?
    /// A trusted TrueDepth reading inside the thing's box.
    public var objectDepthM: Double?
    /// The fingertip nearest the thing, if a hand is in view.
    public var hand: NormPoint?
    public var handDepthM: Double?
    public var hfovDeg: Double
    /// Photo width / height.
    public var aspect: Double

    public init(tMs: Int64, object: NormBox?, objectDepthM: Double? = nil, hand: NormPoint? = nil,
                handDepthM: Double? = nil, hfovDeg: Double, aspect: Double) {
        self.tMs = tMs
        self.object = object
        self.objectDepthM = objectDepthM
        self.hand = hand
        self.handDepthM = handDepthM
        self.hfovDeg = hfovDeg
        self.aspect = aspect
    }
}

public enum GuideEnd: String, Sendable, Equatable {
    case reached, lost, timedOut, stopped
}

public enum GuideOutput: Sendable, Equatable {
    case watch(GuideMessage)
    case say(String)
    case end(GuideEnd)
}

/// The hand guide's rules, free of Vision and the watch so they run in
/// `swift test`: one camera frame in, what to tap / say / stop out.
///
/// - the watch (and the phone's own ticks and beeps) get the gap ~10 times a
///   second and speed up as it shrinks;
/// - the voice adds the *direction* ("Left.") every 1.2 s, because taps
///   alone say "warmer" but never "which way";
/// - a fingertip within `reachM` of the thing's surface for `reachHoldMs`
///   ends it with "You've got it.";
/// - the thing out of sight for `lostAfterMs`, or `timeoutMs` overall, ends it.
public struct GuideSession: Sendable {
    public static let reachM = 0.04
    public static let reachHoldMs: Int64 = 300
    public static let lostAfterMs: Int64 = 2_500
    public static let timeoutMs: Int64 = 60_000
    public static let watchEveryMs: Int64 = 100
    /// Between direction words, the same word included: the hand is still
    /// moving and needs to hear it is still the right way (2026-09-27: five
    /// seconds of silence felt like no feedback at all).
    public static let hintEveryMs: Int64 = 1_200
    public static let noHandPromptEveryMs: Int64 = 6_000
    /// No hints this close: the taps are enough and words would lag.
    public static let quietWithinM = 0.08
    /// Assumed when TrueDepth has not measured the thing (then nothing is
    /// shown as a number).
    public static let assumedDepthM = 0.5

    public static let reachedPhrase = "You've got it."
    public static let noHandPrompt = "Reach out in front of you."
    public static let timedOutPhrase = "Let's stop there. Ask me again when you're ready."

    public static func startPhrase(watch: Bool) -> String {
        watch ? "Reach out slowly. The beeps and your watch speed up as you get closer."
            : "Reach out slowly. The beeps speed up as you get closer."
    }

    public static func lostPhrase(_ target: String) -> String {
        "I lost sight of the \(target). Ask me again to start over."
    }

    public let target: String
    public let voiceOnly: Bool
    public private(set) var state: GuideState = .findHand
    public private(set) var finished = false
    /// The newest gap (fingertip to surface, m); nil with no hand in view.
    public private(set) var gapM: Double?
    /// Whether `gapM` is measured (TrueDepth) or estimated from the photo.
    public private(set) var metric = false
    /// Hand to thing, camera metres: its sign says which ear to beep in.
    public private(set) var towards: Point3?
    /// The last direction word said.
    public private(set) var hintText: String?

    private let startMs: Int64
    private var lastObjectMs: Int64
    private var objectDepthM: Double?
    private var nearSinceMs: Int64?
    private var lastWatchMs: Int64?
    private var lastSentState: GuideState?
    private var lastHintMs: Int64
    private var lastNoHandPromptMs: Int64

    /// `startMs` is when the start phrase was said: the first "reach out"
    /// reminder waits a full interval after it.
    public init(target: String, startMs: Int64, voiceOnly: Bool = false) {
        self.target = target
        self.voiceOnly = voiceOnly
        self.startMs = startMs
        lastObjectMs = startMs
        lastHintMs = startMs
        lastNoHandPromptMs = startMs
    }

    public mutating func update(_ frame: GuideFrame) -> [GuideOutput] {
        guard !finished else { return [] }
        let now = frame.tMs
        if now - startMs >= Self.timeoutMs {
            return finish(.timedOut, now: now, say: Self.timedOutPhrase)
        }
        guard let object = frame.object else {
            if now - lastObjectMs >= Self.lostAfterMs {
                return finish(.lost, now: now, say: Self.lostPhrase(target))
            }
            return []
        }
        lastObjectMs = now
        if let depth = frame.objectDepthM { objectDepthM = depth }

        guard let hand = frame.hand else {
            nearSinceMs = nil
            gapM = nil
            towards = nil
            state = .findHand
            var out = watch(now: now, gap: nil, metric: false)
            if now - lastNoHandPromptMs >= Self.noHandPromptEveryMs {
                lastNoHandPromptMs = now
                lastHintMs = now
                out.append(.say(Self.noHandPrompt))
            }
            return out
        }
        state = .guiding
        lastNoHandPromptMs = now

        let objectDepth = objectDepthM ?? Self.assumedDepthM
        // A fingertip reading that disagrees wildly with the thing is the
        // table or the wall behind a thin finger: fall back to "same depth".
        let handDepth = frame.handDepthM.flatMap { abs($0 - objectDepth) < 0.6 ? $0 : nil }
        let metric = objectDepthM != nil && handDepth != nil
        let (gap, towards) = GuideGeometry.gap(
            hand: hand, handDepthM: handDepth ?? objectDepth, object: object, objectDepthM: objectDepth,
            hfovDeg: frame.hfovDeg, aspect: frame.aspect
        )
        self.gapM = gap
        self.metric = metric
        self.towards = towards

        if gap <= Self.reachM {
            if let since = nearSinceMs {
                if now - since >= Self.reachHoldMs {
                    return finish(.reached, now: now, say: Self.reachedPhrase, gap: gap)
                }
            } else {
                nearSinceMs = now
            }
        } else {
            nearSinceMs = nil
        }

        var out: [GuideOutput] = []
        if let spoken = hint(towards: towards, gap: gap, now: now) {
            out.append(.say(spoken))
        }
        out += watch(now: now, gap: gap, metric: metric)
        return out
    }

    /// The wearer said "stop" (or a new question replaced this one).
    public mutating func stop(nowMs: Int64) -> [GuideOutput] {
        guard !finished else { return [] }
        return finish(.stopped, now: nowMs, say: nil)
    }

    // MARK: - Internals

    /// One word, never a sentence: the ticks and beeps carry closeness, and
    /// every extra syllable is latency.
    private mutating func hint(towards: Point3, gap: Double, now: Int64) -> String? {
        guard gap > Self.quietWithinM, let direction = GuideGeometry.direction(towards) else { return nil }
        guard now - lastHintMs >= Self.hintEveryMs else { return nil }
        lastHintMs = now
        hintText = direction.spoken
        return direction.spoken
    }

    private mutating func watch(now: Int64, gap: Double?, metric: Bool) -> [GuideOutput] {
        guard lastWatchMs.map({ now - $0 >= Self.watchEveryMs }) ?? true || lastSentState != state else { return [] }
        lastWatchMs = now
        lastSentState = state
        return [.watch(GuideMessage(state: state, gapM: gap, metric: metric, target: target, hint: hintText, sentMs: now))]
    }

    private mutating func finish(_ end: GuideEnd, now: Int64, say: String?, gap: Double? = nil) -> [GuideOutput] {
        finished = true
        state = switch end {
        case .reached: .reached
        case .lost: .lost
        case .timedOut, .stopped: .stopped
        }
        lastSentState = state
        var out: [GuideOutput] = [.watch(GuideMessage(state: state, gapM: gap, metric: gap != nil, target: target,
                                                      hint: nil, sentMs: now))]
        if let say { out.append(.say(say)) }
        out.append(.end(end))
        return out
    }
}
