import CaneContracts
import CaneFeedback
import Foundation

/// The outcome of replaying one recording.
public struct ReplayResult: Equatable, Sendable {
    public let tally: ReplayTally
    /// Lines that parsed into an event. Blank lines are not counted here.
    public let linesRead: Int
    /// Lines that did not parse. A truncated recording -- a killed walk --
    /// ends in one of these, and must still yield its numbers.
    public let linesRejected: Int
}

public enum ReplayError: Error, CustomStringConvertible {
    case unreadable(URL, underlying: String)

    public var description: String {
        switch self {
        case let .unreadable(url, underlying):
            return "could not read the recording at \(url.path): \(underlying)"
        }
    }
}

/// Drives a recorded walk through the real gauntlet.
///
/// The virtual clock is driven from each line's `t_emit_ms`, never from the
/// wall clock. That is what makes a replay reproducible, and reproducibility is
/// the whole reason the walk is recorded instead of measured live: the
/// before/after comparison has to run on byte-identical input.
public enum ReplayRunner {
    /// The key `RecordingEventSink` adds. It must come off before the line
    /// reaches the contract, which rejects unknown keys.
    static let emitStampKey = "t_emit_ms"

    public static func run(contentsOf url: URL) async throws -> ReplayResult {
        let text: String
        do {
            text = try String(contentsOf: url, encoding: .utf8)
        } catch {
            throw ReplayError.unreadable(url, underlying: error.localizedDescription)
        }
        return await run(lines: text.components(separatedBy: .newlines))
    }

    public static func run(lines: [String]) async -> ReplayResult {
        let clock = VirtualClock()
        let sink = CollectingSink()
        let manager = FeedbackManager(sink: sink, clock: clock.reader)

        var spans = ReplayTally()
        var read = 0
        var rejected = 0

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            // A trailing newline is not a fault; a truncated line is.
            if trimmed.isEmpty { continue }
            guard let parsed = parse(trimmed) else {
                rejected += 1
                continue
            }
            read += 1
            spans = spans.observing(emitMs: parsed.emitMs)
            clock.set(parsed.emitMs)
            await manager.ingest(parsed.event, now: parsed.emitMs)
            // The sink returns at once, so this drains the queue the gauntlet
            // just filled rather than modelling speaker time -- see the note on
            // `ReplayTally`.
            await manager.settle()
        }

        let tally = sink.drain().reduce(spans) { $0.adding($1) }
        return ReplayResult(tally: tally, linesRead: read, linesRejected: rejected)
    }

    /// `nil` for anything the baseline cannot trust: bad JSON, a missing or
    /// non-numeric emit stamp, or a payload the contract refuses.
    static func parse(_ line: String) -> (event: AwarenessEvent, emitMs: Int64)? {
        guard let data = line.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let stamp = object[emitStampKey],
              let emitMs = int64(from: stamp)
        else { return nil }

        var stripped = object
        stripped.removeValue(forKey: emitStampKey)

        guard let reencoded = try? JSONSerialization.data(withJSONObject: stripped),
              let event = try? AwarenessEvent.decode(reencoded)
        else { return nil }

        return (event, emitMs)
    }

    private static func int64(from any: Any) -> Int64? {
        switch any {
        case let value as Int64: return value
        case let value as Int: return Int64(value)
        case let value as NSNumber: return value.int64Value
        case let value as Double: return Int64(value)
        default: return nil
        }
    }
}

/// The replay's clock. Set from the recording, read by the gauntlet.
final class VirtualClock: @unchecked Sendable {
    private let lock = NSLock()
    private var nowMs: Int64 = 0

    func set(_ ms: Int64) {
        lock.withLock { nowMs = ms }
    }

    var reader: @Sendable () -> Int64 {
        { [self] in lock.withLock { nowMs } }
    }
}

/// Collects what the gauntlet decided to present. Deliberately inert: it does
/// no timing and no rendering, so the replay measures policy alone.
final class CollectingSink: AnnouncementSink, @unchecked Sendable {
    private let lock = NSLock()
    private var items: [Announcement] = []

    func present(_ announcement: Announcement) async {
        guard !Task.isCancelled else { return }
        lock.withLock { items.append(announcement) }
    }

    func cancelCurrent() {}

    func drain() -> [Announcement] {
        lock.withLock {
            let out = items
            items = []
            return out
        }
    }
}
