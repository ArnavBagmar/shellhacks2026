import CaneAssist
import Foundation
import Observation
import UIKit

/// UI state for the two screens. Owns one `Runtime` per walk.
@MainActor
@Observable
final class AppModel {
    enum Mode { case setup, walking }

    /// What the chest reactor shows, most important first.
    enum Activity: Equatable {
        case offline, idle, listening, thinking, speaking, hazard(Int)
    }

    let settings = AppSettings()
    private(set) var mode: Mode = .setup
    private(set) var connected = false
    private(set) var speaking = false
    private(set) var assist: QueryCoordinator.State = .idle
    private(set) var hazardUrgency: Int?
    private(set) var lastHeard = ""
    private(set) var lastSaid = ""
    /// Microphone loudness 0...1 (drives the orb).
    private(set) var level = 0.0
    private(set) var notes: [String] = []
    /// Live TrueDepth reading for the debug panel; nil when this phone
    /// has no depth sensor or none has arrived yet.
    private(set) var depthLine: String?

    private var runtime: Runtime?
    private var hazardClear: Task<Void, Never>?
    private let settingsBox: SettingsBox

    init() {
        settingsBox = SettingsBox(AppSettings().snapshot)
    }

    var activity: Activity {
        if let hazardUrgency { return .hazard(hazardUrgency) }
        if !connected { return .offline }
        if speaking { return .speaking }
        if case .looking = assist { return .thinking }
        return mode == .walking ? .listening : .idle
    }

    func startWalking() async {
        guard mode == .setup else { return }
        guard let url = settings.laptopURL else {
            note("set the laptop address first")
            return
        }
        settingsBox.update(settings.snapshot)
        notes.removeAll()
        depthLine = nil
        let runtime = Runtime(url: url, settings: settingsBox, callbacks: callbacks())
        self.runtime = runtime
        mode = .walking
        UIApplication.shared.isIdleTimerDisabled = true
        await runtime.start()
    }

    func stopWalking() {
        runtime?.stop()
        runtime = nil
        mode = .setup
        connected = false
        speaking = false
        level = 0
        lastHeard = ""
        lastSaid = ""
        assist = .idle
        UIApplication.shared.isIdleTimerDisabled = false
    }

    func toggleWalking() async {
        if mode == .walking { stopWalking() } else { await startWalking() }
    }

    func settingsChanged() {
        settingsBox.update(settings.snapshot)
    }

    func typed(_ text: String) {
        runtime?.typed(text)
    }

    // MARK: - Runtime callbacks (arrive on any thread)

    private func callbacks() -> Runtime.Callbacks {
        Runtime.Callbacks(
            onConnected: { [weak self] value in Task { @MainActor in self?.connected = value } },
            onSpeaking: { [weak self] value in Task { @MainActor in self?.speaking = value } },
            onHazard: { [weak self] urgency in Task { @MainActor in self?.flashHazard(urgency) } },
            onHeard: { [weak self] text in Task { @MainActor in self?.lastHeard = text } },
            onSaid: { [weak self] text in Task { @MainActor in self?.lastSaid = text } },
            onLevel: { [weak self] value in Task { @MainActor in self?.level = value } },
            onAssist: { [weak self] state in Task { @MainActor in self?.assist = state } },
            onNote: { [weak self] text in Task { @MainActor in self?.note(text) } },
            onDepth: { [weak self] line in Task { @MainActor in self?.showDepth(line) } }
        )
    }

    private func flashHazard(_ urgency: Int) {
        hazardUrgency = urgency
        hazardClear?.cancel()
        hazardClear = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(urgency >= 3 ? 1_500 : 900))
            guard !Task.isCancelled else { return }
            self?.hazardUrgency = nil
        }
    }

    private func note(_ text: String) {
        notes.append(text)
        if notes.count > 12 { notes.removeFirst(notes.count - 12) }
    }

    func showDepth(_ line: String) {
        depthLine = line
    }
}
