import SwiftUI

extension Font {
    /// A design size that follows the system text size (owner 2026-11-08 item 7 “系统调大字号”),
    /// capped at 1.35× so floating chips and docks keep their shape. Chart canvas labels and icons
    /// in fixed-size buttons keep `.system(size:)` on purpose.
    static func scaled(size: CGFloat, weight: Font.Weight = .regular, design: Font.Design = .default) -> Font {
        .system(size: min(size * 1.35, UIFontMetrics.default.scaledValue(for: size)), weight: weight, design: design)
    }
}
import UIKit

/// One minimal design system (owner 2026-11-07 “简约，好看，iPhone 毛玻璃，色调统一”; Stitch native-2.3.0).
/// Frosted surfaces are drawn by the app — material + white veil + soft shadow, never a dark outline —
/// so they look the same with Increase Contrast / Reduce Transparency, where system Liquid Glass
/// buttons grow grey borders (owner screenshots 2026-11-07).
extension Color {
    static let ink = Color(light: Color(red: 0.067, green: 0.094, blue: 0.153), dark: .white)
    static let muted = Color(light: Color(red: 0.42, green: 0.45, blue: 0.50), dark: Color.white.opacity(0.6))
}

struct Frosted<S: Shape>: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    let shape: S
    var veil: Double = 0.62
    var shadow = true
    func body(content: Content) -> some View {
        content.background {
            shape.fill(.ultraThinMaterial)
                .overlay(shape.fill(scheme == .dark ? Color.white.opacity(0.06) : Color.white.opacity(veil)))
                .overlay(shape.stroke(Color.white.opacity(scheme == .dark ? 0.08 : 0.7), lineWidth: 0.5))
                .shadow(color: Color(red: 0.06, green: 0.09, blue: 0.16).opacity(shadow ? (scheme == .dark ? 0.4 : 0.08) : 0), radius: 14, y: 5)
        }
    }
}

extension View {
    /// Floating frosted control (chart overlays, tab bar, dock).
    func frosted<S: Shape>(_ shape: S, veil: Double = 0.62, shadow: Bool = true) -> some View {
        modifier(Frosted(shape: shape, veil: veil, shadow: shadow))
    }
}

/// Pill buttons: soft (light tinted), tinted (accent/color at 12 %), solid (one primary per screen),
/// frosted (over the chart). Disabled keeps the hue at reduced opacity — never grey, never outlined.
struct PillButtonStyle: ButtonStyle {
    enum Kind { case soft, tinted(Color), solid(Color), frosted, plain }
    var kind: Kind = .soft
    var height: CGFloat? = nil
    func makeBody(configuration: Configuration) -> some View { PillBody(configuration: configuration, kind: kind, height: height) }

    private struct PillBody: View {
        let configuration: Configuration
        let kind: Kind
        let height: CGFloat?
        @Environment(\.isEnabled) private var enabled
        var body: some View {
            configuration.label
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(foreground)
                .padding(.horizontal, 14)
                .frame(minHeight: height ?? 34)
                .background { background }
                .contentShape(.capsule)
                .opacity(enabled ? 1 : disabledOpacity)
                .scaleEffect(configuration.isPressed ? 0.96 : 1)
                .animation(.spring(response: 0.25, dampingFraction: 0.75), value: configuration.isPressed)
        }
        private var disabledOpacity: Double { if case .solid = kind { return 0.38 } else { return 0.45 } }
        private var foreground: Color {
            switch kind {
            case .soft, .frosted, .plain: .ink
            case .tinted(let c): c
            case .solid: .white
            }
        }
        @ViewBuilder private var background: some View {
            switch kind {
            case .soft: Capsule().fill(Color.field)
            case .tinted(let c): Capsule().fill(c.opacity(0.12))
            case .solid(let c): Capsule().fill(c).shadow(color: c.opacity(0.28), radius: 8, y: 3)
            case .frosted: Capsule().fill(.clear).frosted(Capsule())
            case .plain: Color.clear
            }
        }
    }
}

extension ButtonStyle where Self == PillButtonStyle {
    static func pill(_ kind: PillButtonStyle.Kind = .soft, height: CGFloat? = nil) -> PillButtonStyle { PillButtonStyle(kind: kind, height: height) }
}

/// Round icon button (close, bell, gear) — frosted circle, no outline.
struct RoundIcon: View {
    let systemName: String
    var size: CGFloat = 36
    var badge = false
    var action: () -> Void
    var body: some View {
        Button(action: { Haptics.tap(); action() }) {
            Image(systemName: systemName).font(.system(size: size * 0.42, weight: .semibold)).foregroundStyle(Color.ink)
                .frame(width: size, height: size)
                .frosted(Circle(), veil: 0.75)
                .overlay(alignment: .topTrailing) { if badge { Circle().fill(.red).frame(width: 8, height: 8).offset(x: -6, y: 6) } }
                .contentShape(.circle)
        }
        .buttonStyle(.plain)
    }
}

/// Sheet close button.
struct CloseButton: View {
    var action: () -> Void
    var body: some View { RoundIcon(systemName: "xmark", size: 32, action: action).accessibilityLabel("关闭") }
}

