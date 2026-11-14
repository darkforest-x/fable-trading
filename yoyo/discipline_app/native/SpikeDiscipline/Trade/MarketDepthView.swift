import Foundation
import CoreFoundation
import Observation
import SwiftUI

/// Compact, read-only OKX perpetual depth ladder. Price selection is a callback only; this view
/// never changes order intent or sends an order. The caller owns the current-price source and gates.
@MainActor struct MarketDepthView: View {
    let symbol: String
    let latestPrice: String?
    let todayChangePct: Double?
    let tick: String?
    let quoteFresh: Bool
    let active: Bool
    let canSelectPrice: Bool
    let onSelectPrice: (String) -> Void

    @State private var model = MarketDepthModel()

    private var online: Bool { NetworkWatch.shared.online }
    private var streamEnabled: Bool { active && quoteFresh && online }
    private var currentQuoteAvailable: Bool {
        guard streamEnabled, let value = Double(latestPrice ?? "") else { return false }
        return value.isFinite && value > 0
    }
    private var streamKey: String { "\(symbol)|\(streamEnabled)" }
    private var currentBook: MarketDepthSnapshot? {
        guard streamEnabled, model.status == .live,
              let snapshot = model.snapshot, snapshot.symbol == symbol,
              snapshot.isTouchable(at: Date()) else { return nil }
        return snapshot
    }

    var body: some View {
        VStack(spacing: 5) {
            header

            if let book = currentBook {
                columnHeader
                let largest = (book.asks + book.bids).compactMap { Double($0.contracts) }.max() ?? 1
                VStack(spacing: 2) {
                    // OKX sends asks lowest-first. Reverse them so the best sell price sits
                    // immediately above the center price, with higher asks above it.
                    ForEach(Array(book.asks.reversed()), id: \.price) { level in
                        levelButton(level, side: .sell, largest: largest)
                    }
                    latestRow
                    // OKX sends bids highest-first; the best buy price belongs next to center.
                    ForEach(book.bids, id: \.price) { level in
                        levelButton(level, side: .buy, largest: largest)
                    }
                }
            } else {
                latestRow
            }
        }
        .padding(7)
        .background(Color.field.opacity(0.26), in: .rect(cornerRadius: 12))
        .onChange(of: streamKey, initial: true) { _, key in
            model.start(key: key, symbol: symbol, url: MarketDepthEndpoint.url, enabled: streamEnabled)
        }
        .onDisappear { model.deactivate(key: streamKey) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(Fmt.coin(symbol)) 永续盘口")
    }

    private var header: some View {
        HStack(spacing: 4) {
            Text("盘口")
                .font(.caption.weight(.bold))
                .foregroundStyle(Color.ink)
            Spacer(minLength: 2)
            Circle()
                .fill(statusColor)
                .frame(width: 5, height: 5)
            Text(headerStatus)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Color.muted)
                .lineLimit(1)
        }
    }

    private var columnHeader: some View {
        HStack(spacing: 4) {
            Text("价格")
                .frame(maxWidth: .infinity, alignment: .leading)
            Text("数量(张)")
                .frame(width: 44, alignment: .trailing)
        }
        .font(.caption2)
        .foregroundStyle(Color.muted)
        .padding(.horizontal, 5)
        .frame(minHeight: 16)
    }

    private var headerStatus: String {
        if !active { return "暂停" }
        if !online { return "断网" }
        if !quoteFresh { return "行情过期" }
        if !currentQuoteAvailable { return "行情无效" }
        return switch model.status {
        case .idle: "等待"
        case .connecting, .waiting: "连接中"
        case .live: currentBook == nil ? "等待深度" : "OKX"
        case .reconnecting: "重连中"
        case .stale: "深度过期"
        }
    }

    private var statusColor: Color {
        currentBook == nil ? (online && quoteFresh && active ? Color.amber : Color.muted.opacity(0.55)) : Color.up
    }

    private var priceColor: Color {
        guard currentQuoteAvailable else { return .muted }
        let tone = Fmt.tone(todayChangePct)
        return tone == .flat ? .ink : tone.color
    }

