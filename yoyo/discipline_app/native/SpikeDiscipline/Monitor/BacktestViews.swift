import SwiftUI

/// 历史开单 (2.7.0, Stitch native-2.7.0 backtest-list 04710b28…): the saved backtest trades of one
/// strategy, 50 per page, newest / oldest / by P&L. Same ledger as the web booklet; Binance history,
/// not live P&L.
struct BacktestListView: View {
    enum Order: String, CaseIterable, Hashable {
        case newest, oldest, pnl
        var title: String { switch self { case .newest: "最新"; case .oldest: "最早"; case .pnl: "盈亏" } }
        var query: (String, String) { switch self { case .newest: ("time", "desc"); case .oldest: ("time", "asc"); case .pnl: ("pnl", "desc") } }
    }

    @Environment(\.dismiss) private var dismiss
    let key: String
    let label: String
    let connection: Connection
    @State private var order: Order = .newest
    @State private var rows: [BacktestTrade] = []
    @State private var page: BacktestPage?
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                summary
                SoftSegment(selection: $order, options: Order.allCases.map { ($0, $0.title) }, height: 34)
                if let error {
                    VStack(spacing: 8) {
                        Label(error, systemImage: "exclamationmark.arrow.triangle.2.circlepath").font(.footnote).foregroundStyle(Color.amber)
                        Button("重试") { Task { await load(reset: rows.isEmpty) } }.buttonStyle(.pill(.soft))
                    }
                    .frame(maxWidth: .infinity).padding(14)
                }
                if !rows.isEmpty {
                    VStack(spacing: 0) {
                        ForEach(Array(rows.enumerated()), id: \.element.id) { index, trade in
                            NavigationLink(value: StrategyPage.trade(key: key, trade: trade)) { BacktestTradeRow(trade: trade) }
                                .buttonStyle(RowPressStyle())
                            if index < rows.count - 1 { Divider().padding(.leading, 56) }
                        }
                    }
                    .glassCard(cornerRadius: 22)
                } else if loading {
                    HStack(spacing: 8) { ProgressView(); Text("正在读取历史开单").font(.footnote).foregroundStyle(Color.muted) }
                        .frame(maxWidth: .infinity).padding(24)
                } else if page != nil {
                    Text("这个策略没有保存的回测交易").font(.footnote).foregroundStyle(Color.muted).frame(maxWidth: .infinity).padding(24).glassCard()
                }
                if let page {
                    if page.hasMore {
                        Button { Task { await load(reset: false) } } label: {
                            Text(loading ? "读取中…" : "再显示 50 笔").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.pill(.soft, height: 40)).disabled(loading)
                    }
                    Text("显示 \(rows.count) / \(page.total) 笔 · 点交易看 K 线").font(.caption2).foregroundStyle(Color.muted).frame(maxWidth: .infinity)
                }
            }
            .padding(.horizontal, 14).padding(.bottom, 28)
        }
        .scrollEdgeEffectHidden(true, for: .top)
        .containerBackground(for: .navigation) { AmbientBackground() }
        .navigationTitle("\(label) · 历史开单")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        .task(id: order) { await load(reset: true) }
    }

    @ViewBuilder private var summary: some View {
        let meta = page?.meta
        VStack(alignment: .leading, spacing: 8) {
            Text("策略全样本结算").font(.caption.weight(.semibold)).foregroundStyle(Color.muted)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(meta?.trades.map { "\(Int($0)) 笔" } ?? "— 笔").font(.scaled(size: 26, weight: .bold, design: .rounded)).foregroundStyle(Color.ink)
                Text("·").foregroundStyle(Color.muted)
                Text(StrategyFmt.signedMoney(meta?.pnl) + " U").font(.scaled(size: 24, weight: .bold, design: .rounded)).monospacedDigit()
                    .foregroundStyle(Fmt.tone(meta?.pnl).color).lineLimit(1).minimumScaleFactor(0.6)
            }
            Text([meta?.source == "Binance" ? "币安历史" : meta?.source, rangeText(meta)].compactMap { $0 }.joined(separator: " · "))
                .font(.system(.caption2, design: .monospaced)).foregroundStyle(Color.muted)
            if let basis = meta?.basis { Text(basis).font(.caption2).foregroundStyle(Color.muted).fixedSize(horizontal: false, vertical: true) }
            if let limitation = meta?.limitation {
                Label(limitation, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(Color.amber)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.amber.opacity(0.08), in: .rect(cornerRadius: 12))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16).glassCard(cornerRadius: 24)
    }

    private func rangeText(_ meta: BacktestPage.Meta?) -> String? {
        guard let start = meta?.start, let end = meta?.end else { return nil }
        return "\(start.prefix(10)) — \(end.prefix(10))"
    }

    private func load(reset: Bool) async {
        if reset { rows = []; page = nil }
        guard !loading else { return }
        loading = true; error = nil
        let (sort, direction) = order.query
        do {
            let next: BacktestPage = try await API.get("/v1/monitor/backtest", query: [
                URLQueryItem(name: "track", value: key), URLQueryItem(name: "sort", value: sort),
                URLQueryItem(name: "direction", value: direction), URLQueryItem(name: "offset", value: String(rows.count))], connection: connection)
            withAnimation(.snappy) { rows = (rows + next.rows).uniqued(by: \.id); page = next }
        } catch is CancellationError {
        } catch { self.error = error.localizedDescription }
        loading = false
    }
}

