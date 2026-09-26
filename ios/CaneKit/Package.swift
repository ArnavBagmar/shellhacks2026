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
        .library(name: "CaneOutput", targets: ["CaneOutput"]),
        .library(name: "CaneVoice", targets: ["CaneVoice"]),
        .library(name: "CaneCamera", targets: ["CaneCamera"]),
        .library(name: "CaneAssist", targets: ["CaneAssist"]),
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
        // Step 10 — haptics, speech, earcon and the output arbiter. Pure policy
        // is Foundation-only; engine wrappers are iOS-only behind protocols.
        .target(name: "CaneOutput", dependencies: ["CaneContracts", "CaneFeedback"]),
        .testTarget(name: "CaneOutputTests", dependencies: ["CaneOutput", "CaneFeedback", "CaneContracts"]),
        // Step 11 + 20 — open mic: wake word, intent grammar, on-device speech.
        .target(name: "CaneVoice"),
        .testTarget(name: "CaneVoiceTests", dependencies: ["CaneVoice"]),
        // The shared front (selfie) camera: the chest mount faces the screen
        // out, so the front camera looks forward. P1's detector plugs in here.
        .target(name: "CaneCamera"),
        .testTarget(name: "CaneCameraTests", dependencies: ["CaneCamera"]),
        // Step 20 — "Hey Cane, is there a bathroom around?": intent -> query ->
        // the laptop's Gemini answer -> speech.
        .target(name: "CaneAssist", dependencies: ["CaneTransport", "CaneOutput", "CaneVoice"]),
        .testTarget(name: "CaneAssistTests", dependencies: ["CaneAssist", "CaneTransport", "CaneOutput", "CaneVoice"]),
        // Step 12 — navigation engine (Routes API, heading, turn cues). Deliberately
        // independent of CaneContracts: CoreLocation never feeds the awareness path.
        .target(name: "CaneNavigation"),
        .testTarget(name: "CaneNavigationTests", dependencies: ["CaneNavigation"]),
        // Dev tool: live Routes/Places check once the team has a Maps key (step 12 §F7).
        .executableTarget(name: "NavCLI", dependencies: ["CaneNavigation"]),
    ]
)
