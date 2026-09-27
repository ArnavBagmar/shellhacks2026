import CaneAssist
import CaneGuide
import Foundation
import WatchConnectivity
import os

/// The phone's end of the hand guide's watch channel. One per app:
/// `WCSession` has a single delegate, and walks come and go.
///
/// Live gaps go by `sendMessage` (only while the watch app is open --
/// `isReachable`); a start or an end also goes by application context so a
/// watch that opens late still shows the right state.
final class WatchLink: NSObject, WCSessionDelegate, GuideWatch, @unchecked Sendable {
    static let shared = WatchLink()

    private let session: WCSession? = WCSession.isSupported() ? .default : nil
    private let logger = Logger(subsystem: "cane", category: "watch")

    private override init() {
        super.init()
        session?.delegate = self
        session?.activate()
    }

    var isReachable: Bool {
        guard let session, session.activationState == .activated else { return false }
        return session.isPaired && session.isWatchAppInstalled && session.isReachable
    }

    /// For the setup screen: why the watch will or will not tap.
    var status: String {
        guard let session else { return "not supported on this device" }
        guard session.activationState == .activated else { return "starting" }
        if !session.isPaired { return "no Apple Watch paired" }
        if !session.isWatchAppInstalled { return "Cane is not installed on the watch" }
        return session.isReachable ? "connected" : "open Cane on the watch"
    }

    func send(_ message: GuideMessage) {
        guard let session, session.activationState == .activated else { return }
        let payload = message.dictionary
        if session.isReachable {
            session.sendMessage(payload, replyHandler: nil) { [logger] error in
                logger.warning("watch message failed: \(error.localizedDescription, privacy: .public)")
            }
        }
        if message.state.isFinal || message.state == .findHand {
            try? session.updateApplicationContext(payload)
        }
    }

    // MARK: - WCSessionDelegate

    func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        if let error {
            logger.warning("watch session: \(error.localizedDescription, privacy: .public)")
        }
    }

    func sessionDidBecomeInactive(_ session: WCSession) {}

    /// After a watch switch: activate again for the new one.
    func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }
}
