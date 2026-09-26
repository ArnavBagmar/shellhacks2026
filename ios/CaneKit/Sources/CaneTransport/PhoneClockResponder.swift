import Foundation

/// The wire shape of an inbound `ping` frame
/// (`contracts/transport_envelope.schema.json`).
private struct PingFrame: Decodable {
    let type: String
    let t1: Int64
}

/// The wire shape of an outbound `pong` frame.
private struct PongFrame: Encodable {
    let type = "pong"
    let t1: Int64
    let t2: Int64
    let t3: Int64
}

/// Pure: answers a laptop `ping` with a `pong`. This is the only
/// clock-sync code Swift has -- the phone never estimates an offset (D1).
public enum PhoneClockResponder {
    /// Parses a `{"type": "ping", "t1": ...}` frame and produces a
    /// `{"type": "pong", "t1", "t2", "t3"}` frame: `t2 = now()` taken at
    /// parse time, `t3 = now()` taken at encode time. Throws
    /// `TransportError.malformed` on anything else -- never traps.
    public static func pong(forPing data: Data, now: () -> Int64) throws -> Data {
        let ping: PingFrame
        do {
            ping = try JSONDecoder().decode(PingFrame.self, from: data)
        } catch {
            throw TransportError.malformed
        }
        guard ping.type == "ping" else {
            throw TransportError.malformed
        }

        let t2 = now()
        let response = PongFrame(t1: ping.t1, t2: t2, t3: now())
        do {
            return try JSONEncoder().encode(response)
        } catch {
            throw TransportError.malformed
        }
    }
}
