import SwiftUI

/// 监控 · 触价 (owner Notion #76, 2026-11-07): BTC's position against this week's NY Monday range on
/// top, then today's 1.618 touches with 全部/做多/做空 and one sort icon (最新 / 最早 / 评分 / 成交量).
/// Read-only: rows open the coin's chart; nothing here places orders.
struct TouchesSection: View {
    @Environment(AccountStore.self) private var account
    @Environment(SessionStore.self) private var session
    @Environment(TouchFeed.self) private var feed
    @Environment(\.openChart) private var openChart
    let connection: Connection
    let refresh: Int
    @AppStorage("touch.source") private var source = "monday"
    @AppStorage("touch.side") private var side = "all"
    @AppStorage("touch.sort") private var sort = "latest"
    @State private var sortMenu = false

    private static let sorts: [(String, String)] = [("latest", "最新"), ("earliest", "最早"), ("score", "评分"), ("volume", "成交量")]

    private var rows: [PaperSignal] {
        let quotes = account.state?.quotes ?? [:]
        let filtered = feed.signals.filter { side == "all" || (side == "long" ? $0.side == .long : $0.side == .short) }
        switch sort {
        case "earliest": return filtered.sorted { ($0.observedMs ?? 0) < ($1.observedMs ?? 0) }
        case "score": return filtered.sorted { ($0.score ?? -1) > ($1.score ?? -1) }
        case "volume":
            return filtered.sorted { (Trading.turnover(quotes[$0.symbol]) ?? -1) > (Trading.turnover(quotes[$1.symbol]) ?? -1) }
        default: return filtered.sorted { ($0.observedMs ?? 0) > ($1.observedMs ?? 0) }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SoftSegment(selection: $source, options: [("monday", "周一 1.618"), ("month", "月首周 1.618")], height: 36)
            if source == "month" {
                MonthFirstWeekSection(connection: connection, refresh: refresh)
            } else {
                mondaySection
            }
        }
    }

    private var mondaySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            BTCStatusCard(connection: connection, refresh: refresh)
            HStack(spacing: 10) {
                SoftSegment(selection: $side, options: [("all", "全部"), ("long", "做多"), ("short", "做空")], height: 34)
                sortButton
            }
            .zIndex(2)
            LoadState(loading: !feed.loaded && feed.error == nil, error: feed.error, stale: feed.loaded)
            SectionHeader(title: "今日触价", count: rows.count, trailing: Self.sorts.first { $0.0 == sort }?.1)
            if feed.loaded && rows.isEmpty {
                Text(feed.signals.isEmpty ? "今天还没有触价" : "没有符合筛选的触价").font(.footnote).foregroundStyle(Color.muted)
                    .frame(maxWidth: .infinity).padding(14).glassCard(cornerRadius: 18)
            } else if !rows.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { index, signal in
                        row(signal, now: feed.nowMs)
                        if index < rows.count - 1 { Divider().padding(.leading, 54) }
                    }
                }
                .glassCard(cornerRadius: 18)
            }
        }
        .task(id: refresh) { await feed.watch(connection: connection, session: session) }
    }

    private var sortButton: some View {
        Button { Haptics.tap(); withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { sortMenu.toggle() } } label: {
            Image(systemName: "arrow.up.arrow.down").font(.system(size: 14, weight: .bold)).foregroundStyle(Color.ink)
                .frame(width: 36, height: 36).frosted(Circle(), veil: 0.8).contentShape(.circle)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("排序，当前\(Self.sorts.first { $0.0 == sort }?.1 ?? "")")
        .overlay(alignment: .topTrailing) {
            if sortMenu {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Self.sorts, id: \.0) { option in
                        Button {
                            Haptics.select(); sort = option.0
                            withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { sortMenu = false }
                        } label: {
                            HStack {
                                Text(option.1).font(.scaled(size: 15, weight: .semibold)).foregroundStyle(Color.ink)
                                Spacer(minLength: 16)
                                if sort == option.0 { Image(systemName: "checkmark").font(.system(size: 13, weight: .bold)).foregroundStyle(Color.accentColor) }
                            }
                            .padding(.horizontal, 14).frame(width: 150, height: 40).contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 6)
                .frosted(RoundedRectangle(cornerRadius: 18, style: .continuous), veil: 0.9)
                .offset(y: 44)
                .transition(.scale(scale: 0.85, anchor: .topTrailing).combined(with: .opacity))
            }
        }
    }

    private func row(_ signal: PaperSignal, now: Double) -> some View {
        let volume = Trading.turnover(account.state?.quotes[signal.symbol])
        let excluded = signal.status == "excluded" || signal.status == "feature_error"
        return Button { Haptics.open(); openChart(signal.symbol) } label: {
            HStack(spacing: 11) {
                CoinLogo(coin: Fmt.coin(signal.symbol), size: 32)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(Fmt.coin(signal.symbol)).font(.scaled(size: 16, weight: .semibold)).foregroundStyle(Color.ink)
                        SidePill(side: signal.side)
                    }
                    Text(sort == "volume" ? Trading.turnoverLabel(volume) : Fmt.clock(signal.observedMs, now: now))
                        .font(.scaled(size: 12).monospacedDigit()).foregroundStyle(Color.muted).lineLimit(1)
                }
                Spacer()
                ScoreBadge(score: signal.score)
                Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 12).frame(minHeight: 56).contentShape(.rect)
            .opacity(excluded ? 0.5 : 1)
        }
        .buttonStyle(RowPressStyle())
    }
}

