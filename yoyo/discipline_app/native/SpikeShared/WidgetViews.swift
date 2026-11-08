import ActivityKit
import SwiftUI
import WidgetKit

/// Widget and Live Activity views (Stitch home-widgets a7cc13d6…, live-activity), shared so the
/// DEBUG widget gallery in the app can render them for simulator acceptance. Display only.

// MARK: palette (mirrors the app's Theme; the extension does not share the app's design system)

enum WPalette {
    static let up = Color(red: 0x16 / 255, green: 0x98 / 255, blue: 0x73 / 255)
    static let down = Color(red: 0xD7 / 255, green: 0x35 / 255, blue: 0x5D / 255)
    static let ink = Color.primary
    static let muted = Color.secondary
    static let orange = LinearGradient(colors: [Color(red: 1, green: 0.62, blue: 0.04), Color(red: 0.96, green: 0.45, blue: 0.1)],
                                       startPoint: .topLeading, endPoint: .bottomTrailing)
    static func tone(_ value: Double) -> Color { value > 0 ? up : value < 0 ? down : ink }
    static func signed(_ value: Double, _ digits: Int = 2) -> String {
        (value > 0 ? "+" : "") + value.formatted(.number.precision(.fractionLength(digits)).locale(Locale(identifier: "en_US")))
    }
}

/// Coin logo from the bundled set (the app's own bundle, or the containing app from the extension).
struct WCoin: View {
    let symbol: String
    var size: CGFloat = 20
    var body: some View {
        let coin = WidgetSnapshot.coin(symbol)
        let url = Bundle.main.url(forResource: coin.lowercased(), withExtension: "png", subdirectory: "coins")
            ?? Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
                .appendingPathComponent("coins").appendingPathComponent(coin.lowercased() + ".png")
        Group {
            if let image = UIImage(contentsOfFile: url.path) { Image(uiImage: image).resizable().scaledToFit() }
            else { Text(String(coin.prefix(1))).font(.system(size: size * 0.5, weight: .bold)).foregroundStyle(.white)
                    .frame(width: size, height: size).background(Color.gray, in: .circle) }
        }
        .frame(width: size, height: size).clipShape(.circle)
    }
}

struct WScore: View {
    let score: Double?
    let threshold: Double
    var body: some View {
        let high = (score ?? -1) >= threshold
        Text(score.map { String(format: "%.2f", $0) } ?? "—")
            .font(.system(size: 12, weight: .bold).monospacedDigit()).lineLimit(1).fixedSize()
            .foregroundStyle(high ? Color.white : WPalette.muted)
            .padding(.horizontal, high ? 6 : 0).padding(.vertical, high ? 2 : 0)
            .background { if high { Capsule().fill(WPalette.orange) } }
    }
}

struct TouchesView: View {
    let snapshot: WidgetSnapshot
    @Environment(\.widgetFamily) private var family
    var body: some View {
        let s = snapshot, rows = Array(s.highTouches.prefix(3))
        switch family {
        case .accessoryInline:
            Text(rows.first.map { "高分触价 \(WidgetSnapshot.coin($0.symbol)) \(String(format: "%.2f", $0.score ?? 0))" } ?? "今日暂无高分触价")
        case .accessoryRectangular:
            VStack(alignment: .leading, spacing: 1) {
                Text("高分触价").font(.caption2.weight(.semibold)).widgetAccentable()
                if rows.isEmpty { Text("今日暂无").font(.caption) }
                ForEach(rows.prefix(2), id: \.self) { t in
                    Text("\(WidgetSnapshot.coin(t.symbol))  \(String(format: "%.2f", t.score ?? 0))").font(.caption.monospacedDigit())
                }
            }
        default:
            VStack(alignment: .leading, spacing: 7) {
                HStack {
                    Text("高分触价").font(.system(size: 14, weight: .bold)).foregroundStyle(WPalette.ink)
                    Spacer()
                    Circle().fill(Color.orange).frame(width: 6, height: 6)
                }
                if rows.isEmpty {
                    Spacer(minLength: 0)
                    Text("今日暂无高分触价").font(.system(size: 12)).foregroundStyle(WPalette.muted)
                }
                ForEach(rows, id: \.self) { t in
                    HStack(spacing: 6) {
                        WCoin(symbol: t.symbol, size: 18)
                        Text(WidgetSnapshot.coin(t.symbol)).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                        Spacer(minLength: 2)
                        WScore(score: t.score, threshold: s.threshold)
                    }
                }
                Spacer(minLength: 0)
                HStack {
                    Text("评分 ≥ \(String(format: "%.1f", s.threshold))").font(.system(size: 10)).foregroundStyle(WPalette.muted)
                    Spacer()
                    Text(s.updated > 0 ? WidgetSnapshot.clock(s.updated) : "—").font(.system(size: 10).monospacedDigit()).foregroundStyle(WPalette.muted)
                }
            }
            .widgetURL(URL(string: "spike://fib"))
        }
    }
}

