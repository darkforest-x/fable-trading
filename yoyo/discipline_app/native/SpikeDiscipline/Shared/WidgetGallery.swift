#if DEBUG
import ActivityKit
import SwiftUI

/// DEBUG-only acceptance gallery (`-SpikeQAWidgets 1`): renders the widget and Live Activity views at
/// system sizes from the snapshot the app just wrote, because the simulator home screen cannot be
/// scripted. It is not the widget host; the real Live Activity is checked on the simulator lock screen.
struct WidgetGallery: View {
    @Environment(AccountStore.self) private var account
    @State private var snapshot = WidgetSnapshot.load()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("小组件预览 · 测试数据").font(.headline)
                if let snapshot {
                    HStack(alignment: .top, spacing: 14) {
                        TouchesView(snapshot: snapshot).padding(14).frame(width: 170, height: 170).background(Color(.systemBackground), in: .rect(cornerRadius: 22))
                        VStack(alignment: .leading, spacing: 6) {
                            Text("锁屏").font(.caption).foregroundStyle(.secondary)
                            Text(snapshot.highTouches.first.map { "高分触价 \(WidgetSnapshot.coin($0.symbol)) \(String(format: "%.2f", $0.score ?? 0))" } ?? "今日暂无高分触价")
                                .font(.caption.monospacedDigit())
                        }
                    }
                    OverviewView(snapshot: snapshot).padding(14).frame(width: 364, height: 170).background(Color(.systemBackground), in: .rect(cornerRadius: 22))
                } else {
                    Text("还没有快照").foregroundStyle(.secondary)
                }
                if let state = account.state, let position = state.positions.first {
                    let attributes = PositionActivityAttributes(positionId: position.id, symbol: position.symbol, side: position.side, leverage: position.leverage,
                                                                entry: Double(position.entry) ?? 0, decimals: Fmt.decimals(tick: state.instruments[position.symbol]?.tick))
                    ActivityCard(attributes: attributes, state: PositionActivities.contentState(position, state))
                        .padding(16).frame(width: 364).background(Color(.systemBackground).opacity(0.85), in: .rect(cornerRadius: 24))
                }
            }
            .padding(20)
        }
        .background(LinearGradient(colors: [Color(red: 0.86, green: 0.89, blue: 0.97), Color(red: 0.95, green: 0.9, blue: 0.94)], startPoint: .top, endPoint: .bottom).ignoresSafeArea())
        .task { while !Task.isCancelled { try? await Task.sleep(for: .seconds(3)); snapshot = WidgetSnapshot.load() } }
    }
}
#endif
