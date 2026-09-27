import CaneNavigation
import MapKit
import SwiftUI

/// The debug map (triple-tap the chest screen): where navigation is trying
/// to take the walker. Yellow line = Google's walking route; flag = the
/// place it resolved; blue dot + ring = the phone's GPS fix and its error;
/// the arrow = which way navigation thinks the walker faces; orange = the
/// next maneuver. For the sighted helper only -- nothing reads it aloud.
struct NavigationMapView: View {
    let debug: NavigationDebug
    @State private var position: MapCameraPosition = .automatic
    @State private var follow = true

    private var s: NavigationSnapshot { debug.snapshot }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Map(position: $position, interactionModes: [.pan, .zoom]) {
                if s.routePoints.count > 1 {
                    MapPolyline(coordinates: s.routePoints.map(\.coordinate))
                        .stroke(.yellow, style: StrokeStyle(lineWidth: 5, lineCap: .round, lineJoin: .round))
                }
                if let destination = s.destination {
                    Marker(s.placeName ?? debug.query, systemImage: "flag.checkered", coordinate: destination.coordinate)
                        .tint(.green)
                }
                if let next = s.nextManeuverPoint {
                    Annotation("", coordinate: next.coordinate) {
                        Circle().fill(.orange).stroke(.black, lineWidth: 2).frame(width: 14, height: 14)
                    }
                }
                if let fix = s.fix {
                    MapCircle(center: fix.latLng.coordinate, radius: max(fix.horizontalAccuracyM, 1))
                        .foregroundStyle(.blue.opacity(0.18))
                        .stroke(.blue.opacity(0.6), lineWidth: 1)
                    Annotation("", coordinate: fix.latLng.coordinate) {
                        ZStack {
                            Circle().fill(.blue).stroke(.white, lineWidth: 2).frame(width: 16, height: 16)
                            if let heading = s.headingDeg {
                                Image(systemName: "location.north.fill")
                                    .font(.system(size: 18, weight: .bold))
                                    .foregroundStyle(.white, .blue)
                                    .offset(y: -18)
                                    .rotationEffect(.degrees(heading))
                            }
                        }
                    }
                }
            }
            .mapStyle(.standard(pointsOfInterest: .excludingAll))
            .frame(height: 300)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .onMapCameraChange(frequency: .onEnd) {
                // A pan or zoom by hand stops the auto-follow until "Follow".
                if position.positionedByUser { follow = false }
            }
            .onChange(of: s.fix?.timestampMs) { if follow { position = .automatic } }
            .overlay(alignment: .topTrailing) {
                Button(follow ? "Following" : "Follow") { follow = true; position = .automatic }
                    .font(.caption.bold())
                    .buttonStyle(.borderedProminent)
                    .tint(follow ? .gray : .blue)
                    .padding(8)
            }

            Group {
                Text("→ \(s.placeName ?? debug.query)  ·  \(status)")
                    .font(.callout.bold())
                Text(lineNext)
                Text(lineGPS)
                if let cue = debug.lastCue { Text("last said: \(cue)") }
            }
            .font(.caption.monospaced())
            .foregroundStyle(.white.opacity(0.85))
        }
    }

    private var status: String {
        switch s.status {
        case .idle: "idle"
        case .resolving: "finding the place"
        case .routing: "asking Google for a route"
        case .navigating: s.distanceToDestinationM.map { "\(Int($0.rounded())) m to go" } ?? "navigating"
        case .arrived: "arrived"
        case .failed(let error): "failed: \(error)"
        }
    }

    private var lineNext: String {
        guard let d = s.distanceToNextManeuverM, let m = s.nextManeuver else {
            return s.routeMeters.map { "route \($0) m" } ?? "no route yet"
        }
        return "next \(m.rawValue.lowercased().replacingOccurrences(of: "_", with: " ")) in \(Int(d.rounded())) m"
    }

    private var lineGPS: String {
        guard let fix = s.fix else { return "GPS: no fix yet" }
        let off = s.offRouteM.map { "off route \(Int($0.rounded())) m · " } ?? ""
        let compass = s.compassOffsetDeg.map { "compass offset \(Int($0.rounded()))°" } ?? "compass: learning (walk a few steps)"
        return "\(off)GPS ±\(Int(fix.horizontalAccuracyM.rounded())) m · \(compass)"
    }
}

private extension LatLng {
    var coordinate: CLLocationCoordinate2D { CLLocationCoordinate2D(latitude: latitude, longitude: longitude) }
}
