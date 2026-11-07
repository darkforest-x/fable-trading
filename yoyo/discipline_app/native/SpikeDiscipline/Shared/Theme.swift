import SwiftUI

/// One visual system for every screen: system Liquid Glass (iOS 26) over a cool ambient field.
/// Green/red only for direction and P&L; system blue for interaction; grey for everything else.
extension Color {
    static let up = Color(red: 0.12, green: 0.62, blue: 0.33)
    static let down = Color(red: 0.90, green: 0.22, blue: 0.23)
    static let amber = Color(red: 0.64, green: 0.37, blue: 0.0)
}

extension Fmt.Tone {
    var color: Color { switch self { case .up: .up; case .down: .down; case .flat: .secondary } }
}

/// Slowly flowing mesh of sky blue, lavender and mint: something for clear glass to refract
/// (owner 2026-11-07 “玻璃更通透 / 配色与背景”). Static when Reduce Motion is on.
struct AmbientBackground: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { context in
            let t = reduceMotion ? 0 : context.date.timeIntervalSinceReferenceDate
            MeshGradient(width: 3, height: 3, points: points(t), colors: scheme == .dark ? dark : light, smoothsColors: true)
        }
        .ignoresSafeArea()
    }

    private func points(_ t: Double) -> [SIMD2<Float>] {
        func w(_ speed: Double, _ phase: Double, _ amount: Float) -> Float { Float(sin(t * speed + phase)) * amount }
        return [
            [0, 0], [0.5 + w(0.21, 0, 0.12), 0], [1, 0],
            [0, 0.5 + w(0.17, 1, 0.1)], [0.5 + w(0.13, 2, 0.14), 0.45 + w(0.19, 3, 0.12)], [1, 0.5 + w(0.23, 4, 0.1)],
            [0, 1], [0.5 + w(0.15, 5, 0.12), 1], [1, 1],
        ]
    }

    private let light: [Color] = [
        Color(red: 0.80, green: 0.87, blue: 1.00), Color(red: 0.90, green: 0.93, blue: 1.00), Color(red: 0.86, green: 0.84, blue: 1.00),
        Color(red: 0.88, green: 0.95, blue: 1.00), Color(red: 0.97, green: 0.98, blue: 1.00), Color(red: 0.84, green: 0.91, blue: 1.00),
        Color(red: 0.82, green: 0.95, blue: 0.90), Color(red: 0.93, green: 0.95, blue: 1.00), Color(red: 0.88, green: 0.86, blue: 0.99),
    ]
    private let dark: [Color] = [
        Color(red: 0.05, green: 0.09, blue: 0.20), Color(red: 0.06, green: 0.07, blue: 0.14), Color(red: 0.12, green: 0.08, blue: 0.22),
        Color(red: 0.04, green: 0.12, blue: 0.18), Color(red: 0.03, green: 0.04, blue: 0.08), Color(red: 0.07, green: 0.10, blue: 0.22),
        Color(red: 0.03, green: 0.15, blue: 0.13), Color(red: 0.05, green: 0.06, blue: 0.12), Color(red: 0.10, green: 0.07, blue: 0.18),
    ]
}

extension View {
    /// Content card in clear system glass (more transparent than .regular); optional colour tint.
    func glassCard(cornerRadius: CGFloat = 24, tint: Color? = nil, interactive: Bool = false) -> some View {
        var glass: Glass = .clear
        if let tint { glass = glass.tint(tint.opacity(0.18)) }
        if interactive { glass = glass.interactive() }
        return self.glassEffect(glass, in: .rect(cornerRadius: cornerRadius))
    }

    /// Cards and rows ease in as they scroll into view.
    func scrollReveal() -> some View {
        scrollTransition(.interactive, axis: .vertical) { content, phase in
            content.opacity(phase.isIdentity ? 1 : 0.35).scaleEffect(phase.isIdentity ? 1 : 0.96).blur(radius: phase.isIdentity ? 0 : 1.5)
        }
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
            .glassEffect(.clear.tint(tint.opacity(0.10)), in: .capsule)
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

/// Bundled public OKX coin artwork (shared with the 1.x app), falling back to initials.
struct CoinLogo: View {
    let coin: String
    var size: CGFloat = 32
    private static var cache: [String: UIImage] = [:]
    private var image: UIImage? {
        let key = coin.lowercased()
        if let hit = Self.cache[key] { return hit }
        guard let url = Bundle.main.url(forResource: key, withExtension: "png", subdirectory: "coins"),
              let image = UIImage(contentsOfFile: url.path) else { return nil }
        Self.cache[key] = image
        return image
    }
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() }
            else { Text(String(coin.prefix(2))).font(.system(size: size * 0.36, weight: .bold)).foregroundStyle(.secondary) }
        }
        .frame(width: size, height: size)
        .background(.quaternary.opacity(0.6), in: .circle)
        .clipShape(.circle)
    }
}
