import SwiftUI

/// One paper account (2.7.0 strategy frame, Stitch native-2.7.0 account-breadth): equity, realized P&L
/// and win rate, the strategy booklet and saved backtest trades, then 持仓 / 已结束 / 触价 (信号 for
/// live-breadth). Breadth rows keep their semantics: SOL is funded, BTC/ETH are comparison shadows
/// that never move the account, and holdings show the six-day exit. Read-only simulation.
struct AccountDetailView: View {
    enum Tab: String, Hashable { case open, closed, events }

    @Environment(\.openChart) private var openChart
    @Environment(\.dismiss) private var dismiss
    let account: PaperAccount
    let connection: Connection
    @State private var data: PaperSnapshot?
    @State private var error: String?
    @State private var tab: Tab = .open
    @State private var older = OlderRecords()

    private var current: PaperAccount { data?.accounts.first { $0.key == account.key } ?? account }
    private var detail: PaperSnapshot.Detail? { data?.detail.key == account.key ? data?.detail : nil }
    private var now: Double { data?.nowMs ?? Date().timeIntervalSince1970 * 1000 }

    var body: some View {
        let a = current
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header(a)
                actions(a)
                LoadState(loading: detail == nil && error == nil, error: error, stale: detail != nil)
                if let detail {
                    SoftSegment(selection: $tab, options: [(.open, "持仓 \(detail.open.count)"),
                                                           (.closed, "已结束 \(closedRows(detail).count)"),
                                                           (.events, (a.isBreadth ? "信号 " : "触价 ") + "\(eventCount(detail, breadth: a.isBreadth))")])
                    switch tab {
                    case .open: openList(detail.open, breadth: a.isBreadth)
                    case .closed: closedList(detail, breadth: a.isBreadth)
                    case .events: eventList(detail, breadth: a.isBreadth)
                    }
                }
            }
            .padding(.horizontal, 14).padding(.bottom, 28)
        }
        .scrollEdgeEffectHidden(true, for: .top)
        .containerBackground(for: .navigation) { AmbientBackground() }
        .navigationTitle(account.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        .modifier(Polled(key: "paper-\(account.key)", every: .seconds(10), load: {
            try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: account.key)], connection: connection)
        }, value: $data, error: $error))
    }

    // MARK: header

    private func header(_ a: PaperAccount) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Text(a.subtitle.isEmpty ? a.label : a.subtitle).font(.footnote.weight(.semibold)).foregroundStyle(Color.muted)
                if a.isBreadth { BreadthTag(account: a) }
                Spacer(minLength: 0)
                Pill(text: "模拟", color: .accentColor)
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(Fmt.money(a.equity)).font(.scaled(size: 40, weight: .semibold, design: .rounded)).monospacedDigit()
                    .foregroundStyle(Color.ink).lineLimit(1).minimumScaleFactor(0.6)
                    .contentTransition(.numericText(value: a.equity ?? 0))
                Text("U").font(.title3).foregroundStyle(Color.muted)
                Spacer(minLength: 4)
                ChangeCapsule(value: a.returnPct)
            }
            Text("已实现盈亏 \(StrategyFmt.signedMoney(a.pnl)) U · 起始 \(Fmt.money(a.startEquity)) U" + (a.startedMs.map { " · \(Fmt.clock($0, now: now)) 起" } ?? "") + (a.refilled == true ? " · 含补充本金" : ""))
                .font(.caption).foregroundStyle(Color.muted).monospacedDigit().lineLimit(2)
            HStack(spacing: 0) {
                stat("胜率", a.stats.winPct.map { "\(StrategyFmt.number($0, digits: 0))%" } ?? "—")
                Divider().frame(height: 26)
                stat("已平", "\(Int(a.stats.closed ?? 0)) 笔")
                Divider().frame(height: 26)
                stat("平均", a.stats.meanNetBp.map { String(format: "%+.0fbp", $0) } ?? "—", tone: Fmt.tone(a.stats.meanNetBp).color)
                Divider().frame(height: 26)
                stat("持仓", "\(a.openCount ?? detail?.open.filter(\.funded).count ?? 0)", tone: .accentColor)
            }
            .padding(.vertical, 9)
            .background(Color.field.opacity(0.8), in: .rect(cornerRadius: 14))
            if a.isBreadth, let rule = a.breadthRule { breadthRule(rule) }
        }
        .padding(16).glassCard(cornerRadius: 26)
    }

    private func stat(_ label: String, _ value: String, tone: Color = .ink) -> some View {
        VStack(spacing: 3) {
            Text(label).font(.caption2).foregroundStyle(Color.muted)
            Text(value).font(.system(.footnote, design: .monospaced).weight(.semibold)).foregroundStyle(tone == .secondary ? Color.ink : tone)
                .lineLimit(1).minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity)
    }

    private func breadthRule(_ rule: PaperAccount.BreadthRule) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            FlowLayout(spacing: 6) {
                chip("\(Int(rule.countMin ?? 0))–\(Int(rule.countMax ?? 0)) 个币 / \(Int(rule.barMinutes ?? 5))分钟")
                chip("观察 \(Int(rule.observeMinutes ?? 0)) 分钟零新增")
                chip("持有 \(Int(rule.holdDays ?? 0)) 天")
            }
            let shadows = rule.shadows.map(Fmt.coin).joined(separator: " / ")
            Label("\(Fmt.coin(rule.funded ?? "SOL")) 计入资金；\(shadows.isEmpty ? "对照币" : shadows) 同额影子，只作对照，不动资金", systemImage: "info.circle")
                .font(.caption2).foregroundStyle(Color.muted)
                .padding(.horizontal, 10).padding(.vertical, 7).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.field.opacity(0.8), in: .rect(cornerRadius: 12))
        }
    }

    private func chip(_ text: String) -> some View {
        Text(text).font(.caption2.weight(.medium)).foregroundStyle(Color.ink)
            .padding(.horizontal, 9).padding(.vertical, 4).background(Color.field, in: .capsule)
    }

    private func actions(_ a: PaperAccount) -> some View {
        HStack(spacing: 10) {
            NavigationLink(value: StrategyPage.booklet(key: a.key, label: a.label)) {
                Label("策略小册", systemImage: "book.closed").frame(maxWidth: .infinity)
            }
            .buttonStyle(.pill(.tinted(.accentColor), height: 42))
            NavigationLink(value: StrategyPage.backtest(key: a.key, label: a.label)) {
                Label("历史开单", systemImage: "chart.bar.xaxis").frame(maxWidth: .infinity)
            }
            .buttonStyle(.pill(.tinted(.accentColor), height: 42))
        }
    }

    // MARK: lists

    private func closedRows(_ d: PaperSnapshot.Detail) -> [PaperPosition] {
        (d.closed + older.positions).uniqued(by: \.rowKey)
    }

    private func eventCount(_ d: PaperSnapshot.Detail, breadth: Bool) -> Int {
        breadth ? ((d.breadthSignals ?? []) + older.signals).uniqued(by: \.rowId).count
            : ((d.touches ?? []) + older.touches).uniqued(by: \.rowId).count
    }

    @ViewBuilder private func openList(_ rows: [PaperPosition], breadth: Bool) -> some View {
        card(empty: rows.isEmpty ? (breadth ? "当前没有持仓 · 等待下一次宽度信号" : "当前没有模拟持仓") : nil) {
            ForEach(Array(rows.enumerated()), id: \.element.rowKey) { index, p in
                Button { Haptics.open(); openChart(p.symbol) } label: {
                    Group {
                        if breadth { BreadthPositionRow(position: p, now: now, closed: false) }
                        else { PositionRow(position: p, now: now, closed: false) }
                    }
                    .padding(.horizontal, 12).padding(.vertical, 10).contentShape(.rect)
                }
                .buttonStyle(RowPressStyle())
                if index < rows.count - 1 { Divider().padding(.leading, 54) }
            }
        }
        if !breadth { Text("参考浮动收益未扣费；策略净值包含已减仓盈亏，不包含余仓浮盈。").font(.caption2).foregroundStyle(Color.muted).padding(.horizontal, 4) }
    }

    @ViewBuilder private func closedList(_ d: PaperSnapshot.Detail, breadth: Bool) -> some View {
        let rows = closedRows(d)
        card(empty: rows.isEmpty && !older.loading ? "还没有已结束记录" : nil) {
            ForEach(Array(rows.enumerated()), id: \.element.rowKey) { index, p in
                Button { Haptics.open(); openChart(p.symbol) } label: {
                    Group {
                        if breadth { BreadthPositionRow(position: p, now: now, closed: true) }
                        else { PositionRow(position: p, now: now, closed: true) }
                    }
                    .padding(.horizontal, 12).padding(.vertical, 10).contentShape(.rect)
                }
                .buttonStyle(RowPressStyle())
                if index < rows.count - 1 { Divider().padding(.leading, 54) }
            }
        }
        loadMore(tab: "closed")
        Text(breadth ? "SOL 净收益已扣双边手续费；BTC/ETH 对照只看收益率，不计入资金。" : "净收益已扣手续费；滑点情景仅供对照，不重复扣入策略净值。")
            .font(.caption2).foregroundStyle(Color.muted).padding(.horizontal, 4)
    }

    @ViewBuilder private func eventList(_ d: PaperSnapshot.Detail, breadth: Bool) -> some View {
        if breadth {
            let rows = ((d.breadthSignals ?? []) + older.signals).uniqued(by: \.rowId)
            card(empty: rows.isEmpty && !older.loading ? "还没有信号" : nil) {
                ForEach(Array(rows.enumerated()), id: \.element.rowId) { index, s in
                    BreadthSignalRow(signal: s, now: now).padding(.horizontal, 12).padding(.vertical, 11)
                    if index < rows.count - 1 { Divider().padding(.leading, 14) }
                }
            }
            loadMore(tab: "events")
            Text("同一根5分钟K 31–62 个 OKX 币首次碰周一下轨、之后30分钟无新币才开仓；31–62 是币安 50–99 的近似换算。")
                .font(.caption2).foregroundStyle(Color.muted).padding(.horizontal, 4)
        } else {
            let rows = ((d.touches ?? []) + older.touches).uniqued(by: \.rowId)
            card(empty: rows.isEmpty && !older.loading ? "还没有触价记录" : nil) {
                ForEach(Array(rows.enumerated()), id: \.element.rowId) { index, s in
                    Button { Haptics.open(); openChart(s.symbol) } label: {
                        TouchRow(signal: s, now: now).padding(.horizontal, 12).padding(.vertical, 10).contentShape(.rect)
                    }
                    .buttonStyle(RowPressStyle())
                    if index < rows.count - 1 { Divider().padding(.leading, 54) }
                }
            }
            loadMore(tab: "events")
            Text(account.key == "live-allin3" ? "本策略独立触价前评分，门槛 0.6；分数不是胜率。" : "共用触价记录；评分 > 0.5 高亮，非胜率。各策略是否入场请看持仓与已结束。")
                .font(.caption2).foregroundStyle(Color.muted).padding(.horizontal, 4)
        }
    }

    private func card<Content: View>(empty: String?, @ViewBuilder content: () -> Content) -> some View {
        VStack(spacing: 0) {
            if let empty { Text(empty).font(.footnote).foregroundStyle(Color.muted).frame(maxWidth: .infinity).padding(18) }
            content()
        }
        .glassCard(cornerRadius: 20)
    }

    /// 已结束 / 触价 list today's rows from the snapshot; older pages come from /v1/monitor/paper/history.
    @ViewBuilder private func loadMore(tab: String) -> some View {
        let page = tab == "closed" ? older.closedPage : older.eventPage
        if older.loading && older.loadingTab == tab {
            HStack(spacing: 8) { ProgressView(); Text("正在读取更早记录").font(.footnote).foregroundStyle(Color.muted) }
                .frame(maxWidth: .infinity).padding(.vertical, 8)
        } else if page == nil || page?.hasMore == true {
            Button { Task { await loadOlder(tab) } } label: {
                Text(page == nil ? "加载更早记录" : "再加载 50 条").frame(maxWidth: .infinity)
            }
            .buttonStyle(.pill(.soft, height: 40))
        } else if let page {
            Text("更早记录已全部显示 · 共 \(page.total) 条").font(.caption2).foregroundStyle(Color.muted).frame(maxWidth: .infinity)
        }
        if let message = older.error, older.errorTab == tab {
            Label(message, systemImage: "exclamationmark.arrow.triangle.2.circlepath").font(.footnote).foregroundStyle(Color.amber)
        }
    }

    private func loadOlder(_ tab: String) async {
        guard !older.loading else { return }
        older.loading = true; older.loadingTab = tab; older.error = nil
        let offset = tab == "closed" ? older.closedOffset : older.eventOffset
        do {
            let page: PaperHistoryPage = try await API.get("/v1/monitor/paper/history", query: [
                URLQueryItem(name: "track", value: account.key), URLQueryItem(name: "tab", value: tab),
                URLQueryItem(name: "offset", value: String(offset))], connection: connection)
            withAnimation(.snappy) {
                if tab == "closed" {
                    older.positions += page.positions; older.closedPage = page; older.closedOffset += page.count
                } else {
                    older.touches += page.touches; older.signals += page.breadthSignals; older.eventPage = page; older.eventOffset += page.count
                }
            }
        } catch {
            older.error = error.localizedDescription; older.errorTab = tab
        }
        older.loading = false
    }
}