    private var latestRow: some View {
        VStack(spacing: 1) {
            Text(currentQuoteAvailable ? Fmt.price(latestPrice, tick: tick) : "—")
                .font(.scaled(size: 16, weight: .bold).monospacedDigit())
                .foregroundStyle(priceColor)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            if currentQuoteAvailable, let todayChangePct {
                Text("今日 \(Fmt.signedPercent(todayChangePct))")
                    .font(.caption2.weight(.semibold).monospacedDigit())
                    .foregroundStyle(Fmt.tone(todayChangePct).color)
                    .lineLimit(1)
            } else {
                Text(currentQuoteAvailable ? "最新价" : quoteStatusCaption)
                    .font(.caption2)
                    .foregroundStyle(Color.muted)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(minHeight: 44)
        .background(Color.field.opacity(0.7), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(currentQuoteAvailable ? "最新价 \(Fmt.price(latestPrice, tick: tick))" : quoteStatusCaption)
        .accessibilityValue(currentQuoteAvailable ? todayChangePct.map { "今日 " + Fmt.signedPercent($0) } ?? "" : "")
    }

    private var quoteStatusCaption: String {
        if !active { return "页面未激活" }
        if !online { return "网络断开" }
        return quoteFresh ? "最新价不可用" : "行情过期"
    }

    private enum Side: Equatable { case sell, buy
        var color: Color { self == .sell ? .down : .up }
        var label: String { self == .sell ? "卖价" : "买价" }
    }

    private func levelButton(_ level: MarketDepthLevel, side: Side, largest: Double) -> some View {
        let value = Trading.priceAtTick(level.price, tick: tick)
        let selectable = currentQuoteAvailable && canSelectPrice && value != nil
        return Button {
            guard selectable, let value,
                  let latest = currentBook,
                  (side == .sell ? latest.asks : latest.bids).contains(where: { $0.price == level.price }) else { return }
            onSelectPrice(value)
        } label: {
            HStack(spacing: 4) {
                Text(Fmt.price(level.price, tick: tick))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(Fmt.coinQuantity(level.contracts))
                    .frame(width: 44, alignment: .trailing)
                    .foregroundStyle(Color.ink)
            }
            .font(.scaled(size: 12, weight: .semibold).monospacedDigit())
            .foregroundStyle(side.color)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .padding(.horizontal, 5)
            .frame(minHeight: 32)
            .background {
                GeometryReader { geo in
                    let fraction = min(1, (Double(level.contracts) ?? 0) / largest)
                    RoundedRectangle(cornerRadius: 3)
                        .fill(side.color.opacity(0.11))
                        .frame(width: geo.size.width * fraction)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
            .contentShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
        }
        .buttonStyle(.plain)
        .disabled(!selectable)
        .accessibilityLabel("\(side.label) \(level.price)，\(Fmt.coinQuantity(level.contracts)) 张")
        .accessibilityHint(selectable ? "点按仅填入限价" : "当前不可选择")
    }
}

struct MarketDepthLevel: Equatable, Identifiable {
    let price: String
    let contracts: String
    var id: String { price }
}

struct MarketDepthSnapshot: Equatable {
    let symbol: String
    let asks: [MarketDepthLevel]  // ascending price, as received from OKX
    let bids: [MarketDepthLevel]  // descending price, as received from OKX
    let timestampMs: Double
    let receivedAt: Date

    /// `books5` publishes changed snapshots at 100 ms and may send a no-change refresh around
    /// 60 s later. Allow a small delivery margin for that refresh.
    func isTouchable(at now: Date) -> Bool {
        let exchangeAgeMs = now.timeIntervalSince1970 * 1000 - timestampMs
        let receiveAge = now.timeIntervalSince(receivedAt)
        return exchangeAgeMs >= -5_000 && exchangeAgeMs <= 65_000 && receiveAge >= 0 && receiveAge <= 75
    }
}

/// Decoder for OKX's public `books5` snapshot envelope. It rejects mismatched symbols so a late
/// packet from a previous contract can never repopulate the currently selected ladder.
enum MarketDepthDecoder {
    static func snapshot(from data: Data, expectedSymbol: String, receivedAt: Date = Date()) -> MarketDepthSnapshot? {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arg = root["arg"] as? [String: Any],
              arg["channel"] as? String == "books5",
              arg["instId"] as? String == expectedSymbol,
              let rows = root["data"] as? [[String: Any]],
              let row = rows.first,
              (row["instId"] as? String).map({ $0 == expectedSymbol }) ?? true,
              let asks = parseLevels(row["asks"]),
              let bids = parseLevels(row["bids"]),
              !asks.isEmpty, !bids.isEmpty else { return nil }

        guard let timestamp = numericText(row["ts"]).flatMap(Double.init), timestamp.isFinite, timestamp > 0 else { return nil }
        let exchangeAgeMs = receivedAt.timeIntervalSince1970 * 1000 - timestamp
        // Reject old messages arriving from a stalled network buffer, but tolerate small local
        // clock skew. Selection later rechecks the wider quiet-book age allowance above.
        guard exchangeAgeMs >= -5_000 && exchangeAgeMs <= 15_000 else { return nil }
        return MarketDepthSnapshot(symbol: expectedSymbol,
                                   asks: Array(asks.sorted { decimal($0.price) < decimal($1.price) }.prefix(5)),
                                   bids: Array(bids.sorted { decimal($0.price) > decimal($1.price) }.prefix(5)),
                                   timestampMs: timestamp,
                                   receivedAt: receivedAt)
    }

    static func isSubscriptionError(_ data: Data) -> Bool {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let event = root["event"] as? String else { return false }
        return event == "error" || event == "channel-conn-count-error"
    }

    private static func parseLevels(_ value: Any?) -> [MarketDepthLevel]? {
        guard let rows = value as? [[Any]] else { return nil }
        return rows.compactMap { row in
            guard row.count >= 2,
                  let price = numericText(row[0]), let size = numericText(row[1]),
                  let p = Double(price), p.isFinite, p > 0,
                  let q = Double(size), q.isFinite, q > 0 else { return nil }
            return MarketDepthLevel(price: price, contracts: size)
        }
    }

    private static func numericText(_ value: Any?) -> String? {
        if let text = value as? String, text.count <= 32,
           text.range(of: #"^\d+(?:\.\d+)?$"#, options: .regularExpression) != nil { return text }
        if let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite {
            return number.stringValue
        }
        return nil
    }

    private static func decimal(_ raw: String) -> Decimal {
        Decimal(string: raw, locale: Locale(identifier: "en_US_POSIX")) ?? .zero
    }
}

@MainActor @Observable
final class MarketDepthModel {
    enum Status: Equatable { case idle, connecting, waiting, live, reconnecting, stale }

    private(set) var snapshot: MarketDepthSnapshot?
    private(set) var status: Status = .idle
    private var streamTask: Task<Void, Never>?
    private var desiredKey: String?
    private var activeToken: UUID?
    private var activeSocket: URLSessionWebSocketTask?

    func start(key: String, symbol: String, url: URL, enabled: Bool) {
        select(key: key)
        guard enabled, streamTask == nil else { return }
        let token = UUID()
        streamTask = Task { [weak self] in
            await self?.watch(token: token, key: key, symbol: symbol, url: url, enabled: true)
        }
    }

    /// Synchronously retires the old key before SwiftUI starts the new task. A cancelled task that
    /// wakes later cannot claim another key or cancel the socket it does not own.
    func select(key: String) {
        guard desiredKey != key else { return }
        desiredKey = key
        streamTask?.cancel()
        streamTask = nil
        let priorSocket = activeSocket
        activeSocket = nil
        activeToken = nil
        snapshot = nil
        status = .idle
        priorSocket?.cancel(with: .goingAway, reason: nil)
    }

    func deactivate(key: String) {
        guard desiredKey == key else { return }
        desiredKey = nil
        streamTask?.cancel()
        streamTask = nil
        let priorSocket = activeSocket
        activeSocket = nil
        activeToken = nil
        snapshot = nil
        status = .idle
        priorSocket?.cancel(with: .goingAway, reason: nil)
    }

    func watch(token: UUID, key: String, symbol: String, url: URL, enabled: Bool) async {
        guard !Task.isCancelled, desiredKey == key, activeToken == nil else { return }
        guard enabled, Self.validSwapSymbol(symbol), ["wss", "ws"].contains(url.scheme?.lowercased() ?? "") else {
            status = .idle
            return
        }

        activeToken = token
        status = .connecting
        defer {
            if activeToken == token {
                activeSocket?.cancel(with: .goingAway, reason: nil)
                activeSocket = nil
                snapshot = nil
                status = .idle
                activeToken = nil
            }
        }

        while !Task.isCancelled, desiredKey == key, activeToken == token {
            let socket = URLSession.shared.webSocketTask(with: url)
            guard desiredKey == key, activeToken == token else {
                socket.cancel(with: .goingAway, reason: nil)
                return
            }
            activeSocket = socket
            socket.resume()
            do {
                let subscription = try JSONSerialization.data(withJSONObject: [
                    "op": "subscribe",
                    "args": [["channel": "books5", "instId": symbol]]
                ])
                guard let text = String(data: subscription, encoding: .utf8) else { throw FeedError.encoding }
                try await socket.send(.string(text))
                guard desiredKey == key, activeToken == token else { return }
                status = .waiting
                try await receive(socket: socket, symbol: symbol, key: key, token: token)
                if !Task.isCancelled { throw FeedError.closed }
            } catch {
                socket.cancel(with: .goingAway, reason: nil)
                guard !Task.isCancelled, desiredKey == key, activeToken == token else { return }
                // Never show the prior contract's or prior connection's book while reconnecting.
                snapshot = nil
                status = .reconnecting
                try? await Task.sleep(for: .seconds(1))
                guard !Task.isCancelled, desiredKey == key, activeToken == token else { return }
            }
        }
    }

    private func receive(socket: URLSessionWebSocketTask, symbol: String, key: String, token: UUID) async throws {
        let activity = MarketDepthActivity()
        let startedAt = Date()
        let keepalive = Task {
            while !Task.isCancelled {
                try await Task.sleep(for: .seconds(10))
                guard await activity.needsPing() else { continue }
                do { try await socket.send(.string("ping")) }
                catch { socket.cancel(with: .abnormalClosure, reason: nil); return }
            }
        }
        let watchdog = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try await Task.sleep(for: .seconds(2))
                guard let self, self.desiredKey == key, self.activeToken == token else { return }
                let now = Date()
                if let snapshot = self.snapshot, snapshot.symbol == symbol {
                    if !snapshot.isTouchable(at: now) {
                        self.snapshot = nil
                        self.status = .stale
                    }
                } else if now.timeIntervalSince(startedAt) > 8 {
                    // A successful socket handshake without a snapshot, including a silent bad
                    // subscription, must reset and retry instead of staying "connecting" forever.
                    self.status = .reconnecting
                    socket.cancel(with: .abnormalClosure, reason: nil)
                }
            }
        }
        defer { keepalive.cancel(); watchdog.cancel() }

        while !Task.isCancelled, desiredKey == key, activeToken == token {
            let message = try await socket.receive()
            await activity.received()
            let data: Data?
            switch message {
            case .string(let text):
                if text == "pong" { continue }
                data = text.data(using: .utf8)
            case .data(let value): data = value
            @unknown default: data = nil
            }
            guard let data else { continue }
            if MarketDepthDecoder.isSubscriptionError(data) { throw FeedError.subscription }
            guard let update = MarketDepthDecoder.snapshot(from: data, expectedSymbol: symbol) else { continue }
            guard desiredKey == key, activeToken == token else { return }
            snapshot = update
            status = .live
        }
    }

    private static func validSwapSymbol(_ symbol: String) -> Bool {
        symbol.range(of: #"^[A-Z0-9]{1,40}-USDT-SWAP$"#, options: .regularExpression) != nil
    }

    private enum FeedError: Error { case encoding, closed, subscription }
}

private actor MarketDepthActivity {
    private var receivedAt = Date()
    func received() { receivedAt = Date() }
    func needsPing(now: Date = Date()) -> Bool { now.timeIntervalSince(receivedAt) >= 20 }
}

/// Production uses the unauthenticated public endpoint. DEBUG permits only loopback overrides so
/// acceptance can replay synthetic depth without contacting OKX or altering the shared fake app.
private enum MarketDepthEndpoint {
    static var url: URL {
        #if DEBUG
        if let raw = UserDefaults.standard.string(forKey: "SpikeDepthFixtureURL"),
           let url = URL(string: raw), url.scheme == "ws",
           ["127.0.0.1", "localhost"].contains(url.host ?? "") { return url }
        if let host = AppConfig.serviceURL.host,
           ["127.0.0.1", "localhost"].contains(host),
           let port = AppConfig.serviceURL.port,
           let url = URL(string: "ws://\(host):\(port)/ws/v5/public") { return url }
        #endif
        return URL(string: "wss://ws.okx.com/ws/v5/public")!
    }
}
