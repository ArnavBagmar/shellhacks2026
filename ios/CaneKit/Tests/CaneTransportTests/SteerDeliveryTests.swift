import CaneContracts
import Foundation
import XCTest

@testable import CaneTransport

/// Step 24 owns the consumption path step 21 deliberately left out: a decoded
/// `steer` frame has to reach a consumer, on a stream of its own, without
/// disturbing the event or answer streams.
final class SteerDeliveryTests: XCTestCase {

    private func sinks() -> (InboundSinks, AsyncStream<AwarenessEvent>, AsyncStream<SceneAnswer>, AsyncStream<SteerCommand>) {
        let (events, eventContinuation) = AsyncStream<AwarenessEvent>.makeStream()
        let (answers, answerContinuation) = AsyncStream<SceneAnswer>.makeStream()
        let (steers, steerContinuation) = AsyncStream<SteerCommand>.makeStream(
            bufferingPolicy: .bufferingNewest(InboundSinks.steerBuffer)
        )
        // `voices` arrived with the natural-voice work; this test does not read it.
        let (_, voiceContinuation) = AsyncStream<VoiceClip>.makeStream()
        return (
            InboundSinks(
                events: eventContinuation,
                answers: answerContinuation,
                steers: steerContinuation,
                voices: voiceContinuation
            ),
            events, answers, steers
        )
    }

    private func steerFrame(steer: Double, tCaptureMs: Int64 = 1_790_000_000_000) throws -> InboundMessage {
        let command = try SteerCommand(
            tCaptureMs: tCaptureMs, ttlMs: SteerLimits.ttlMs, steer: steer, proximity: 0.5,
            gapBearingDeg: 12.0, blocked: false, proximityAnchored: true, source: .x4Cloud
        )
        return try InboundMessage.decode(command.encoded())
    }

    func testADecodedSteerFrameReachesTheSteerStream() async throws {
        let (sinks, _, _, steers) = sinks()
        sinks.deliver(try steerFrame(steer: 0.42))
        var iterator = steers.makeAsyncIterator()
        let received = await iterator.next()
        XCTAssertEqual(received?.steer, 0.42)
    }

    /// A steering command is only interesting if it is the latest one: an
    /// older one is superseded and would be stale anyway (plan §3's 400 ms).
    /// So the stream keeps one, and a slow consumer sees the newest -- never a
    /// queue of commands to walk through after the fact.
    func testOnlyTheNewestSteerCommandIsKeptForASlowConsumer() async throws {
        let (sinks, _, _, steers) = sinks()
        for step in 1...5 {
            sinks.deliver(try steerFrame(steer: Double(step) / 10, tCaptureMs: 1_790_000_000_000 + Int64(step)))
        }
        var iterator = steers.makeAsyncIterator()
        let newest = await iterator.next()
        XCTAssertEqual(newest?.steer, 0.5)
    }

    /// The other two streams are untouched by the new one.
    func testDeliveringASteerFrameDoesNotDisturbTheOtherStreams() async throws {
        let (sinks, events, _, _) = sinks()
        sinks.deliver(try steerFrame(steer: 0.1))
        sinks.deliver(.unknown(type: "health"))
        let event = try AwarenessEvent(
            source: .x4Cloud, trackId: "t1", tCaptureMs: 1_790_000_000_000, ttlMs: 1_500,
            cls: .person, confidence: 0.9, bearingDeg: 0, rangeM: nil, rangeBand: .mid, closingMps: nil
        )
        sinks.deliver(.event(event))
        var iterator = events.makeAsyncIterator()
        let received = await iterator.next()
        XCTAssertEqual(received?.trackId, "t1")
    }
}
