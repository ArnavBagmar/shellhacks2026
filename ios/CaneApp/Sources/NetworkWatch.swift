import Foundation
import Network

/// What the phone can see of its own network, for saying *why* the laptop
/// link is down ("Wi-Fi is off" beats "can't reach the laptop").
final class NetworkWatch: @unchecked Sendable {
    private let monitor = NWPathMonitor()
    private let lock = NSLock()
    private var up = true
    private var wifi = true

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            guard let self else { return }
            let up = path.status == .satisfied
            let wifi = path.availableInterfaces.contains { $0.type == .wifi } && path.usesInterfaceType(.wifi)
            self.lock.withLock {
                self.up = up
                self.wifi = wifi
            }
        }
        monitor.start(queue: DispatchQueue(label: "cane.network"))
    }

    var state: (networkUp: Bool, onWiFi: Bool) { lock.withLock { (up, wifi) } }

    func stop() { monitor.cancel() }
}
