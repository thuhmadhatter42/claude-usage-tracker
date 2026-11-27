// swift-tools-version:5.9
import PackageDescription

// jusage 0.2.0 menu bar app. One executable (`Jusage`) and one XCTest target (`JusageTests`).
//   swift build                                   compile
//   swift test --scratch-path "$TMPDIR/jusage-swift"   run every test
// Sparkle is pinned exactly: updates ship app and engine together, so the updater never floats.
let package = Package(
    name: "Jusage",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1"),
    ],
    targets: [
        .executableTarget(
            name: "Jusage",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/Jusage"
        ),
        .testTarget(
            name: "JusageTests",
            dependencies: ["Jusage"],
            path: "Tests/JusageTests"
        ),
    ]
)
