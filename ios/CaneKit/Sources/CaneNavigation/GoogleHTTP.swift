import Foundation

/// The two things every Google endpoint in this module does the same
/// way: wrap transport failures as `NavigationError.transport`, and turn
/// a non-2xx reply into `.routesAPI(status:message:)` using the
/// `google.rpc.Status` body when there is one.
///
/// `.routesAPI` carries Places errors too: brief F1 lists one HTTP-error
/// case for the module, and a Places 403 and a Routes 403 need the same
/// handling (enable the API on the key).
enum GoogleHTTP {
    static func send(_ request: HTTPRequest, through transport: any HTTPTransport) async throws
        -> HTTPResponse
    {
        do {
            return try await transport.send(request)
        } catch let error as NavigationError {
            throw error
        } catch {
            throw NavigationError.transport(String(describing: error))
        }
    }

    static func statusError(from response: HTTPResponse) -> NavigationError {
        let envelope = try? JSONDecoder().decode(GoogleRPCStatusEnvelope.self, from: response.body)
        let message = envelope?.error.message
            ?? String(data: response.body, encoding: .utf8)
            ?? "no message"
        return .routesAPI(status: response.statusCode, message: message)
    }
}
