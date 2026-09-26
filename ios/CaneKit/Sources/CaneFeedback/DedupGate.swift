import CaneContracts

/// Gate 4 (§4.7.3, A3-A5). Two producers see the same object; exactly one
/// of them should be heard. Bearing + time proximity, never data
/// association.
///
/// Note the standing fact from the design review: at the X4's 2-3 fps this
/// 300 ms window cannot catch every cross-source pair on its own. It is the
/// fast path; the spatial refractory (A6) is the guarantee.
public enum DedupGate {
    /// §4.7: "another event within 10 degrees and 300 ms".
    public static let dedupBearingDeg = 10.0
    public static let dedupWindowMs: Int64 = 300
    /// A5: the X4's crops overlap here, so one object can be detected twice
    /// with two track ids.
    public static let seamHalfWidthDeg = 12.0
    public static let seamBearingsDeg: [Double] = [-180, -150, -90, -30, 30, 90, 150, 180]

    public enum DropReason: Equatable, Sendable {
        /// Lost the cross-source total order (A4).
        case crossSource
        /// Lost the same-source crop-seam rule (A5).
        case seam
    }

    public enum Outcome: Equatable, Sendable {
        /// The event proceeds; these queued keys lost to it and must be
        /// evicted if they have not been presented yet (A4).
        case keep(evicting: [FeedbackKey])
        case drop(reason: DropReason)
    }

    public static func decide(_ event: AwarenessEvent, recent: [AcceptedRecord]) -> Outcome {
        var evicting: [FeedbackKey] = []
        for candidate in recent where isCandidate(candidate, for: event) {
            switch verdict(for: event, against: candidate) {
            case .incomingLoses(let reason):
                return .drop(reason: reason)
            case .incomingWins:
                evicting.append(candidate.key)
            case .unrelated:
                continue
            }
        }
        return .keep(evicting: evicting)
    }

    // MARK: - Candidate test (A3, A4)

    static func isCandidate(_ candidate: AcceptedRecord, for event: AwarenessEvent) -> Bool {
        guard candidate.key != FeedbackKey(event) else { return false }
        guard AngularMath.isWithin(dedupBearingDeg, candidate.bearingDeg, event.bearingDeg) else { return false }
        guard abs(candidate.tCaptureMs - Int64(event.tCaptureMs)) <= dedupWindowMs else { return false }
        return classesMayBeTheSameObject(candidate.cls, event.cls)
    }

    /// Classes must agree, with one directional exception: an
    /// `unknown_obstacle` and a classified detection at the same bearing and
    /// time are the same object seen by a weaker classifier.
    private static func classesMayBeTheSameObject(_ a: ObjectClass, _ b: ObjectClass) -> Bool {
        a == b || a == .unknownObstacle || b == .unknownObstacle
    }

    // MARK: - Total order (A4) and the seam rule (A5)

    private enum Verdict {
        case incomingWins
        case incomingLoses(DropReason)
        case unrelated
    }

    private static func verdict(for event: AwarenessEvent, against candidate: AcceptedRecord) -> Verdict {
        if candidate.key.source == event.source {
            return seamVerdict(for: event, against: candidate)
        }
        // `unknown_obstacle` may lose, never win -- whatever the sources are.
        if event.cls == .unknownObstacle, candidate.cls != .unknownObstacle {
            return .incomingLoses(.crossSource)
        }
        if candidate.cls == .unknownObstacle, event.cls != .unknownObstacle {
            return .incomingWins
        }
        let incoming = rank(source: event.source, hasTrustedRange: RangeTrust.isTrusted(event))
        let existing = rank(source: candidate.key.source, hasTrustedRange: candidate.hasTrustedRange)
        // Different sources always rank differently, so there is no tie.
        return incoming > existing ? .incomingWins : .incomingLoses(.crossSource)
    }

    /// The A4 total order, independent of arrival order:
    /// phone-with-metric-range > `x4_local` > `x4_cloud` > phone-without.
    static func rank(source: Source, hasTrustedRange: Bool) -> Int {
        switch source {
        case .phone: return hasTrustedRange ? 3 : 0
        case .x4Local: return 2
        case .x4Cloud: return 1
        }
    }

    private static func seamVerdict(for event: AwarenessEvent, against candidate: AcceptedRecord) -> Verdict {
        guard bothNearTheSameSeam(candidate.bearingDeg, event.bearingDeg) else { return .unrelated }
        let incomingCapture = Int64(event.tCaptureMs)
        if incomingCapture != candidate.tCaptureMs {
            return incomingCapture < candidate.tCaptureMs ? .incomingWins : .incomingLoses(.seam)
        }
        return event.trackId < candidate.key.trackId ? .incomingWins : .incomingLoses(.seam)
    }

    static func bothNearTheSameSeam(_ a: Double, _ b: Double) -> Bool {
        seamBearingsDeg.contains { seam in
            AngularMath.isWithin(seamHalfWidthDeg, a, seam) && AngularMath.isWithin(seamHalfWidthDeg, b, seam)
        }
    }
}
