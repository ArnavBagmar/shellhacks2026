import Foundation

/// Resolves `contracts/fixtures/awareness_events.golden.json` from the
/// repo root for `CaneTransportTests`. Mirrors
/// `CaneContractsTests/ContractPaths.swift`, duplicated (not shared)
/// because the two are separate test targets/modules.
enum FixturePaths {
    /// `#filePath` for this file is
    /// `<repo root>/ios/CaneKit/Tests/CaneTransportTests/FixturePaths.swift`;
    /// its directory is four levels below the repo root.
    private static var repoRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // FixturePaths.swift -> CaneTransportTests/
            .deletingLastPathComponent() // CaneTransportTests/ -> Tests/
            .deletingLastPathComponent() // Tests/ -> CaneKit/
            .deletingLastPathComponent() // CaneKit/ -> ios/
            .deletingLastPathComponent() // ios/ -> repo root
    }

    static var goldenFixtureURL: URL {
        repoRoot.appendingPathComponent("contracts/fixtures/awareness_events.golden.json")
    }

    /// Step 20's `query`/`answer` frames: `{name, expect, frame}` cases.
    static var queryAnswerFixtureURL: URL {
        repoRoot.appendingPathComponent("contracts/fixtures/transport_query_answer.golden.json")
    }

    static func queryAnswerCases() throws -> [(name: String, valid: Bool, frame: [String: Any])] {
        let data = try Data(contentsOf: queryAnswerFixtureURL)
        guard
            let fixture = try JSONSerialization.jsonObject(with: data) as? [String: Any],
            let cases = fixture["cases"] as? [[String: Any]]
        else {
            throw FixturePathsError.malformedFixture(path: queryAnswerFixtureURL.path)
        }
        return try cases.map { item in
            guard let name = item["name"] as? String,
                  let expect = item["expect"] as? String,
                  let frame = item["frame"] as? [String: Any]
            else {
                throw FixturePathsError.malformedFixture(path: queryAnswerFixtureURL.path)
            }
            return (name, expect == "valid", frame)
        }
    }

    /// The fixture's first `expect: valid` case's raw `event` object
    /// (`phone_metric_both_range_fields`, task-3-brief.md G).
    static func firstValidEventObject() throws -> [String: Any] {
        let data = try Data(contentsOf: goldenFixtureURL)
        guard
            let fixture = try JSONSerialization.jsonObject(with: data) as? [String: Any],
            let cases = fixture["cases"] as? [[String: Any]],
            let firstValid = cases.first(where: { ($0["expect"] as? String) == "valid" }),
            let event = firstValid["event"] as? [String: Any]
        else {
            throw FixturePathsError.malformedFixture(path: goldenFixtureURL.path)
        }
        return event
    }
}

enum FixturePathsError: Error, CustomStringConvertible {
    case malformedFixture(path: String)

    var description: String {
        switch self {
        case let .malformedFixture(path):
            return "\(path) did not contain a valid case with an 'event' object"
        }
    }
}
