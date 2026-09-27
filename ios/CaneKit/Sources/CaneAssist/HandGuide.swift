import CaneGuide
import Foundation

/// Where the guide's taps go. `WatchLink` (the app) is the real one.
public protocol GuideWatch: Sendable {
    /// The watch app is open and can take messages right now.
    var isReachable: Bool { get }
    func send(_ message: GuideMessage)
}

#if os(iOS)
import AVFoundation
import CaneCamera
import CaneOutput
import CaneTransport
import CoreImage
import Vision
import os

/// Where the live tracking goes for the laptop page. `EventLinkClient` is
/// the real one.
public protocol GuideLink: Sendable {
    func send(_ update: GuideUpdate) async throws
}

extension EventLinkClient: GuideLink {}

/// "Guide my hand to the Rubik's cube", after Gemini found it in the phone
/// photo: follows the thing and the wearer's fingertips live in the front
/// camera, measures both with TrueDepth, and turns the gap into ticks and
/// beeps (`GuideTicker`), watch taps and direction words (`GuideSession`
/// holds the rules). The laptop page gets the same picture live.
///
/// No detection model: Apple Vision's object tracker (seeded with Gemini's
/// box) and hand-pose request, both built into iOS.
///
/// Threading: frames arrive on the camera queue and are handed, at most one
/// at a time, to `work`; everything below `// MARK: - Work queue` runs
/// there only.
public final class HandGuide: FrameConsumer, @unchecked Sendable {
    /// The tracker runs on every frame: the less the picture moves between
    /// two frames, the less it loses the thing (2026-09-27: at 15 fps a
    /// chest camera's sway lost it within seconds).
    static let everyNthFrame = 1
    /// Hand pose is the heavy request; a fingertip at 15 Hz is plenty.
    static let handEveryNthFrame = 2
    /// A fingertip older than this is gone.
    static let handHoldMs: Int64 = 200
    /// Below this the tracker has lost the thing for this frame. Low on
    /// purpose: a short dip is common and `GuideSession` waits 1.5 s anyway.
    static let minTrackConfidence: Float = 0.15
    /// The tracker follows the box grown by this much per side (and never
    /// smaller than `minTrackSide`): a small cube alone has too little
    /// texture to follow; with a margin of table around it, it holds.
    static let trackPad = 0.15
    static let minTrackSide = 0.1
    static let minJointConfidence: Float = 0.3
    /// A fingertip inside the thing's box grown by this much covers it, and
    /// the tracker would follow the hand: the box is held still instead.
    static let coverMargin = 0.5
    /// Side of the patch measured around a fingertip, photo fraction.
    static let fingertipPatch = 0.04
    /// Lower than a spoken distance needs: a small patch on a finger.
    static let minDepthCoverage = 0.2
    /// A whole-map copy is ~1 MB; a reaching hand does not need it at 15 Hz.
    static let depthEveryMs: Int64 = 150
    /// The laptop page: positions this often, a camera frame half as often.
    static let laptopEveryMs: Int64 = 125
    static let laptopFrameEveryMs: Int64 = 250
    static let laptopFramePx = 480.0

    private let camera: FrontCamera
    private let speaker: AssistSpeaker
    private let watch: GuideWatch
    private let laptop: GuideLink?
    private let ticker: GuideTicker?
    private let clock: @Sendable () -> Int64
    private let onEnd: @Sendable (String, GuideEnd) -> Void
    private let work = DispatchQueue(label: "cane.guide", qos: .userInitiated)
    private let lock = NSLock()
    private var active = false
    private var busy = false
    private var frameCount = 0
    private var refinder: (@Sendable (String) async -> PhoneBox?)?
    private let logger = Logger(subsystem: "cane", category: "guide")

    // Work queue only.
    private var session: GuideSession?
    private var box = NormBox(xMin: 0, yMin: 0, xMax: 1, yMax: 1)
    private var sequence = VNSequenceRequestHandler()
    private var track: VNTrackObjectRequest?
    private var held = false
    private var workFrames = 0
    private var lastHand: (point: NormPoint, atMs: Int64)?
    private var objectDepthM: Double?
    private var depth: (map: DepthMap, atMs: Int64)?
    private var lastLaptopMs: Int64?
    private var lastLaptopFrameMs: Int64?
    private let ciContext = CIContext()

