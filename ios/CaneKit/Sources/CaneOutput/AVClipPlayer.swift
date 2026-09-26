#if os(iOS)
import AVFoundation
import Foundation
import os

/// `AVAudioPlayer` behind a `play` that cannot hang: resume-once, and a
/// watchdog at the clip's own length + 1.5 s in case the delegate never
/// fires. Uses the one shared audio session (`AudioSessionConfig`).
public final class AVClipPlayer: NSObject, ClipPlayer, AVAudioPlayerDelegate, @unchecked Sendable {
    static let watchdogSlackMs = 1_500

    private let lock = NSLock()
    private var player: AVAudioPlayer?
    private var pending: CheckedContinuation<Bool, Never>?
    private let logger = Logger(subsystem: "cane", category: "clip-player")

    override public init() {
        super.init()
    }

    public func play(_ audio: Data) async -> Bool {
        guard !Task.isCancelled else { return false }
        let player: AVAudioPlayer
        do {
            player = try AVAudioPlayer(data: audio)
        } catch {
            logger.warning("clip would not decode: \(String(describing: error), privacy: .public)")
            return false
        }
        player.delegate = self
        player.prepareToPlay()
        let watchdogMs = Int(player.duration * 1000) + Self.watchdogSlackMs
        return await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
                let previous = lock.withLock { () -> CheckedContinuation<Bool, Never>? in
                    let previous = pending
                    self.player?.stop()
                    self.player = player
                    pending = continuation
                    return previous
                }
                previous?.resume(returning: false)
                guard player.play() else {
                    finish(player, played: false)
                    return
                }
                Task { [weak self] in
                    try? await Task.sleep(for: .milliseconds(watchdogMs))
                    self?.finish(player, played: true)
                }
            }
        } onCancel: {
            self.stop()
        }
    }

    public func stop() {
        let (player, continuation) = lock.withLock { () -> (AVAudioPlayer?, CheckedContinuation<Bool, Never>?) in
            defer {
                self.player = nil
                pending = nil
            }
            return (self.player, pending)
        }
        player?.stop()
        continuation?.resume(returning: false)
    }

    public func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        finish(player, played: flag)
    }

    public func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        finish(player, played: false)
    }

    /// Resume-once, and only for the clip that is still current.
    private func finish(_ which: AVAudioPlayer, played: Bool) {
        let continuation = lock.withLock { () -> CheckedContinuation<Bool, Never>? in
            guard self.player === which else { return nil }
            defer {
                self.player = nil
                pending = nil
            }
            return pending
        }
        continuation?.resume(returning: played)
    }
}
#endif