/// Paged older records held by the account sheet.
struct OlderRecords {
    var positions: [PaperPosition] = []
    var touches: [PaperSignal] = []
    var signals: [BreadthSignal] = []
    var closedPage: PaperHistoryPage?
    var eventPage: PaperHistoryPage?
    var closedOffset = 0
    var eventOffset = 0
    var loading = false
    var loadingTab = ""
    var error: String?
    var errorTab = ""
}

/// live-breadth holding / closed row: funded tag or comparison shadow, six-day progress.
struct BreadthPositionRow: View {
    let position: PaperPosition
    let now: Double
    let closed: Bool
    private var shadow: Bool { position.shadow == true || !position.funded }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 11) {
                CoinLogo(coin: Fmt.coin(position.symbol), size: 30)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 5) {
                        Text(Fmt.coin(position.symbol)).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                        SidePill(side: position.side)
                        Pill(text: shadow ? "影子 · 对照" : "计入资金", color: shadow ? .secondary : .accentColor)
                    }
                    Text(closed ? "6天到期平仓 · \(Fmt.clock(position.exitMs, now: now))" : "买入 \(Fmt.price(position.entry)) · \(Fmt.clock(position.fillMs, now: now))")
                        .font(.caption2).foregroundStyle(Color.muted).lineLimit(1)
                }
                Spacer(minLength: 4)
                VStack(alignment: .trailing, spacing: 2) {
                    if closed {
                        if shadow {
                            Text(position.netBp.map { Fmt.signedPercent($0 / 100) } ?? "—").font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(position.netBp).color)
                            Text("不计入资金").font(.caption2).foregroundStyle(Color.muted)
                        } else {
                            Text(StrategyFmt.signedMoney(position.pnl) + " U").font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(position.pnl).color)
                            Text(position.netBp.map { Fmt.signedPercent($0 / 100) } ?? "").font(.caption2).foregroundStyle(Color.muted)
                        }
                    } else {
                        Text(position.unrealizedBp.map { Fmt.signedPercent($0 / 100) } ?? "—").font(.subheadline.weight(.semibold))
                            .foregroundStyle(Fmt.tone(position.unrealizedBp).color)
                        Text(shadow ? "不计入资金" : "浮动 · 未扣费").font(.caption2).foregroundStyle(Color.muted)
                    }
                }
                .monospacedDigit()
            }
            if !closed, let fill = position.fillMs, let due = position.dueMs {
                VStack(spacing: 4) {
                    HoldBar(fillMs: fill, dueMs: due, now: now)
                    HStack {
                        Text("持有 \(StrategyFmt.span(now - fill)) / 6天")
                        Spacer()
                        Text("到期 \(Fmt.clock(due, now: now)) 平仓")
                    }
                    .font(.caption2).foregroundStyle(Color.muted).monospacedDigit()
                }
                .padding(.horizontal, 10).padding(.vertical, 8)
                .background(Color.field.opacity(0.7), in: .rect(cornerRadius: 12))
            }
        }
    }
}

