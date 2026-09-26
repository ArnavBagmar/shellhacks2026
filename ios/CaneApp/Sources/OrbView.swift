import SwiftUI

/// The chest orb: a living, Siri-like sphere whose colour, motion and size
/// show what Cane is doing. Built from a moving mesh gradient, drifting
/// blurred light blobs, a glass highlight and a slowly turning rim, with a
/// soft glow behind it. `level` (0...1, the microphone) makes it swell while
/// the wearer talks; while Cane speaks it pulses on its own.
struct OrbView: View {
    let activity: AppModel.Activity
    let level: Double

    var body: some View {
        TimelineView(.animation) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            let style = OrbStyle(activity)
            GeometryReader { geometry in
                let size = min(geometry.size.width, geometry.size.height)
                ZStack {
                    // Glow behind the sphere.
                    mesh(t: t, style: style)
                        .frame(width: size, height: size)
                        .clipShape(Circle())
                        .blur(radius: size * 0.16)
                        .scaleEffect(1.18 + 0.08 * pulse(t, style))
                        .opacity(0.55 * style.glow)

                    sphere(t: t, style: style, size: size)
                        .scaleEffect(1 + 0.035 * pulse(t, style) + 0.14 * voice(t))
                }
                .frame(width: geometry.size.width, height: geometry.size.height)
                .opacity(style.flash ? flash(t) : 1)
            }
        }
        .animation(.easeInOut(duration: 0.6), value: activity)
        .animation(.spring(response: 0.22, dampingFraction: 0.55), value: level)
        .accessibilityHidden(true)
    }

    // MARK: - Layers

    private func sphere(t: Double, style: OrbStyle, size: CGFloat) -> some View {
        ZStack {
            mesh(t: t, style: style)
            blobs(t: t, style: style, size: size)
                .blendMode(.plusLighter)
            // Depth: darker towards the lower edge, like a lit glass ball.
            RadialGradient(
                colors: [.clear, .black.opacity(0.45)],
                center: UnitPoint(x: 0.42, y: 0.35), startRadius: size * 0.2, endRadius: size * 0.62
            )
            // Specular highlight.
            Ellipse()
                .fill(RadialGradient(colors: [.white.opacity(0.55), .white.opacity(0)],
                                     center: .center, startRadius: 0, endRadius: size * 0.2))
                .frame(width: size * 0.42, height: size * 0.26)
                .offset(x: -size * 0.13, y: -size * 0.22)
                .blur(radius: size * 0.02)
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(
            Circle().strokeBorder(
                AngularGradient(colors: style.colors + [style.colors[0]], center: .center,
                                angle: .degrees(t * 40 * style.speed)),
                lineWidth: max(1.5, size * 0.006)
            )
            .opacity(0.7)
            .blendMode(.plusLighter)
        )
        .shadow(color: style.colors[1].opacity(0.45), radius: size * 0.08)
    }

    /// A 3 x 3 mesh whose inner points wander, so colour flows inside.
    private func mesh(t: Double, style: OrbStyle) -> some View {
        let s = t * style.speed
        func wobble(_ phase: Double, _ amount: Double = 0.16) -> Float {
            Float(sin(s * 1.3 + phase) * amount)
        }
        let points: [SIMD2<Float>] = [
            [0, 0], [0.5 + wobble(0.0), 0], [1, 0],
            [0, 0.5 + wobble(1.1)], [0.5 + wobble(2.0, 0.22), 0.5 + wobble(3.1, 0.22)], [1, 0.5 + wobble(4.2)],
            [0, 1], [0.5 + wobble(5.3), 1], [1, 1],
        ]
        let c = style.colors
        let shift = Int(s * 0.35) % c.count
        func color(_ i: Int) -> Color { c[(i + shift) % c.count] }
        return MeshGradient(
            width: 3, height: 3, points: points,
            colors: [color(0), color(1), color(2), color(3), color(4), color(0), color(2), color(3), color(1)],
            smoothsColors: true
        )
    }

    /// Soft light blobs orbiting inside the sphere.
    private func blobs(t: Double, style: OrbStyle, size: CGFloat) -> some View {
        Canvas { context, canvas in
            context.addFilter(.blur(radius: canvas.width * 0.09))
            let centre = CGPoint(x: canvas.width / 2, y: canvas.height / 2)
            let s = t * style.speed
            for (i, colour) in style.colors.enumerated() {
                let phase = Double(i) * 1.7
                let orbit = canvas.width * (0.16 + 0.05 * sin(s * 0.7 + phase))
                let x = centre.x + orbit * cos(s * (0.8 + 0.15 * Double(i)) + phase)
                let y = centre.y + orbit * sin(s * (0.6 + 0.2 * Double(i)) + phase * 1.3)
                let r = canvas.width * (0.2 + 0.04 * sin(s + phase))
                context.fill(Path(ellipseIn: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)),
                             with: .color(colour.opacity(0.5)))
            }
        }
    }

    // MARK: - Motion

    private func pulse(_ t: Double, _ style: OrbStyle) -> Double {
        sin(t * style.breathe)
    }

    /// The microphone while listening; a talking rhythm while Cane speaks.
    private func voice(_ t: Double) -> Double {
        switch activity {
        case .speaking: return 0.35 + 0.3 * abs(sin(t * 7.3)) * abs(sin(t * 3.1 + 1))
        case .listening, .idle: return level
        default: return 0
        }
    }

    private func flash(_ t: Double) -> Double {
        0.55 + 0.45 * (sin(t * 22) > 0 ? 1 : 0)
    }
}

