import SwiftUI

/// One visual system for every screen: system Liquid Glass (iOS 26) over a cool ambient field.
/// Green/red only for direction and P&L; system blue for interaction; grey for everything else.
extension Color {
    static let up = Color(red: 0.12, green: 0.62, blue: 0.33)
    static let down = Color(red: 0.90, green: 0.22, blue: 0.23)
    static let amber = Color(red: 0.64, green: 0.37, blue: 0.0)
    /// Bright surfaces (owner 2026-11-07 “灰朦朦的”): inputs and sheet canvas, light and slightly blue.
    static let field = Color(light: Color(red: 0.937, green: 0.953, blue: 0.980), dark: Color.white.opacity(0.08))
    static let canvas = Color(light: Color(red: 0.965, green: 0.973, blue: 0.990), dark: Color(red: 0.06, green: 0.07, blue: 0.10))
}

extension Color {
    init(light: Color, dark: Color) {
        self.init(UIColor { $0.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light) })
    }
}

extension Fmt.Tone {
    var color: Color { switch self { case .up: .up; case .down: .down; case .flat: .secondary } }
}

/// Clean cool-white field with soft blue and teal light (Stitch monitor reference #EEF2F7).
/// Static and drawn once behind every page: no per-frame work under the cards (owner 2026-11-07:
/// “灰色不好看 / 点进去卡卡的”).
struct AmbientBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        ZStack {
            (scheme == .dark ? Color(red: 0.04, green: 0.06, blue: 0.10) : Color(red: 0.937, green: 0.953, blue: 0.976))
            RadialGradient(colors: [Color(red: 0.04, green: 0.52, blue: 1).opacity(scheme == .dark ? 0.26 : 0.17), .clear],
                           center: UnitPoint(x: 0.0, y: 0.0), startRadius: 0, endRadius: 420)
            RadialGradient(colors: [Color(red: 0.19, green: 0.69, blue: 0.78).opacity(scheme == .dark ? 0.18 : 0.13), .clear],
                           center: UnitPoint(x: 1.05, y: 0.34), startRadius: 0, endRadius: 360)
            RadialGradient(colors: [Color(red: 0.19, green: 0.82, blue: 0.35).opacity(scheme == .dark ? 0.08 : 0.06), .clear],
                           center: UnitPoint(x: 0.1, y: 0.85), startRadius: 0, endRadius: 320)
        }
        // No drawingGroup(): it renders only inside the layout frame and clipped the safe-area
        // extension, which showed as white bands under the status bar and home indicator.
        .ignoresSafeArea()
    }
}

/// Bright frosted white card: translucent white over a thin material, white rim, soft long shadow.
/// Liquid Glass stays on controls (tab bar, toolbar, segmented, banner) as Apple recommends.
struct FrostedCard: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    let cornerRadius: CGFloat
    let tint: Color?
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        content
            .background {
                shape.fill(.ultraThinMaterial)
                    .overlay(shape.fill(scheme == .dark ? Color.white.opacity(0.06) : Color.white.opacity(0.93)))
                    .overlay(shape.fill((tint ?? .clear).opacity(scheme == .dark ? 0.18 : 0.08)))
                    .overlay(shape.strokeBorder(LinearGradient(colors: [.white.opacity(scheme == .dark ? 0.16 : 0.95), .white.opacity(scheme == .dark ? 0.04 : 0.45)],
                                                               startPoint: .top, endPoint: .bottom), lineWidth: 1))
                    .shadow(color: Color(red: 0.06, green: 0.13, blue: 0.25).opacity(scheme == .dark ? 0.35 : 0.06), radius: 12, y: 4)
            }
    }
}

extension View {
    func glassCard(cornerRadius: CGFloat = 24, tint: Color? = nil, interactive: Bool = false) -> some View {
        modifier(FrostedCard(cornerRadius: cornerRadius, tint: tint))
    }
    /// Frosted capsule for small chips and notes.
    func frostedCapsule(tint: Color? = nil) -> some View { modifier(FrostedCard(cornerRadius: 99, tint: tint)) }

    /// Light entrance as rows scroll in (no blur: it is expensive on every frame).
    /// Cards taller than the screen are never "fully visible", so the old transition left long lists
    /// at 60 % opacity — the grey 今日触价 / 回测复刻 cards (owner Notion #76). Now a card counts as
    /// fully shown once 5 % of it is on screen, and only fades slightly while entering.
    func scrollReveal() -> some View {
        scrollTransition(.animated(.smooth(duration: 0.25)).threshold(.visible(0.05)), axis: .vertical) { content, phase in
            content.opacity(phase.isIdentity ? 1 : 0.85).scaleEffect(phase.isIdentity ? 1 : 0.99)
        }
    }
}

/// Cards spring down slightly under the finger.
struct CardPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.spring(response: 0.28, dampingFraction: 0.72), value: configuration.isPressed)
    }
}

struct SectionHeader: View {
    let title: String
    var count: Int? = nil
    var trailing: String? = nil
    var body: some View {
        HStack(spacing: 8) {
            Text(title).font(.title3.weight(.bold)).fontDesign(.rounded)
            if let count {
                Text("\(count)").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    .padding(.horizontal, 8).padding(.vertical, 2).background(.quaternary, in: .capsule)
            }
            Spacer()
            if let trailing { Text(trailing).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(.horizontal, 4)
        .padding(.top, 8)
    }
}

struct Pill: View {
    let text: String
    var color: Color = .secondary
    var body: some View {
        Text(text).font(.caption2.weight(.semibold)).foregroundStyle(color)
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.13), in: .capsule)
    }
}

struct SidePill: View {
    let side: Side
    var body: some View { Pill(text: side == .long ? "做多" : "做空", color: side == .long ? .up : .down) }
}

struct StatusNote: View {
    let text: String
    var systemImage: String = "checkmark.shield"
    var tint: Color = .up
    var body: some View {
        Label(text, systemImage: systemImage)
            .font(.footnote.weight(.medium)).foregroundStyle(.primary)
            .labelStyle(TintedIconLabel(tint: tint))
            .padding(.horizontal, 14).padding(.vertical, 9)
            .frostedCapsule(tint: tint)
    }
}

private struct TintedIconLabel: LabelStyle {
    let tint: Color
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 6) { configuration.icon.foregroundStyle(tint); configuration.title }
    }
}

/// Wrapping layout for coin chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 7
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > width, x > 0 { x = 0; y += line + spacing; line = 0 }
            x += size.width + spacing; line = max(line, size.height)
        }
        return CGSize(width: width == .infinity ? x : width, height: y + line)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX { x = bounds.minX; y += line + spacing; line = 0 }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing; line = max(line, size.height)
        }
    }
}

/// Open a coin on the full-screen 1.618 chart (monitor rows, alarm coins, cloud cards, positions).
struct OpenChartAction {
    var run: (String) -> Void = { _ in }
    var runMonthlyTouch: (MonthFirstWeekTouch) -> Void = { _ in }
    func callAsFunction(_ symbol: String) { run(symbol) }
    func monthlyTouch(_ event: MonthFirstWeekTouch) { runMonthlyTouch(event) }
}
private struct OpenChartKey: EnvironmentKey { static let defaultValue = OpenChartAction() }
extension EnvironmentValues {
    var openChart: OpenChartAction { get { self[OpenChartKey.self] } set { self[OpenChartKey.self] = newValue } }
}
