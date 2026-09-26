import CaneTransport
import Foundation

/// The phone's black-box recorder. Every event goes to
/// `Documents/diagnostics/cane-YYYYMMDD.jsonl` on the phone (visible in the
/// Files app under Cane) and, best effort, to the laptop as a `diag` frame,
/// which writes `backend/logs/phone-YYYYMMDD.jsonl`. Text and numbers only:
/// no audio is ever recorded.
///
/// Kinds: `start`, `note`, `link`, `transcript`, `gate`, `intent`, `say`,
/// `speaking`, `mic`, `query`, `answer`, `assist`, `hazard`.
final class Diagnostics: @unchecked Sendable {
    /// Volatile transcripts arrive many times a second; keep one per 250 ms.
    static let volatileEveryMs: Int64 = 250
    /// The mic's loudest level over each window -- "it didn't hear me" is
    /// often "the mic heard nothing".
    static let micWindowMs: Int64 = 2_000

    let fileURL: URL?
    private let client: EventLinkClient
    private let queue = DispatchQueue(label: "cane.diagnostics")
    private let lock = NSLock()
    private var handle: FileHandle?
    private var lastVolatile = ""
    private var lastVolatileAtMs: Int64 = 0
    private var micPeak = 0.0
    private var micWindowStartMs: Int64 = 0

    init(client: EventLinkClient) {
        self.client = client
        let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first?
            .appendingPathComponent("diagnostics", isDirectory: true)
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd"
        let url = folder?.appendingPathComponent("cane-\(formatter.string(from: Date())).jsonl")
        fileURL = url
        if let folder, let url {
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: url.path) {
                FileManager.default.createFile(atPath: url.path, contents: nil)
            }
            handle = try? FileHandle(forWritingTo: url)
            _ = try? handle?.seekToEnd()
        }
    }

    deinit {
        try? handle?.close()
    }

    func record(_ kind: String, _ data: [String: DiagnosticEvent.Value] = [:]) {
        let event = DiagnosticEvent(tPhoneMs: Self.now(), kind: kind, data: data)
        queue.async { [weak self] in
            guard let self, let handle = self.handle,
                  var line = try? JSONSerialization.data(withJSONObject: event.jsonObject, options: [.sortedKeys])
            else { return }
            line.append(0x0A)
            try? handle.write(contentsOf: line)
        }
        let client = client
        Task { try? await client.send(event) }
    }

    /// Finals always; volatile results deduplicated and thinned.
    func transcript(_ text: String, isFinal: Bool) {
        let now = Self.now()
        let keep = lock.withLock { () -> Bool in
            if isFinal {
                lastVolatile = ""
                return true
            }
            guard text != lastVolatile, now - lastVolatileAtMs >= Self.volatileEveryMs else { return false }
            lastVolatile = text
            lastVolatileAtMs = now
            return true
        }
        if keep { record("transcript", ["text": .text(text), "final": .flag(isFinal)]) }
    }

    func micLevel(_ level: Double) {
        let now = Self.now()
        let peak = lock.withLock { () -> Double? in
            micPeak = max(micPeak, level)
            guard now - micWindowStartMs >= Self.micWindowMs else { return nil }
            defer {
                micPeak = 0
                micWindowStartMs = now
            }
            return micPeak
        }
        if let peak { record("mic", ["peak": .number((peak * 100).rounded() / 100)]) }
    }

    static func now() -> Int64 { Int64(Date().timeIntervalSince1970 * 1_000) }
}
