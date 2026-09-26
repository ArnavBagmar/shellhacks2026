import Foundation

/// Where a point falls on a polyline. `alongM` is deliberately
/// *unclamped*: a walker 8 m short of a corner has a negative `alongM`
/// on the next leg, and the follower needs that to report a shrinking
/// distance to the maneuver after it has advanced.
struct SegmentProjection: Equatable {
    let segmentIndex: Int
    let alongM: Double
    let offsetM: Double
    let bearingDeg: Double
}

/// Pure polyline maths shared by the follower and the walk simulator.
enum RouteGeometry {
    /// Cumulative distance to each point; `lengths.last` is the whole
    /// polyline's length.
    static func cumulativeLengths(_ points: [LatLng]) -> [Double] {
        var total = 0.0
        var lengths = [0.0]
        lengths.reserveCapacity(points.count)
        for (start, end) in zip(points, points.dropFirst()) {
            total += Geo.haversineMeters(start, end)
            lengths.append(total)
        }
        return lengths
    }

    static func totalLength(_ points: [LatLng]) -> Double {
        cumulativeLengths(points).last ?? 0
    }

    /// The segment of `points` the walker is closest to, skipping
    /// zero-length segments (their bearing is undefined). `nil` for a
    /// polyline with no real segment at all.
    static func nearestSegment(to point: LatLng, on points: [LatLng]) -> SegmentProjection? {
        var best: SegmentProjection?
        for index in 0..<max(0, points.count - 1) {
            let start = points[index]
            let end = points[index + 1]
            guard Geo.haversineMeters(start, end) > 0 else { continue }
            let candidate = SegmentProjection(
                segmentIndex: index,
                alongM: Geo.alongTrackMeters(point: point, start: start, end: end),
                offsetM: Geo.distanceToSegmentMeters(point: point, start: start, end: end),
                bearingDeg: Geo.initialBearingDeg(from: start, to: end)
            )
            if candidate.offsetM < (best?.offsetM ?? .infinity) { best = candidate }
        }
        return best
    }

    /// How far along `points` the walker has come: the projection onto
    /// the nearest segment, clamped so a point beside the polyline
    /// cannot report more progress than the polyline holds.
    static func progressAlong(_ points: [LatLng], from point: LatLng) -> Double {
        guard let projection = nearestSegment(to: point, on: points) else { return 0 }
        let lengths = cumulativeLengths(points)
        let segmentLength = lengths[projection.segmentIndex + 1] - lengths[projection.segmentIndex]
        let along = min(max(0, projection.alongM), segmentLength)
        return lengths[projection.segmentIndex] + along
    }

    /// The point `distanceM` along `points`, and the bearing of the
    /// segment it lands on. Clamped to the ends.
    static func point(at distanceM: Double, along points: [LatLng]) -> (LatLng, Double)? {
        guard points.count > 1 else { return points.first.map { ($0, 0) } }
        let lengths = cumulativeLengths(points)
        let clamped = min(max(0, distanceM), lengths.last ?? 0)
        for index in 0..<(points.count - 1) where clamped <= lengths[index + 1] {
            let bearing = Geo.initialBearingDeg(from: points[index], to: points[index + 1])
            let offset = clamped - lengths[index]
            // Exactly on a vertex: return it, rather than round-tripping
            // it through trigonometry and picking up 1e-14 of drift.
            guard offset > 0 else { return (points[index], bearing) }
            return (
                Geo.destinationPoint(from: points[index], bearingDeg: bearing, distanceMeters: offset),
                bearing
            )
        }
        let last = points.count - 1
        return (points[last], Geo.initialBearingDeg(from: points[last - 1], to: points[last]))
    }
}
