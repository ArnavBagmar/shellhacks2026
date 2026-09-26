import Foundation

/// Runs one `AnnouncementSink.present` under a deadline (A11).
///
/// A hung renderer is a real failure mode (H3), and the pump must not wait
/// for it: on timeout the sink is cancelled and the task is *abandoned*,
/// not awaited. The outer task's own cancellation -- which is how
/// preemption unblocks -- resolves the wait immediately for the same
/// reason.
enum Presentation {
    /// Returns `true` when the deadline fired.
    static func run(
        sink: AnnouncementSink,
        announcement: Announcement,
        timeoutMs: Int64
    ) async -> Bool {
        let outcome = Outcome()
        let work = Task {
            // Preemption can land between creating this task and its first
            // instruction; starting a presentation that is already cancelled
            // would speak the announcement the pump just dropped.
            guard !Task.isCancelled else {
                await outcome.settle(timedOut: false)
                return
            }
            await sink.present(announcement)
            await outcome.settle(timedOut: false)
        }
        let deadline = Task {
            do {
                try await Task.sleep(nanoseconds: UInt64(max(0, timeoutMs)) * 1_000_000)
            } catch {
                return  // cancelled: the presentation finished first
            }
            await outcome.settle(timedOut: true)
        }
        let timedOut = await withTaskCancellationHandler {
            await outcome.wait()
        } onCancel: {
            // Preemption: cut the renderer, cancel the work (an unstructured
            // task does not inherit cancellation), and stop waiting.
            // `cancelCurrent` is documented idempotent, so the double call is
            // safe.
            sink.cancelCurrent()
            work.cancel()
            Task { await outcome.settle(timedOut: false) }
        }
        deadline.cancel()
        if timedOut {
            sink.cancelCurrent()
            work.cancel()
        }
        return timedOut
    }

    /// A one-shot race result. An `actor` rather than a lock because both
    /// writers are already in async context.
    private actor Outcome {
        private var settled: Bool?
        private var waiter: CheckedContinuation<Bool, Never>?

        func wait() async -> Bool {
            if let settled { return settled }
            return await withCheckedContinuation { continuation in
                // Still inside the actor's isolation: `settled` cannot
                // change between the check above and this store.
                if let settled {
                    continuation.resume(returning: settled)
                } else {
                    waiter = continuation
                }
            }
        }

        func settle(timedOut: Bool) {
            guard settled == nil else { return }
            settled = timedOut
            waiter?.resume(returning: timedOut)
            waiter = nil
        }
    }
}
