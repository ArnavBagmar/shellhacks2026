import Foundation

/// Resolves `contracts/awareness_event.schema.json` and
/// `contracts/fixtures/awareness_events.golden.json` from the repo root, so
/// the `../../../../` literal exists exactly once for both test files.
enum ContractPaths {
    /// `#filePath` for this file is
    /// `<repo root>/ios/CaneKit/Tests/CaneContractsTests/ContractPaths.swift`;
    /// its directory is four levels below the repo root.
    private static var repoRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // ContractPaths.swift -> CaneContractsTests/
            .deletingLastPathComponent() // CaneContractsTests/ -> Tests/
            .deletingLastPathComponent() // Tests/ -> CaneKit/
            .deletingLastPathComponent() // CaneKit/ -> ios/
            .deletingLastPathComponent() // ios/ -> repo root
    }

    static var schemaURL: URL {
        repoRoot.appendingPathComponent("contracts/awareness_event.schema.json")
    }

    static var fixtureURL: URL {
        repoRoot.appendingPathComponent("contracts/fixtures/awareness_events.golden.json")
    }

    /// Loads and parses a JSON file, throwing a message that contains the
    /// absolute path on failure.
    static func loadJSONObject(at url: URL) throws -> [String: Any] {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw ContractPathError.unreadable(path: url.path, underlying: error)
        }
        guard
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            throw ContractPathError.notAJSONObject(path: url.path)
        }
        return object
    }
}

enum ContractPathError: Error, CustomStringConvertible {
    case unreadable(path: String, underlying: Error)
    case notAJSONObject(path: String)

    var description: String {
        switch self {
        case let .unreadable(path, underlying):
            return "could not read \(path): \(underlying)"
        case let .notAJSONObject(path):
            return "\(path) did not contain a top-level JSON object"
        }
    }
}
