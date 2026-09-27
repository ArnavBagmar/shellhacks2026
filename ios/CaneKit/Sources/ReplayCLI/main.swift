import CaneContracts
import CaneFeedback
import CaneReplay
import Foundation

// Step 28's read-out. Replays a walk recorded by the bridge's
// `--record-events` and prints what the phone would have announced.
//
//     swift run replay-cli logs/walk-a.jsonl
//
// Writes `<recording>.tally.csv` beside the input so two takes can be diffed
// without re-reading the terminal.

let usage = """
usage: replay-cli <recording.jsonl>

Replays a walk recorded by `python -m cane.bridge --record-events` through the
feedback gauntlet and reports what the phone would have announced.
"""

let arguments = CommandLine.arguments.dropFirst()
guard arguments.count == 1, let path = arguments.first, !path.hasPrefix("-") else {
    FileHandle.standardError.write(Data((usage + "\n").utf8))
    exit(2)
}

let url = URL(fileURLWithPath: path)

/// Pads without ever truncating: a label that outgrows the column shifts the
/// row rather than losing characters.
func pad(_ text: String, to width: Int) -> String {
    text.count >= width ? text : text + String(repeating: " ", count: width - text.count)
}

func padLeft(_ text: String, to width: Int) -> String {
    text.count >= width ? text : String(repeating: " ", count: width - text.count) + text
}

func minutesAndSeconds(_ ms: Int64) -> String {
    let total = Int(ms / 1_000)
    return "\(total / 60)m \(padLeft(String(total % 60), to: 2).replacingOccurrences(of: " ", with: "0"))s"
}

func row(_ label: String, _ count: Int, _ tally: ReplayTally) -> String {
    let rate = String(format: "%.1f", tally.perMinute(count))
    return "  \(pad(label, to: 22))\(padLeft(String(count), to: 6))   \(padLeft(rate, to: 6)) /min"
}

do {
    let result = try await ReplayRunner.run(contentsOf: url)
    let tally = result.tally
    let byClass = tally.classes
        .sorted { ($0.value, $1.key.rawValue) > ($1.value, $0.key.rawValue) }

    var report: [String] = [
        "",
        url.lastPathComponent,
        "  \(pad("events replayed", to: 22))\(padLeft(String(result.linesRead), to: 6))"
            + "   (\(result.linesRejected) rejected)",
        "  \(pad("span", to: 22))\(padLeft(minutesAndSeconds(tally.spanMs), to: 6))",
        "",
        row("announcements", tally.announcements, tally),
        row("urgency 3  Stop", tally.count(urgency: 3), tally),
        row("urgency 2  act soon", tally.count(urgency: 2), tally),
        row("urgency 1  aware", tally.count(urgency: 1), tally),
        "",
        row("spoken", tally.spoken, tally),
        row("haptic only", tally.hapticOnly, tally),
    ]

    if !byClass.isEmpty {
        report.append("")
        report.append("  by class")
        for (cls, count) in byClass {
            report.append("    \(pad(cls.rawValue, to: 20))\(padLeft(String(count), to: 6))")
        }
    }

    report += [
        "",
        "  Counts are gauntlet decisions, not seconds of audio: the replay sink",
        "  returns at once, so totals are an upper bound on what would be heard.",
        "  `urgency 3` is exact -- a Stop preempts everything and always speaks.",
        "",
    ]
    print(report.joined(separator: "\n"))

    var csv = "metric,count,per_minute\n"
    csv += "events_replayed,\(result.linesRead),\n"
    csv += "lines_rejected,\(result.linesRejected),\n"
    csv += "span_ms,\(tally.spanMs),\n"
    for (label, count) in [
        ("announcements", tally.announcements),
        ("urgency_3_stop", tally.count(urgency: 3)),
        ("urgency_2_act_soon", tally.count(urgency: 2)),
        ("urgency_1_aware", tally.count(urgency: 1)),
        ("spoken", tally.spoken),
        ("haptic_only", tally.hapticOnly),
    ] {
        csv += "\(label),\(count),\(String(format: "%.3f", tally.perMinute(count)))\n"
    }
    for (cls, count) in byClass {
        csv += "class_\(cls.rawValue),\(count),\(String(format: "%.3f", tally.perMinute(count)))\n"
    }

    let csvURL = url.deletingPathExtension().appendingPathExtension("tally.csv")
    try csv.write(to: csvURL, atomically: true, encoding: .utf8)
    print("  wrote \(csvURL.lastPathComponent)\n")
} catch {
    FileHandle.standardError.write(Data("replay-cli: \(error)\n".utf8))
    exit(1)
}
