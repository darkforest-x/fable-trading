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

    func restore() {
        #if DEBUG
        if let token = AppConfig.qaToken, !token.isEmpty {
            Task { try? await connect(token: token, remember: false) }
            return
        }
        #endif
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

    func forget() {
        Keychain.clear()
        connection = nil
        phase = .signedOut
    }

    /// A 401 anywhere means the stored authorization is no longer valid.
    func handle(_ error: Error) {
        if let api = error as? APIError, api.status == 401 { forget(); self.error = "设备授权已失效，请重新连接" }
    }
}
