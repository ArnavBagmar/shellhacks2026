import CaneContracts
import Foundation

@testable import CaneFeedback

/// The step-10 renderer, stubbed. Records what it was asked to say, how long
/// it took, and what was cut short.
///
/// `@unchecked Sendable` is deliberate and the reason is structural:
/// `AnnouncementSink.cancelCurrent()` is synchronous by contract (A13) --
/// the pump must be able to cut speech without awaiting -- and an `actor`
/// cannot offer a synchronous method to a non-isolated caller. Every stored
/// property below is private and touched only while `lock` is held, which is
/// exactly the invariant the compiler would otherwise check.
final class RecordingSink: AnnouncementSink, @unchecked Sendable {
    private let lock = NSLock()
    private var storedPresented: [Announcement] = []
    private var storedCompleted: [UInt64] = []
    private var storedCancelled: [UInt64] = []
    private var current: UInt64?

    /// How long a presentation takes; 0 returns immediately.
    private let presentDurationMs: Int64
    /// Simulates a wedged AVSpeech delegate (H3's failure mode): the sleep
    /// is not cancellable, so only `PRESENT_TIMEOUT_MS` can free the pump.
    private let ignoresCancellation: Bool

    init(presentDurationMs: Int64 = 0, ignoresCancellation: Bool = false) {
        self.presentDurationMs = presentDurationMs
        self.ignoresCancellation = ignoresCancellation
    }

    var presented: [Announcement] { lock.withLock { storedPresented } }

    /// Waits until the renderer has actually *started* this many
    /// presentations. Without it a test can preempt an announcement that has
    /// not begun, which the pump correctly refuses to speak at all -- a real
    /// behaviour, but not the one a preemption test is about.
    func waitUntilPresenting(atLeast count: Int, timeoutMs: Int = 2_000) async {
        let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1_000.0)
        while presented.count < count && Date() < deadline {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
    }
    var completed: [UInt64] { lock.withLock { storedCompleted } }
    var cancelled: [UInt64] { lock.withLock { storedCancelled } }

    func present(_ announcement: Announcement) async {
        lock.withLock {
            storedPresented.append(announcement)
            current = announcement.id
        }
        // Preempted before this task got to run: the renderer contract says
        // return at once, and the cancellation still has to be recorded even
        // though `cancelCurrent()` arrived while nothing was in flight.
        guard !Task.isCancelled else {
            markCancelled(announcement.id)
            return
        }
        guard presentDurationMs > 0 else {
            finish(announcement.id)
            return
        }
        let seconds = Double(presentDurationMs) / 1_000.0
        if ignoresCancellation {
            await Self.uninterruptibleSleep(seconds: seconds)
        } else {
            do {
                try await Task.sleep(nanoseconds: UInt64(presentDurationMs) * 1_000_000)
            } catch {
                markCancelled(announcement.id)
                return
            }
        }
        finish(announcement.id)
    }

    func cancelCurrent() {
        lock.withLock {
            guard let inFlight = current else { return }
            if !storedCancelled.contains(inFlight) { storedCancelled.append(inFlight) }
            current = nil
        }
    }

    private func markCancelled(_ id: UInt64) {
        lock.withLock {
            if !storedCancelled.contains(id) { storedCancelled.append(id) }
            if current == id { current = nil }
        }
    }

    private func finish(_ id: UInt64) {
        lock.withLock {
            storedCompleted.append(id)
            if current == id { current = nil }
        }
    }

    /// Sleeps without a cancellation path, off the cooperative pool.
    private static func uninterruptibleSleep(seconds: Double) async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.global().asyncAfter(deadline: .now() + seconds) {
                continuation.resume()
            }
        }
    }
}

/// The hazard-map hook (A13). Synchronous and non-blocking, same reasoning
/// as above for the unchecked conformance.
final class RecordingAmbientSink: AmbientSink, @unchecked Sendable {
    private let lock = NSLock()
    private var storedEvents: [AwarenessEvent] = []

    var events: [AwarenessEvent] { lock.withLock { storedEvents } }

    func record(_ event: AwarenessEvent, urgency: Int) {
        lock.withLock { storedEvents.append(event) }
    }
}

/// A clock the test drives by hand: no `Date()` anywhere in the gauntlet.
final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var storedNow: Int64

    init(now: Int64) { storedNow = now }

    var now: Int64 { lock.withLock { storedNow } }

    func set(_ value: Int64) { lock.withLock { storedNow = value } }

    var reader: @Sendable () -> Int64 {
        { [weak self] in self?.now ?? 0 }
    }
}
