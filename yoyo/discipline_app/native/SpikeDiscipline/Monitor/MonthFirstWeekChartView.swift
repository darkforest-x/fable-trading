import Observation
import SwiftUI

/// A shared event replay / monitored-symbol chart request. Event ids and symbols are mutually exclusive
/// because the service contract intentionally keeps these two query modes separate.
struct MonthFirstWeekChartRequest: Hashable, Identifiable {
    enum Mode: Hashable { case event(String), symbol(String) }
    let mode: Mode

    var id: String {
        switch mode {
        case .event(let eventId): "event:\(eventId)"
        case .symbol(let symbol): "symbol:\(symbol)"
        }
    }

    var eventId: String? { if case .event(let value) = mode { value } else { nil } }
    var symbol: String? { if case .symbol(let value) = mode { value } else { nil } }
    var isEvent: Bool { eventId != nil }

    static func event(_ eventId: String) -> Self { Self(mode: .event(eventId)) }
    static func symbol(_ symbol: String) -> Self { Self(mode: .symbol(symbol)) }
}

@MainActor @Observable
final class MonthFirstWeekChartFeed {
    private(set) var snapshot: MonthFirstWeekChartSnapshot?
    private(set) var loading = false
    private(set) var error: String?

    func load(request: MonthFirstWeekChartRequest, period: CandlePeriod, connection: Connection?) async {
        loading = true
        error = nil
        defer { loading = false }

        do {
            var query: [URLQueryItem] = []
            if let eventId = request.eventId {
                query.append(URLQueryItem(name: "event_id", value: eventId))
            } else if let symbol = request.symbol {
                query.append(URLQueryItem(name: "symbol", value: symbol))
            } else {
                throw APIError(status: 400, message: "缺少触价记录或合约")
            }
            query.append(URLQueryItem(name: "period", value: period.rawValue))

            let next: MonthFirstWeekChartSnapshot = try await API.get(
                "/v1/monitor/month-first-week-1618/chart", query: query, connection: connection)
            if let eventId = request.eventId {
                try next.validate(eventId: eventId, period: period)
            } else if let symbol = request.symbol {
                try next.validate(symbol: symbol, period: period)
            }
            guard !Task.isCancelled else { return }
            snapshot = next
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            if let api = error as? APIError, api.status == 404 {
                self.error = "这条月首周触价记录暂时没有图表（404）"
            } else if let api = error as? APIError, api.status == 503 {
                self.error = "月首周 K 线服务暂不可用（503）"
            } else {
                self.error = error.localizedDescription
            }
        }
    }
}

enum MonthFirstWeekPresentation: Identifiable {
    case chooser
    case chart(MonthFirstWeekChartRequest)

    var id: String {
        switch self {
        case .chooser: "chooser"
        case .chart(let request): request.id
        }
    }
}

/// Searchable universe picker for the monthly chart's symbol-only mode. ETH is pinned first when it
/// exists in the monitor snapshot, with every other monitored contract available through search.
struct MonthFirstWeekSymbolPicker: View {
    let symbols: [String]
    let onSelect: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""

    private var rows: [String] {
        let unique = Array(Set(symbols)).sorted()
        let prioritized = unique.sorted { a, b in
            if a == "ETH-USDT-SWAP" { return true }
            if b == "ETH-USDT-SWAP" { return false }
            return a < b
        }
        guard !search.isEmpty else { return prioritized }
        let q = search.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        return prioritized.filter { $0.localizedCaseInsensitiveContains(q) || Fmt.coin($0).localizedCaseInsensitiveContains(q) }
    }

