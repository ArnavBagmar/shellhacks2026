import CaneAssist
import CaneCamera
import CaneContracts
import CaneFeedback
import CaneGuide
import CaneOutput
import CaneTransport
import CaneVoice
import Foundation
import os

/// Everything that runs during a walk, wired once per walk:
///
///     laptop --events--> FeedbackManager --> OutputSink --> OutputArbiter --> haptics / speech
///     laptop --steer---> FeedbackManager --> SteerCueOutput -^ (step 24)
///     laptop <--query--- QueryCoordinator <-- VoiceRouter <-- SpeechListener (open mic)
///     laptop --answer--> QueryCoordinator --> OutputArbiter
///                             `--> HandGuide (front camera + TrueDepth) --> ticks, beeps, watch taps
///     laptop <--guide--- HandGuide (live boxes + small frames for the page)
///     laptop <--speak--- NaturalSpeechRenderer (answers, prompts) --voice--> clip player
///
/// The phone is the master clock: every `now` here is phone wall-clock ms.
@MainActor
final class Runtime {
    struct Callbacks {
        var onConnected: @Sendable (Bool) -> Void
        var onSpeaking: @Sendable (Bool) -> Void
        var onHazard: @Sendable (Int) -> Void
        var onHeard: @Sendable (String) -> Void
        var onSaid: @Sendable (String) -> Void
        var onLevel: @Sendable (Double) -> Void
        var onAssist: @Sendable (QueryCoordinator.State) -> Void
        var onNote: @Sendable (String) -> Void
        /// A live TrueDepth reading for the debug panel, once a second.
        var onDepth: @Sendable (String) -> Void
        /// The laptop link is down (banner title + what to check), or nil.
        var onLinkProblem: @Sendable (LinkProblem?) -> Void
    }

    struct LinkProblem: Equatable, Sendable {
        let title: String
        let detail: String
    }

    static let tickMs = 100
    static let gateTickMs = 100

    let arbiter: OutputArbiter
    /// Held rather than only captured in `init`: step 29's ground loop runs
    /// in `startLoops` and needs the live haptic gain too.
    let settings: SettingsBox
    let client: EventLinkClient
    let camera: FrontCamera
    /// "Guide my hand to ...": live tracking once Gemini found the thing.
    let guide: HandGuide
    private let laptopVoice: LaptopVoice
    private let feedback: FeedbackManager
    private let coordinator: QueryCoordinator
    private let voice: VoiceRouter
    private let navigation: NavigationController
    private let listener = SpeechListener()
    private let callbacks: Callbacks
    let diagnostics: Diagnostics
    private let laptopURL: URL
    private let network = NetworkWatch()
    private var tasks: [Task<Void, Never>] = []
    private var interruptionObserver: NSObjectProtocol?
    private let logger = Logger(subsystem: "cane", category: "runtime")

