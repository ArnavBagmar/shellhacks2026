// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "CaneKit",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "CaneContracts", targets: ["CaneContracts"]),
        .library(name: "CaneTransport", targets: ["CaneTransport"]),
        .library(name: "CaneFeedback", targets: ["CaneFeedback"]),
        .library(name: "CaneNavigation", targets: ["CaneNavigation"]),
        .executable(name: "nav-cli", targets: ["NavCLI"]),
    ],
    targets: [
        .target(name: "CaneContracts"),
        .testTarget(name: "CaneContractsTests", dependencies: ["CaneContracts"]),
        .target(name: "CaneTransport", dependencies: ["CaneContracts"]),
        .testTarget(name: "CaneTransportTests", dependencies: ["CaneTransport", "CaneContracts"]),
        // Step 9 — feedback manager gauntlet (consumes AwarenessEvent, emits Announcement).
        .target(name: "CaneFeedback", dependencies: ["CaneContracts"]),
        .testTarget(name: "CaneFeedbackTests", dependencies: ["CaneFeedback", "CaneContracts"]),
        // Step 12 — navigation engine (Routes API, heading, turn cues). Deliberately
        // independent of CaneContracts: CoreLocation never feeds the awareness path.
        .target(name: "CaneNavigation"),
        .testTarget(name: "CaneNavigationTests", dependencies: ["CaneNavigation"]),
        // Dev tool: live Routes/Places check once the team has a Maps key (step 12 §F7).
        .executableTarget(name: "NavCLI", dependencies: ["CaneNavigation"]),
    ]
)