/// BTC against this week's NY Monday range (high/low of Monday 00:00–24:00 America/New_York, 1.618
/// extensions = high/low ± 0.618 × range, as the 1.618 monitor). Owner rule of thumb: BTC below the
/// lower 1.618 → watch altcoin longs; above the upper 1.618 → watch altcoin shorts. Display only.
struct BTCStatusCard: View {
    @Environment(\.openChart) private var openChart
    let connection: Connection
    let refresh: Int
    @State private var range: (high: Double, low: Double, complete: Bool)?
    @State private var price: Double?
    @State private var failed = false

    private static let symbol = "BTC-USDT-SWAP"

    var body: some View {
        Button { Haptics.open(); openChart(Self.symbol) } label: { card }
            .buttonStyle(CardPressStyle())
            .task(id: refresh) { await loop() }
    }

    private var status: (text: String, tone: Color)? {
        guard let range, let price else { return nil }
        let r = range.high - range.low, upper = range.high + 0.618 * r, lower = range.low - 0.618 * r
        if !range.complete { return ("周一区间形成中 · 等待周一收盘", Color.muted) }
        if price < lower { return ("跌破下沿 1.618 · 关注山寨做多机会", ChartPalette.up) }
        if price > upper { return ("突破上沿 1.618 · 关注山寨做空机会", ChartPalette.down) }
        if price < range.low { return ("周一低与下 1.618 之间 · 接近下沿", ChartPalette.up.opacity(0.85)) }
        if price > range.high { return ("周一高与上 1.618 之间 · 接近上沿", ChartPalette.down.opacity(0.85)) }
        return ("周一区间内 · 等待", Color.muted)
    }

    private var card: some View {
        let s = status
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                CoinLogo(coin: "BTC", size: 32)
                VStack(alignment: .leading, spacing: 1) {
                    Text("BTC").font(.headline).foregroundStyle(Color.ink)
                    Text(s?.text ?? (failed ? "BTC 区间暂不可用" : "正在读取 BTC 周一区间…")).font(.footnote.weight(.semibold))
                        .foregroundStyle(s?.tone ?? Color.muted)
                }
                Spacer()
                Text(price.map { $0.formatted(.number.precision(.fractionLength(1))) } ?? "—")
                    .font(.title3.weight(.bold).monospacedDigit()).foregroundStyle(Color.ink)
                    .contentTransition(.numericText(value: price ?? 0)).animation(.snappy, value: price)
                Image(systemName: "chevron.right").font(.caption.weight(.bold)).foregroundStyle(.tertiary)
            }
            if let range, let price, range.complete { RangeBar(high: range.high, low: range.low, price: price) }
        }
        .padding(14)
        .glassCard(cornerRadius: 20, tint: status?.tone == ChartPalette.up ? .up : status?.tone == ChartPalette.down ? .down : nil)
    }

    private func loop() async {
        while !Task.isCancelled {
            await load()
            try? await Task.sleep(for: .seconds(30))
        }
    }

    /// 1H bars from the service relay; Monday = NY calendar Monday of the current NY week.
    private func load() async {
        do {
            let page: CandlePage = try await API.get("/v1/market/candles", query: [.init(name: "symbol", value: Self.symbol), .init(name: "period", value: "1H")], connection: connection)
            let bars = Candle.parse(page.data)
            guard let last = bars.last else { throw APIError(status: 0, message: "empty") }
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = TimeZone(identifier: "America/New_York")!
            calendar.firstWeekday = 2
            let now = Date()
            guard let week = calendar.dateInterval(of: .weekOfYear, for: now) else { return }
            let monday = week.start.timeIntervalSince1970, end = monday + 86_400
            let mondayBars = bars.filter { $0.time >= monday && $0.time < end }
            guard let high = mondayBars.map(\.high).max(), let low = mondayBars.map(\.low).min() else { throw APIError(status: 0, message: "no monday") }
            withAnimation(.snappy) {
                range = (high, low, now.timeIntervalSince1970 >= end)
                price = last.close
            }
            failed = false
        } catch is CancellationError {
        } catch { failed = true }
    }
}

/// 下1.618 · 周一低 · 周一高 · 上1.618 on one rail with the current price dot (clamped at the ends).
struct RangeBar: View {
    let high: Double
    let low: Double
    let price: Double
    private var upper: Double { high + 0.618 * (high - low) }
    private var lower: Double { low - 0.618 * (high - low) }
    private func f(_ v: Double) -> CGFloat { CGFloat(min(max((v - lower) / max(upper - lower, 1e-9), 0), 1)) }

    var body: some View {
        GeometryReader { g in
            let w = g.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(Color.field).frame(height: 6).offset(y: -6)
                Capsule().fill(ChartPalette.monday.opacity(0.35)).frame(width: (f(high) - f(low)) * w, height: 6).offset(x: f(low) * w, y: -6)
                ForEach([(lower, "下1.618", ChartPalette.lower), (low, "周一低", ChartPalette.monday), (high, "周一高", ChartPalette.monday), (upper, "上1.618", ChartPalette.upper)], id: \.1) { tick in
                    VStack(spacing: 3) {
                        Rectangle().fill(tick.2).frame(width: 2, height: 12)
                        Text(tick.1).font(.scaled(size: 9, weight: .semibold)).foregroundStyle(tick.2).fixedSize()
                    }
                    .position(x: min(max(f(tick.0) * w, 18), w - 18), y: 18)
                }
                Circle().fill(Color.ink).frame(width: 12, height: 12).overlay(Circle().stroke(.white, lineWidth: 2))
                    .position(x: f(price) * w, y: 6)
            }
        }
        .frame(height: 36)
        .accessibilityLabel("BTC 区间位置")
    }
}
