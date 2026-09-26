import Foundation

/// Resolves `contracts/fixtures/awareness_events.golden.json` from the repo
/// root. Mirrors `CaneContractsTests/ContractPaths` -- test targets cannot
/// share source, so the `../../../../` literal exists once per target.
enum FeedbackFixturePaths {
    /// `#filePath` is
    /// `<repo root>/ios/CaneKit/Tests/CaneFeedbackTests/FeedbackFixturePaths.swift`.
    private static var repoRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // -> CaneFeedbackTests/
            .deletingLastPathComponent() // -> Tests/
            .deletingLastPathComponent() // -> CaneKit/
            .deletingLastPathComponent() // -> ios/
            .deletingLastPathComponent() // -> repo root
    }

    static var goldenFixtureURL: URL {
        repoRoot.appendingPathComponent("contracts/fixtures/awareness_events.golden.json")
    }

    struct GoldenCase {
        let name: String
        let expectValid: Bool
        let json: Data
    }

    /// Returns the fixture's `cases` as raw JSON payloads plus the
    /// `malformed_payloads` strings, which are not JSON objects at all.
    static func load() throws -> (cases: [GoldenCase], malformed: [Data]) {
        let data = try Data(contentsOf: goldenFixtureURL)
        guard
            let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
            let rawCases = root["cases"] as? [[String: Any]],
            let rawMalformed = root["malformed_payloads"] as? [String]
        else {
            throw FixtureError.unusable(path: goldenFixtureURL.path)
        }
        let cases: [GoldenCase] = try rawCases.map { raw in
            guard
                let name = raw["name"] as? String,
                let expect = raw["expect"] as? String,
                let event = raw["event"] as? [String: Any]
            else {
                throw FixtureError.unusable(path: goldenFixtureURL.path)
            }
            return GoldenCase(
                name: name,
                expectValid: expect == "valid",
                json: try JSONSerialization.data(withJSONObject: event)
            )
        }
        return (cases, rawMalformed.map { Data($0.utf8) })
    }

    enum FixtureError: Error, CustomStringConvertible {
        case unusable(path: String)

        var description: String {
            switch self {
            case let .unusable(path): return "golden fixture unusable at \(path)"
            }
        }
    }
}
