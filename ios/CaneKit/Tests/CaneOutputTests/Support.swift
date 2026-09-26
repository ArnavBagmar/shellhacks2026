import CaneContracts
import CaneFeedback
import Foundation

@testable import CaneOutput

enum Make {
    static let t0: Int64 = 1_790_000_000_000

    static func announcement(
        level: AnnouncementLevel = .actSoon,
        cls: ObjectClass = .person,
        bearingDeg: Double = 0,
        rangeM: Double? = nil,
        band: RangeBand? = nil,
        closingMps: Double? = nil,
        id: UInt64 = 1
    ) throws -> Announcement {
        let event = try AwarenessEvent(
            source: rangeM == nil ? .x4Cloud : .phone,
            trackId: "t\(id)",
            tCaptureMs: t0,
            ttlMs: 15_000,
            cls: cls,
            confidence: 0.9,
            bearingDeg: bearingDeg,
            rangeM: rangeM,
            rangeBand: band,
            closingMps: closingMps
        )
        return Announcement(id: id, level: level, event: event, decidedAtMs: t0 + 50)
    }
}

/// Finishes after `durationMs`, or at once on `stop()`. Resume-once.
final class FakeClockedRenderer: @unchecked Sendable {
    private let lock = NSLock()
    private var current: (id: Int, continuation: CheckedContinuation<Void, Never>)?
    private var nextId = 0

    func run(durationMs: Int) async {
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            let (id, previous) = lock.withLock { () -> (Int, CheckedContinuation<Void, Never>?) in
                nextId += 1
                let previous = current?.continuation
                current = (nextId, c)
                return (nextId, previous)
            }
            previous?.resume()
            Task {
                try? await Task.sleep(for: .milliseconds(durationMs))
                self.finish(id)
            }
        }
    }

    func stop() {
        let c = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            defer { current = nil }
            return current?.continuation
        }
        c?.resume()
    }

    private func finish(_ id: Int) {
        let c = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            guard current?.id == id else { return nil }
            defer { current = nil }
            return current?.continuation
        }
        c?.resume()
    }
}

final class FakeHaptics: HapticRenderer, @unchecked Sendable {
    let isAvailable: Bool
    private let lock = NSLock()
    private let clocked = FakeClockedRenderer()
    private var _played: [HapticScript] = []
    private let durationMs: Int?

    init(available: Bool = true, durationMs: Int? = nil) {
        self.isAvailable = available
        self.durationMs = durationMs
    }

    var played: [HapticScript] { lock.withLock { _played } }

    func play(_ script: HapticScript) async {
        lock.withLock { _played.append(script) }
        await clocked.run(durationMs: durationMs ?? script.totalMs)
    }

    func stop() { clocked.stop() }
}

final class FakeSpeech: SpeechRenderer, @unchecked Sendable {
    private let lock = NSLock()
    private let clocked = FakeClockedRenderer()
    private var _spoken: [SpokenPhrase] = []
    private var _startTimes: [ContinuousClock.Instant] = []
    let durationMs: Int

    init(durationMs: Int = 200) { self.durationMs = durationMs }

    var spoken: [SpokenPhrase] { lock.withLock { _spoken } }
    var startTimes: [ContinuousClock.Instant] { lock.withLock { _startTimes } }

    func speak(_ phrase: SpokenPhrase) async {
        lock.withLock {
            _spoken.append(phrase)
            _startTimes.append(.now)
        }
        await clocked.run(durationMs: durationMs)
    }

    func stop() { clocked.stop() }
}

/// A settable millisecond clock for the arbiter's speech-gap rule.
final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var nowMs: Int64 = Make.t0
    var now: Int64 { lock.withLock { nowMs } }
    func advance(_ ms: Int64) { lock.withLock { nowMs += ms } }
}

func elapsedMs(_ body: () async -> Void) async -> Double {
    let start = ContinuousClock.now
    await body()
    let d = ContinuousClock.now - start
    return Double(d.components.seconds) * 1000 + Double(d.components.attoseconds) / 1e15
}
