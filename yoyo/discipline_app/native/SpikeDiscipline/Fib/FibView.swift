import SwiftUI

/// 1.618 (owner 2026-11-07): the chart covers the whole screen, down to the bottom edge, like a
/// wallpaper (workbench monday-fib-chart.js wallpaper view). Floating frosted controls only: coin
/// capsule (tap to switch), period capsule, and one slim bar with 内部线 / 六均线 / 云带, follow and a
/// small 开单 pill. Cloud band on by default. Layout reference: Stitch design/native-2.3.0 fib-fullscreen.
struct FibView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(FibStore.self) private var fib
    let active: Bool
    var onQuick: (FibContract, CandlePeriod) -> Void
    @State private var candles = CandleStore()
    @State private var chooser = false
    @State private var periods = false
    @State private var follow = 0

    static let choices: [CandlePeriod] = [.m1, .m5, .m15, .h1, .h4, .h12]

    var body: some View {
        GeometryReader { proxy in
            let top = proxy.safeAreaInsets.top
            ZStack(alignment: .top) {
                chart(top: top).ignoresSafeArea()
                VStack(spacing: 8) {
                    HStack(alignment: .top) {
                        coinButton
                        Spacer(minLength: 8)
                    }
                    Spacer(minLength: 0)
                    if fib.current == nil && fib.error != nil && candles.bars.isEmpty { offlineNote }
                    dock
                }
                .padding(.horizontal, 14)
                .padding(.top, 6)
                .padding(.bottom, 78)
            }
        }
        .sheet(isPresented: $chooser) { FibChooser().presentationDetents([.large]).presentationDragIndicator(.visible) }
        .task(id: active) {
            guard active else { candles.stop(); return }
            await fib.poll()
        }
        .onChange(of: chartKey) { _, _ in showCandles() }
        .onChange(of: active) { _, now in if now { showCandles() } }
        .onAppear { if active { showCandles() } }
    }

    private var chartKey: String { "\(fib.chartSymbol)|\(fib.period.rawValue)|\(session.connection?.token.hashValue ?? 0)" }

    private func showCandles() {
        let symbol = fib.chartSymbol
        guard active, !symbol.isEmpty else { candles.stop(); return }
        candles.show(symbol: symbol, period: fib.period, connection: session.connection)
    }

    private var decimals: Int {
        if let tick = account.state?.instruments[fib.chartSymbol]?.tick { return Fmt.decimals(tick: tick) }
        guard let row = fib.selected else { return 4 }
        let places = [row.high, row.low].map { v -> Int in let t = String(v).split(separator: "."); return t.count > 1 ? t[1].count : 0 }.max() ?? 2
        return min(12, max(2, places))
    }

    private var lastPrice: Double? { candles.bars.last?.close ?? fib.selected?.price }

    // MARK: pieces

    private func chart(top: CGFloat) -> some View {
        let row = fib.selected
        return ZStack {
            CandleChart(bars: candles.bars, generation: candles.generation, period: fib.period, decimals: decimals,
                        range: row.flatMap { fib.range(for: $0) }, innerLines: fib.showInternal, averages: fib.showAverages, cloud: fib.showCloud,
                        watermark: fib.chartSymbol.isEmpty ? nil : Fmt.coin(fib.chartSymbol) + "USDT", followToken: follow, wallpaper: true,
                        insets: EdgeInsets(top: top + 58, leading: 0, bottom: 0, trailing: 0),
                        onNeedOlder: { candles.loadOlder() })
            if candles.bars.isEmpty && !fib.chartSymbol.isEmpty {
                if candles.error == nil { ProgressView() } else { Text(candles.error ?? "").font(.footnote).foregroundStyle(Color.muted) }
            }
        }
    }

    /// Coin capsule (tap to switch): logo, coin, live price; the dot marks this week's touched edge.
    private var coinButton: some View {
        Button { Haptics.open(); chooser = true } label: {
            HStack(spacing: 8) {
                if !fib.chartSymbol.isEmpty { CoinLogo(coin: Fmt.coin(fib.chartSymbol), size: 30) }
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 3) {
                        Text(fib.chartSymbol.isEmpty ? "选择币种" : Fmt.coin(fib.chartSymbol)).font(.system(size: 16, weight: .bold))
                        Image(systemName: "chevron.down").font(.system(size: 10, weight: .bold)).foregroundStyle(Color.muted)
                    }
                    if let price = lastPrice {
                        Text(price.formatted(.number.precision(.fractionLength(decimals)).grouping(.never)))
                            .font(.system(size: 13, weight: .semibold).monospacedDigit())
                            .foregroundStyle((candles.bars.last.map { $0.close >= $0.open } ?? true) ? ChartPalette.up : ChartPalette.down)
                            .contentTransition(.numericText(value: price)).animation(.snappy, value: price)
                    }
                }
                if let touch = fib.selected?.latestTouch {
                    Circle().fill(touch.side == .upper ? ChartPalette.upper : ChartPalette.lower).frame(width: 6, height: 6)
                        .accessibilityLabel(touch.side == .upper ? "本周触及上沿 1.618" : "本周触及下沿 1.618")
                }
            }
            .foregroundStyle(Color.ink)
            .padding(.leading, 6).padding(.trailing, 14).padding(.vertical, 6)
            .frosted(Capsule())
        }
        .buttonStyle(CardPressStyle())
        .accessibilityLabel("切换币种，当前 \(Fmt.coin(fib.chartSymbol))")
    }

    /// Period chip at the bottom-left (owner Notion #76); the list opens upward. Custom drawn —
    /// the system menu drew a grey halo on device.
    private var periodChip: some View {
        Button { Haptics.tap(); withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) { periods.toggle() } } label: {
            HStack(spacing: 3) {
                Text(fib.period.rawValue).font(.system(size: 13, weight: .bold).monospacedDigit()).lineLimit(1)
                Image(systemName: "chevron.up").font(.system(size: 8, weight: .bold)).rotationEffect(.degrees(periods ? 180 : 0))
            }
            .fixedSize()
            .foregroundStyle(Color.white)
            .padding(.horizontal, 10).frame(height: 34)
            .background(Color.accentColor, in: .capsule)
            .contentShape(.capsule)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("K线周期 \(fib.period.rawValue)")
    }

    private var periodList: some View {
        @Bindable var fib = fib
        return VStack(spacing: 2) {
            ForEach(Self.choices.reversed()) { p in
                Button {
                    Haptics.select(); fib.period = p
                    withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) { periods = false }
                } label: {
                    Text(p.rawValue).font(.system(size: 14, weight: .semibold).monospacedDigit())
                        .foregroundStyle(fib.period == p ? Color.white : Color.ink)
                        .frame(width: 56, height: 34)
                        .background(fib.period == p ? Color.accentColor : .clear, in: .capsule)
                        .contentShape(.capsule)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(4)
        .frosted(RoundedRectangle(cornerRadius: 20, style: .continuous), veil: 0.85)
        .transition(.scale(scale: 0.8, anchor: .bottomLeading).combined(with: .opacity))
    }

    /// One slim frosted bar: layer chips, follow, and a small 开单 pill.
    private var dock: some View {
        @Bindable var fib = fib
        let locked = account.pendingEntry != nil || account.closeAttempt != nil || session.connection == nil
        let row = fib.selected
        return HStack(spacing: 4) {
            periodChip
            ToggleChip(title: "内部线", on: $fib.showInternal)
            ToggleChip(title: "六均线", on: $fib.showAverages)
            ToggleChip(title: "云带", on: $fib.showCloud)
            Spacer(minLength: 0)
            Button { Haptics.tap(); follow += 1 } label: {
                Image(systemName: "arrow.right.to.line").font(.system(size: 13, weight: .bold)).foregroundStyle(Color.ink)
                    .frame(width: 30, height: 34).contentShape(.circle)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("跟随最新")
            Button { if let row { Haptics.open(); onQuick(row, fib.period) } } label: {
                HStack(spacing: 3) { Image(systemName: "bolt.fill"); Text("开单") }
                    .font(.system(size: 13, weight: .bold)).lineLimit(1).fixedSize()
            }
            .buttonStyle(.pill(.solid(.accentColor), height: 34))
            .disabled(locked || row == nil || !fib.fresh || !fib.live)
            .accessibilityLabel("快捷开单 1R")
        }
        .padding(5)
        .frosted(Capsule())
        .overlay(alignment: .bottomLeading) {
            if periods { periodList.offset(x: 2, y: -52) }
        }
    }

    private var offlineNote: some View {
        Button { Haptics.tap(); Task { try? await fib.refresh() } } label: {
            Label("触价监控暂未连接 · 点此重试", systemImage: "wifi.exclamationmark").font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                .padding(.horizontal, 14).padding(.vertical, 10).frosted(Capsule())
        }
        .buttonStyle(.plain)
    }

    static func touchTime(_ ms: Double) -> String {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "MM/dd HH:mm"
        return f.string(from: Date(timeIntervalSince1970: ms / 1000))
    }
}

struct ToggleChip: View {
    let title: String
    @Binding var on: Bool
    var body: some View {
        Button { Haptics.select(); withAnimation(.snappy) { on.toggle() } } label: {
            Text(title).font(.system(size: 13, weight: .semibold)).lineLimit(1).fixedSize()
                .foregroundStyle(on ? Color.accentColor : Color.ink)
                .padding(.horizontal, 9).frame(height: 34)
                .background(on ? Color.accentColor.opacity(0.13) : .clear, in: .capsule)
                .contentShape(.capsule)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

struct TrailingIcon: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 3) { configuration.title; configuration.icon }
    }
}
