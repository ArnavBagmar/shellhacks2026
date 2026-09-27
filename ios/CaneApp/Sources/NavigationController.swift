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
    private let steerBias: @Sendable (Double) -> Void
    private lazy var sources = CoreLocationSources()
    private var engine: NavigationEngine?
    private var cueTask: Task<Void, Never>?
    /// Indoors or outdoors, from how well GPS is working. Lives here because
    /// this is where the app's single `CLLocationManager` lives, and a GPS fix
    /// must never reach the awareness path (see `CaneNavigation.md`).
    private var estimator = IndoorOutdoorEstimator()
    private var watch: Task<Void, Never>?
    private var onSettingChange: (@MainActor (WalkSetting) -> Void)?
    /// True while a route is active. Following walking directions *is* being
    /// outdoors, and it also means the engine owns the location stream.
    private var routing = false
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
        steerBias: @escaping @Sendable (Double) -> Void = { _ in }
    ) {
        self.arbiter = arbiter
        self.userGain = userGain
        self.steerBias = steerBias
    }

    /// Indoors, outdoors, or `nil` before GPS has said anything either way.
    ///
    /// `nil` matters: it is not "outdoors". A destination asked before the
    /// first fix should go to the engine that needs no fix.
    var setting: WalkSetting? {
        routing ? .outdoors : estimator.setting
    }

    /// Start watching GPS quality so the app knows which engine owns a
    /// destination. `onChange` fires only when the verdict actually moves, so
    /// it is the right place to say it out loud.
    func watchSetting(onChange: @escaping @MainActor (WalkSetting) -> Void) {
        onSettingChange = onChange
        startWatch()
    }

    func start(to place: String) async {
        guard let key = try? NavigationSecrets.apiKey() else {
            await say("Navigation isn't set up on this phone.")
            return
        }
        stop()
        // The engine becomes the location stream's only consumer for the walk.
        routing = true
        watch?.cancel()
        watch = nil
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
        routing = false
        startWatch()
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
            return await say("You're not navigating right now. Say: take me to, and a place.")
        }
        let last = lastCue.map { " Last direction: \($0)." } ?? ""
        await say("Heading to \(destination).\(last) Say stop navigation to end it.")
    }

    // MARK: - Watching GPS quality

    private func startWatch() {
        guard !routing, watch == nil, onSettingChange != nil else { return }
        sources.requestAuthorization()
        let sources = self.sources
        watch = Task { [weak self] in
            // A slow heartbeat beside the stream: a phone carried inside stops
            // producing fixes altogether, and that silence is itself the
            // evidence that the wearer is no longer outside.
            let heartbeat = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(5))
                    guard !Task.isCancelled else { return }
                    self?.noteNoFix()
                }
            }
            defer { heartbeat.cancel() }
            for await fix in sources.locations() {
                guard !Task.isCancelled else { break }
                self?.note(fix)
            }
        }
    }

    private func note(_ fix: LocationFix) {
        apply(estimator.seeing(fix))
    }

    private func noteNoFix() {
        apply(estimator.seeingNothing(atMs: Int64(Date().timeIntervalSince1970 * 1_000)))
    }

    private func apply(_ next: IndoorOutdoorEstimator) {
        let before = estimator.setting
        estimator = next
        guard let now = next.setting, now != before else { return }
        logger.info("setting is now \(now.rawValue, privacy: .public)")
        onSettingChange?(now)
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