struct BacktestTradeRow: View {
    let trade: BacktestTrade
    var body: some View {
        HStack(spacing: 11) {
            CoinLogo(coin: trade.coin, size: 32)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 5) {
                    Text(trade.coin).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                    SidePill(side: trade.side)
                }
                Text("开 \(StrategyFmt.utcTime(trade.entryMs)) UTC")
                    .font(.system(.caption2, design: .monospaced)).foregroundStyle(Color.muted).lineLimit(1).minimumScaleFactor(0.8)
                HStack(spacing: 4) {
                    Text("平 \(StrategyFmt.utcShortTime(trade.exitMs))").font(.system(.caption2, design: .monospaced)).foregroundStyle(Color.muted)
                    Text("· " + detail).font(.caption2).foregroundStyle(trade.score != nil ? Color.accentColor : Color.muted)
                }
                .lineLimit(1).minimumScaleFactor(0.8)
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 2) {
                Text(StrategyFmt.signedMoney(trade.pnl) + " U").font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(trade.pnl).color)
                Text(Fmt.signedPercent(trade.returnPct)).font(.caption2).foregroundStyle(Fmt.tone(trade.returnPct).color)
            }
            .monospacedDigit()
            Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 12).padding(.vertical, 10).contentShape(.rect)
    }
    private var detail: String {
        if let count = trade.count { return "\(Int(count)) 个币" + (trade.exitReason == "time_6d" ? " · 6天到期" : "") }
        if let score = trade.score { return String(format: "评分 %.4f", score) + (trade.kind == "add5" ? " · 加仓" : "") }
        return trade.kind == "add5" ? "加仓" : "评分未留存"
    }
}

/// K-line replay of one saved trade (Stitch native-2.7.0 backtest-replay): Binance history with the
/// original fill prices as lines and the trade's Monday 1.618 ranges. Reuses the app's CandleChart
/// through its public inputs only (pan / pinch as on the 1.618 page).
struct BacktestChartView: View {
    @Environment(\.dismiss) private var dismiss
    let key: String
    let trade: BacktestTrade
    let connection: Connection
    @State private var period: CandlePeriod
    @State private var data: BacktestChartData?
    @State private var error: String?
    @State private var generation = 0
    private static let periods: [CandlePeriod] = [.m5, .m15, .h1, .h4]

    init(key: String, trade: BacktestTrade, connection: Connection) {
        self.key = key; self.trade = trade; self.connection = connection
        // Open on a period where the whole holding span fits the default zoom (~45 bars).
        let hold = (trade.exitMs ?? 0) - (trade.entryMs ?? 0)
        _period = State(initialValue: hold > 2 * 86_400_000 ? .h4 : hold > 12 * 3_600_000 ? .h1 : hold > 3 * 3_600_000 ? .m15 : .m5)
    }

