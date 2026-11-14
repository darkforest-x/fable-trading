import CryptoKit
import Foundation
import Observation

/// Live account snapshot (/v1/state every 2 s while active) plus the order and close flows,
/// ported from the 1.x app (App.tsx submitEntry/close, useCloseRecovery.ts):
/// - an entry's request identity is persisted BEFORE it is sent, so a restart never creates a retry order;
///   the POST is not awaited for the outcome — status is read back by id and a timeout never resends;
///   only six consecutive "not_found" reads abandon the request server-side;
/// - a close persists its identity the same way; on restore it is only read back. "以原请求重试" re-sends
///   the same identity and percent, and only when the server reports the attempt as `ready` (never received).
@MainActor @Observable
final class AccountStore {
    struct EntryIntent: Codable, Equatable { let id: String; let symbol: String; let side: String; let kind: String }
    struct EntryProgress: Equatable { let intent: EntryIntent; var status: String; var message: String? }
    struct CloseAttempt: Codable, Equatable { let id: String; let position: String; let symbol: String; let percent: Int; let created: Double }

    private(set) var state: AccountState?
    private(set) var receivedAt = Date()
    private(set) var online = false
    /// Last snapshot from a previous launch, for display only until the first live read (LaunchCache).
    private(set) var cached: AccountState?
    var display: AccountState? { state ?? cached }
    private var cachedAt = Date.distantPast
    var lastError: String?
    /// Persisted entry identity: while set, every new entry is locked.
    private(set) var pendingEntry: EntryIntent?
    var entryProgress: EntryProgress?
    private(set) var closeAttempt: CloseAttempt?
    private(set) var closeStatus = "checking"
    var notice: Notice?
    var paused = false   // replay desk open: live polling stops, like the 1.x app

    struct Notice: Equatable, Identifiable { let id = UUID(); let text: String; let success: Bool }

    private(set) var connection: Connection?
    private(set) var scope = ""
    private var terminalEntry: String?
    private var closeNonce = 0

    var live: Bool { (state?.mode ?? connection?.mode) == "okx_live" }
    var activeEntry: EntryProgress? {
        if let pendingEntry { return EntryProgress(intent: pendingEntry, status: entryProgress?.intent.id == pendingEntry.id ? entryProgress!.status : "checking") }
        return entryProgress
    }
    /// Quote for `symbol` is within the policy's quote age (1.x `fresh`).
    func quoteFresh(_ symbol: String) -> Bool {
        guard online, let state, let q = state.quotes[symbol] else { return false }
        let age = now - q.time
        return age >= 0 && age <= (state.policy?.quoteMaxAgeSeconds ?? 15)
    }

    /// Server time advanced by the local clock since the last snapshot (countdowns).
    var now: Double { (state?.serverTime ?? 0) + Date().timeIntervalSince(receivedAt) }

    func attach(_ connection: Connection?) {
        guard connection != self.connection else { return }
        self.connection = connection
        state = nil; online = false; pendingEntry = nil; entryProgress = nil; closeAttempt = nil; closeStatus = "checking"
        guard let connection else { scope = ""; cached = nil; return }
        let digest = SHA256.hash(data: Data((AppConfig.serviceURL.absoluteString + "\n" + connection.token).utf8))
        scope = digest.prefix(16).map { String(format: "%02x", $0) }.joined() + ":\(connection.mode):\(connection.accountId)"
        cached = LaunchCache.read(AccountState.self, "state", scope: scope)
        if let data = UserDefaults.standard.data(forKey: entryKey), let saved = try? JSONDecoder().decode(EntryIntent.self, from: data), Trading.isRequestId(saved.id) {
            pendingEntry = saved
        }
        if let data = UserDefaults.standard.data(forKey: closeKey), let saved = try? JSONDecoder().decode(CloseAttempt.self, from: data), Trading.isRequestId(saved.id) {
            closeAttempt = saved
        }
    }

    private var entryKey: String { "spike-pending:" + scope }
    private var closeKey: String { "spike-close:" + scope }

    // MARK: loops

