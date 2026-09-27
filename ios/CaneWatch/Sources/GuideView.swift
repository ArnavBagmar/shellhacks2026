import CaneGuide
import SwiftUI

/// One glanceable face: what Cane is guiding to, how far, which way. The
/// wearer feels the taps; the face is for whoever is watching the demo.
struct GuideView: View {
    let model: GuideModel

    var body: some View {
        VStack(spacing: 6) {
            if let message = model.message {
                Text(message.target.uppercased())
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                ZStack {
                    Circle().stroke(.gray.opacity(0.3), lineWidth: 8)
                    Circle()
                        .trim(from: 0, to: closeness(message))
                        .stroke(colour(message), style: StrokeStyle(lineWidth: 8, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                        .animation(.easeOut(duration: 0.15), value: closeness(message))
                    Text(headline(message))
                        .font(.system(.title2, design: .rounded).weight(.bold))
                        .monospacedDigit()
                        .minimumScaleFactor(0.6)
                        .lineLimit(1)
                        .padding(.horizontal, 10)
                }
                .frame(maxWidth: 120, maxHeight: 120)
                Text(message.state == .guiding ? (message.hint ?? " ") : " ")
                    .font(.headline)
            } else {
                Image(systemName: "hand.point.up.left")
                    .font(.largeTitle)
                Text("Ask Cane to guide your hand")
                    .font(.headline)
                    .multilineTextAlignment(.center)
                Text(model.reachable ? "Phone connected" : "Waiting for the phone")
                    .font(.caption2)
                    .foregroundStyle(model.reachable ? .green : .secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func headline(_ m: GuideMessage) -> String {
        switch m.state {
        case .looking: "Looking"
        case .findHand: "Reach out"
        case .guiding: m.distanceText ?? "Closer"
        case .reached: "Got it"
        case .lost: "Lost it"
        case .stopped: "Stopped"
        }
    }

    /// 0 at `GuidePulse.farM` or beyond, 1 touching.
    private func closeness(_ m: GuideMessage) -> Double {
        if m.state == .reached { return 1 }
        guard m.state == .guiding, let gap = m.gapM else { return 0 }
        return 1 - min(max(gap / GuidePulse.farM, 0), 1)
    }

    private func colour(_ m: GuideMessage) -> Color {
        if m.state == .reached { return .green }
        guard let gap = m.gapM else { return .gray }
        return gap <= GuidePulse.nearM ? .green : .yellow
    }
}
