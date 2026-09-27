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
import Vision
import os

/// "Guide my hand to the Rubik's cube", after Gemini found it in the phone
/// photo: follows the thing and the wearer's fingertips live in the front
/// camera, measures both with TrueDepth, and turns the gap into watch taps
/// and direction words (`GuideSession` holds the rules).
///
/// No detection model: Apple Vision's object tracker (seeded with Gemini's
/// box) and hand-pose request, both built into iOS.
///
/// Threading: frames arrive on the camera queue and are handed, at most one
/// at a time, to `work`; everything below `// MARK: - Work queue` runs
/// there only.
public final class HandGuide: FrameConsumer, @unchecked Sendable {
    /// Half of the camera's 30 fps: plenty for a reaching hand.
    static let everyNthFrame = 2
    /// Below this the tracker has lost the thing for this frame.
    static let minTrackConfidence: Float = 0.3
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

    private let camera: FrontCamera
    private let speaker: AssistSpeaker
    private let watch: GuideWatch
    private let clock: @Sendable () -> Int64
    private let onEnd: @Sendable (String, GuideEnd) -> Void
    private let work = DispatchQueue(label: "cane.guide", qos: .userInitiated)
    private let lock = NSLock()
    private var active = false
    private var busy = false
    private var frameCount = 0
    private let logger = Logger(subsystem: "cane", category: "guide")

    // Work queue only.
    private var session: GuideSession?
    private var box = NormBox(xMin: 0, yMin: 0, xMax: 1, yMax: 1)
    private var sequence = VNSequenceRequestHandler()
    private var track: VNTrackObjectRequest?
    private var held = false
    private var objectDepthM: Double?
    private var depth: (map: DepthMap, atMs: Int64)?

    public init(
        camera: FrontCamera,
        speaker: AssistSpeaker,
        watch: GuideWatch,
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        onEnd: @escaping @Sendable (String, GuideEnd) -> Void = { _, _ in }
    ) {
        self.camera = camera
        self.speaker = speaker
        self.watch = watch
        self.clock = clock
        self.onEnd = onEnd
        camera.addConsumer(self)
    }

    public var isActive: Bool { lock.withLock { active } }

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
            seed(box)
        }
        lock.withLock { active = true }
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
        let request = VNTrackObjectRequest(detectedObjectObservation: VNDetectedObjectObservation(boundingBox: Self.visionRect(box)))
        request.trackingLevel = .accurate
        track = request
    }

    private func process(_ buffer: CVPixelBuffer, hfovDeg: Double) {
        guard var current = session else { return }
        let now = clock()
        let aspect = Double(CVPixelBufferGetWidth(buffer)) / Double(max(CVPixelBufferGetHeight(buffer), 1))

        let hand = fingertip(in: buffer, nearestTo: box.centre)
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
        deliver(outputs, target: current.target)
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
        return Self.normBox(result.boundingBox)
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
                Task {
                    if final {
                        // "You've got it." must be heard: it waits and cuts a hint.
                        speaker.cancel(upTo: .prompt)
                        _ = await speaker.say(SpokenPhrase(text: text), on: .answer, haptic: nil, pan: 0, maxWaitMs: 3_000)
                    } else {
                        // A late hint is a wrong hint: dropped if anything is talking.
                        _ = await speaker.say(SpokenPhrase(text: text), on: .prompt, haptic: nil, pan: 0, maxWaitMs: 0)
                    }
                }
            case .end(let end):
                lock.withLock { active = false }
                logger.info("guide ended: \(end.rawValue, privacy: .public)")
                onEnd(target, end)
            }
        }
    }

    static func visionRect(_ box: NormBox) -> CGRect {
        CGRect(x: box.xMin, y: 1 - box.yMax, width: box.width, height: box.height)
    }

    static func normBox(_ rect: CGRect) -> NormBox {
        NormBox(xMin: Double(rect.minX), yMin: 1 - Double(rect.maxY), xMax: Double(rect.maxX), yMax: 1 - Double(rect.minY))
    }
}
#endif
