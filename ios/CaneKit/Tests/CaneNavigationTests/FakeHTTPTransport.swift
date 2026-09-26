import Foundation

@testable import CaneNavigation

/// Records every request and replays queued replies. An `actor` because
/// the client calls it from whatever executor the caller is on, and the
/// tests read `requests` afterwards.
actor FakeHTTPTransport: HTTPTransport {
    private(set) var requests: [HTTPRequest] = []
    private var replies: [Result<HTTPResponse, any Error>]

    init(replies: [Result<HTTPResponse, any Error>]) {
        self.replies = replies
    }

    /// Replies 200 with `json`, forever.
    static func alwaysOK(json: String) -> FakeHTTPTransport {
        FakeHTTPTransport(replies: [.success(HTTPResponse(statusCode: 200, body: Data(json.utf8)))])
    }

    /// Replies 200 with raw bytes (used with the recorded fixture).
    static func alwaysOK(data: Data) -> FakeHTTPTransport {
        FakeHTTPTransport(replies: [.success(HTTPResponse(statusCode: 200, body: data))])
    }

    static func always(status: Int, json: String) -> FakeHTTPTransport {
        FakeHTTPTransport(
            replies: [.success(HTTPResponse(statusCode: status, body: Data(json.utf8)))]
        )
    }

    static func alwaysFailing(with error: any Error) -> FakeHTTPTransport {
        FakeHTTPTransport(replies: [.failure(error)])
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        requests.append(request)
        // The last reply repeats, so a reroute test does not have to
        // queue one reply per fetch.
        let reply = replies.count > 1 ? replies.removeFirst() : (replies.first ?? .failure(FakeTransportExhausted()))
        return try reply.get()
    }

    /// The decoded JSON body of request `index`, for shape assertions.
    func requestBody(at index: Int) throws -> [String: Any] {
        guard
            index < requests.count,
            let body = requests[index].body,
            let object = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        else {
            throw FakeTransportExhausted()
        }
        return object
    }
}

struct FakeTransportExhausted: Error {}
