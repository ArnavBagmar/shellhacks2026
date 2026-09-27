import CaneNavigation
import Foundation

/// The current indoor/outdoor verdict, readable from any thread.
///
/// `NavigationController` decides it on the main actor; the query coordinator
/// and the voice router read it from their own actors. Following the
/// `SettingsBox` precedent: a lock around one value beats making either of
/// them depend on the other.
final class WalkSettingBox: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: WalkSetting?

    var value: WalkSetting? {
        lock.withLock { stored }
    }

    func set(_ next: WalkSetting?) {
        lock.withLock { stored = next }
    }
}
