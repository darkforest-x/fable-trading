import SwiftUI

/// 1.618 (replica of the workbench page, FibHome.tsx): price card, full chart with the NY Monday
/// range, 列表 of this week's touches, one control dock, then 快捷开单 · 1R and 模拟盘.
/// Orders still go through the ticket (TicketView), which re-reads the wick stop server-side.
struct FibView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(FibStore.self) private var fib
    let active: Bool
    @Binding var showSettings: Bool
    var onQuick: (FibContract, CandlePeriod) -> Void
    var onReplay: () -> Void
    @State private var candles = CandleStore()
    @State private var chooser = false
    @State private var follow = 0

    private static let periods: [CandlePeriod] = [.m1, .m5, .m15, .h1, .h4]

    var body: some View {
        @Bindable var fib = fib
        NavigationStack {
            VStack(spacing: 10) {
                if let row = fib.selected, let touch = row.latestTouch {
                    priceCard(row, touch: touch)
                    chart(row)
                    dock(fib: fib)
                    actions(row)
                } else {
                    empty
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 92)
            .containerBackground(for: .navigation) { AmbientBackground() }
            .toolbarBackground(.hidden, for: .navigationBar)
            .navigationTitle("1.618")
            .toolbarTitleDisplayMode(.inlineLarge)
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { Haptics.open(); chooser = true } label: {
                        HStack(spacing: 5) {
                            Circle().fill(fib.live ? Color.up : Color.secondary.opacity(0.5)).frame(width: 7, height: 7)
                            Text("列表").font(.subheadline.weight(.semibold))
                            Text("\(fib.rows.count)").font(.subheadline.weight(.bold).monospacedDigit()).contentTransition(.numericText())
                        }
                    }
                    .disabled(fib.current == nil)
                    .accessibilityLabel("打开触价列表，\(fib.rows.count) 个")
                    Button { Haptics.tap(); showSettings = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("设置")
                }
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

    private var chartKey: String { "\(fib.selected?.symbol ?? "")|\(fib.period.rawValue)|\(session.connection?.token.hashValue ?? 0)" }

    private func showCandles() {
        guard active, let symbol = fib.selected?.symbol else { candles.stop(); return }
        candles.show(symbol: symbol, period: fib.period, connection: session.connection)
    }

    private func decimals(_ row: FibContract) -> Int {
        if let tick = account.state?.instruments[row.symbol]?.tick, let value = Double(tick), value > 0 {
            return min(12, max(2, Int(ceil(-log10(value) - 1e-9))))
        }
        let places = [row.high, row.low].map { v -> Int in
            let text = String(v); return text.split(separator: ".").count > 1 ? text.split(separator: ".")[1].count : 0
        }.max() ?? 2
        return min(12, max(2, places))
    }

    // MARK: sections

    private func priceCard(_ row: FibContract, touch: FibTouch) -> some View {
        let coin = Fmt.coin(row.symbol)
        return HStack(alignment: .center, spacing: 12) {
            CoinLogo(coin: coin, size: 40)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(coin).font(.headline.weight(.bold))
                    Text("USDT 永续").font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                        .padding(.horizontal, 6).padding(.vertical, 2).background(.quaternary, in: .capsule)
                    if !fib.fresh {
                        Pill(text: fib.error == nil ? "更新中" : "离线缓存", color: .amber)
                    }
                }
                HStack(spacing: 5) {
                    Circle().fill(touch.side == .upper ? Color.down : Color.up).frame(width: 6, height: 6)
                    Text("\(touch.side == .upper ? "上沿" : "下沿") 1.618 · \(Self.touchTime(touch.occurredMs))")
                        .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 4) {
                Text(row.price.map { $0.formatted(.number.precision(.fractionLength(decimals(row))).grouping(.never)) } ?? "—")
                    .font(.title2.weight(.bold).monospacedDigit())
                    .contentTransition(.numericText(value: row.price ?? 0))
                    .animation(.snappy, value: row.price)
                    .minimumScaleFactor(0.6).lineLimit(1)
                if let recent = fib.rows.first, recent.symbol != row.symbol {
                    Button { Haptics.select(); withAnimation(.snappy) { fib.symbol = recent.symbol } } label: {
                        Label("最新 \(Fmt.coin(recent.symbol))", systemImage: "arrow.right").labelStyle(TrailingIcon())
                            .font(.caption2.weight(.semibold))
                    }
                    .buttonStyle(.glass).controlSize(.mini)
                }
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .glassCard(cornerRadius: 22)
    }

    private func chart(_ row: FibContract) -> some View {
        ZStack {
            CandleChart(bars: candles.bars, generation: candles.generation, period: fib.period, decimals: decimals(row),
                        range: fib.range(for: row), innerLines: fib.showInternal, averages: fib.showAverages,
                        watermark: Fmt.coin(row.symbol) + "USDT", followToken: follow, onNeedOlder: { candles.loadOlder() })
            if candles.bars.isEmpty {
                VStack(spacing: 8) {
                    if candles.error == nil { ProgressView() } else { Image(systemName: "wifi.exclamationmark").font(.title2).foregroundStyle(.secondary) }
                    Text(candles.error ?? "正在加载图表…").font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 8).padding(.leading, 4)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .glassCard(cornerRadius: 22)
    }

    /// One glass control dock (period · 内部线 · 六均线 · 跟随最新), like the workbench.
    private func dock(fib: FibStore) -> some View {
        @Bindable var fib = fib
        return HStack(spacing: 2) {
            Menu {
                Picker("K线周期", selection: $fib.period) {
                    ForEach(Self.periods) { Text($0.rawValue).tag($0) }
                }
            } label: {
                HStack(spacing: 3) {
                    Text(fib.period.rawValue).font(.subheadline.weight(.bold).monospacedDigit())
                    Image(systemName: "chevron.up.chevron.down").font(.system(size: 10, weight: .bold))
                }
                .foregroundStyle(.primary)
                .padding(.horizontal, 12).frame(height: 40).contentShape(.capsule)
            }
            .onChange(of: fib.period) { _, _ in Haptics.select() }
            .accessibilityLabel("K线周期 \(fib.period.rawValue)")
            Divider().frame(height: 18)
            ToggleChip(title: "内部线", on: $fib.showInternal)
            ToggleChip(title: "六均线", on: $fib.showAverages)
            Spacer(minLength: 0)
            Button { Haptics.tap(); follow += 1 } label: {
                HStack(spacing: 4) { Image(systemName: "arrow.right.to.line"); Text("最新") }
                    .font(.subheadline.weight(.semibold)).padding(.horizontal, 12).frame(height: 40).contentShape(.capsule)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("跟随最新")
        }
        .padding(.horizontal, 4)
        .glassEffect(.regular.interactive(), in: .capsule)
    }

    private func actions(_ row: FibContract) -> some View {
        let locked = account.pendingEntry != nil || account.closeAttempt != nil || session.connection == nil
        return HStack(spacing: 10) {
            Button { Haptics.open(); onQuick(row, fib.period) } label: {
                HStack(spacing: 6) {
                    Image(systemName: "bolt.fill")
                    Text("快捷开单").fontWeight(.bold)
                    Text("· 1R").foregroundStyle(.white.opacity(0.8))
                }
                .font(.body).frame(maxWidth: .infinity).frame(height: 44)
            }
            .buttonStyle(.glassProminent)
            .disabled(locked || !fib.fresh || !fib.live)
            Button { Haptics.open(); onReplay() } label: {
                Label("模拟盘", systemImage: "play.circle").font(.subheadline.weight(.semibold)).frame(height: 44).padding(.horizontal, 4)
            }
            .buttonStyle(.glass)
        }
    }

    private var empty: some View {
        VStack(spacing: 14) {
            Spacer()
            Image(systemName: "chart.line.uptrend.xyaxis").font(.system(size: 34, weight: .medium)).foregroundStyle(.blue)
                .frame(width: 78, height: 78).frostedCapsule()
            Text(fib.error != nil ? "监控暂不可用" : fib.current != nil ? "本周暂无触价" : "正在读取触价记录").font(.title3.weight(.bold))
            Text(fib.error != nil ? "稍后重试，已有交易保护保持独立运行。" : fib.current?.mondayComplete == false ? "纽约周一结束后锁定高低点。" : "首次触及区间外 1.618 的合约会显示在这里。")
                .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            HStack(spacing: 10) {
                if fib.error != nil {
                    Button("重新加载") { Haptics.tap(); Task { try? await fib.refresh() } }.buttonStyle(.glassProminent)
                }
                Button { Haptics.open(); onReplay() } label: { Label("模拟盘", systemImage: "play.circle") }.buttonStyle(.glass)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity)
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
            HStack(spacing: 4) {
                Image(systemName: on ? "checkmark.circle.fill" : "circle").font(.system(size: 13, weight: .semibold))
                Text(title).font(.subheadline.weight(.semibold))
            }
            .foregroundStyle(on ? Color.accentColor : .primary)
            .padding(.horizontal, 10).frame(height: 32)
            .background(on ? Color.accentColor.opacity(0.12) : .clear, in: .capsule)
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