    var body: some View {
        List {
            Section {
                ForEach(rows, id: \.self) { symbol in
                    Button {
                        Haptics.open()
                        onSelect(symbol)
                    } label: {
                        HStack(spacing: 10) {
                            CoinLogo(coin: Fmt.coin(symbol), size: 30)
                            Text(Fmt.coin(symbol)).font(.headline).foregroundStyle(Color.ink)
                            Spacer()
                            if symbol == "ETH-USDT-SWAP" { Pill(text: "默认", color: .blue) }
                            Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
                        }
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
            } header: {
                Text("选择监控合约 · \(symbols.count)")
            } footer: {
                Text("月首周触价前也可查看当月 K 线；有已知区间水平时会显示参考线。")
            }

            if rows.isEmpty {
                Text(search.isEmpty ? "当前没有可选的监控合约" : "没有匹配的合约")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .scrollContentBackground(.hidden)
        .navigationTitle("看月度 K 线")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $search, prompt: "搜索合约")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } } }
    }
}

/// Event-specific historical replay and symbol-only live view share the monthly 1.618 chart surface.
struct MonthFirstWeekChartView: View {
    let request: MonthFirstWeekChartRequest
    let connection: Connection?
    @Environment(\.dismiss) private var dismiss
    @State private var feed = MonthFirstWeekChartFeed()
    @State private var period: CandlePeriod = .h1
    @State private var generation = 0
    @State private var retry = 0

    private var chartReady: Bool {
        guard let snapshot = feed.snapshot else { return false }
        return snapshot.bar == period.rawValue && !snapshot.candles.isEmpty
    }

    var body: some View {
        VStack(spacing: 10) {
            if let snapshot = feed.snapshot, snapshot.bar == period.rawValue {
                summary(snapshot)
            }

            ZStack {
                if let snapshot = feed.snapshot, snapshot.bar == period.rawValue, !snapshot.candles.isEmpty {
                    chart(snapshot)
                } else if feed.loading {
                    ProgressView("正在读取 K 线")
                        .font(.footnote).foregroundStyle(Color.muted)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = feed.error {
                    retryState(title: error)
                } else if let snapshot = feed.snapshot, snapshot.bar == period.rawValue, snapshot.candles.isEmpty {
                    retryState(title: "当前周期暂无 K 线数据")
                } else {
                    ProgressView("正在读取 K 线")
                        .font(.footnote).foregroundStyle(Color.muted)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .frame(minHeight: 250)
            .glassCard(cornerRadius: 22)

            PeriodChips(selection: $period)

            if chartReady, let error = feed.error {
                HStack(spacing: 8) {
                    Label(error, systemImage: "exclamationmark.arrow.triangle.2.circlepath")
                        .font(.caption).foregroundStyle(Color.amber).lineLimit(2)
                    Spacer(minLength: 4)
                    Button("重试") { retry += 1 }.buttonStyle(.pill(.soft))
                }
                .padding(.horizontal, 4)
            }
        }
        .padding(.horizontal, 12).padding(.bottom, 12)
        .containerBackground(for: .navigation) { AmbientBackground() }
        .navigationTitle("月首周 K 线")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        .onChange(of: period) { _, _ in generation += 1 }
        .task(id: "\(request.id)|\(period.rawValue)|\(retry)") {
            await feed.load(request: request, period: period, connection: connection)
            guard !request.isEvent else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                guard !Task.isCancelled else { return }
                await feed.load(request: request, period: period, connection: connection)
            }
        }
    }

    private func summary(_ data: MonthFirstWeekChartSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 8) {
                CoinLogo(coin: Fmt.coin(data.symbol), size: 31)
                Text(Fmt.coin(data.symbol)).font(.headline).foregroundStyle(Color.ink)
                Text("纽约 · \(data.month)").font(.caption.monospacedDigit()).foregroundStyle(Color.muted)
                Spacer(minLength: 4)
                if let side = data.side, let level = data.level {
                    Pill(text: "\(MonthTouchStyle.edge(side)) 1.618 · \(MonthTouchStyle.price(level))", color: MonthTouchStyle.tint)
                } else {
                    Pill(text: "监控中", color: .secondary)
                }
            }

            HStack(spacing: 7) {
                rangeValue("首周高", data.anchor.high)
                rangeValue("首周低", data.anchor.low)
                rangeValue("上 1.618", data.anchor.upper)
                rangeValue("下 1.618", data.anchor.lower)
            }

            if let source = data.source {
                HStack(spacing: 5) {
                    Image(systemName: source == "history" ? "clock.arrow.circlepath" : "dot.radiowaves.left.and.right")
                    Text(source == "history" ? "历史回补" : "实时首次触价")
                    if source == "history", period.seconds < CandlePeriod.h1.seconds,
                       let marker = data.locatedMarkerMs {
                        Text("· \(period.rawValue) 首碰已定位")
                        Text(CandleChart.fullTime(marker / 1000) + " 北京时间")
                            .foregroundStyle(Color.ink)
                    } else if source == "history", data.locatedMarkerMs == nil {
                        Text("· 触价 K 线暂缺，未替代标记")
                            .foregroundStyle(Color.amber)
                    } else if let marker = data.locatedMarkerMs {
                        Text("· \(period.rawValue) \(CandleChart.fullTime(marker / 1000)) 开盘")
                            .foregroundStyle(Color.ink)
                    } else {
                        Text("· 触价 K 线暂缺，未替代标记")
                            .foregroundStyle(Color.amber)
                    }
                }
                .font(.caption2.monospacedDigit()).foregroundStyle(Color.muted).lineLimit(1).minimumScaleFactor(0.8)
            }
        }
        .padding(12).glassCard(cornerRadius: 18)
    }

