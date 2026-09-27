import CaneFeedback
import CaneOutput
import SwiftUI

/// Before the phone goes on the chest: a sighted helper sets the laptop
/// address and the two comfort knobs, runs the haptic self-test with the
/// wearer, then starts the walk. Everything here is persisted.
struct SetupView: View {
    @Environment(AppModel.self) private var model
    @State private var testingHaptics = false

    var body: some View {
        @Bindable var settings = model.settings
        NavigationStack {
            Form {
                Section {
                    Button {
                        Task { await model.startWalking() }
                    } label: {
                        Label("Start walking", systemImage: "figure.walk")
                            .font(.title3.weight(.semibold))
                            .frame(maxWidth: .infinity, minHeight: 56)
                    }
                    .buttonStyle(.borderedProminent)
                    .listRowInsets(EdgeInsets())
                } footer: {
                    Text("Then strap the phone on, screen facing out. Say “Hey Cane, is there a bathroom around?”. Two-finger double-tap stops.")
                }

                Section("Laptop link") {
                    LabeledContent("Address") {
                        TextField("172.20.10.2", text: $settings.laptopHost)
                            // Letters too: an IPv6 address (T-Mobile hotspots
                            // are IPv6-only) has hex digits a-f.
                            .keyboardType(.asciiCapable)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .multilineTextAlignment(.trailing)
                    }
                    LabeledContent("Port") {
                        TextField("8765", value: $settings.laptopPort, format: .number.grouping(.never))
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                    }
                }

                Section {
                    LabeledContent("Speech rate", value: settings.speechRate.formatted(.number.precision(.fractionLength(2))))
                    Slider(value: $settings.speechRate, in: 0.45...0.70, step: 0.01)
                        .accessibilityLabel("Speech rate")
                    LabeledContent("Haptic strength", value: settings.hapticGain.formatted(.number.precision(.fractionLength(1))))
                    Slider(value: $settings.hapticGain, in: 0.6...1.4, step: 0.1)
                        .accessibilityLabel("Haptic strength")
                    Toggle("Natural voice for answers", isOn: $settings.naturalVoice)
                    Toggle("Show the phone camera on the laptop", isOn: $settings.cameraOnLaptop)
                    Button(testingHaptics ? "Playing…" : "Play the haptic alphabet") {
                        testingHaptics = true
                        Task {
                            await HapticReference.playAlphabet(gain: Float(settings.hapticGain), rate: Float(settings.speechRate))
                            testingHaptics = false
                        }
                    }
                    .disabled(testingHaptics)
                } header: {
                    Text("Comfort")
                } footer: {
                    Text("Two clicks is left, three quick clicks is right. A short bump is ahead, a long swell is behind. A long hard rattle means act now. The natural voice (ElevenLabs, through the laptop) speaks answers; warnings always use the phone's own voice. With the phone camera on the laptop, the live view shows what the chest camera sees; off, photos leave the phone only when you ask something.")
                }

                if !model.notes.isEmpty {
                    Section("Last walk") {
                        ForEach(Array(model.notes.enumerated()), id: \.offset) { _, line in
                            Text(line).font(.caption.monospaced())
                        }
                    }
                }
            }
            .navigationTitle("Cane")
            .onChange(of: settings.speechRate) { model.settingsChanged() }
            .onChange(of: settings.hapticGain) { model.settingsChanged() }
            .onChange(of: settings.naturalVoice) { model.settingsChanged() }
            .onChange(of: settings.cameraOnLaptop) { model.settingsChanged() }
        }
        .accessibilityAction(.magicTap) { Task { await model.startWalking() } }
    }
}

/// The pre-walk self-test (step-10 design Amendment 1): every symbol with
/// its spoken name. Also how the wearer tunes haptic strength.
@MainActor
enum HapticReference {
    static func playAlphabet(gain: Float, rate: Float) async {
        AudioSessionConfig.configure()
        let haptics = CoreHapticsRenderer()
        let speech = AVSpeechRenderer(rate: { rate })
        let alphabet: [(HapticSymbol, String)] = [
            (.ahead, "Ahead"), (.left, "Left"), (.right, "Right"), (.behind, "Behind"), (.alarm, "Act now"),
        ]
        for (symbol, name) in alphabet {
            await speech.speak(SpokenPhrase(text: name))
            try? await Task.sleep(for: .milliseconds(250))
            for _ in 0..<2 {
                await haptics.play(HapticScript.hazard(symbol, level: symbol == .alarm ? .immediate : .actSoon,
                                                       userGain: Double(gain)))
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }
}
