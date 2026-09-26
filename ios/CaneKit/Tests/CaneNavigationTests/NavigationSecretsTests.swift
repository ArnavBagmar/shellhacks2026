import XCTest

@testable import CaneNavigation

/// The key lookup: environment first, Info.plist second, and a
/// `NavigationError.missingAPIKey` value (never a trap, never a
/// committed default) when neither has one.
final class NavigationSecretsTests: XCTestCase {
    func testEnvironmentVariableWins() throws {
        let key = try NavigationSecrets.apiKey(
            environment: [NavigationSecrets.environmentVariableName: "from-environment"],
            infoPlistValue: "from-plist"
        )

        XCTAssertEqual(key, "from-environment")
    }

    func testFallsBackToInfoPlist() throws {
        let key = try NavigationSecrets.apiKey(environment: [:], infoPlistValue: "from-plist")

        XCTAssertEqual(key, "from-plist")
    }

    func testMissingKeyThrows() {
        XCTAssertThrowsError(try NavigationSecrets.apiKey(environment: [:], infoPlistValue: nil)) {
            XCTAssertEqual($0 as? NavigationError, .missingAPIKey)
        }
    }

    func testBlankKeyCountsAsMissing() {
        XCTAssertThrowsError(
            try NavigationSecrets.apiKey(
                environment: [NavigationSecrets.environmentVariableName: "   "],
                infoPlistValue: "\n"
            )
        ) {
            XCTAssertEqual($0 as? NavigationError, .missingAPIKey)
        }
    }

    func testKeyIsTrimmed() throws {
        let key = try NavigationSecrets.apiKey(
            environment: [NavigationSecrets.environmentVariableName: " padded-key\n"],
            infoPlistValue: nil
        )

        XCTAssertEqual(key, "padded-key")
    }

    func testNamesMatchTheDocumentedLookup() {
        XCTAssertEqual(NavigationSecrets.environmentVariableName, "GOOGLE_MAPS_API_KEY")
        XCTAssertEqual(NavigationSecrets.infoPlistKey, "GoogleMapsAPIKey")
    }
}
