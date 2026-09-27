import SwiftUI

/// The walking screen. It faces *out* from the wearer's chest, so it is for
/// the people around them: the orb shows what Cane is doing, the captions
/// show the conversation, and a plain notice says a camera is on.
///
/// For the wearer it is inert: one accessibility element, a two-finger
/// double-tap (Magic Tap) to stop, no live regions -- VoiceOver must never
/// talk over hazard speech (step-10 design §3 Q7).
struct ChestDisplayView: View {
    @Environment(AppModel.self) private var model
    @State private var showDebug = false
    @State private var command = ""

    var body: some View {
        ZStack {
            background
            VStack(spacing: 0) {
                cameraNotice
                    .padding(.top, 12)
                if let problem = model.linkProblem {
                    linkBanner(problem)
                        .padding(.top, 16)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }
                Spacer(minLength: 16)
                // Small while the debug panel (and its map) is open, so both fit.
                OrbView(activity: model.activity, level: model.level)
                    .frame(maxWidth: showDebug ? 90 : 300, maxHeight: showDebug ? 90 : 300)
                    .aspectRatio(1, contentMode: .fit)
                    .padding(showDebug ? 8 : 40)
                statusLine
                    .padding(.top, 4)
                Spacer(minLength: 16)
                captions
                    .padding(.bottom, 24)
                if showDebug { debugPanel }
            }
            .padding(.horizontal, 24)
        }
        .animation(.snappy, value: model.linkProblem)
        .contentShape(Rectangle())
        .onTapGesture(count: 3) { withAnimation(.snappy) { showDebug.toggle() } }
        .onChange(of: showDebug) { _, shown in model.watchNavigation(shown) }
        .onDisappear { model.watchNavigation(false) }
        .onLongPressGesture(minimumDuration: 2) { model.stopWalking() }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Cane is running. Two-finger double-tap to stop.")
        .accessibilityAddTraits(.isModal)
        .accessibilityAction(.magicTap) { model.stopWalking() }
    }

    /// Near-black with a faint wash of the orb's colour.
    private var background: some View {
        ZStack {
            Color.black
            RadialGradient(
                colors: [OrbStyle(model.activity).colors[1].opacity(0.22), .clear],
                center: .center, startRadius: 40, endRadius: 520
            )
            .animation(.easeInOut(duration: 0.8), value: model.activity)
        }
        .ignoresSafeArea()
    }

    private var cameraNotice: some View {
        HStack(spacing: 8) {
            Circle().fill(Color.red).frame(width: 7, height: 7)
            Text("Camera on · assistive device for a blind walker")
                .font(.footnote.weight(.medium))
                .foregroundStyle(.white.opacity(0.5))
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.white.opacity(0.06), in: Capsule())
    }

    /// Big enough to read from across a room: the wearer cannot see it, so
    /// it is for whoever is helping (the same warning is also spoken).
    private func linkBanner(_ problem: Runtime.LinkProblem) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 34, weight: .bold))
            VStack(alignment: .leading, spacing: 4) {
                Text("NOT CONNECTED · \(problem.title.uppercased())")
                    .font(.system(.headline, design: .rounded).weight(.heavy))
                Text(problem.detail)
                    .font(.system(.subheadline, design: .rounded))
                    .opacity(0.9)
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(16)
        .frame(maxWidth: 520)
        .background(Color.red.opacity(0.85), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var statusLine: some View {
        Text(statusText)
            .font(.system(.subheadline, design: .rounded).weight(.semibold))
            .tracking(4)
            .foregroundStyle(.white.opacity(0.75))
            .contentTransition(.opacity)
            .animation(.easeInOut(duration: 0.25), value: statusText)
    }

    /// The last exchange, Siri-style: what was heard, then Cane's reply.
    private var captions: some View {
        VStack(spacing: 10) {
            if !model.lastHeard.isEmpty {
                Text(model.lastHeard)
                    .font(.system(.title3, design: .rounded))
                    .foregroundStyle(.white.opacity(0.55))
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                    .id("heard-\(model.lastHeard)")
            }
            if !model.lastSaid.isEmpty {
                Text(model.lastSaid)
                    .font(.system(.title2, design: .rounded).weight(.semibold))
                    .foregroundStyle(.white)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                    .id("said-\(model.lastSaid)")
            }
        }
        .multilineTextAlignment(.center)
        .lineLimit(3)
        .minimumScaleFactor(0.7)
        .frame(maxWidth: 520)
        .animation(.smooth(duration: 0.35), value: model.lastHeard)
        .animation(.smooth(duration: 0.35), value: model.lastSaid)
    }

    private var statusText: String {
        switch model.activity {
        case .offline: return model.linkProblem == nil ? "CONNECTING" : "NO LAPTOP"
        case .idle: return "READY"
        case .listening: return "SAY “HEY CANE”"
        case .thinking: return "LOOKING AROUND"
        case .speaking: return "SPEAKING"
        case .hazard(let u): return u >= 3 ? "STOP" : "HAZARD"
        }
    }

    private var debugPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let nav = model.navDebug {
                NavigationMapView(debug: nav)
            } else {
                Text("Not navigating. Say “Hey Cane, navigate me to …” and the route shows here.")
                    .font(.caption).foregroundStyle(.white.opacity(0.5))
            }
            HStack {
                TextField("Type a command, e.g. is there a bathroom around", text: $command)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.send)
                    .onSubmit(send)
                Button("Send", action: send)
            }
            if let depth = model.depthLine {
                Text(depth).font(.callout.monospaced().bold()).foregroundStyle(.cyan)
            }
            ForEach(Array(model.notes.suffix(model.navDebug == nil ? 12 : 3).enumerated()), id: \.offset) { _, line in
                Text(line).font(.caption.monospaced()).foregroundStyle(.white.opacity(0.6))
            }
            Text("Long-press 2 s to stop · triple-tap hides this")
                .font(.caption2).foregroundStyle(.white.opacity(0.4))
        }
        .padding(12)
        .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 14))
        .padding(.bottom, 12)
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    private func send() {
        let text = command.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        model.typed(text)
        command = ""
    }
}

#Preview {
    OrbView(activity: .speaking, level: 0)
        .frame(width: 300, height: 300)
        .background(.black)
}
