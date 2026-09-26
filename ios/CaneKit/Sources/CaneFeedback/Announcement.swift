import CaneContracts

/// The three levels that are ever announced. Urgency 0 is not one of them
/// (it is logged and mapped, never spoken), so an ambient `Announcement` is
/// unrepresentable.
public enum AnnouncementLevel: Int, Equatable, Sendable, CaseIterable {
    case aware = 1
    case actSoon = 2
    case immediate = 3
}

public enum HapticKind: Equatable, Sendable {
    /// Urgency 3: a burst, no direction to think about.
    case burst
    /// Urgency 1 and 2: directional, driven by `bearingDeg`.
    case directional
}

public enum SpeechVerbosity: Equatable, Sendable {
    /// One word. Step 10 chooses which -- it depends on the bearing sector.
    case oneWord
    /// Two or three words.
    case brief
}

/// Range as *data*, with the rounding policy already applied: step 10
/// renders "about two metres", step 9 never builds the string.
public enum RangeHint: Equatable, Sendable {
    case aboutMetres(Int)
    case band(RangeBand)
    case unknown
}

/// A12 -- what the feedback manager hands to the renderer. Structured, so
/// step 10 owns every word and this step owns every decision.
public struct Announcement: Equatable, Sendable {
    /// Monotonic; correlates a `cancelCurrent()` with the `present` it cut.
    public let id: UInt64
    public let urgency: Int
    public let cls: ObjectClass
    public let bearingDeg: Double
    public let range: RangeHint
    public let closingMps: Double?
    public let haptic: HapticKind
    /// `nil` means silent. An urgency-1 announcement with speech cannot be
    /// constructed: the table below is the only thing that sets this.
    public let speech: SpeechVerbosity?
    public let source: Source
    public let trackId: String
    public let tCaptureMs: Int64
    /// Phone clock at dequeue -- §4.9 end-to-end latency, for free.
    public let decidedAtMs: Int64
    /// `tCaptureMs` + clamped TTL: after this the announcement is a lie.
    public let expiresAtMs: Int64

    public var key: FeedbackKey {
        FeedbackKey(source: source, trackId: trackId)
    }

    /// The single initializer. `haptic` and `speech` come from the private
    /// table, never from the caller.
    public init(id: UInt64, level: AnnouncementLevel, event: AwarenessEvent, decidedAtMs: Int64) {
        let output = Self.output(for: level)
        self.id = id
        self.urgency = level.rawValue
        self.cls = event.cls
        self.bearingDeg = event.bearingDeg
        self.range = Self.rangeHint(for: event)
        self.closingMps = event.closingMps
        self.haptic = output.haptic
        self.speech = output.speech
        self.source = event.source
        self.trackId = event.trackId
        self.tCaptureMs = Int64(event.tCaptureMs)
        self.decidedAtMs = decidedAtMs
        self.expiresAtMs = TTLTable.expiresAtMs(event)
    }

    /// §4.7's output column, and the only place it is written.
    private static func output(for level: AnnouncementLevel) -> (haptic: HapticKind, speech: SpeechVerbosity?) {
        switch level {
        case .immediate: return (.burst, .oneWord)
        case .actSoon: return (.directional, .brief)
        case .aware: return (.directional, nil)
        }
    }

    private static func rangeHint(for event: AwarenessEvent) -> RangeHint {
        if let metres = event.rangeM {
            return .aboutMetres(Int(metres.rounded()))
        }
        if let band = event.rangeBand {
            return .band(band)
        }
        return .unknown
    }
}
