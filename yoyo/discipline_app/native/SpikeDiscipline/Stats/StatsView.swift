import Charts
import SwiftUI

/// 统计 (Statistics.tsx): app-managed trades only. Account hero, 交易纪律 link, period,
/// closed-trade summary, cumulative net PnL steps, long/short and per-symbol tables.
struct StatsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Binding var showSettings: Bool
    let active: Bool
    @AppStorage("stats.days") private var days = 30
    @State private var data: Statistics?
    @State private var dataKey = ""
    @State private var error: String?
    @State private var loading = false
    @State private var discipline = false

    private var key: String { "\(session.connection?.accountId ?? "")|\(session.connection?.mode ?? "")|\(days)" }

    var body: some View {
            ZStack {
                AmbientBackground()
                ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    PageHeader(title: "统计") {
                        RoundIcon(systemName: "gearshape") { showSettings = true }.accessibilityLabel("设置")
                    }
                    if session.connection == nil { ConnectPrompt() }
                    if let error { StatusNote(text: "统计暂不可用\(data == nil ? "" : "，以下为上次记录")：\(error)", systemImage: "exclamationmark.triangle", tint: .amber) }
                    hero
                    Button { Haptics.open(); discipline = true } label: { disciplineRow }.buttonStyle(CardPressStyle())
                    SoftSegment(selection: $days, options: [(7, "近 7 天"), (30, "近 30 天"), (0, "全部")])
                    summary
                    curve
                    directions
                    symbols
                    explainer
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 24)
            }
            .refreshable { await load(force: true); Haptics.success() }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .scrollEdgeEffectHidden(true, for: [.top, .bottom])
            }
        .sheet(isPresented: $discipline) {
            NavigationStack { DisciplineView() }.presentationDragIndicator(.visible)
        }
        .task(id: "\(key)|\(active)") {
            guard active, session.connection != nil else { return }
            while !Task.isCancelled {
                await load(force: false)
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    private func load(force: Bool) async {
        guard let connection = session.connection else { return }
        let requested = key
        loading = true
        defer { loading = false }
        do {
            let next = try await account.statistics(days: days)
            guard next.mode == connection.mode, next.periodDays == days, next.accountId == connection.accountId else { throw APIError(status: 0, message: "统计响应与当前账户不匹配") }
            guard requested == key else { return }
            withAnimation(.snappy) { data = next; dataKey = requested }
            error = nil
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription; session.handle(error)
        }
    }

    private var current: Statistics? { dataKey == key ? data : data }
    private var live: Bool { session.connection?.mode == "okx_live" }

    private func tone(_ text: String?) -> Color {
        guard let v = text.flatMap(Double.init), v != 0 else { return .primary }
        return v > 0 ? .up : .down
    }

    private var hero: some View {
        let s = current, state = account.state
        let currentAt = state.map { live ? ($0.privateAt ?? 0) : $0.serverTime } ?? 0
        let cachedAt = s.map { $0.accountUpdatedAt ?? $0.serverTime } ?? 0
        let useCurrent = state != nil && currentAt > 0 && currentAt >= cachedAt
        let equity = useCurrent ? state?.equity : s?.account.equity
        let updatedAt = useCurrent ? currentAt : cachedAt
        let stale = !account.online || updatedAt == 0 || Date().timeIntervalSince1970 - updatedAt > 15 || (useCurrent && state?.privateError != nil)
        let a = s?.account
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(live ? "OKX USDT 当前净值" : "当前模拟账户净值").font(.subheadline.weight(.semibold))
                Spacer()
                Text(updatedAt > 0 ? "\(stale ? "上次记录" : "截至") \(Self.time(updatedAt))\(loading ? " · 更新中" : "")" : loading ? "正在读取…" : "待连接")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(Fmt.amount(equity)).font(.system(size: 38, weight: .bold, design: .rounded).monospacedDigit())
                    .contentTransition(.numericText(value: Double(equity ?? "0") ?? 0)).animation(.snappy, value: equity)
                Text("USDT").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            }
            HStack(spacing: 6) {
                Text(live ? "App 累计净盈亏" : "累计净盈亏").font(.subheadline).foregroundStyle(.secondary)
                Text(Fmt.amount(a?.netPnl) + " USDT").font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(tone(a?.netPnl))
                if let r = a?.returnFraction { Text("(\(Fmt.fractionPercent(r, 2))%)").font(.subheadline.monospacedDigit()).foregroundStyle(.secondary) }
            }
            HStack {
                Metric(title: live ? "App 已实现净盈亏" : "已实现净盈亏", value: Fmt.amount(a?.realizedPnl), tone: tone(a?.realizedPnl))
                Metric(title: live ? "App 当前浮动盈亏" : "当前浮动盈亏", value: Fmt.amount(a?.unrealizedPnl), tone: tone(a?.unrealizedPnl))
            }
            if live {
                Text((a?.returnMethod == "app_managed_fixed_capital" && a?.initialEquity != nil ? "固定本金 \(Fmt.amount(a?.initialEquity)) USDT；" : "") + "盈亏仅统计本 App 管理交易，外部仓位不计入。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if stale && equity != nil { Text("当前显示上次记录，等待账户数据更新。").font(.caption).foregroundStyle(Color.amber) }
        }
        .padding(18).glassCard(cornerRadius: 26)
    }

    private var disciplineRow: some View {
        let state = account.state
        return HStack(spacing: 12) {
            Image(systemName: "checkmark.shield.fill").font(.title3).foregroundStyle(.blue)
                .frame(width: 40, height: 40).background(Color.blue.opacity(0.12), in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text("交易纪律").font(.headline).foregroundStyle(.primary)
                Text(state.map { "今日开仓 \($0.daily?.entries ?? 0) / \($0.discipline?.entriesLimit ?? $0.policy?.maxEntries ?? 3) 次 · \($0.blockers.isEmpty ? "规则生效中" : "开仓受限")" } ?? "连接后读取规则")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.subheadline.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(14).glassCard()
    }

    private var summary: some View {
        let s = current?.closed
        return VStack(alignment: .leading, spacing: 12) {
            HStack { Text("已平仓表现").font(.headline); Spacer(); Text("仅计完全平仓").font(.caption).foregroundStyle(.secondary) }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                stat("已平仓", s.map { "\($0.count)" } ?? "—", "笔")
                stat("胜率", Fmt.fractionPercent(s?.winRate), "%")
                stat("盈亏比", Fmt.amount(s?.payoffRatio), "均盈 ÷ 均亏")
                stat("平均净 R", Fmt.amount(s?.averageNetR), "R")
            }
            if let s, s.count > 0, s.payoffRatio == nil {
                Text(s.losses == 0 ? "暂无亏损样本" : s.wins == 0 ? "暂无盈利样本" : "盈亏比暂无可比样本").font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 14) {
                Text("盈利 \(s.map { "\($0.wins)" } ?? "—")").foregroundStyle(Color.up)
                Text("亏损 \(s.map { "\($0.losses)" } ?? "—")").foregroundStyle(Color.down)
                Text("净零 \(s.map { "\($0.flat)" } ?? "—")").foregroundStyle(.secondary)
            }
            .font(.subheadline.weight(.semibold).monospacedDigit())
            HStack {
                Text(live ? "周期内已平仓交易手续费" : "周期内已结手续费").font(.subheadline).foregroundStyle(.secondary)
                Spacer()
                Text(Fmt.amount(current?.fees) + " USDT").font(.subheadline.weight(.semibold).monospacedDigit())
            }
        }
        .padding(16).glassCard()
    }

    private func stat(_ title: String, _ value: String, _ unit: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2.weight(.bold).monospacedDigit()).contentTransition(.numericText())
            Text(unit).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var curve: some View {
        let points = current?.curve ?? []
        let net = current?.closed.netPnl
        return VStack(alignment: .leading, spacing: 10) {
            Text("已平仓累计净盈亏").font(.headline)
            Text(Fmt.amount(net) + " USDT").font(.title2.weight(.bold).monospacedDigit()).foregroundStyle(tone(net))
            if points.isEmpty {
                VStack(spacing: 6) {
                    Image(systemName: "chart.line.uptrend.xyaxis").font(.title2).foregroundStyle(.secondary)
                    Text(session.connection == nil ? "连接后查看统计" : loading && current == nil ? "正在读取记录…" : error != nil && current == nil ? "记录暂不可用" : "暂无已平仓记录").font(.subheadline.weight(.semibold))
                    Text("完成交易后生成统计曲线").font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 24)
            } else {
                let last = Double(points.last?.pnl ?? "0") ?? 0
                Chart {
                    RuleMark(y: .value("零", 0)).foregroundStyle(.secondary.opacity(0.4)).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 5]))
                    ForEach(points, id: \.date) { p in
                        LineMark(x: .value("日期", p.date), y: .value("净盈亏", Double(p.pnl) ?? 0))
                            .interpolationMethod(.stepEnd)
                            .foregroundStyle(last >= 0 ? Color.up : Color.down)
                            .lineStyle(StrokeStyle(lineWidth: 2.5, lineJoin: .round))
                    }
                    if let p = points.last {
                        PointMark(x: .value("日期", p.date), y: .value("净盈亏", Double(p.pnl) ?? 0)).foregroundStyle(last >= 0 ? Color.up : Color.down)
                    }
                }
                .chartXAxis { AxisMarks(values: [points.first!.date, points.last!.date]) { AxisValueLabel() } }
                .frame(height: 170)
            }
            Text("按平仓日累计净盈亏，不是历史账户净值。").font(.caption).foregroundStyle(.secondary)
        }
        .padding(16).glassCard()
    }

    private var directions: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("多空表现").font(.headline)
            HStack(spacing: 10) {
                ForEach(["long", "short"], id: \.self) { side in
                    let row = current?.sides.first { $0.side == side }
                    VStack(alignment: .leading, spacing: 4) {
                        Text(side == "long" ? "做多" : "做空").font(.subheadline.weight(.bold)).foregroundStyle(side == "long" ? Color.up : Color.down)
                        Text("\(row.map { "\($0.count)" } ?? "—") 笔 · 胜率 \(Fmt.fractionPercent(row?.winRate))%").font(.caption).foregroundStyle(.secondary)
                        Text(Fmt.amount(row?.netPnl) + " USDT").font(.headline.monospacedDigit()).foregroundStyle(tone(row?.netPnl))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12).background(Color.field, in: .rect(cornerRadius: 14))
                }
            }
        }
        .padding(16).glassCard()
    }

    private var symbols: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("币种表现").font(.headline)
            if let rows = current?.symbols, !rows.isEmpty {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                    GridRow { Text("合约"); Text("笔数"); Text("净盈亏"); Text("胜率") }.font(.caption).foregroundStyle(.secondary)
                    ForEach(rows, id: \.symbol) { row in
                        GridRow {
                            Text(Fmt.coin(row.symbol)).font(.subheadline.weight(.semibold))
                            Text("\(row.count)").monospacedDigit()
                            Text(Fmt.amount(row.netPnl)).monospacedDigit().foregroundStyle(tone(row.netPnl))
                            Text(Fmt.fractionPercent(row.winRate) + "%").monospacedDigit()
                        }
                        .font(.subheadline)
                    }
                }
            } else {
                Text((current == nil ? "读取服务后显示" : "暂无已平仓合约") + "。净盈亏单位为 USDT。").font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16).glassCard()
    }

    private var explainer: some View {
        Explainer(title: "统计口径", paragraphs: [
            "账户概览始终显示当前全量数据。周期为最近 7 / 30 个连续自然时长天数；交易归属以完全平仓时刻为准，手续费按当前交易模式的结算记录归集。",
            "胜率 = 净盈利笔数 ÷ 已平仓笔数，净零也计入分母。盈亏比 = 平均盈利 ÷ 平均亏损绝对值，两侧均有样本才计算。平均净 R = 每笔完整净盈亏 ÷ 各自初始固定风险 R 后的算术平均。",
            live ? "实盘盈亏仅统计本 App 管理交易，已实现净盈亏包含交易所结算的手续费与资金费，浮动盈亏按剩余持仓计价。累计百分比 = App 累计净盈亏 ÷ 本人确认的固定本金；充提、外部交易和统计周期不改变分母。未设置固定本金时不计算百分比；这不是全账户资金加权收益率。"
                : "净盈亏已计入模型手续费及成交滑点；没有再次扣除风险预留。未计资金费，不代表交易所账户收益。",
        ])
    }

    static func time(_ seconds: Double) -> String {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "HH:mm:ss"
        return f.string(from: Date(timeIntervalSince1970: seconds))
    }
}

struct Explainer: View {
    let title: String
    let paragraphs: [String]
    @State private var open = false
    var body: some View {
        DisclosureGroup(isExpanded: $open.animation(.snappy)) {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(paragraphs, id: \.self) { Text($0) }
            }
            .font(.footnote).foregroundStyle(.secondary).padding(.top, 6)
        } label: {
            Text(title).font(.subheadline.weight(.semibold))
        }
        .onChange(of: open) { _, _ in Haptics.select() }
        .padding(16).glassCard()
    }
}
