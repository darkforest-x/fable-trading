import Foundation
import Observation

/// Device authorization for the discipline service. The token lives only in this device's Keychain.
@MainActor @Observable
final class SessionStore {
    enum Phase: Equatable { case restoring, signedOut, connected }
    var phase: Phase = .restoring
    var connection: Connection?
    var health: Health?
    var error: String?
    var busy = false

    /// Owner 2026-11-07 “服务访问令牌直接去掉吧”: no token entry in the app. The build copies the Mac's
    /// existing service token into this personal build (never into git), and the app connects by itself.
    /// The service keeps checking it, because the public endpoint can place real orders.
    static var embeddedToken: String? {
        guard AppConfig.serviceURL == AppConfig.productionURL,
              let url = Bundle.main.url(forResource: "service-token", withExtension: nil),
              let value = try? String(contentsOf: url, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
              value.count >= 32 else { return nil }
        return value
    }

    func restore() {
        #if DEBUG
        if let token = AppConfig.qaToken, !token.isEmpty {
            Task { try? await connect(token: token, remember: false) }
            return
        }
        #endif
        if let token = Self.embeddedToken {
            let saved = Keychain.read().flatMap { try? JSONDecoder().decode(Connection.self, from: $0) }
            if let saved, saved.token == token {
                connection = saved; phase = .connected
                Task { health = try? await API.get("/health", connection: nil) }
            } else {
                Task { do { try await connect(token: token) } catch { self.error = error.localizedDescription; phase = .signedOut } }
            }
            return
        }
        if let data = Keychain.read(), let saved = try? JSONDecoder().decode(Connection.self, from: data) {
            connection = saved
            phase = .connected
            Task { health = try? await API.get("/health", connection: nil) }
        } else {
            phase = .signedOut
        }
    }

    func connect(token raw: String, remember: Bool = true) async throws {
        busy = true; error = nil
        defer { busy = false }
        let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard token.count >= 32 else { throw APIError(status: 0, message: "服务令牌格式不正确") }
        let health: Health = try await API.get("/health", connection: nil)
        guard health.service == "spike-discipline", ["paper", "okx_live"].contains(health.mode) else {
            throw APIError(status: 0, message: "不是独立纪律服务")
        }
        var candidate = Connection(token: token, accountId: "primary", mode: health.mode)
        let state: StateProbe = try await API.get("/v1/state", connection: candidate)
        guard state.mode == health.mode else { throw APIError(status: 0, message: "账户模式不匹配") }
        candidate.accountId = state.accountId ?? "primary"
        if remember {
            guard let data = try? JSONEncoder().encode(candidate), Keychain.write(data) else {
                throw APIError(status: 0, message: "无法保存本机授权")
            }
        }
        self.health = health
        connection = candidate
        phase = .connected
    }

    /// 设置 · 交易账户: switch the account the app reads and trades (1.x switchAccount).
    func switchAccount(_ id: String) async throws {
        guard var next = connection, next.accountId != id else { return }
        next.accountId = id
        let probe: StateProbe = try await API.get("/v1/state", connection: next)
        guard probe.accountId == id, probe.mode == next.mode else { throw APIError(status: 0, message: "账户响应不匹配") }
        if let data = try? JSONEncoder().encode(next) { _ = Keychain.write(data) }
        connection = next
    }

    func forget() {
        Keychain.clear()
        connection = nil
        phase = .signedOut
    }

    /// Re-run the automatic connection (settings “重新连接”).
    func reconnect() {
        Keychain.clear(); connection = nil; phase = .restoring; error = nil
        restore()
    }

    /// A 401 anywhere means the stored authorization is no longer valid.
    func handle(_ error: Error) {
        if let api = error as? APIError, api.status == 401 { forget(); self.error = "内置授权已失效：请在 Mac 上重新构建并安装" }
    }
}