    var body: some View {
        VStack(spacing: 10) {
            strip
            ZStack {
                if let data, !data.candles.isEmpty {
                    // The chart opens at its right edge: end the bars a few periods after the exit so the
                    // trade itself is on screen (earlier history stays a drag away).
                    let last = ((trade.exitMs ?? .infinity) / 1000) + period.seconds * 8
                    let bars = data.candles.filter { $0.time / 1000 <= last }.map {
                        Candle(time: $0.time / 1000, open: $0.open, high: $0.high, low: $0.low, close: $0.close, volume: $0.volume, confirmed: true)
                    }
                    CandleChart(bars: bars, generation: generation, period: period, decimals: StrategyFmt.decimals(trade.entryPrice ?? data.candles.last?.close),
                                range: ranges(data).current, history: ranges(data).past, lines: lines,
                                watermark: trade.coin + "USDT")
                } else if let error {
                    VStack(spacing: 8) {
                        Label(error, systemImage: "exclamationmark.arrow.triangle.2.circlepath").font(.footnote).foregroundStyle(Color.amber)
                        Button("重试") { generation += 1; Task { await load() } }.buttonStyle(.pill(.soft))
                    }
                    .padding(20)
                } else {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .glassCard(cornerRadius: 22)
            dock
        }
        .padding(.horizontal, 12).padding(.bottom, 12)
        .containerBackground(for: .navigation) { AmbientBackground() }
        .navigationTitle("\(trade.coin) \(trade.side == .long ? "做多" : "做空") · 历史开单")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        .task(id: period) { await load() }
    }

    private var strip: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("开 \(Fmt.price(trade.entryPrice)) → 平 \(Fmt.price(trade.exitPrice))").font(.system(.subheadline, design: .monospaced).weight(.semibold)).foregroundStyle(Color.ink)
                Spacer(minLength: 4)
                Text(StrategyFmt.signedMoney(trade.pnl) + " U · " + Fmt.signedPercent(trade.returnPct))
                    .font(.subheadline.weight(.bold)).monospacedDigit().foregroundStyle(Fmt.tone(trade.pnl).color)
            }
            Text("\(StrategyFmt.utcTime(trade.entryMs)) — \(StrategyFmt.utcShortTime(trade.exitMs)) UTC" + (trade.exitReason == "time_6d" ? " · 6天到期" : trade.score.map { String(format: " · 评分 %.4f", $0) } ?? ""))
                .font(.system(.caption2, design: .monospaced)).foregroundStyle(Color.muted)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .glassCard(cornerRadius: 18)
    }

    private var dock: some View {
        VStack(spacing: 6) {
            HStack(spacing: 4) {
                ForEach(Self.periods) { p in
                    let on = p == period
                    Button { guard !on else { return }; Haptics.select(); period = p } label: {
                        Text(p.rawValue).font(.footnote.weight(on ? .bold : .medium)).foregroundStyle(on ? Color.accentColor : Color.ink)
                            .frame(maxWidth: .infinity).frame(height: 32)
                            .background(on ? Color.accentColor.opacity(0.12) : .clear, in: .capsule)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(4).frosted(Capsule(), veil: 0.8)
            Text("币安历史行情 · 图中时间为北京时间 · 双指缩放 · 线为原回测成交价").font(.caption2).foregroundStyle(Color.muted)
        }
    }

    private var lines: [ChartLine] {
        var out: [ChartLine] = []
        // CandleChart prints the price after the title itself.
        if let p = trade.entryPrice { out.append(ChartLine(price: p, title: trade.side == .long ? "买" : "卖", color: ChartPalette.up, dashed: true)) }
        if let p = trade.exitPrice { out.append(ChartLine(price: p, title: "平", color: ChartPalette.accent, dashed: true)) }
        return out
    }

    private func ranges(_ data: BacktestChartData) -> (current: FibRange?, past: [FibRange]) {
        let all = data.weeklyRanges.map { w in
            FibRange(high: w.high, low: w.low, upper: w.upper, lower: w.lower, start: w.endMs / 1000, end: (w.expiresMs ?? w.endMs + 6 * 86_400_000) / 1000)
        }
        let entry = (trade.entryMs ?? 0) / 1000
        let current = all.last { $0.start <= entry + 86_400 && entry < $0.end }
        return (current, all.filter { $0 != current })
    }

    private func load() async {
        error = nil
        do {
            let value: BacktestChartData = try await API.get("/v1/monitor/backtest/candles", query: [
                URLQueryItem(name: "track", value: key), URLQueryItem(name: "trade", value: String(Int(trade.id))),
                URLQueryItem(name: "period", value: period.rawValue)], connection: connection)
            data = value; generation += 1
        } catch is CancellationError {
        } catch { self.error = error.localizedDescription }
    }
}
