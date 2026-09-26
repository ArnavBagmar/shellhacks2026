import Foundation

@testable import CaneFeedback

/// The step-24 half of the A13 seam, stubbed. Synchronous and non-blocking
/// for the same reason `RecordingAmbientSink` is: the cue is emitted from
/// inside the tick, and a tick that waits on a renderer is a tick that stops
/// integrating.
final class RecordingSteerSink: SteerCueSink, @unchecked Sendable {
    private let lock = NSLock()
    private var storedPulses: [SteerCue] = []
    private var storedSilences = 0

    var pulses: [SteerCue] { lock.withLock { storedPulses } }
    var silences: Int { lock.withLock { storedSilences } }
    /// Everything in order, for asserting that nothing was emitted *after* a
    /// silence -- "never hold a stale steer" is a statement about ordering.
    private var storedLog: [String] = []
    var log: [String] { lock.withLock { storedLog } }

    func pulse(_ cue: SteerCue) {
        lock.withLock {
            storedPulses.append(cue)
            storedLog.append("pulse")
        }
    }

    func silence() {
        lock.withLock {
            storedSilences += 1
            storedLog.append("silence")
        }
    }
}
