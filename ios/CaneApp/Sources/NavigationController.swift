import CaneAssist
import CaneNavigation
import CaneOutput
import CaneTransport
import Foundation
import os

/// "Hey Cane, navigate me to the library": step 12's engine, its cues spoken on
/// the navigation channel (below every hazard and answer). A late turn cue
/// is a wrong cue, so ordinary cues are dropped when they cannot play; the
/// state cues (arrived, off route, GPS lost/back) are retried briefly
/// (runbook "Demo decisions" #2).
@MainActor
final class NavigationController {
    private let arbiter: OutputArbiter
    private let userGain: @Sendable () -> Float
    private let steerBias: @Sendable (Double) -> Void
    /// A new one per route: stopping a route finishes its streams for good,
    /// so a reused one gave every later route no GPS at all -- "I couldn't
    /// find a walking route" to places Google Maps walks to fine
    /// (2026-09-27).
    private var sources: CoreLocationSources?
    private let record: @Sendable (String, [String: DiagnosticEvent.Value]) -> Void
    private var engine: NavigationEngine?
    private var cueTask: Task<Void, Never>?
    /// Where the current route goes, and its last spoken cue ("repeat that").
    private(set) var destination: String?
    private var lastCue: String?

    var isActive: Bool { engine != nil }
    private let logger = Logger(subsystem: "cane", category: "navigation")

    static let retriedKinds: Set<CueKind> = [.arrived, .offRoute, .gpsLost, .gpsRestored]

    /// Step 24: the kinds that lean the *steering* mix as well as speaking.
    /// A maneuver that is happening now, and a heading correction, are about
    /// where to put the next step; "left in 30 metres" is not, and must not
    /// pull the wearer sideways a block early.
    static let biasingKinds: Set<CueKind> = [.now, .correctHeading]

    init(
        arbiter: OutputArbiter,
        userGain: @escaping @Sendable () -> Float,
        steerBias: @escaping @Sendable (Double) -> Void = { _ in },
        record: @escaping @Sendable (String, [String: DiagnosticEvent.Value]) -> Void = { _, _ in }
    ) {
        self.record = record
        self.arbiter = arbiter
        self.userGain = userGain
        self.steerBias = steerBias
    }

    func start(to place: String) async {
        guard let key = try? NavigationSecrets.apiKey() else {
            await say("Navigation isn't set up on this phone.")
            return
        }
        stop()
        let sources = CoreLocationSources()
        self.sources = sources
        sources.requestAuthorization()
        let transport = URLSessionTransport()
        let engine = NavigationEngine(
            resolver: PlacesTextSearchResolver(transport: transport, apiKey: { key }),
            routes: RoutesClient(transport: transport, apiKey: { key }),
            locations: sources,
            headings: sources
        )
        self.engine = engine
        destination = place
        lastCue = nil
        let arbiter = arbiter, gain = userGain
        let steerBias = self.steerBias
        cueTask = Task { [weak self] in
            for await cue in engine.cues {
                self?.lastCue = cue.phrase
                steerBias(Self.lateralBias(for: cue))
                await Self.present(cue, arbiter: arbiter, gain: gain())
            }
        }
        record("nav_start", ["query": .text(place)])
        await say("Finding a route to \(place).")
        let record = self.record
        await engine.start(to: place) { [weak self] found, route in
            record("nav_route", [
                "query": .text(place), "place": .text(found.name), "address": .text(found.formattedAddress),
                "lat": .number(found.latLng.latitude), "lng": .number(found.latLng.longitude),
                "route_m": .number(Double(route.distanceMeters)), "steps": .number(Double(route.steps.count)),
            ])
            // Before any cue: the walker hears which place was chosen, so a
            // mishearing ("graham hall" -> Graham Center) is caught at once.
            await self?.say(Self.headingPhrase(found.name, metres: Double(route.distanceMeters)))
        }
        if case .failed(let error) = await engine.status {
            logger.warning("navigation failed: \(String(describing: error), privacy: .public)")
            record("nav_failed", ["query": .text(place), "error": .text(String(describing: error))])
            await say(Self.failurePhrase(error, place: place))
            if self.engine === engine { stop() }
        }
    }

    static func headingPhrase(_ name: String, metres: Double) -> String {
        metres < 15 ? "\(name) is right here." : "Heading to \(name), \(DistanceWords.away(metres))."
    }

    /// Why, in words the walker can act on -- not one message for every failure.
    static func failurePhrase(_ error: NavigationError, place: String) -> String {
        switch error {
        case .noFix:
            "I can't get a GPS fix here. Try outside or near a window, then ask again."
        case .destinationNotFound:
            "I couldn't find \(place) within walking distance."
        case .noRoute:
            "Google Maps has no walking route to \(place)."
        case .missingAPIKey:
            "Navigation isn't set up on this phone."
        case .routesAPI, .transport, .decoding:
            "I can't reach Google Maps right now. Check the internet connection."
        }
    }

    func stop() {
        sources = nil
        cueTask?.cancel()
        cueTask = nil
        if let engine {
            Task { await engine.stop() }
        }
        engine = nil
        destination = nil
        lastCue = nil
    }

    /// "Stop navigation" / "cancel": ends the route, cuts any cue being
    /// spoken ("Rerouting..."), releases the steering lean. Returns whether
    /// there was a route to stop.
    @discardableResult
    func cancelByVoice(announce: Bool = true) async -> Bool {
        guard isActive else { return false }
        stop()
        arbiter.cancel(upTo: .navigation)
        steerBias(0)
        if announce { await say("Navigation stopped.") }
        return true
    }

    /// "Repeat that": the last direction, or why there is none.
    func repeatLast() async {
        guard isActive, let destination else { return await say("You're not navigating right now.") }
        await say(lastCue.map { "\($0)." } ?? "Heading to \(destination). No new direction yet.")
    }

    /// "Where am I going?"
    func status() async {
        guard isActive, let destination else {
            return await say("You're not navigating right now. Say: navigate me to, and a place.")
        }
        let last = lastCue.map { " Last direction: \($0)." } ?? ""
        await say("Heading to \(destination).\(last) Say stop navigation to end it.")
    }

    /// -1 left, +1 right, 0 for anything that is not a turn happening now.
    /// The same sign convention as `SteerCommand.steer` and `BearingSector`.
    static func lateralBias(for cue: TurnCue) -> Double {
        guard biasingKinds.contains(cue.kind) else { return 0 }
        switch cue.hapticHint {
        case .leftTurn: return -1
        case .rightTurn: return 1
        case .ahead, .none: return 0
        }
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