/// Colours and tempo per state.
struct OrbStyle {
    let colors: [Color]
    let speed: Double
    let breathe: Double
    let glow: Double
    let flash: Bool

    init(_ activity: AppModel.Activity) {
        switch activity {
        case .listening, .idle:
            colors = [Color(hue: 0.53, saturation: 0.9, brightness: 1), Color(hue: 0.62, saturation: 0.95, brightness: 1),
                      Color(hue: 0.78, saturation: 0.8, brightness: 1), Color(hue: 0.92, saturation: 0.7, brightness: 1),
                      Color(hue: 0.48, saturation: 0.8, brightness: 0.95)]
            speed = 0.6; breathe = 1.6; glow = 0.8; flash = false
        case .thinking:
            colors = [Color(hue: 0.74, saturation: 0.85, brightness: 1), Color(hue: 0.66, saturation: 0.9, brightness: 1),
                      Color(hue: 0.86, saturation: 0.75, brightness: 1), Color(hue: 0.55, saturation: 0.9, brightness: 1),
                      Color(hue: 0.95, saturation: 0.6, brightness: 1)]
            speed = 2.2; breathe = 3.0; glow = 1.0; flash = false
        case .speaking:
            colors = [Color(hue: 0.52, saturation: 0.75, brightness: 1), Color(hue: 0.58, saturation: 0.95, brightness: 1),
                      Color(hue: 0.5, saturation: 0.35, brightness: 1), Color(hue: 0.45, saturation: 0.85, brightness: 0.95),
                      Color(hue: 0.65, saturation: 0.85, brightness: 1)]
            speed = 1.2; breathe = 2.2; glow = 1.1; flash = false
        case .hazard(let urgency):
            colors = [Color(hue: 0.0, saturation: 0.95, brightness: 1), Color(hue: 0.05, saturation: 0.95, brightness: 1),
                      Color(hue: 0.97, saturation: 0.85, brightness: 0.9), Color(hue: 0.09, saturation: 0.9, brightness: 1),
                      Color(hue: 0.02, saturation: 0.7, brightness: 1)]
            speed = urgency >= 3 ? 3.5 : 2.0; breathe = 8; glow = 1.3; flash = urgency >= 3
        case .offline:
            colors = [Color(hue: 0.1, saturation: 0.55, brightness: 0.75), Color(hue: 0.08, saturation: 0.4, brightness: 0.55),
                      Color(hue: 0.6, saturation: 0.15, brightness: 0.5), Color(hue: 0.12, saturation: 0.5, brightness: 0.65),
                      Color(hue: 0.6, saturation: 0.1, brightness: 0.35)]
            speed = 0.25; breathe = 0.9; glow = 0.35; flash = false
        }
    }
}

#Preview("Listening") {
    OrbView(activity: .listening, level: 0.3).frame(width: 300, height: 300).padding(60).background(.black)
}

#Preview("Thinking") {
    OrbView(activity: .thinking, level: 0).frame(width: 300, height: 300).padding(60).background(.black)
}

#Preview("Hazard") {
    OrbView(activity: .hazard(3), level: 0).frame(width: 300, height: 300).padding(60).background(.black)
}
