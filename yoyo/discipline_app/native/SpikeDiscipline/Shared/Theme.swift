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

struct AmbientBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        ZStack {
            (scheme == .dark ? Color(red: 0.04, green: 0.06, blue: 0.09) : Color(red: 0.93, green: 0.95, blue: 0.97))
            RadialGradient(colors: [Color.blue.opacity(scheme == .dark ? 0.22 : 0.16), .clear], center: .init(x: 0.05, y: 0.02), startRadius: 0, endRadius: 360)
            RadialGradient(colors: [Color.teal.opacity(scheme == .dark ? 0.16 : 0.14), .clear], center: .init(x: 1.05, y: 0.32), startRadius: 0, endRadius: 320)
            RadialGradient(colors: [Color.green.opacity(0.07), .clear], center: .init(x: 0.1, y: 0.8), startRadius: 0, endRadius: 300)
        }
        .ignoresSafeArea()
    }
}

extension View {
    /// Content card in system glass.
    func glassCard(cornerRadius: CGFloat = 24, tint: Color? = nil) -> some View {
        self.glassEffect(tint.map { Glass.regular.tint($0.opacity(0.16)) } ?? .regular, in: .rect(cornerRadius: cornerRadius))
    }
}

struct SectionHeader: View {
    let title: String
    var count: Int? = nil
    var trailing: String? = nil
    var body: some View {
        HStack(spacing: 8) {
            Text(title).font(.title3.weight(.bold))
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
            .glassEffect(.regular.tint(tint.opacity(0.08)), in: .capsule)
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
