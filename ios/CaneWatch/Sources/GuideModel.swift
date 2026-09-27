import CaneGuide
import Foundation
import Observation
import WatchConnectivity
import WatchKit

/// The watch's whole job: take the phone's gap and tap the wrist faster as
/// the hand closes on the thing. It decides nothing -- the phone owns the
/// guide (`GuideSession`); this only renders `GuideMessage`s.
@MainActor
@Observable
final class GuideModel: NSObject {
    /// No message for this long: the phone is gone, stop tapping.
    static let staleAfterS = 1.5

    private(set) var message: GuideMessage?
    private(set) var reachable = false
    private var receivedAt: Date?
    private var pulses: Task<Void, Never>?
    private var runtime: WKExtendedRuntimeSession?

    override init() {
        super.init()
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    var isFresh: Bool {
        receivedAt.map { Date.now.timeIntervalSince($0) < Self.staleAfterS } ?? false
    }

    func receive(_ next: GuideMessage) {
        // Application context can deliver an old end after a newer start.
        if let message, next.sentMs < message.sentMs { return }
        let changed = next.state != message?.state
        message = next
        receivedAt = .now
        guard changed else { return }
        switch next.state {
        case .reached:
            WKInterfaceDevice.current().play(.success)
            endRuntime()
        case .lost:
            WKInterfaceDevice.current().play(.failure)
            endRuntime()
        case .stopped:
            endRuntime()
        case .looking, .findHand, .guiding:
            beginRuntime()
            startPulses()
        }
    }

    /// A parking sensor on the wrist: `GuidePulse` sets the rhythm.
    private func startPulses() {
        guard pulses == nil else { return }
        pulses = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, let message = self.message, !message.state.isFinal else { break }
                if self.isFresh, message.state == .guiding, let gap = message.gapM {
                    let tap: WKHapticType = GuidePulse.tap(gapM: gap) == .strong ? .start : .click
                    WKInterfaceDevice.current().play(tap)
                    try? await Task.sleep(for: .seconds(GuidePulse.interval(gapM: gap)))
                } else {
                    try? await Task.sleep(for: .milliseconds(100))
                }
            }
            self?.pulses = nil
        }
    }

    /// Keeps the taps going with the wrist lowered (a reaching arm is not
    /// looking at its watch).
    private func beginRuntime() {
        guard runtime == nil else { return }
        let session = WKExtendedRuntimeSession()
        session.delegate = self
        session.start()
        runtime = session
    }

    private func endRuntime() {
        runtime?.invalidate()
        runtime = nil
    }

    fileprivate func setReachable(_ value: Bool) {
        reachable = value
    }

    fileprivate func runtimeEnded() {
        runtime = nil
    }
}

extension GuideModel: WCSessionDelegate {
    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        let reachable = session.isReachable
        let context = GuideMessage(dictionary: session.receivedApplicationContext)
        Task { @MainActor in
            self.setReachable(reachable)
            if let context { self.receive(context) }
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in self.setReachable(reachable) }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        guard let parsed = GuideMessage(dictionary: message) else { return }
        Task { @MainActor in self.receive(parsed) }
    }

    nonisolated func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        guard let parsed = GuideMessage(dictionary: context) else { return }
        Task { @MainActor in self.receive(parsed) }
    }
}

extension GuideModel: WKExtendedRuntimeSessionDelegate {
    nonisolated func extendedRuntimeSession(
        _ extendedRuntimeSession: WKExtendedRuntimeSession,
        didInvalidateWith reason: WKExtendedRuntimeSessionInvalidationReason,
        error: Error?
    ) {
        Task { @MainActor in self.runtimeEnded() }
    }

    nonisolated func extendedRuntimeSessionDidStart(_ extendedRuntimeSession: WKExtendedRuntimeSession) {}

    nonisolated func extendedRuntimeSessionWillExpire(_ extendedRuntimeSession: WKExtendedRuntimeSession) {}
}