    static let now: @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1_000) }

    init(url: URL, settings: SettingsBox, callbacks ui: Callbacks) {
        self.settings = settings
        laptopURL = url
        let client = EventLinkClient(url: url)
        self.client = client
        let diagnostics = Diagnostics(client: client)
        self.diagnostics = diagnostics
        let callbacks = Self.recording(ui, into: diagnostics)
        self.callbacks = callbacks
        let haptics = CoreHapticsRenderer()
        let speech = AVSpeechRenderer(rate: { settings.current.speechRate })
        let laptopVoice = LaptopVoice(link: client, clock: Self.now)
        self.laptopVoice = laptopVoice
        let natural = NaturalSpeechRenderer(
            fallback: speech,
            player: AVClipPlayer(),
            onOutcome: { text, outcome in
                switch outcome {
                case .natural(let waitedMs):
                    diagnostics.record("voice", ["source": .text("elevenlabs"), "waited_ms": .number(Double(waitedMs)),
                                                 "text": .text(text)])
                case .fallback(let reason):
                    diagnostics.record("voice", ["source": .text("phone"), "reason": .text(reason), "text": .text(text)])
                }
            },
            fetch: { text in
                guard settings.current.naturalVoice else { return nil }
                return await laptopVoice.audio(for: text)
            }
        )
        let arbiter = OutputArbiter(haptics: haptics, speech: speech, naturalSpeech: natural,
                                    earcon: EarconPlayer(), clock: Self.now)
        self.arbiter = arbiter

        let sink = NotifyingSink(
            inner: OutputSink(
                arbiter: arbiter,
                userGain: { settings.current.hapticGain },
                panRoute: { AudioSessionConfig.panRoute() },
                clock: Self.now
            ),
            onPresent: callbacks.onHazard
        )
        // Step 24: the steering cue shares the actuator and the 100 ms tick
        // with the hazard path, and sits below everything on the arbiter.
        let feedback = FeedbackManager(
            sink: sink,
            steerSink: SteerCueOutput(
                arbiter: arbiter,
                userGain: { settings.current.hapticGain },
                panRoute: { AudioSessionConfig.panRoute() }
            ),
            // Hazards are vehicles coming at the wearer, nothing else
            // (2026-09-26); steering still guides around every obstacle.
            state: FeedbackState(hazardScope: .approachingVehicles),
            clock: Self.now
        )
        self.feedback = feedback

        let camera = FrontCamera()
        self.camera = camera
        let guide = HandGuide(
            camera: camera, speaker: arbiter, watch: WatchLink.shared, laptop: client,
            ticker: GuideTicker(hazardActive: { arbiter.isPresentingHazard }, speaking: { arbiter.isSpeaking },
                                clock: Self.now),
            streamIdle: { settings.current.cameraOnLaptop },
            clock: Self.now,
            onEnd: { target, end in
                diagnostics.record("guide_end", ["target": .text(target), "end": .text(end.rawValue)])
            }
        )
        self.guide = guide
        let coordinator = QueryCoordinator(
            link: client,
            speaker: arbiter,
            snapshot: {
                guard let shot = camera.snapshotJPEG(withDepth: true) else { return FrontCapture() }
                let frame = try? PhoneFrame(jpeg: shot.jpeg, hfovDeg: shot.hfovDeg, width: shot.width, height: shot.height)
                return FrontCapture(frame: frame, depth: shot.depth)
            },
            clock: Self.now,
            userGain: { settings.current.hapticGain },
            onDistance: { outcome in
                var fields: [String: DiagnosticEvent.Value] = ["outcome": .text(outcome.label)]
                if let sample = outcome.sample {
                    fields["metres"] = .number(sample.metres)
                    fields["nearest_m"] = .number(sample.nearestMetres)
                    fields["coverage"] = .number(sample.coverage)
                    fields["accuracy"] = .text(sample.accuracy.rawValue)
                }
                diagnostics.record("distance", fields)
            },
            onGuide: { request in
                diagnostics.record("guide_start", [
                    "target": .text(request.target), "watch": .text(WatchLink.shared.status),
                    "box": .text(String(format: "%.2f,%.2f,%.2f,%.2f", request.box.xMin, request.box.yMin,
                                        request.box.xMax, request.box.yMax)),
                ])
                return guide.start(request)
            }
        )
        self.coordinator = coordinator
        // A lost thing is found again by asking Gemini once more, quietly.
        guide.setRefinder { target in await coordinator.refind(target) }
        let navigation = NavigationController(
            arbiter: arbiter,
            userGain: { settings.current.hapticGain },
            // Shepherd's stage 6: a turn cue that is happening *now* also leans
            // the steering mix, scaled by (1 - proximityFactor) so obstacles
            // win. The spoken cue's arbitration is unchanged.
            steerBias: { lateral in
                Task { await feedback.setNavigationBias(lateral: lateral, now: Self.now()) }
            },
            record: { kind, fields in diagnostics.record(kind, fields) }
        )
        self.navigation = navigation
        voice = VoiceRouter(
            arbiter: arbiter,
            coordinator: coordinator,
            navigation: navigation,
            guide: guide,
            onHeard: callbacks.onHeard,
            diagnostics: diagnostics
        )
    }

    /// Every UI callback also lands in the black-box recorder.
    private static func recording(_ ui: Callbacks, into diag: Diagnostics) -> Callbacks {
        Callbacks(
            onConnected: { value in diag.record("link", ["connected": .flag(value)]); ui.onConnected(value) },
            onSpeaking: { value in diag.record("speaking", ["on": .flag(value), "mic_muted": .flag(value)]); ui.onSpeaking(value) },
            onHazard: { urgency in diag.record("hazard", ["urgency": .number(Double(urgency))]); ui.onHazard(urgency) },
            onHeard: { text in ui.onHeard(text) },
            onSaid: { text in diag.record("say", ["text": .text(text)]); ui.onSaid(text) },
            onLevel: { value in diag.micLevel(value); ui.onLevel(value) },
            onAssist: { state in diag.record("assist", ["state": .text(String(describing: state))]); ui.onAssist(state) },
            onNote: { text in diag.record("note", ["text": .text(text)]); ui.onNote(text) },
            // Already recorded by the depth loop itself; this only drives the panel.
            onDepth: { line in ui.onDepth(line) },
            onLinkProblem: { problem in
                diag.record("link_alert", ["problem": .text(problem?.title ?? "none")])
                ui.onLinkProblem(problem)
            }
        )
    }

    func start() async {
        let info = Bundle.main.infoDictionary
        diagnostics.record("start", [
            "laptop": .text(laptopURL.absoluteString),
            "version": .text("\(info?["CFBundleShortVersionString"] ?? "?") (\(info?["CFBundleVersion"] ?? "?"))"),
            "file": .text(diagnostics.fileURL?.lastPathComponent ?? "-"),
        ])
        let session = AudioSessionConfig.configure()
        callbacks.onNote("audio: \(session)")
        if ProcessInfo.processInfo.isLowPowerModeEnabled {
            callbacks.onNote("Low Power Mode is on -- haptics may feel weak")
        }
        let listener = listener, callbacks = callbacks
        arbiter.observeSpeaking { speaking in
            listener.setPhoneSpeaking(speaking)
            callbacks.onSpeaking(speaking)
            if speaking { callbacks.onLevel(0) }
        }
        arbiter.observePhrases(callbacks.onSaid)
        await coordinator.observeState(callbacks.onAssist)

        client.connect()
        startLoops()
        await startCamera()
        await startMicrophone()
        callbacks.onNote("watch: \(WatchLink.shared.status)")
        interruptionObserver = AudioSessionConfig.observeInterruptions { [arbiter] began in
            guard !began else { return }
            Task { await arbiter.say(SpokenPhrase(text: "Output interrupted. Back now."), on: .prompt, maxWaitMs: 3_000) }
        }

        let ready = arbiter.haptics.isAvailable ? "Cane ready." : "Cane ready. Haptics unavailable, speech only."
        await arbiter.say(SpokenPhrase(text: ready), on: .prompt, maxWaitMs: 3_000)
    }

    func stop() {
        tasks.forEach { $0.cancel() }
        tasks.removeAll()
        listener.stop()
        network.stop()
        guide.stop()
        camera.stop()
        navigation.stop()
        client.disconnect()
        arbiter.cancelHazard()
        arbiter.cancel(upTo: .answer)
        if let interruptionObserver { NotificationCenter.default.removeObserver(interruptionObserver) }
    }

    /// The debug map's data; nil when no route is running.
    func navigationDebug() async -> NavigationDebug? {
        await navigation.debugInfo()
    }

    /// The typed command from the setup screen (no microphone needed).
    func typed(_ text: String) {
        let voice = voice
        Task { await voice.command(text) }
    }

    // MARK: - Loops

    private func startLoops() {
        let client = client, feedback = feedback, coordinator = coordinator, voice = voice, callbacks = callbacks
        let diagnostics = diagnostics, laptopVoice = laptopVoice
        let network = network, arbiter = arbiter, settings = self.settings
        let host = laptopURL.host ?? "", address = "\(laptopURL.host ?? "?"):\(laptopURL.port ?? 0)"
        tasks.append(Task {
            for await clip in client.voices {
                await laptopVoice.receive(clip)
            }
        })
        tasks.append(Task {
            for await event in client.events {
                await feedback.ingest(event, now: Self.now())
            }
        })
        // Step 24: one `steer` frame per sensing tick, gated on its own TTL
        // inside the manager. The stream keeps only the newest.
        tasks.append(Task {
            for await command in client.steerCommands {
                await feedback.ingest(command, now: Self.now())
            }
        })
        // Step 29: ground discontinuities. Gated here rather than inside the
        // feedback manager because a `ground` cue has no `track_id` to key a
        // refractory on and no urgency ladder that could score it -- the ladder
        // needs metric range or a closing rate, and a hole has neither. That is
        // the same reason it arrives on its own frame instead of as an event.
        tasks.append(Task {
            var gate = GroundGate.State()
            for await cue in client.groundCues {
                let now = Self.now()
                let (decision, next) = GroundGate.decide(cue, state: gate, now: now)
                gate = next
                guard decision == .announce else { continue }
                let route = AudioSessionConfig.panRoute()
                let plan = GroundPhrase.plan(
                    for: cue,
                    hapticsAvailable: arbiter.haptics.isAvailable,
                    userGain: settings.current.hapticGain,
                    route: route.route,
                    channels: route.channels
                )
                diagnostics.record("ground", [
                    "bearing": .number(cue.bearingDeg),
                    "span_deg": .number(cue.spanDeg),
                    "drop_ratio": .number(cue.dropRatio),
                    "confidence": .number(cue.confidence),
                ])
                await arbiter.presentHazard(
                    haptic: plan.haptic, phrase: plan.phrase, pan: plan.pan
                )
            }
        })
        tasks.append(Task {
            for await answer in client.answers {
                diagnostics.record("answer", [
                    "query_id": .text(answer.queryId), "status": .text(answer.status.rawValue),
                    "subject": .text(answer.subject ?? ""), "bearing": answer.bearingDeg.map { .number($0) } ?? .text("none"),
                    "latency_ms": .number(Double(answer.latencyMs)),
                ])
                await coordinator.receive(answer)
            }
        })
        // Step 9 requires a tick: it releases items held by the speech
        // throttle and drops ones that went stale in the queue.
        tasks.append(Task {
            while !Task.isCancelled {
                await feedback.tick(now: Self.now())
                try? await Task.sleep(for: .milliseconds(Self.tickMs))
            }
        })
        tasks.append(Task {
            while !Task.isCancelled {
                await voice.tick(nowMs: Self.now())
                try? await Task.sleep(for: .milliseconds(Self.gateTickMs))
            }
        })
        // The link, once a second: the orb's state, and -- past a short
        // grace -- a spoken warning and a banner with the likeliest cause.
        tasks.append(Task {
            var last: Bool?
            var alert = LinkAlert()
            while !Task.isCancelled {
                let connected = await client.hasConnection
                if connected != last {
                    callbacks.onConnected(connected)
                    // Step 24's failsafe: a known-dead link decays the steering
                    // cue to neutral without waiting out the 400 ms TTL. The
                    // TTL still covers the case where frames simply stop.
                    await feedback.setLinkUp(connected)
                }
                last = connected
                let net = network.state
                let reason = LinkAlert.reason(networkUp: net.networkUp, onWiFi: net.onWiFi, laptopHost: host)
                switch alert.update(connected: connected, reason: reason, nowMs: Self.now()) {
                case .none:
                    break
                case .lost(let reason):
                    let banner = LinkAlert.banner(reason, address: address)
                    callbacks.onLinkProblem(LinkProblem(title: banner.title, detail: banner.detail))
                    await arbiter.say(SpokenPhrase(text: LinkAlert.spoken(reason)), on: .prompt, maxWaitMs: 3_000)
                case .restored:
                    callbacks.onLinkProblem(nil)
                    await arbiter.say(SpokenPhrase(text: LinkAlert.restoredPhrase), on: .prompt, maxWaitMs: 3_000)
                }
                try? await Task.sleep(for: .seconds(1))
            }
        })
    }

    private func startCamera() async {
        guard await FrontCamera.requestPermission() else {
            callbacks.onNote("camera: no permission -- questions use the 360 camera only")
            return
        }
        // Prototype: measure what is straight ahead. Only phones with a
        // TrueDepth sensor get readings; the rest run exactly as before.
        camera.wantsDepth = true
        do {
            try camera.start()
            if camera.hasDepth {
                callbacks.onNote("camera: front, mirroring off, TrueDepth on")
                startDepthLog()
            } else {
                callbacks.onNote("camera: front, mirroring off, no TrueDepth on this phone")
            }
            diagnostics.record("depth_available", ["has_depth": .text(camera.hasDepth ? "yes" : "no")])
            callbacks.onNote("camera view: \(camera.formatSummary)")
            diagnostics.record("camera_format", ["summary": .text(camera.formatSummary)])
        } catch {
            callbacks.onNote("camera: \(error)")
        }
    }

    /// Logs one reading a second to the black box, so the range and the
    /// sunlight question can be answered from real numbers rather than
    /// guessed at. Purely observational: nothing is spoken from it yet.
    private func startDepthLog() {
        tasks.append(Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self, let sample = self.camera.depth else { continue }
                let trusted = sample.isTrustworthy()
                self.callbacks.onDepth(String(
                    format: "ahead %.2f m · coverage %.0f%% · %@%@",
                    sample.metres, sample.coverage * 100, sample.accuracy.rawValue,
                    trusted ? "" : "  NOT TRUSTED"))
                self.diagnostics.record("depth", [
                    "metres": .number(sample.metres),
                    "nearest_m": .number(sample.nearestMetres),
                    "coverage": .number(sample.coverage),
                    "accuracy": .text(sample.accuracy.rawValue),
                    "trustworthy": .text(sample.isTrustworthy() ? "yes" : "no"),
                ])
            }
        })
    }

    private func startMicrophone() async {
        guard await SpeechListener.requestPermissions() else {
            callbacks.onNote("mic: no permission -- use the typed command box")
            return
        }
        AudioSessionConfig.upgradeForRecording()
        let voice = voice, diagnostics = diagnostics
        do {
            try await listener.start(
                onTranscript: { text, isFinal in
                    diagnostics.transcript(text, isFinal: isFinal)
                    Task { await voice.transcript(text, isFinal: isFinal, nowMs: Runtime.now()) }
                },
                onLevel: callbacks.onLevel
            )
            callbacks.onNote("mic: listening for \"Hey Cane\"")
        } catch {
            logger.error("listener failed: \(String(describing: error), privacy: .public)")
            callbacks.onNote("mic: \(error)")
            await arbiter.say(SpokenPhrase(text: "Voice control is unavailable."), on: .prompt, maxWaitMs: 3_000)
        }
    }
}

/// Lets the chest display flash on a hazard without the output layer
/// knowing a screen exists.
final class NotifyingSink: AnnouncementSink, @unchecked Sendable {
    private let inner: AnnouncementSink
    private let onPresent: @Sendable (Int) -> Void

    init(inner: AnnouncementSink, onPresent: @escaping @Sendable (Int) -> Void) {
        self.inner = inner
        self.onPresent = onPresent
    }

    func present(_ announcement: Announcement) async {
        onPresent(announcement.urgency)
        await inner.present(announcement)
    }

    func cancelCurrent() {
        inner.cancelCurrent()
    }
}
