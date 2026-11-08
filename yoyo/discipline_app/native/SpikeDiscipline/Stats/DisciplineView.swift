import SwiftUI

/// 交易纪律 (1.x 纪律 page): 我的系统, live status, remaining budget, effective rules, pending policy
/// change, today's record and the calculation notes. Read-only: rules are server-owned.
struct DisciplineView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account

    var body: some View {
        let state = account.state, p = state?.policy
        let symbol = state?.positions.first?.symbol ?? "BTC-USDT-SWAP"
        let reason = Trading.availability(state: state, symbol: symbol, connected: session.connection != nil, online: account.online,
                                          fresh: account.online, pending: account.pendingEntry != nil || account.closeAttempt != nil,
                                          room: 1, now: account.now)?.text
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                mySystem
                StatusNote(text: reason ?? "服务端规则生效中", systemImage: reason == nil ? "checkmark.shield" : "lock", tint: reason == nil ? .up : .amber)
                DisciplineBudgetRow(state: state, online: account.online)
                summary(p)
                if let proposal = state?.policyProposal { proposalCard(proposal) }
                ForEach(groups(p), id: \.title) { group in RuleCard(title: group.title, systemImage: group.icon, rows: group.rows) }
                RuleCard(title: "当前记录", systemImage: "clock", rows: [
                    ("今日已用次数", "\(state?.daily?.entries.description ?? "—") / \(state?.discipline?.entriesLimit ?? p?.maxEntries ?? 3)"),
                    ("今日盈利奖励", "+\(state?.discipline?.entriesBonus ?? 0) 次"),
                    ("连续净亏损", "\(state?.lossStreak.description ?? "—") 笔"),
                    ("冷静期结束", state.map { $0.cooldownUntil > $0.serverTime ? Fmt.beijing($0.cooldownUntil) : "未触发" } ?? "待连接"),
                ])
                Explainer(title: "规则计算与运行边界", paragraphs: [
                    "每日按北京时间 00:00 切换。未成交撤单经交易所确认后返还次数，已成交不返还。规则以当前生效版本为准。",
                    "外部交易不显示，不占用 App 开仓次数、连亏、仓位或保证金额度。日亏只计算 App 管理交易的当日已实现与浮动盈亏；交易所可用资金仍需满足开仓要求。同币种沿用交易所当前杠杆，App 只管理自己新增的数量。",
                    "保证金占比按账户净值计算：BTC、ETH 各自不超过 10%，山寨合计不超过 5%；本 App 对应额度池的持仓与挂单占用会扣除，单笔计划止损风险仍不超过净值 5%。名义仓位按当前规则分别核验各额度池。开仓与成交前检查，并预留入场费用与不利价差；行情变化仍可能使已有仓位超过比例上限。",
                    "R 按确认的开仓计划固定，包含费用与滑点预留。止盈比例按原始仓位计算。成本预留费率每边 \(Fmt.plainPercent(p?.feeRate ?? "0.0005"))%，滑点预留 \(Fmt.plainPercent(p?.slippageRate ?? "0.001"))%，未计资金费，不代表你的实际交易所费率。",
                    "日亏停手线不代替每笔止损，多个持仓风险会叠加。" + (account.live ? "资金费、滑点与全仓强平仍受交易所规则影响；实盘止损不是最大亏损保证。" : "当前模拟不计算交易所真实强平价。"),
                ])
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 24)
        }
        .contentMargins(.bottom, 96, for: .scrollContent)
        .scrollEdgeEffectHidden(true, for: .top)
        .toolbarBackground(.hidden, for: .navigationBar)
        .background(Color.canvas)
        .navigationTitle("交易纪律")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
    }

    /// Owner principles (Notion task 68, edited 2026-10-23); bundled reading only.
    private var mySystem: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("我的系统").font(.headline)
            ForEach(Array(["只在区间外 1.618 开单", "等信号确认", "及时止盈止损，或分批止盈"].enumerated()), id: \.offset) { i, text in
                HStack(spacing: 10) {
                    Text("\(i + 1)").font(.subheadline.weight(.bold)).foregroundStyle(.white)
                        .frame(width: 24, height: 24).background(Color.accentColor, in: .circle)
                    Text(text).font(.body.weight(.semibold))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16).glassCard(tint: .blue)
    }

    private func summary(_ p: Policy?) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(p == nil ? "连接后读取规则" : "当前生效规则", systemImage: "lock.fill").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            HStack {
                VStack(alignment: .leading) {
                    Text(p.map { Fmt.plainPercent($0.riskFraction) } ?? "—").font(.scaled(size: 30, weight: .bold, design: .rounded)) + Text("%").font(.headline)
                    Text("单笔计划风险").font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .leading) {
                    Text(p.map { "\($0.maxEntries)" } ?? "—").font(.scaled(size: 30, weight: .bold, design: .rounded)) + Text("次").font(.headline)
                    Text("每日基础次数").font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Text((p?.profitEntryBonus ?? 0) > 0 ? "盈利全平 +1 次 · 日亏与连亏停手优先" : "刷新、退出或换手机，记录依然保留。").font(.footnote).foregroundStyle(.secondary)
        }
        .padding(16).glassCard()
    }

    private func proposalCard(_ proposal: PolicyProposal) -> some View {
        let p = proposal.policy
        return VStack(alignment: .leading, spacing: 6) {
            Label("待生效变更", systemImage: "clock").font(.caption.weight(.semibold)).foregroundStyle(Color.amber)
            Text("\(Fmt.marginMode(p.marginMode)) · 单笔风险 \(Fmt.plainPercent(p.riskFraction))%").font(.headline)
            Text("账户名义上限：BTC / ETH \(p.majorExposureMultiple ?? "—")倍 · 山寨 \(p.altExposureMultiple ?? "—")倍").font(.subheadline)
            Text("最早 \(Fmt.beijing(proposal.effectiveAfter))（北京时间）生效，届时需无持仓、无挂单。").font(.subheadline)
            Text("当前规则、次数和冷静期继续保留。").font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16).glassCard(tint: .amber)
    }

    private struct Group { let title: String; let icon: String; let rows: [(String, String)] }

    private func groups(_ p: Policy?) -> [Group] {
        let wait = "待连接服务"
        var sizing: [(String, String)] = [
            ("单笔计划风险", p.map { "净值的 \(Fmt.plainPercent($0.riskFraction))%" } ?? wait),
            ("保证金模式", p.map { Fmt.marginMode($0.marginMode) } ?? wait),
            ("杠杆上限", p.map { "BTC / ETH \($0.majorLeverage)x · 山寨 \($0.altLeverage)x" } ?? wait),
        ]
        if let p, p.majorAllowanceScope == "independent" {
            let major = "保证金 \(Fmt.plainPercent(p.majorMarginFraction ?? "0"))% · 名义 \(p.majorExposureMultiple ?? "—")倍"
            sizing += [("BTC 独立额度", major), ("ETH 独立额度", major),
                       ("山寨币共享额度", "保证金 \(Fmt.plainPercent(p.altMarginFraction ?? "0"))% · 名义 \(p.altExposureMultiple ?? "—")倍")]
        } else {
            sizing.append(("保证金占比上限", p.map { "BTC / ETH 合计 \(Fmt.plainPercent($0.majorMarginFraction ?? "0"))% · 山寨合计 \(Fmt.plainPercent($0.altMarginFraction ?? "0"))%" } ?? wait))
        }
        sizing += [
            ("同时持仓与挂单", p.map { "合计最多 \($0.maxPositions) 笔" } ?? wait),
            ("每日基础次数", p.map { "\($0.maxEntries) 次" } ?? wait),
            ("盈利平仓奖励", p.map { ($0.profitEntryBonus ?? 0) > 0 ? "整笔净盈利平仓，当天 +1 次" : "未启用" } ?? wait),
            ("奖励计次", "每笔仅一次，分批止盈不重复计"),
            ("额度计算", p?.majorAllowanceScope == "independent" ? "BTC、ETH 各自独立，山寨币共享；持仓与挂单合计占用" : "同类持仓与挂单共享保证金额度"),
        ]
        let takeProfits = p.map { p in p.takeProfits.enumerated().map { i, tp in "\(tp.first ?? "")R " + (i == p.takeProfits.count - 1 ? "全部余仓" : "平原始 \(Fmt.plainPercent(tp.last))%") }.joined(separator: " · ") }
        return [
            Group(title: "单笔与仓位", icon: "arrow.left.arrow.right", rows: sizing),
            Group(title: "停手与冷静", icon: "clock", rows: [
                ("连续净亏损", p.map { "\($0.lossStreak) 笔停手" } ?? wait),
                ("日亏停手线", p.map { "当日起始净值的 \(Fmt.plainPercent($0.dailyLossFraction))%" } ?? wait),
                ("冷静期触发", p.map { "连亏 \($0.cooldownAfterLosses ?? 1) 笔后 \(($0.lossCooldownSeconds / 3600).formatted()) 小时" } ?? wait),
                ("盈利 / 分批止盈", "不触发冷静期"),
            ]),
            Group(title: "自动保护", icon: "checkmark.shield", rows: [
                ("成本保本", p.map { "\($0.breakevenR)R" } ?? wait),
                ("分批止盈", takeProfits ?? wait),
            ]),
        ]
    }
}

