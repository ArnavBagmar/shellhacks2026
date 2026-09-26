import CaneNavigation
import CaneOutput
import Foundation
import os

/// "Hey Cane, take me to the library": step 12's engine, its cues spoken on
/// the navigation channel (below every hazard and answer). A late turn cue
/// is a wrong cue, so ordinary cues are dropped when they cannot play; the
/// state cues (arrived, off route, GPS lost/back) are retried briefly
/// (runbook "Demo decisions" #2).
@MainActor
final class NavigationController {
    private let arbiter: OutputArbiter
    private let userGain: @Sendable () -> Float
    private lazy var sources = CoreLocationSources()
    private var engine: NavigationEngine?
    private var cueTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "cane", category: "navigation")

    static let retriedKinds: Set<CueKind> = [.arrived, .offRoute, .gpsLost, .gpsRestored]

    init(arbiter: OutputArbiter, userGain: @escaping @Sendable () -> Float) {
        self.arbiter = arbiter
        self.userGain = userGain
    }

    func start(to place: String) async {
        guard let key = try? NavigationSecrets.apiKey() else {
            await say("Navigation isn't set up on this phone.")
            return
        }
        stop()
        sources.requestAuthorization()
        let transport = URLSessionTransport()
        let engine = NavigationEngine(
            resolver: PlacesTextSearchResolver(transport: transport, apiKey: { key }),
            routes: RoutesClient(transport: transport, apiKey: { key }),
            locations: sources,
            headings: sources
        )
        self.engine = engine
        let arbiter = arbiter, gain = userGain
        cueTask = Task {
            for await cue in engine.cues {
                await Self.present(cue, arbiter: arbiter, gain: gain())
            }
        }
        await say("Finding a route to \(place).")
        await engine.start(to: place)
        if case .failed(let error) = await engine.status {
            logger.warning("navigation failed: \(String(describing: error), privacy: .public)")
            await say("I couldn't find a walking route to \(place).")
        }
    }

    func stop() {
        cueTask?.cancel()
        cueTask = nil
        if let engine {
            Task { await engine.stop() }
        }
        engine = nil
    }

    private static func present(_ cue: TurnCue, arbiter: OutputArbiter, gain: Float) async {
        let side: Side? = switch cue.hapticHint {
        case .leftTurn: .left
        case .rightTurn: .right
        case .ahead: .center
        case .none: nil
        }
        let haptic = HapticScript.navigation(side: side, userGain: Double(gain))
        let phrase = SpokenPhrase(text: cue.phrase)
        if await arbiter.say(phrase, on: .navigation, haptic: haptic) { return }
        guard retriedKinds.contains(cue.kind) else { return }
        for _ in 0..<10 {
            try? await Task.sleep(for: .milliseconds(500))
            if Task.isCancelled { return }
            if await arbiter.say(phrase, on: .navigation, haptic: haptic) { return }
        }
    }

    private func say(_ text: String) async {
        await arbiter.say(SpokenPhrase(text: text), on: .prompt, maxWaitMs: 3_000)
    }
}
