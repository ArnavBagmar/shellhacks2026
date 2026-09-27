import SwiftUI

/// Cane on the wrist: taps that get faster as the hand closes on the thing
/// the phone is guiding it to. Open it before "Hey Cane, guide my hand to ...".
@main
struct CaneWatchApp: App {
    @State private var model = GuideModel()

    var body: some Scene {
        WindowGroup {
            GuideView(model: model)
        }
    }
}