struct RuleCard: View {
    let title: String
    let systemImage: String
    let rows: [(String, String)]
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage).font(.headline)
            ForEach(rows, id: \.0) { row in
                HStack(alignment: .top) {
                    Text(row.0).font(.subheadline).foregroundStyle(.secondary)
                    Spacer(minLength: 12)
                    Text(row.1).font(.subheadline.weight(.semibold)).multilineTextAlignment(.trailing)
                }
            }
        }
        .padding(16).glassCard()
    }
}

/// DisciplineBudget.tsx: one compact row of server-owned limits.
struct DisciplineBudgetRow: View {
    let state: AccountState?
    let online: Bool
    var body: some View {
        let d = state?.discipline
        let fresh = online && (state.map { s in s.privateAt.map { s.serverTime - $0 <= 15 } ?? true } ?? true) && state?.privateError == nil
        let halted = state?.daily?.lockedReason != nil || (state.map { $0.cooldownUntil > $0.serverTime } ?? false)
        HStack(spacing: 0) {
            cell("今日可开", d.map { "\($0.entriesRemaining)" } ?? "—", "次")
            Divider().frame(height: 30)
            cell("再连亏停手", d.map { "\($0.lossesRemaining)" } ?? "—", "笔")
            Divider().frame(height: 30)
            cell("日亏剩余" + (!fresh && d != nil ? " · 上次" : ""), d.map { (Double($0.dailyLossRemaining) ?? 0).formatted(.number.precision(.fractionLength(0...2))) } ?? "—", "U")
        }
        .padding(.vertical, 12)
        .glassCard(tint: halted ? .down : nil)
        .opacity(fresh ? 1 : 0.75)
        .accessibilityElement(children: .combine)
    }

    private func cell(_ title: String, _ value: String, _ unit: String) -> some View {
        VStack(spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value).font(.title3.weight(.bold).monospacedDigit()).contentTransition(.numericText())
                Text(unit).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
    }
}
