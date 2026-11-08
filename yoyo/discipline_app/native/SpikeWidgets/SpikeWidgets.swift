import ActivityKit
import SwiftUI
import WidgetKit

/// 天才交易员 widgets and the position Live Activity (owner 2026-11-08 items 4–5; Stitch
/// live-activity / home-widgets, native-2.6.0). Display only: nothing here can place or close orders.
@main
struct SpikeWidgetsBundle: WidgetBundle {
    var body: some Widget {
        TouchesWidget()
        OverviewWidget()
        PositionLiveActivity()
    }
}

// MARK: 高分触价 (small + lock screen)

struct TouchesWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "spike.touches", provider: SnapshotProvider()) { entry in
            TouchesView(snapshot: entry.snapshot).containerBackground(for: .widget) { Color(.systemBackground) }
        }
        .configurationDisplayName("高分触价")
        .description("今天评分最高的 1.618 触价（模拟评分，仅展示）。")
        .supportedFamilies([.systemSmall, .accessoryRectangular, .accessoryInline])
    }
}

// MARK: 概览 (medium): position + today's touches

struct OverviewWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "spike.overview", provider: SnapshotProvider()) { entry in
            OverviewView(snapshot: entry.snapshot).containerBackground(for: .widget) { Color(.systemBackground) }
        }
        .configurationDisplayName("持仓与触价")
        .description("持仓浮盈与今日 1.618 触价（仅展示）。")
        .supportedFamilies([.systemMedium])
    }
}

// MARK: Live Activity (lock screen + Dynamic Island)

struct PositionLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: PositionActivityAttributes.self) { context in
            ActivityCard(attributes: context.attributes, state: context.state)
                .padding(16)
                .widgetURL(URL(string: "spike://trade"))
                .activityBackgroundTint(Color(.systemBackground).opacity(0.85))
        } dynamicIsland: { context in
            let a = context.attributes, s = context.state, coin = WidgetSnapshot.coin(a.symbol)
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 6) { WCoin(symbol: a.symbol, size: 22); Text("\(coin) \(a.side == "short" ? "空" : "多") \(a.leverage)x").font(.system(size: 14, weight: .semibold)) }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(WPalette.signed(s.pnl) + " U").font(.system(size: 16, weight: .bold).monospacedDigit()).foregroundStyle(WPalette.tone(s.pnl))
                }
                DynamicIslandExpandedRegion(.bottom) { StopBar(attributes: a, state: s) }
            } compactLeading: {
                HStack(spacing: 4) {
                    Circle().fill(WPalette.up).frame(width: 6, height: 6)
                    Text("\(coin) \(a.side == "short" ? "空" : "多")").font(.system(size: 13, weight: .semibold))
                }
            } compactTrailing: {
                Text(WPalette.signed(s.pnl, 1) + "U").font(.system(size: 13, weight: .bold).monospacedDigit()).foregroundStyle(WPalette.tone(s.pnl))
            } minimal: {
                Text(s.pnl >= 0 ? "+" : "−").font(.system(size: 14, weight: .bold)).foregroundStyle(WPalette.tone(s.pnl))
            }
        }
    }
}

