import Foundation

@testable import CaneNavigation

/// Scripted `LocationSource`. A `final class` holding only `let`
/// properties (an `AsyncStream` and its continuation are both
/// `Sendable` and thread-safe), so it is `Sendable` without any
/// unchecked escape hatch.
final class FakeLocationSource: LocationSource {
    private let stream: AsyncStream<LocationFix>
    private let continuation: AsyncStream<LocationFix>.Continuation

    init() {
        (stream, continuation) = AsyncStream.makeStream(bufferingPolicy: .unbounded)
    }

    func locations() -> AsyncStream<LocationFix> { stream }

    func stop() { continuation.finish() }

    func emit(_ fix: LocationFix) { continuation.yield(fix) }

    func emit(_ samples: [WalkSample]) {
        for sample in samples { continuation.yield(sample.fix) }
    }

    /// The source itself going quiet -- the stream stays open, the
    /// fixes simply stop, which is what a walker under a bridge sees.
    func finish() { continuation.finish() }
}

final class FakeHeadingSource: HeadingSource {
    private let stream: AsyncStream<HeadingSample>
    private let continuation: AsyncStream<HeadingSample>.Continuation

    init() {
        (stream, continuation) = AsyncStream.makeStream(bufferingPolicy: .unbounded)
    }

    func headings() -> AsyncStream<HeadingSample> { stream }

    func stop() { continuation.finish() }

    func emit(_ heading: HeadingSample) { continuation.yield(heading) }
}

/// Resolves to a fixed place, or throws a fixed error.
struct FakeDestinationResolver: DestinationResolver {
    let result: Result<ResolvedPlace, NavigationError>

    static func returning(_ place: ResolvedPlace) -> FakeDestinationResolver {
        FakeDestinationResolver(result: .success(place))
    }

    static func failing(_ error: NavigationError) -> FakeDestinationResolver {
        FakeDestinationResolver(result: .failure(error))
    }

    func resolve(freeText: String, near: LatLng?) async throws -> ResolvedPlace {
        try result.get()
    }
}

/// Virtual time: nothing sleeps for real, and the test decides when
/// "later" happens. Sleepers waiting past the current instant suspend
/// until `advance(byMs:)` reaches their deadline, so the engine's 1 Hz
/// watchdog cannot race ahead of the scripted fixes.
actor ManualClock: NavigationClock {
    private var now: Int64
    private var nextID = 0
    private var sleepers: [Int: (deadline: Int64, continuation: CheckedContinuation<Void, Never>)] = [:]

    init(startMs: Int64 = 0) {
        self.now = startMs
    }

    func nowMs() async -> Int64 { now }

    func sleep(forMilliseconds milliseconds: Int64) async throws {
        let deadline = now + milliseconds
        let id = nextID
        nextID += 1
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                if deadline <= now {
                    continuation.resume()
                } else {
                    sleepers[id] = (deadline, continuation)
                }
            }
        } onCancel: {
            Task { await self.wake(id) }
        }
        try Task.checkCancellation()
    }

    /// Moves time forward and releases everything now due.
    func advance(byMs milliseconds: Int64) {
        now += milliseconds
        for (id, sleeper) in sleepers where sleeper.deadline <= now {
            sleepers.removeValue(forKey: id)
            sleeper.continuation.resume()
        }
    }

    /// Releases every sleeper regardless of deadline; called at the end
    /// of a test so no continuation is left dangling.
    func releaseAll() {
        for (id, sleeper) in sleepers {
            sleepers.removeValue(forKey: id)
            sleeper.continuation.resume()
        }
    }

    private func wake(_ id: Int) {
        guard let sleeper = sleepers.removeValue(forKey: id) else { return }
        sleeper.continuation.resume()
    }
}
