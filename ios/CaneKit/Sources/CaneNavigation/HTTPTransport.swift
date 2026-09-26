import Foundation

/// One HTTP request, as a value. Kept free of `URLRequest` so the whole
/// Routes/Places layer is testable with a fake and so request shapes can
/// be asserted field by field.
public struct HTTPRequest: Sendable, Equatable {
    public let url: URL
    public let method: String
    public let headers: [String: String]
    public let body: Data?

    public init(url: URL, method: String, headers: [String: String], body: Data?) {
        self.url = url
        self.method = method
        self.headers = headers
        self.body = body
    }
}

public struct HTTPResponse: Sendable, Equatable {
    public let statusCode: Int
    public let body: Data

    public init(statusCode: Int, body: Data) {
        self.statusCode = statusCode
        self.body = body
    }
}

/// The seam between navigation and the network. Production uses
/// `URLSessionTransport`; tests use a fake that records requests, so no
/// test in this module ever touches the network.
public protocol HTTPTransport: Sendable {
    func send(_ request: HTTPRequest) async throws -> HTTPResponse
}

/// `URLSession` adapter. Non-2xx replies come back as values (the
/// clients turn them into `NavigationError.routesAPI`); only genuine
/// transport failures throw, as `NavigationError.transport`.
public struct URLSessionTransport: HTTPTransport {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        var urlRequest = URLRequest(url: request.url)
        urlRequest.httpMethod = request.method
        urlRequest.httpBody = request.body
        for (name, value) in request.headers {
            urlRequest.setValue(value, forHTTPHeaderField: name)
        }

        do {
            let (data, response) = try await session.data(for: urlRequest)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            return HTTPResponse(statusCode: status, body: data)
        } catch {
            throw NavigationError.transport(String(describing: error))
        }
    }
}