    public init(
        camera: FrontCamera,
        speaker: AssistSpeaker,
        watch: GuideWatch,
        laptop: GuideLink? = nil,
        ticker: GuideTicker? = nil,
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        onEnd: @escaping @Sendable (String, GuideEnd) -> Void = { _, _ in }
    ) {
        self.camera = camera
        self.speaker = speaker
        self.watch = watch
        self.laptop = laptop
        self.ticker = ticker
        self.clock = clock
        self.onEnd = onEnd
        camera.addConsumer(self)
    }

    public var isActive: Bool { lock.withLock { active } }

    /// How to ask Gemini again when the tracker loses the thing
    /// (`QueryCoordinator.refind`); set once, after both exist.
    public func setRefinder(_ refind: @escaping @Sendable (String) async -> PhoneBox?) {
        lock.withLock { refinder = refind }
    }

    /// Starts following at once (the photo is already a few seconds old) and
    /// returns what to say after the answer: "Reach out slowly ...".
    public func start(_ request: GuideRequest) -> String {
        let voiceOnly = !watch.isReachable
        let now = clock()
        let box = NormBox(xMin: request.box.xMin, yMin: request.box.yMin, xMax: request.box.xMax, yMax: request.box.yMax)
        work.sync {
            if var old = session { deliver(old.stop(nowMs: now), target: old.target); session = nil }
            session = GuideSession(target: request.target, startMs: now, voiceOnly: voiceOnly)
            objectDepthM = nil
            depth = nil
            lastLaptopMs = nil
            lastLaptopFrameMs = nil
            seed(box)
            sendLaptop(state: .findHand, target: request.target, now: now, force: true)
        }
        lock.withLock { active = true }
        ticker?.start()
        watch.send(GuideMessage(state: .findHand, target: request.target, sentMs: now))
        logger.info("guide started: \(request.target, privacy: .public), watch \(voiceOnly ? "not reachable" : "reachable")")
        return GuideSession.startPhrase(watch: !voiceOnly)
    }

    /// "Stop", or any new question: ends quietly.
    public func stop() {
        guard lock.withLock({ active }) else { return }
        lock.withLock { active = false }
        let now = clock()
        work.async { [self] in
            guard var current = session else { return }
            deliver(current.stop(nowMs: now), target: current.target)
            session = nil
        }
    }

    // MARK: - Camera queue

