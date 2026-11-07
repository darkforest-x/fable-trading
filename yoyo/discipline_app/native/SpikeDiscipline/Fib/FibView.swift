import SwiftUI

/// 1.618 (owner 2026-11-07): the chart is a full-screen wallpaper (workbench monday-fib-chart.js
/// wallpaper view) with floating glass controls — tap the coin at top-left to switch, period at
/// top-right, one control dock and 快捷开单 · 1R / 模拟盘 above the tab bar. Cloud band on by default.
/// Layout reference: Stitch design/native-2.2.0 fib-wallpaper.
struct FibView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(FibStore.self) private var fib
    let active: Bool
    var onQuick: (FibContract, CandlePeriod) -> Void
    var onReplay: () -> Void
    @State private var candles = CandleStore()
    @State private var chooser = false
    @State private var follow = 0

    static let periods: [CandlePeriod] = [.m1, .m5, .m15, .h1, .h4, .h12]

    var body: some View {
        GeometryReader { proxy in
            let top = proxy.safeAreaInsets.top
            ZStack(alignment: .top) {
                chart(top: top)
                    .ignoresSafeArea()
                VStack(spacing: 10) {
                    HStack(alignment: .top) {
                        coinButton
                        Spacer(minLength: 8)
                        periodMenu
                    }
                    Spacer(minLength: 0)
                    if fib.current == nil && fib.error != nil && candles.bars.isEmpty { offlineNote }
                    actions
                    dock
                }
                .padding(.horizontal, 14)
                .padding(.top, 6)
                .padding(.bottom, 84)
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
                        insets: EdgeInsets(top: top + 64, leading: 0, bottom: 246, trailing: 0),
                        onNeedOlder: { candles.loadOlder() })
            if candles.bars.isEmpty && !fib.chartSymbol.isEmpty {
                if candles.error == nil { ProgressView() } else { Text(candles.error ?? "").font(.footnote).foregroundStyle(.secondary) }
            }
        }
    }

    /// Coin capsule (tap to switch): logo, coin, price; the dot shows this week's touched edge.
    private var coinButton: some View {
        Button { Haptics.open(); chooser = true } label: {
            HStack(spacing: 8) {
                if !fib.chartSymbol.isEmpty { CoinLogo(coin: Fmt.coin(fib.chartSymbol), size: 28) }
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 4) {
                        Text(fib.chartSymbol.isEmpty ? "选择币种" : Fmt.coin(fib.chartSymbol)).font(.headline.weight(.bold))
                        Image(systemName: "chevron.down").font(.caption2.weight(.heavy))
                    }
                    if let price = lastPrice {
                        Text(price.formatted(.number.precision(.fractionLength(decimals)).grouping(.never)))
                            .font(.subheadline.weight(.semibold).monospacedDigit())
                            .foregroundStyle((candles.bars.last.map { $0.close >= $0.open } ?? true) ? ChartPalette.up : ChartPalette.down)
                            .contentTransition(.numericText(value: price)).animation(.snappy, value: price)
                    }
                }
                if let touch = fib.selected?.latestTouch {
                    Circle().fill(touch.side == .upper ? ChartPalette.upper : ChartPalette.lower).frame(width: 7, height: 7)
                        .accessibilityLabel(touch.side == .upper ? "本周触及上沿 1.618" : "本周触及下沿 1.618")
                }
            }
            .foregroundStyle(.primary)
            .padding(.leading, 8).padding(.trailing, 14).padding(.vertical, 7)
            .glassEffect(.regular.interactive(), in: .capsule)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("切换币种，当前 \(Fmt.coin(fib.chartSymbol))")
    }

    private var periodMenu: some View {
        @Bindable var fib = fib
        return Menu {
            Picker("K线周期", selection: $fib.period) { ForEach(Self.periods) { Text($0.rawValue).tag($0) } }
        } label: {
            HStack(spacing: 4) {
                Text(fib.period.rawValue).font(.subheadline.weight(.bold).monospacedDigit())
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .bold))
            }
            .foregroundStyle(.primary)
            .padding(.horizontal, 14).frame(height: 40)
            .glassEffect(.regular.interactive(), in: .capsule)
        }
        .onChange(of: fib.period) { _, _ in Haptics.select() }
        .accessibilityLabel("K线周期 \(fib.period.rawValue)")
    }

    private var dock: some View {
        @Bindable var fib = fib
        return HStack(spacing: 2) {
            ToggleChip(title: "内部线", on: $fib.showInternal)
            ToggleChip(title: "六均线", on: $fib.showAverages)
            ToggleChip(title: "云带", on: $fib.showCloud)
            Spacer(minLength: 0)
            Button { Haptics.tap(); follow += 1 } label: {
                Image(systemName: "arrow.right.to.line").font(.subheadline.weight(.bold)).frame(width: 40, height: 40).contentShape(.circle)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("跟随最新")
        }
        .padding(.horizontal, 4)
        .glassEffect(.regular.interactive(), in: .capsule)
    }

    private var actions: some View {
        let locked = account.pendingEntry != nil || account.closeAttempt != nil || session.connection == nil
        let row = fib.selected
        return HStack(spacing: 10) {
            Button { if let row { Haptics.open(); onQuick(row, fib.period) } } label: {
                HStack(spacing: 6) {
                    Image(systemName: "bolt.fill")
                    Text("快捷开单").fontWeight(.bold)
                    Text("· 1R").opacity(0.8)
                }
                .font(.body).frame(maxWidth: .infinity).frame(height: 46)
            }
            .buttonStyle(.glassProminent)
            .disabled(locked || row == nil || !fib.fresh || !fib.live)
            Button { Haptics.open(); onReplay() } label: {
                Image(systemName: "play.rectangle.fill").font(.title3).frame(width: 46, height: 46)
            }
            .buttonStyle(.glass)
            .accessibilityLabel("模拟盘")
        }
    }

    private var offlineNote: some View {
        Button { Haptics.tap(); Task { try? await fib.refresh() } } label: {
            Label("触价监控暂未连接 · 点此重试", systemImage: "wifi.exclamationmark").font(.subheadline.weight(.semibold))
                .padding(.horizontal, 14).padding(.vertical, 10).glassEffect(.regular, in: .capsule)
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
            Text(title).font(.subheadline.weight(.semibold))
                .foregroundStyle(on ? Color.white : .primary)
                .padding(.horizontal, 12).frame(height: 32)
                .background(on ? Color.accentColor : .clear, in: .capsule)
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