/// Coin artwork: bundled public OKX icons → OKX public CDN → coloured monogram.
struct CoinLogo: View {
    let coin: String
    var size: CGFloat = 32
    @State private var remote: UIImage?
    @Environment(\.colorScheme) private var scheme
    private static var cache: [String: UIImage] = [:]
    private static var missing: Set<String> = []

    private var bundled: UIImage? {
        let key = coin.lowercased()
        if let hit = Self.cache[key] { return hit }
        guard let url = Bundle.main.url(forResource: key, withExtension: "png", subdirectory: "coins"),
              let image = UIImage(contentsOfFile: url.path) else { return nil }
        Self.cache[key] = image
        return image
    }

    var body: some View {
        Group {
            if let image = bundled ?? remote { Image(uiImage: image).resizable().scaledToFit() }
            else { monogram }
        }
        .frame(width: size, height: size)
        // Dark mode: a light disc behind the logo so dark glyphs (XRP, NEAR, ADA) stay visible
        // (Stitch c8bea04c… “深色模式币种图标”). Full-colour round logos cover it.
        .background { if scheme == .dark { Circle().fill(Color.white.opacity(0.92)) } }
        .clipShape(.circle)
        .task(id: coin) { await loadRemote() }
    }

    private var monogram: some View {
        let hue = Double(coin.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0xffff } % 360) / 360
        return ZStack {
            Circle().fill(LinearGradient(colors: [Color(hue: hue, saturation: 0.55, brightness: 0.95), Color(hue: hue, saturation: 0.75, brightness: 0.75)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
            Text(String(coin.prefix(coin.count > 3 ? 2 : 3))).font(.scaled(size: size * (coin.count > 2 ? 0.30 : 0.36), weight: .heavy, design: .rounded))
                .foregroundStyle(.white).minimumScaleFactor(0.5).padding(2)
        }
    }

    private func loadRemote() async {
        let key = coin.lowercased()
        guard bundled == nil, !key.isEmpty, !Self.missing.contains(key) else { return }
        guard let url = URL(string: "https://static.okx.com/cdn/oksupport/asset/currency/icon/\(key.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? key).png") else { return }
        var request = URLRequest(url: url, cachePolicy: .returnCacheDataElseLoad, timeoutInterval: 8)
        request.setValue("image/png", forHTTPHeaderField: "Accept")
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200, let image = UIImage(data: data) else {
            Self.missing.insert(key); return
        }
        Self.cache[key] = image
        remote = image
    }
}

/// Soft segmented control: light track, white thumb with a soft shadow sliding between options.
struct SoftSegment<T: Hashable>: View {
    @Binding var selection: T
    let options: [(T, String)]
    var height: CGFloat = 36
    @Namespace private var thumb
    var body: some View {
        HStack(spacing: 0) {
            ForEach(options, id: \.0) { option in
                let on = option.0 == selection
                Button {
                    guard !on else { return }
                    Haptics.select()
                    withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) { selection = option.0 }
                } label: {
                    Text(option.1).font(.scaled(size: 14, weight: on ? .semibold : .medium))
                        .foregroundStyle(on ? Color.ink : Color.muted)
                        .frame(maxWidth: .infinity).frame(height: height - 6)
                        .background {
                            if on {
                                Capsule().fill(Color(light: .white, dark: Color.white.opacity(0.16)))
                                    .shadow(color: .black.opacity(0.08), radius: 6, y: 2)
                                    .matchedGeometryEffect(id: "thumb", in: thumb)
                            }
                        }
                        .contentShape(.capsule)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .padding(3)
        .background(Color.field, in: .capsule)
    }
}


extension Array {
    /// Server rows can repeat an identity (empty ids, re-sent events); SwiftUI lists crash on duplicate keys.
    func uniqued<Key: Hashable>(by key: (Element) -> Key) -> [Element] {
        var seen = Set<Key>()
        return filter { seen.insert(key($0)).inserted }
    }
}

/// Page title row drawn by the app. Tab pages carry no system navigation bar: a UINavigationBar inside
/// the swipeable page TabView hit UIKit's layout assertion (all six device crash logs, 2026-11-07).
struct PageHeader<Trailing: View>: View {
    let title: String
    @ViewBuilder var trailing: () -> Trailing
    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Text(title).font(.scaled(size: 32, weight: .bold)).foregroundStyle(Color.ink)
            Spacer(minLength: 0)
            trailing()
        }
        .padding(.horizontal, 4)
        .padding(.top, 6)
        .accessibilityAddTraits(.isHeader)
    }
}

extension PageHeader where Trailing == EmptyView {
    init(title: String) { self.init(title: title) { EmptyView() } }
}

/// Open a paper account's detail sheet from the monitor list.
struct OpenAccountAction { var run: (PaperAccount) -> Void = { _ in }; func callAsFunction(_ a: PaperAccount) { run(a) } }
private struct OpenAccountKey: EnvironmentKey { static let defaultValue = OpenAccountAction() }
extension EnvironmentValues {
    var openAccount: OpenAccountAction { get { self[OpenAccountKey.self] } set { self[OpenAccountKey.self] = newValue } }
}
