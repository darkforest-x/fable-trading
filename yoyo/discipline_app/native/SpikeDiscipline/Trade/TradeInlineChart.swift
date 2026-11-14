import SwiftUI

/// A live preview in the order ticket. Detailed pan and zoom are available in the full chart.
struct TradeInlineChart: View {
    @Environment(ChartStyle.self) private var style
    let symbol: String
    let tick: String?
    let candles: CandleStore
    @Binding var period: CandlePeriod
    @Binding var expanded: Bool
    let height: CGFloat
    let onOpen: () -> Void

    private static let quickPeriods: [CandlePeriod] = [.m5, .m15, .h1, .h4]
    private var matchesSelection: Bool { candles.symbol == symbol && candles.period == period }
    private var coin: String { Fmt.coin(symbol) + "USDT" }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 2) {
                if expanded {
                    ForEach(Self.quickPeriods) { item in
                        Button { select(item) } label: {
                            Text(item.rawValue)
                                .font(.subheadline.weight(period == item ? .bold : .regular))
                                .foregroundStyle(period == item ? Color.accentColor : .secondary)
                                .padding(.horizontal, 5)
                                .frame(minWidth: 34, minHeight: 44)
                                .contentShape(.rect)
                        }
                        .accessibilityAddTraits(period == item ? .isSelected : [])
                    }
                    Menu {
                        ForEach(CandlePeriod.allCases) { item in
                            Button { select(item) } label: {
                                if item == period { Label(item.rawValue, systemImage: "checkmark") }
                                else { Text(item.rawValue) }
                            }
                        }
                    } label: {
                        HStack(spacing: 3) {
                            Text(Self.quickPeriods.contains(period) ? "更多" : period.rawValue)
                            Image(systemName: "chevron.down").font(.caption2)
                        }
                        .font(.caption.weight(.semibold)).foregroundStyle(Color.muted)
                        .frame(minWidth: 40, minHeight: 44).contentShape(.rect)
                    }
                    .accessibilityLabel("更多 K 线周期").accessibilityValue(period.rawValue)
                } else {
                    Button { expanded = true } label: {
                        Label("K 线 · " + period.rawValue, systemImage: "chart.xyaxis.line")
                            .font(.subheadline.weight(.semibold)).frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .accessibilityLabel("展开 K 线，" + period.rawValue)
                }
                Spacer(minLength: 0)
                Button(action: openChart) {
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                        .frame(width: 40, height: 44).contentShape(.rect)
                }
                .accessibilityLabel("全屏 K 线")
                Button { Haptics.select(); expanded.toggle() } label: {
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .frame(width: 40, height: 44).contentShape(.rect)
                }
                .accessibilityLabel(expanded ? "收起 K 线" : "展开 K 线")
            }
            .buttonStyle(.plain).font(.subheadline.weight(.medium))
            .padding(.horizontal, 10)
            if expanded {
                Button(action: openChart) {
                    ZStack {
                        if matchesSelection && !candles.bars.isEmpty {
                            CandleChart(bars: candles.bars, generation: candles.generation,
                                        period: period, decimals: Fmt.decimals(tick: tick), averages: true,
                                        colors: style.colors)
                                .allowsHitTesting(false).accessibilityHidden(true)
                            if candles.error != nil && !candles.streaming {
                                Text("缓存行情").font(.caption2).foregroundStyle(.secondary)
                                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                                    .padding(6)
                            }
                        } else if matchesSelection, let error = candles.error {
                            Text(error).font(.footnote).foregroundStyle(.secondary)
                        } else {
                            ProgressView()
                        }
                    }
                    .frame(maxWidth: .infinity).frame(height: height).contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(coin + " " + period.label + " K线")
                .accessibilityHint("打开全屏图表")
            }
            Divider()
        }
    }

    private func select(_ value: CandlePeriod) {
        guard value != period else { return }
        Haptics.select()
        period = value
    }

    private func openChart() {
        Haptics.open()
        onOpen()
    }
}
