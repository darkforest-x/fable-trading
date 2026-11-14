import SwiftUI

/// Valid upbreak observations from the existing worker rule. The event row opens its exact 1H chart.
struct TrendlineBreakSection: View {
    @Environment(SessionStore.self) private var session
    let connection: Connection
    let refresh: Int
    let open: (TrendlineBreakEvent) -> Void
    @State private var feed = TrendlineBreakFeed()

    private var stateTitle: String {
        guard let state = feed.snapshot?.state else { return "1H 收线确认 · 有效上破" }
        return switch state {
        case "ready": "1H 收线确认 · 有效上破"
        case "partial", "degraded": "扫描覆盖不完整 · 仅显示已确认记录"
        case "warming", "scanning": "扫描中 · 仅显示已确认记录"
        default: "数据暂不可用"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("1H 突破 4H 趋势线")
                    .font(.scaled(size: 14, weight: .bold)).foregroundStyle(Color.ink)
                Text(stateTitle)
                    .font(.scaled(size: 10, weight: .medium)).foregroundStyle(Color.muted)
                    .lineLimit(1).minimumScaleFactor(0.8)
                Spacer(minLength: 4)
                if let count = feed.snapshot?.total {
                    Text("\(count)").font(.scaled(size: 11, weight: .semibold).monospacedDigit()).foregroundStyle(Color.muted)
                }
            }
            .padding(.vertical, 10)

            LoadState(loading: !feed.loaded && feed.error == nil, error: feed.error, stale: feed.loaded)
            if feed.loaded && feed.items.isEmpty {
                Text(feed.error == nil ? "暂无有效的 1H 上破记录" : "趋势突破数据暂不可用")
                    .font(.footnote).foregroundStyle(Color.muted)
                    .frame(maxWidth: .infinity).padding(.vertical, 18)
            } else if !feed.items.isEmpty {
                LazyVStack(spacing: 0) {
                    ForEach(feed.items) { event in
                        Button { open(event) } label: { row(event) }
                            .buttonStyle(RowPressStyle())
                        Divider().padding(.leading, 54)
                    }
                }
            }
            if let covered = feed.snapshot?.coverage.covered,
               let total = feed.snapshot?.coverage.symbolsTotal, total > 0 {
                HStack(spacing: 5) {
                    Image(systemName: "clock.arrow.circlepath").font(.system(size: 10, weight: .semibold))
                    Text("扫描覆盖 \(covered)/\(total) · 仅上破")
                        .font(.scaled(size: 10, weight: .medium).monospacedDigit())
                    Spacer()
                    if let updatedAt = feed.updatedAt {
                        Text("更新 \(Fmt.clock(updatedAt.timeIntervalSince1970 * 1000))")
                            .font(.scaled(size: 10, weight: .medium).monospacedDigit())
                    }
                }
                .foregroundStyle(Color.muted)
                .padding(.vertical, 8)
            }
        }
        .task(id: "\(refresh)|\(connection.token.hashValue)") {
            await feed.watch(connection: connection, session: session)
        }
    }

    private func row(_ event: TrendlineBreakEvent) -> some View {
        HStack(spacing: 11) {
            CoinLogo(coin: Fmt.coin(event.symbol), size: 32)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(Fmt.coin(event.symbol)).font(.scaled(size: 15, weight: .bold)).foregroundStyle(Color.ink)
                    Text("上破").font(.scaled(size: 10, weight: .semibold)).foregroundStyle(ChartPalette.up)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(ChartPalette.up.opacity(0.09), in: Capsule())
                    Text("4H 下行趋势线").font(.scaled(size: 10, weight: .medium)).foregroundStyle(Color.muted).lineLimit(1)
                }
                Text("\(Fmt.clock(event.confirmedMs, now: feed.snapshot?.asofMs ?? Date().timeIntervalSince1970 * 1000)) 确认 · 线价 \(Fmt.price(event.level))")
                    .font(.scaled(size: 10, weight: .medium).monospacedDigit()).foregroundStyle(Color.muted).lineLimit(1)
            }
            Spacer(minLength: 6)
            VStack(alignment: .trailing, spacing: 3) {
                Text(Fmt.price(event.close)).font(.scaled(size: 13, weight: .bold).monospacedDigit()).foregroundStyle(Color.ink)
                Text(Fmt.signedPercent(event.risePct)).font(.scaled(size: 10, weight: .semibold).monospacedDigit())
                    .foregroundStyle(ChartPalette.up)
            }
            Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 4).frame(minHeight: 56).contentShape(.rect)
    }
}