    public func frontCamera(didOutput sampleBuffer: CMSampleBuffer, hfovDeg: Double) {
        let take = lock.withLock { () -> Bool in
            guard active, !busy else { return false }
            frameCount += 1
            guard frameCount % Self.everyNthFrame == 0 else { return false }
            busy = true
            return true
        }
        guard take, let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            if take { lock.withLock { busy = false } }
            return
        }
        work.async { [self] in
            process(buffer, hfovDeg: hfovDeg)
            lock.withLock { busy = false }
        }
    }

    // MARK: - Work queue

    private func seed(_ box: NormBox) {
        self.box = box
        held = false
        sequence = VNSequenceRequestHandler()
        let request = VNTrackObjectRequest(detectedObjectObservation: VNDetectedObjectObservation(
            boundingBox: Self.visionRect(Self.padded(box))))
        request.trackingLevel = .accurate
        track = request
    }

    private func process(_ buffer: CVPixelBuffer, hfovDeg: Double) {
        guard var current = session else { return }
        let now = clock()
        let aspect = Double(CVPixelBufferGetWidth(buffer)) / Double(max(CVPixelBufferGetHeight(buffer), 1))

        workFrames += 1
        if workFrames % Self.handEveryNthFrame == 0 {
            lastHand = fingertip(in: buffer, nearestTo: box.centre).map { ($0, now) }
        }
        let hand = lastHand.flatMap { now - $0.atMs <= Self.handHoldMs ? $0.point : nil }
        let covering = hand.map { box.expanded(by: Self.coverMargin).contains($0) } ?? false
        var seen: NormBox?
        if covering {
            // The hand is over the thing: the thing has not moved, the hand has.
            held = true
            seen = box
        } else {
            if held { seed(box) }
            seen = follow(buffer)
            if let seen { box = seen }
        }

        if depth.map({ now - $0.atMs >= Self.depthEveryMs }) ?? true, let fresh = camera.liveDepthMap() {
            depth = (fresh, now)
        }
        let map = depth?.map
        if !covering, seen != nil, let map,
           let sample = map.distance(inBox: box.xMin, box.yMin, box.xMax, box.yMax),
           sample.isTrustworthy(minCoverage: Self.minDepthCoverage) {
            objectDepthM = sample.metres
        }
        var handDepthM: Double?
        if let hand, let map {
            let r = Self.fingertipPatch / 2
            if let sample = map.distance(inBox: max(0, hand.x - r), max(0, hand.y - r), min(1, hand.x + r), min(1, hand.y + r)),
               sample.isTrustworthy(minCoverage: Self.minDepthCoverage) {
                handDepthM = sample.metres
            }
        }

        let outputs = current.update(GuideFrame(
            tMs: now, object: seen, objectDepthM: objectDepthM, hand: hand, handDepthM: handDepthM,
            hfovDeg: hfovDeg, aspect: aspect
        ))
        session = current.finished ? nil : current
        if !current.finished {
            ticker?.update(gapM: current.gapM, pan: current.towards.map { $0.x / 0.15 } ?? 0)
            sendLaptop(state: current.state, target: current.target, now: now, object: seen, hand: hand,
                       gapM: current.gapM, metric: current.metric, hint: current.hintText,
                       buffer: buffer, hfovDeg: hfovDeg)
        }
        deliver(outputs, target: current.target)
    }

    /// Gemini's answer to a re-find: start tracking again from its box, or
    /// give up ("I lost sight of ...").
    private func refindResult(_ found: PhoneBox?) {
        guard var current = session, current.isRefinding else { return }
        let now = clock()
        if let found {
            seed(NormBox(xMin: found.xMin, yMin: found.yMin, xMax: found.xMax, yMax: found.yMax))
            objectDepthM = nil
            lastHand = nil
            current.reacquired(nowMs: now)
            session = current
            logger.info("guide: found \(current.target, privacy: .public) again")
            sendLaptop(state: current.state, target: current.target, now: now, force: true, object: box)
        } else {
            let outputs = current.refindFailed(nowMs: now)
            session = current.finished ? nil : current
            deliver(outputs, target: current.target)
        }
    }

    /// Best effort, never blocking the tracker: the page is for watching,
    /// nothing on the laptop acts on it.
    private func sendLaptop(
        state: GuideState, target: String, now: Int64, force: Bool = false, object: NormBox? = nil,
        hand: NormPoint? = nil, gapM: Double? = nil, metric: Bool = false, hint: String? = nil,
        buffer: CVPixelBuffer? = nil, hfovDeg: Double = 0
    ) {
        guard let laptop else { return }
        guard force || lastLaptopMs.map({ now - $0 >= Self.laptopEveryMs }) ?? true else { return }
        lastLaptopMs = now
        var frame: PhoneFrame?
        if let buffer, lastLaptopFrameMs.map({ now - $0 >= Self.laptopFrameEveryMs }) ?? true {
            frame = jpeg(buffer, hfovDeg: hfovDeg)
            if frame != nil { lastLaptopFrameMs = now }
        }
        let wireState = switch state {
        case .findHand: "find_hand"
        default: state.rawValue
        }
        guard let update = try? GuideUpdate(
            tPhoneMs: now, state: wireState, target: String(target.prefix(60)),
            box: object.map { .init(xMin: max(0, $0.xMin), yMin: max(0, $0.yMin), xMax: min(1, $0.xMax), yMax: min(1, $0.yMax)) },
            hand: hand.map { (min(max($0.x, 0), 1), min(max($0.y, 0), 1)) },
            gapM: gapM, metric: metric, hint: hint, frame: frame
        ) else { return }
        Task { try? await laptop.send(update) }
    }

    private func jpeg(_ buffer: CVPixelBuffer, hfovDeg: Double) -> PhoneFrame? {
        var image = CIImage(cvPixelBuffer: buffer)
        let scale = min(1, Self.laptopFramePx / Double(max(image.extent.width, image.extent.height)))
        if scale < 1 { image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale)) }
        let options = [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): 0.5]
        guard hfovDeg > 0, let space = CGColorSpace(name: CGColorSpace.sRGB),
              let data = ciContext.jpegRepresentation(of: image, colorSpace: space, options: options)
        else { return nil }
        return try? PhoneFrame(jpeg: data, hfovDeg: hfovDeg, width: Int(image.extent.width.rounded()),
                               height: Int(image.extent.height.rounded()))
    }

    /// The tracker's box for this frame, or nil when it is not confident.
    private func follow(_ buffer: CVPixelBuffer) -> NormBox? {
        guard let track else { return nil }
        do {
            try sequence.perform([track], on: buffer, orientation: .up)
        } catch {
            logger.warning("tracker: \(error.localizedDescription, privacy: .public)")
            return nil
        }
        guard let result = track.results?.first as? VNDetectedObjectObservation else { return nil }
        track.inputObservation = result
        guard result.confidence >= Self.minTrackConfidence else { return nil }
        return Self.unpadded(Self.normBox(result.boundingBox))
    }

    /// The fingertip (any finger, any hand) nearest the thing.
    private func fingertip(in buffer: CVPixelBuffer, nearestTo target: NormPoint) -> NormPoint? {
        let request = VNDetectHumanHandPoseRequest()
        request.maximumHandCount = 2
        do {
            try VNImageRequestHandler(cvPixelBuffer: buffer, orientation: .up).perform([request])
        } catch {
            return nil
        }
        let tips: [VNHumanHandPoseObservation.JointName] = [.indexTip, .middleTip, .thumbTip, .ringTip, .littleTip]
        var best: (point: NormPoint, d: Double)?
        for hand in request.results ?? [] {
            for joint in tips {
                guard let p = try? hand.recognizedPoint(joint), p.confidence >= Self.minJointConfidence else { continue }
                // Vision's origin is bottom-left.
                let point = NormPoint(x: Double(p.location.x), y: 1 - Double(p.location.y))
                let d = hypot(point.x - target.x, point.y - target.y)
                if best == nil || d < best!.d { best = (point, d) }
            }
        }
        return best?.point
    }

    private func deliver(_ outputs: [GuideOutput], target: String) {
        for output in outputs {
            switch output {
            case .watch(let message):
                watch.send(message)
            case .say(let text):
                let speaker = speaker
                let final = outputs.contains { if case .end = $0 { true } else { false } }
                // The phone's own voice (the navigation channel), never the
                // laptop's: a hint that waits a second for a network voice
                // points where the hand was, not where it is.
                Task {
                    if final {
                        // "You've got it." must be heard at once: cut whatever talks.
                        speaker.cancel(upTo: .answer)
                    }
                    // Dropped, never queued, if anything else is talking.
                    _ = await speaker.say(SpokenPhrase(text: text), on: .navigation, haptic: nil, pan: 0, maxWaitMs: 0)
                }
            case .refind:
                guard let refinder = lock.withLock({ refinder }) else {
                    work.async { [self] in refindResult(nil) }
                    continue
                }
                logger.info("guide: lost \(target, privacy: .public), asking Gemini again")
                Task { [self] in
                    let found = await refinder(target)
                    work.async { [self] in refindResult(found) }
                }
            case .end(let end):
                lock.withLock { active = false }
                ticker?.stop(reached: end == .reached)
                let finalState: GuideState = switch end {
                case .reached: .reached
                case .lost: .lost
                case .timedOut, .stopped: .stopped
                }
                sendLaptop(state: finalState, target: target, now: clock(), force: true, object: box)
                logger.info("guide ended: \(end.rawValue, privacy: .public)")
                onEnd(target, end)
            }
        }
    }

    /// The box the tracker follows: the thing plus a margin, kept inside the frame.
    static func padded(_ box: NormBox) -> NormBox {
        let w = max(box.width * (1 + 2 * trackPad), minTrackSide), h = max(box.height * (1 + 2 * trackPad), minTrackSide)
        let c = box.centre
        let x0 = min(max(c.x - w / 2, 0), 1 - min(w, 1)), y0 = min(max(c.y - h / 2, 0), 1 - min(h, 1))
        return NormBox(xMin: x0, yMin: y0, xMax: min(x0 + w, 1), yMax: min(y0 + h, 1))
    }

    /// Back from the tracker's box to the thing's own size, around its centre.
    static func unpadded(_ box: NormBox) -> NormBox {
        let w = box.width / (1 + 2 * trackPad), h = box.height / (1 + 2 * trackPad)
        let c = box.centre
        return NormBox(xMin: max(c.x - w / 2, 0), yMin: max(c.y - h / 2, 0), xMax: min(c.x + w / 2, 1), yMax: min(c.y + h / 2, 1))
    }

    static func visionRect(_ box: NormBox) -> CGRect {
        CGRect(x: box.xMin, y: 1 - box.yMax, width: box.width, height: box.height)
    }

    static func normBox(_ rect: CGRect) -> NormBox {
        NormBox(xMin: Double(rect.minX), yMin: 1 - Double(rect.maxY), xMax: Double(rect.maxX), yMax: 1 - Double(rect.minY))
    }
}
#endif