    func run(session: SessionStore) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await self.stateLoop(session: session) }
            group.addTask { await self.entryLoop() }
            group.addTask { await self.closeLoop() }
        }
    }

    private func stateLoop(session: SessionStore) async {
        while !Task.isCancelled {
            if !paused { await refresh(session: session) }
            try? await Task.sleep(for: .seconds(2))
        }
    }

    func refresh(session: SessionStore? = nil) async {
        guard let connection else { return }
        do {
            let next: AccountState = try await API.get("/v1/state", connection: connection)
            guard connection == self.connection else { return }
            guard next.mode == connection.mode, (next.accountId ?? "primary") == connection.accountId else {
                throw APIError(status: 0, message: "账户或服务模式已变化，请重新连接")
            }
            state = next; receivedAt = Date(); online = true; lastError = nil
            if Date().timeIntervalSince(cachedAt) > 30 { cachedAt = Date(); LaunchCache.write(next, "state", scope: scope) }
            WidgetBridge.state(next)
            PositionActivities.sync(next)
        } catch is CancellationError {
        } catch {
            online = false; lastError = error.localizedDescription; session?.handle(error)
        }
    }

    private var missingEntryReads = 0
    private func entryLoop() async {
        while !Task.isCancelled {
            var delay: Duration = .milliseconds(500)
            if let connection, let pending = pendingEntry, !paused {
                delay = .seconds(3)
                do {
                    let result: RequestStatus = try await API.get("/v1/order-requests/\(pending.id)", connection: connection)
                    delay = Trading.entryPollDelay(result.status)
                    if result.status == "not_found" {
                        missingEntryReads += 1
                        if missingEntryReads >= 6, pendingEntry?.id == pending.id {
                            let _: RequestStatus = try await API.post("/v1/order-requests/\(pending.id)/abandon-unreceived", body: [:], connection: connection)
                        }
                    } else { missingEntryReads = 0 }
                    if pendingEntry?.id == pending.id, result.id == pending.id, Trading.entryResolved(result.status) {
                        settleEntry(pending, status: result.status, message: result.message)
                    }
                } catch {}   // Read-only reconciliation continues; never resend an order on timeout.
            } else if let connection, let progress = entryProgress, ["pending", "partially_filled"].contains(progress.status), !paused {
                delay = .seconds(3)
                if let result: RequestStatus = try? await API.get("/v1/order-requests/\(progress.intent.id)", connection: connection),
                   entryProgress?.intent.id == progress.intent.id, result.status != progress.status {
                    entryProgress?.status = result.status
                    await refresh()
                }
            }
            try? await Task.sleep(for: delay)
        }
    }

    private func settleEntry(_ pending: EntryIntent, status: String, message: String?) {
        terminalEntry = pending.id
        UserDefaults.standard.removeObject(forKey: entryKey)
        pendingEntry = nil; missingEntryReads = 0
        entryProgress = EntryProgress(intent: pending, status: status, message: message)
        if ["pending", "partially_filled", "filled", "closed"].contains(status) {
            let text = ["pending": "限价单已挂出，可在持仓页撤单", "partially_filled": "部分成交，剩余委托继续等待", "filled": "已成交，保护状态请查看持仓"][status] ?? "订单已完成"
            notice = Notice(text: text, success: true); Haptics.success()
        } else {
            notice = Notice(text: message ?? "本次委托未成交", success: false); Haptics.warning()
        }
        Task { await refresh() }
    }

    private var missingCloseReads = 0
    private func closeLoop() async {
        var lastNonce = closeNonce
        while !Task.isCancelled {
            if let connection, let attempt = closeAttempt, !paused {
                do {
                    var value: RequestStatus = try await API.get("/v1/close-requests/\(attempt.id)", connection: connection)
                    if value.status == "not_found" {
                        missingCloseReads += 1
                        if missingCloseReads >= 6 { value = try await API.post("/v1/close-requests/\(attempt.id)/abandon-unreceived", body: [:], connection: connection) }
                    } else { missingCloseReads = 0 }
                    if closeAttempt?.id == attempt.id {
                        closeStatus = value.status
                        if ["filled", "cancelled", "rejected", "not_received"].contains(value.status) {
                            UserDefaults.standard.removeObject(forKey: closeKey)
                            closeAttempt = nil; missingCloseReads = 0
                            let text = ["filled": "平仓成交已确认", "not_received": "平仓请求未送达，已结束等待", "cancelled": "平仓委托已取消，请查看剩余持仓"][value.status] ?? "平仓请求被拒绝，请查看持仓"
                            notice = Notice(text: text, success: value.status == "filled")
                            value.status == "filled" ? Haptics.success() : Haptics.warning()
                            await refresh()
                        }
                    }
                } catch { if closeAttempt?.id == attempt.id { closeStatus = "checking" } }
            }
            // 3 s cadence; "刷新核对" (checkClose) wakes the loop early.
            for _ in 0..<30 where closeNonce == lastNonce && !Task.isCancelled { try? await Task.sleep(for: .milliseconds(100)) }
            lastNonce = closeNonce
        }
    }

    func checkClose() { closeNonce += 1 }

    // MARK: entry

    /// Live: persist the identity, then send without awaiting the outcome. Paper: /v1/orders.
    /// `requestId` is minted when the ticket is reviewed (1.x review/preview), so a retry from the
    /// confirmation sheet reuses it and the server de-duplicates.
    func submit(_ ticket: Ticket, requestId: String, policyRevision: Int, previewId: String?) async throws {
        guard let connection else { throw APIError(status: 0, message: "尚未连接") }
        guard Trading.isRequestId(requestId) else { throw APIError(status: 0, message: "请求编号无效") }
        if live {
            guard pendingEntry == nil else { throw APIError(status: 0, message: "正在核对上一笔请求") }
            let intent = EntryIntent(id: requestId, symbol: ticket.symbol, side: ticket.side, kind: ticket.kind)
            guard let data = try? JSONEncoder().encode(intent) else { throw APIError(status: 0, message: "本机无法保存恢复记录，本次开仓尚未发送") }
            UserDefaults.standard.set(data, forKey: entryKey)
            guard UserDefaults.standard.data(forKey: entryKey) == data else { throw APIError(status: 0, message: "本机无法保存恢复记录，本次开仓尚未发送") }
            pendingEntry = intent
            entryProgress = EntryProgress(intent: intent, status: "sending")
            let body: [String: Any] = ["request_id": requestId, "policy_revision": policyRevision, "ticket": ticket.json,
                                       "preview_id": previewId ?? NSNull(), "confirmed_live": true]
            Task {
                // Late responses never overwrite a receipt the read-back loop already settled.
                let active = { self.connection == connection && self.pendingEntry?.id == requestId && self.terminalEntry != requestId }
                do {
                    let result: RequestStatus = try await API.post("/v1/order-requests", body: body, connection: connection)
                    if active() { entryProgress = EntryProgress(intent: intent, status: result.status) }
                } catch let error as APIError where (400..<500).contains(error.status) {
                    guard active() else { return }
                    UserDefaults.standard.removeObject(forKey: entryKey)
                    pendingEntry = nil
                    entryProgress = EntryProgress(intent: intent, status: "rejected", message: error.message)
                    notice = Notice(text: error.message, success: false); Haptics.warning()
                } catch {
                    if active() { entryProgress = EntryProgress(intent: intent, status: "checking") }
                }
            }
            return
        }
        let _: Ignored = try await API.post("/v1/orders", body: ["request_id": requestId, "policy_revision": policyRevision, "ticket": ticket.json], connection: connection)
        notice = Notice(text: "模拟委托已受理", success: true); Haptics.success()
        await refresh()
    }

    // MARK: close / cancel / protection

    nonisolated static func fractionText(_ percent: Int) -> String {
        percent >= 100 ? "1" : (Decimal(percent) / 100).description
    }

    /// New close (fresh identity) or, with `recovered`, the stored attempt re-sent unchanged.
    func close(_ position: Position?, percent: Int, recovered: Bool = false) async throws {
        guard let connection else { return }
        let prior = closeAttempt
        if prior != nil && !recovered { throw APIError(status: 0, message: "请先等待原平仓请求完成核对") }
        guard let target = recovered ? prior.map({ ($0.position, $0.symbol, $0.percent, $0.id) }) : position.map({ ($0.id, $0.symbol, percent, Trading.newRequestId()) }) else { return }
        let (identity, symbol, pct, key) = target
        if live && !recovered {
            let attempt = CloseAttempt(id: key, position: identity, symbol: symbol, percent: pct, created: Date().timeIntervalSince1970 * 1000)
            guard let data = try? JSONEncoder().encode(attempt) else { throw APIError(status: 0, message: "本机无法保存恢复记录，本次平仓尚未发送") }
            UserDefaults.standard.set(data, forKey: closeKey)
            closeAttempt = attempt; closeStatus = "checking"
        }
        var body: [String: Any] = ["request_id": key, "fraction": Self.fractionText(pct)]
        if live { body["confirmed_live"] = true }
        do {
            let _: Ignored = try await API.post("/v1/positions/\(identity)/close", body: body, connection: connection)
            if !live { notice = Notice(text: pct == 100 ? "模拟持仓已平仓" : "模拟仓位已部分平仓", success: true) }
            checkClose(); Haptics.success()
            await refresh()
        } catch {
            if live { checkClose(); notice = Notice(text: "正在核对原平仓请求，请查看进度", success: false) } else { throw error }
        }
    }

    func cancel(orderId: String) async throws {
        guard let connection else { return }
        let _: Ignored = try await API.post("/v1/orders/\(orderId)/cancel", body: ["request_id": Trading.newRequestId()], connection: connection)
        notice = Notice(text: "撤单已提交，成交与次数正在核对", success: true); Haptics.success()
        await refresh()
    }

    func entryStop(_ position: Position) async throws {
        guard let connection else { return }
        let _: Ignored = try await API.post("/v1/positions/\(position.id)/entry-stop", body: ["request_id": Trading.newRequestId(), "confirmed_live": live], connection: connection)
        notice = Notice(text: "止损保护已核对，开仓价不包含手续费", success: true); Haptics.success()
        await refresh()
    }

    func updateProtection(_ position: Position, stop: String?, targets: [String?],
                          requestId: String, for owner: Connection?) async throws -> ProtectionReceipt {
        guard let connection, connection == owner, online, quoteFresh(position.symbol),
              state?.positions.first(where: { $0.id == position.id })?.protectionEditable == true else {
            throw APIError(status: 409, message: "持仓状态已变化，请刷新后再调整")
        }
        var body: [String: Any] = ["request_id": requestId, "confirmed_live": live]
        if let stop { body["stop"] = stop }
        if targets.contains(where: { $0 != nil }) { body["take_profits"] = targets }
        let receipt: ProtectionReceipt = try await API.post("/v1/positions/\(position.id)/protection", body: body, connection: connection)
        guard receipt.id == position.id, ["verified", "partial"].contains(receipt.status) else {
            throw APIError(status: 503, message: "修改结果待核对")
        }
        await refresh()
        if connection == self.connection, receipt.status == "verified" {
            notice = Notice(text: "止盈止损已更新", success: true)
            Haptics.success()
        }
        return receipt
    }

    func preview(_ ticket: Ticket) async throws -> PreviewResult {
        guard let connection else { throw APIError(status: 0, message: "尚未连接") }
        return try await API.post("/v1/preview", body: ticket.json, connection: connection)
    }

    func autoStop(_ ticket: Ticket) async throws -> String {
        guard let connection else { throw APIError(status: 0, message: "尚未连接") }
        var body = ticket.json; body["stop"] = ""
        body.removeValue(forKey: "take_profits")
        let value: StopValue = try await API.post("/v1/auto-stop", body: body, connection: connection)
        return value.stop
    }

    func wickStop(symbol: String, period: String, side: String) async throws -> String {
        guard let connection else { throw APIError(status: 0, message: "尚未连接") }
        let value: WickStop = try await API.get("/v1/market/monday-wick", query: [.init(name: "symbol", value: symbol), .init(name: "period", value: period), .init(name: "side", value: side)], connection: connection)
        return value.stopLoss
    }

    func statistics(days: Int) async throws -> Statistics {
        guard let connection else { throw APIError(status: 0, message: "尚未连接") }
        return try await API.get("/v1/statistics", query: [.init(name: "days", value: String(days))], connection: connection)
    }

    func accounts() async throws -> AccountList {
        guard let connection else { throw APIError(status: 0, message: "尚未连接") }
        return try await API.get("/v1/accounts", connection: connection)
    }
}

/// Response body the caller does not read (cancel/close/entry-stop receipts).
struct Ignored: Decodable { init(from decoder: Decoder) throws {} }
