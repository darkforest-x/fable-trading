import Foundation

struct Connection: Equatable, Codable {
    var token: String
    var accountId: String = "primary"
    var mode: String = "okx_live"
}

struct APIError: LocalizedError {
    let status: Int
    let message: String
    var errorDescription: String? { message }
}

/// HTTP client for the discipline service. Order, close and protection writes go through
/// AccountStore only, which persists request identities before sending (see AccountStore.swift).
enum API {
    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 45   // per-request timeouts below; preview needs 45 s
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }()

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], connection: Connection?) async throws -> T {
        try await send(path, method: "GET", query: query, body: nil, connection: connection)
    }

    /// /v1/preview may take up to 45 s server-side (1.x api.ts); everything else 12 s.
    static func post<T: Decodable>(_ path: String, body: [String: Any], connection: Connection) async throws -> T {
        let data = try JSONSerialization.data(withJSONObject: body)
        return try await send(path, method: "POST", query: [], body: data, connection: connection, timeout: path == "/v1/preview" ? 45 : 12)
    }

    private static func send<T: Decodable>(_ path: String, method: String, query: [URLQueryItem], body: Data?, connection: Connection?, timeout: TimeInterval = 12) async throws -> T {
        var components = URLComponents(url: AppConfig.serviceURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let connection {
            request.setValue("Bearer \(connection.token)", forHTTPHeaderField: "Authorization")
            request.setValue(connection.accountId, forHTTPHeaderField: "X-Spike-Account")
        }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw APIError(status: status, message: detail ?? "服务暂不可用（\(status)）")
        }
        return try decoder.decode(T.self, from: data)
    }
}

/// The single monitor write the app may make (owner 2026-11-06): switch 1.618 Bark pushes.
enum BarkSwitch {
    static func set(_ enabled: Bool, connection: Connection) async throws -> NotificationSnapshot {
        try await API.post("/v1/monitor/fib-bark", body: ["enabled": enabled], connection: connection)
    }
}
