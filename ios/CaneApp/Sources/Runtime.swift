import CaneAssist
import CaneCamera
import CaneContracts
import CaneFeedback
import CaneOutput
import CaneTransport
import CaneVoice
import Foundation
import os

/// Everything that runs during a walk, wired once per walk:
///
///     laptop --events--> FeedbackManager --> OutputSink --> OutputArbiter --> haptics / speech
///     laptop <--query--- QueryCoordinator <-- VoiceRouter <-- SpeechListener (open mic)
///     laptop --answer--> QueryCoordinator --> OutputArbiter
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
    }

    static let tickMs = 100
    static let gateTickMs = 100

    let arbiter: OutputArbiter
    let client: EventLinkClient
    let camera: FrontCamera
    private let laptopVoice: LaptopVoice
    private let feedback: FeedbackManager
    private let coordinator: QueryCoordinator
    private let voice: VoiceRouter
    private let navigation: NavigationController
    private let listener = SpeechListener()
    private let callbacks: Callbacks
    let diagnostics: Diagnostics
    private let laptopURL: URL
    private var tasks: [Task<Void, Never>] = []
    private var interruptionObserver: NSObjectProtocol?
    private let logger = Logger(subsystem: "cane", category: "runtime")

    static let now: @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1_000) }

    init(url: URL, settings: SettingsBox, callbacks ui: Callbacks) {
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
        feedback = FeedbackManager(sink: sink, clock: Self.now)

        let camera = FrontCamera()
        self.camera = camera
        coordinator = QueryCoordinator(
            link: client,
            speaker: arbiter,
            snapshot: {
                guard let shot = camera.snapshotJPEG() else { return nil }
                return try? PhoneFrame(jpeg: shot.jpeg, hfovDeg: shot.hfovDeg, width: shot.width, height: shot.height)
            },
            clock: Self.now,
            userGain: { settings.current.hapticGain }
        )
        let navigation = NavigationController(arbiter: arbiter, userGain: { settings.current.hapticGain })
        self.navigation = navigation
        voice = VoiceRouter(
            arbiter: arbiter,
            coordinator: coordinator,
            navigate: { place in await navigation.start(to: place) },
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
            onNote: { text in diag.record("note", ["text": .text(text)]); ui.onNote(text) }
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
        camera.stop()
        navigation.stop()
        client.disconnect()
        arbiter.cancelHazard()
        arbiter.cancel(upTo: .answer)
        if let interruptionObserver { NotificationCenter.default.removeObserver(interruptionObserver) }
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
        tasks.append(Task {
            var last: Bool?
            while !Task.isCancelled {
                let connected = await client.hasConnection
                if connected != last { callbacks.onConnected(connected) }
                last = connected
                try? await Task.sleep(for: .seconds(1))
            }
        })
    }

    private func startCamera() async {
        guard await FrontCamera.requestPermission() else {
            callbacks.onNote("camera: no permission -- questions use the 360 camera only")
            return
        }
        do {
            try camera.start()
            callbacks.onNote("camera: front, mirroring off")
        } catch {
            callbacks.onNote("camera: \(error)")
        }
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
