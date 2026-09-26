// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "CaneKit",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(
            name: "CaneContracts",
            targets: ["CaneContracts"]
        ),
    ],
    targets: [
        .target(
            name: "CaneContracts"
        ),
        .testTarget(
            name: "CaneContractsTests",
            dependencies: ["CaneContracts"]
        ),
    ]
)