struct BreadthSignalRow: View {
    let signal: BreadthSignal
    let now: Double
    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(Fmt.clock(signal.candleMs, now: now)).font(.subheadline.weight(.semibold)).monospacedDigit().foregroundStyle(Color.ink)
                Text(signal.n.map { "\(Int($0)) 个币首次碰周一下轨" } ?? "币数待读取").font(.caption2).foregroundStyle(Color.muted)
            }
            Spacer(minLength: 6)
            let tone = StrategyFmt.breadthTone(signal.status)
            let color: Color = tone == .open ? .up : tone == .watch ? .accentColor : tone == .warn ? .amber : .secondary
            Text(StrategyFmt.breadthStatus(signal, now: now)).font(.caption.weight(.semibold)).foregroundStyle(color)
                .padding(.horizontal, 9).padding(.vertical, 4).background(color.opacity(0.12), in: .capsule)
                .lineLimit(1).minimumScaleFactor(0.8)
        }
    }
}

/// 1.618 touch row of one account (score highlighted above 0.5; a score is not a win rate).
struct TouchRow: View {
    let signal: PaperSignal
    let now: Double
    var body: some View {
        HStack(spacing: 11) {
            CoinLogo(coin: Fmt.coin(signal.symbol), size: 30)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) { Text(Fmt.coin(signal.symbol)).font(.subheadline.weight(.semibold)); SidePill(side: signal.side) }
                Text("\(signal.side == .long ? "下沿" : "上沿") \(Fmt.price(signal.level)) · \(Fmt.clock(signal.observedMs, now: now))")
                    .font(.caption2).foregroundStyle(Color.muted).lineLimit(1)
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 2) {
                Text(signal.score.map { String(format: "%.3f", $0) } ?? "—").font(.subheadline.weight(.bold)).monospacedDigit()
                    .foregroundStyle((signal.score ?? 0) > 0.5 ? Color.orange : Color.ink)
                Text(Fmt.signalStatus(signal)).font(.caption2).foregroundStyle(Color.muted)
            }
        }
    }
}