    private func rangeValue(_ title: String, _ value: Double?) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption2).foregroundStyle(Color.muted).lineLimit(1)
            Text(value.map(MonthTouchStyle.price) ?? "—")
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                .foregroundStyle(value == nil ? Color.muted : Color.ink)
                .lineLimit(1).minimumScaleFactor(0.72)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 8).padding(.vertical, 7)
        .background(Color.field.opacity(0.75), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
    }

    private func chart(_ data: MonthFirstWeekChartSnapshot) -> some View {
        let gray = ChartPalette.hex(0x8B9099)
        let palette = ChartColors(up: gray, down: gray, monday: nil)
        let flagged = data.chartHighlightedBarTimes
        return ZStack(alignment: .topLeading) {
            CandleChart(bars: data.chartCandles, generation: generation, period: period,
                        decimals: priceDecimals(data), range: data.chartRange, lines: data.chartLines,
                        markers: data.chartMarkers, focusRange: data.chartFocusRange, timestampFocus: true,
                        highlightedBarTimes: flagged ?? [],
                        highlightRange: flagged == nil ? data.chartHighlightRange : nil,
                        highlightColor: ChartPalette.hex(0x30D158), focusBarTime: data.locatedMarkerMs.map { $0 / 1000 },
                        fitReferenceLevels: false, colors: palette, watermark: Fmt.coin(data.symbol) + "USDT")
        }
    }

    private func priceDecimals(_ data: MonthFirstWeekChartSnapshot) -> Int {
        let candlePrices = data.candles.flatMap { [$0.open, $0.high, $0.low, $0.close] }
        let referencePrices = [data.level, data.anchor.high, data.anchor.low, data.anchor.upper, data.anchor.lower].compactMap { $0 }
        guard let smallest = (candlePrices + referencePrices).filter({ $0.isFinite && $0 > 0 }).min() else { return 4 }
        // Four significant leading places, clamped to the chart's 12-decimal formatting limit.
        return min(12, max(2, 4 - Int(floor(log10(smallest)))))
    }

    private func retryState(title: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: "chart.xyaxis.line").font(.title3).foregroundStyle(Color.muted)
            Text(title).font(.footnote).foregroundStyle(Color.muted).multilineTextAlignment(.center)
            Button("重试") { retry += 1 }.buttonStyle(.pill(.soft))
        }
        .padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
