/// A14 -- the §3.4 fallback signal, and the only counter that feeds it.
/// Ten one-second buckets, fixed for the life of the process: a rising
/// stale-drop rate is the cleanest trigger for the transport fallback, and
/// reusing the drops the gauntlet already makes means no separate probe.
///
/// Every operation returns a new ring; nothing here is mutated in place.
public struct RateRing: Equatable, Sendable {
    /// The trailing window is `bucketCount * bucketMs` = 10 s.
    public static let bucketCount = 10
    public static let bucketMs: Int64 = 1_000
    /// §3.4 flips to LOCAL when the stale-drop rate crosses this.
    public static let unhealthyRate = 0.2
    /// One hot second is not a trend.
    public static let minimumCompleteBuckets = 2

    struct Bucket: Equatable, Sendable {
        /// Whole seconds of phone clock; `nil` means never written.
        var second: Int64?
        var received: Int
        var staleDropped: Int

        static let empty = Bucket(second: nil, received: 0, staleDropped: 0)
    }

    private var buckets: [Bucket]

    public init() {
        buckets = Array(repeating: .empty, count: Self.bucketCount)
    }

    /// Always `RateRing.bucketCount`; asserted by the bounded-state tests.
    public var bucketCount: Int { buckets.count }

    public func recordingReceived(now: Int64) -> RateRing {
        updating(now: now) { bucket in
            Bucket(second: bucket.second, received: bucket.received + 1, staleDropped: bucket.staleDropped)
        }
    }

    public func recordingStaleDrop(now: Int64) -> RateRing {
        updating(now: now) { bucket in
            Bucket(second: bucket.second, received: bucket.received, staleDropped: bucket.staleDropped + 1)
        }
    }

    /// `staleDrops / received` over the trailing window; 0 when nothing was
    /// received (never NaN -- step 8 reads this straight onto the wire).
    public func rate(now: Int64) -> Double {
        let live = liveBuckets(now: now)
        let received = live.reduce(0) { $0 + $1.received }
        guard received > 0 else { return 0.0 }
        let dropped = live.reduce(0) { $0 + $1.staleDropped }
        return Double(dropped) / Double(received)
    }

    /// The boolean step 8 actually switches on (H4): never sum `futureDated`
    /// or `queueExpired` into this.
    public func isUnhealthy(now: Int64) -> Bool {
        let currentSecond = Self.second(of: now)
        let complete = liveBuckets(now: now).filter { ($0.second ?? currentSecond) < currentSecond }
        guard complete.count >= Self.minimumCompleteBuckets else { return false }
        return rate(now: now) >= Self.unhealthyRate
    }

    private static func second(of now: Int64) -> Int64 {
        // Floor division: negative clocks are impossible (the contract's
        // `t_capture_ms` minimum is year 2020), but flooring keeps the
        // bucket index non-negative regardless.
        now >= 0 ? now / bucketMs : ((now + 1) / bucketMs) - 1
    }

    private static func index(of second: Int64) -> Int {
        Int(((second % Int64(bucketCount)) + Int64(bucketCount)) % Int64(bucketCount))
    }

    private func liveBuckets(now: Int64) -> [Bucket] {
        let currentSecond = Self.second(of: now)
        return buckets.filter { bucket in
            guard let second = bucket.second else { return false }
            let age = currentSecond - second
            return age >= 0 && age < Int64(Self.bucketCount)
        }
    }

    private func updating(now: Int64, _ transform: (Bucket) -> Bucket) -> RateRing {
        let second = Self.second(of: now)
        let slot = Self.index(of: second)
        // A bucket whose stamp is not this second belongs to a lap of the
        // ring that has rolled off the window: reset it rather than add to it.
        let base = buckets[slot].second == second ? buckets[slot] : Bucket(second: second, received: 0, staleDropped: 0)
        var updated = self
        updated.buckets[slot] = transform(base)
        return updated
    }
}