struct OverviewView: View {
    let snapshot: WidgetSnapshot
    var body: some View {
        let s = snapshot, high = s.highTouches.count, newest = s.touches.first
        VStack(spacing: 6) {
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("持仓").font(.system(size: 14, weight: .bold)).foregroundStyle(WPalette.ink)
                    Spacer()
                    if !s.positions.isEmpty { Circle().fill(WPalette.up).frame(width: 6, height: 6) }
                }
                if let p = s.positions.first {
                    HStack(spacing: 6) {
                        WCoin(symbol: p.symbol, size: 20)
                        Text("\(WidgetSnapshot.coin(p.symbol)) \(p.side == "short" ? "空" : "多") \(p.leverage)x").font(.system(size: 14, weight: .semibold)).lineLimit(1)
                    }
                    Text(WPalette.signed(p.pnl) + " U").font(.system(size: 22, weight: .bold, design: .rounded).monospacedDigit())
                        .foregroundStyle(WPalette.tone(p.pnl)).minimumScaleFactor(0.7).lineLimit(1)
                    if s.positions.count > 1 { Text("另有 \(s.positions.count - 1) 笔").font(.system(size: 11)).foregroundStyle(WPalette.muted) }
                } else {
                    Text("无持仓").font(.system(size: 15, weight: .semibold)).foregroundStyle(WPalette.muted)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Divider()
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("今日触价 \(s.touches.count)").font(.system(size: 14, weight: .bold)).foregroundStyle(WPalette.ink)
                    Spacer()
                    Text("高分 \(high)").font(.system(size: 11, weight: .bold)).foregroundStyle(Color.orange)
                        .padding(.horizontal, 7).padding(.vertical, 2).background(Color.orange.opacity(0.14), in: .capsule)
                }
                Text("最新触价").font(.system(size: 11)).foregroundStyle(WPalette.muted)
                if let t = newest {
                    HStack(spacing: 6) {
                        WCoin(symbol: t.symbol, size: 22)
                        VStack(alignment: .leading, spacing: 0) {
                            Text("\(WidgetSnapshot.coin(t.symbol)) \(t.side == "short" ? "上沿" : "下沿")").font(.system(size: 13, weight: .semibold)).lineLimit(1)
                            Text(WidgetSnapshot.clock(t.at / 1000)).font(.system(size: 11).monospacedDigit()).foregroundStyle(WPalette.muted)
                        }
                        Spacer(minLength: 2)
                        WScore(score: t.score, threshold: s.threshold)
                    }
                    .padding(7)
                    .background(Color.secondary.opacity(0.08), in: .rect(cornerRadius: 12))
                } else {
                    Text("今日暂无").font(.system(size: 13)).foregroundStyle(WPalette.muted)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
            HStack {
                HStack(spacing: 4) { Circle().fill(WPalette.up).frame(width: 5, height: 5); Text("天才交易员") }
                Spacer()
                Text(s.updated > 0 ? WidgetSnapshot.clock(s.updated) : "—").monospacedDigit()
            }
            .font(.system(size: 10)).foregroundStyle(WPalette.muted)
        }
        .widgetURL(URL(string: "spike://trade"))
    }
}

private func price(_ value: Double, _ decimals: Int) -> String {
    value.formatted(.number.precision(.fractionLength(min(max(decimals, 0), 8))).locale(Locale(identifier: "en_US")))
}

struct ActivityCard: View {
    let attributes: PositionActivityAttributes
    let state: PositionActivityAttributes.ContentState
    var body: some View {
        let a = attributes, s = state
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                WCoin(symbol: a.symbol, size: 34)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text("\(WidgetSnapshot.coin(a.symbol)) \(a.side == "short" ? "空" : "多") \(a.leverage)x").font(.system(size: 16, weight: .bold))
                        Text("持仓中").font(.system(size: 10, weight: .semibold)).foregroundStyle(WPalette.up)
                            .padding(.horizontal, 6).padding(.vertical, 2).background(WPalette.up.opacity(0.14), in: .capsule)
                    }
                    Text("开仓 \(price(a.entry, a.decimals))").font(.system(size: 12).monospacedDigit()).foregroundStyle(WPalette.muted)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(WPalette.signed(s.pnl) + " U").font(.system(size: 24, weight: .bold, design: .rounded).monospacedDigit()).foregroundStyle(WPalette.tone(s.pnl))
                    if let roe = s.roe { Text(WPalette.signed(roe, 1) + "%").font(.system(size: 12, weight: .semibold).monospacedDigit()).foregroundStyle(WPalette.tone(roe)) }
                }
            }
            StopBar(attributes: a, state: s)
            HStack {
                HStack(spacing: 4) { Circle().fill(WPalette.up).frame(width: 5, height: 5); Text("天才交易员 · \(WidgetSnapshot.clock(s.updated)) 更新") }
                Spacer()
                Text("进入图表 ›").foregroundStyle(Color.accentColor)
            }
            .font(.system(size: 11)).foregroundStyle(WPalette.muted)
        }
    }
}

/// Stop → mark bar (Stitch live-activity 187804d9…): labels above, red→green track, ring at the mark.
struct StopBar: View {
    let attributes: PositionActivityAttributes
    let state: PositionActivityAttributes.ContentState
    var body: some View {
        let a = attributes, s = state
        let far = max(abs(a.entry - s.stop) * 2, abs(s.mark - s.stop), 1e-12)
        let fraction = min(1, max(0, abs(s.mark - s.stop) / far))
        VStack(spacing: 5) {
            HStack(spacing: 4) {
                Text("止损").foregroundStyle(WPalette.muted)
                Text(price(s.stop, a.decimals)).foregroundStyle(WPalette.down)
                Spacer()
                Text("现价").foregroundStyle(WPalette.muted)
                Text(price(s.mark, a.decimals)).foregroundStyle(WPalette.tone(s.pnl))
            }
            .font(.system(size: 11, weight: .medium).monospacedDigit())
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.secondary.opacity(0.15)).frame(height: 4)
                    Capsule().fill(LinearGradient(colors: [WPalette.down, .orange, WPalette.up], startPoint: .leading, endPoint: .trailing))
                        .frame(width: max(4, proxy.size.width * fraction), height: 4)
                    Circle().strokeBorder(WPalette.tone(s.pnl), lineWidth: 2.5).background(Circle().fill(Color(.systemBackground)))
                        .frame(width: 12, height: 12).offset(x: proxy.size.width * fraction - 6)
                }
            }
            .frame(height: 12)
        }
    }
}
