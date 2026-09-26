# Task 12 brief — Navigation: Routes API + turn cues

Extracted from docs/orchestration-runbook.md (Step 12), docs/build-plan.md F7, docs/device-responsibilities.md (phone ownership + CoreLocation boundary), docs/camera-sync-design.md §1.1, docs/steps/step-10-design.md hazards H-12a/H-12b, plus controller rulings. Exact values are binding. Section H (API reference) was produced by the docs-lookup pass on 2026-09-26 and is the source of truth for request/response shapes.

## A. Runbook step text (verbatim)

**Intent:** Turn-by-turn, kept strictly out of the awareness path.
**Chain rationale:** `docs-lookup` first because Routes API request shapes are easy to get subtly wrong from memory and a wrong field costs an hour.

```bash
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-12] Build the navigation engine: Google Routes API walking route, Geocoding for destination lookup, CoreLocation true heading, route-polyline follow at about 1 Hz, heading error mapped to a directional haptic turn cue, and step advance on proximity. CoreLocation feeds navigation ONLY and never the awareness path, where bearings stay torso-relative. Acceptance: a two-turn walking route plays correct turn cues on the demo route; losing GPS degrades navigation without affecting hazard warnings"
```

## B. Spec (verbatim)

### build-plan.md F7 · Turn-by-turn navigation — MVP, P3
- **Hardware** — iPhone GPS + magnetometer
- **Libraries** — `CoreLocation` (`CLLocationManager`, true heading) · `URLSession` · optionally Google Maps SDK for iOS for a judge-facing map view
- **Computation** — route polyline follow, heading error → directional haptic turn cue, step advance on proximity. Runs at ~1 Hz
- **APIs** — **Google Routes API** (`routes.googleapis.com`, walking mode) · Geocoding API for destination lookup
- **Cloud / external** — Google Maps Platform key with Routes + Geocoding enabled
- **Hard boundary** — CoreLocation feeds **navigation only**. It never touches the awareness path; bearings there are torso-relative precisely so `CLLocation.course` (unreliable below ~0.5 m/s — i.e. standing at a curb) can never define the reference frame

### device-responsibilities.md
| CoreLocation | GPS + true heading — **navigation only**, never the awareness path |
- **Navigation** — routes, heading, turn cues. (phone OWNS)
| Turn-by-turn with directional haptics | phone | P3 | ✅ |

### camera-sync-design.md §1.1
Navigation still needs world heading for "turn left in 20 m" — that is CoreLocation on the phone, and it never touches the event path.

## C. Hazards inherited from the step-10 design (verbatim, binding)
- **H-12a (step 12, navigation cues — the important one).** `HapticScript` is reusable by step 12 as E7 requires, but step 12 **must draw its turn cues from a disjoint region of the same design space** and must pass `HapticScript.validate()`. Specifically: no step-12 script may last more than 420 ms, exceed intensity 0.85, or contain a continuous element longer than 400 ms, because those properties are what make urgency 3 unmistakable. A turn cue that feels like the alarm is a wrong-action defect in the most literal sense. Reusing `.left` / `.right` for "turn left/right" is acceptable **only** with a distinct leading accent or a distinct gain tier, and only after re-running the Amendment 7 protocol with the combined alphabet — five symbols passed; nine may not.
- **H-12b (step 12).** Navigation speech and hazard speech share one synthesizer and one 1800 ms gap. Hazard speech must win; step 12 needs an explicit priority below urgency 1, and its phrases must obey the same direction-first word order (WCAG 3.2.4) or the user will be learning two vocabularies.

## D. What earlier steps built (use, do not re-implement)
- `ios/CaneKit/Package.swift` already declares the `CaneNavigation` library target and `CaneNavigationTests` (placeholder files to replace). The target deliberately has **no dependency on CaneContracts, CaneFeedback or CaneTransport** — that is the "never the awareness path" rule made structural. Do not add one.
- Step 10 (not yet built) owns the haptic alphabet and the speech renderer. Step 12 emits a `TurnCue` value; the step-10/app integration renders it. Step 12 does NOT construct `HapticScript`s.

## E. Environment facts (binding)
- No Google Maps Platform key exists on the build machine and none may be committed. Tests run entirely against recorded fixtures and fake transports on macOS (`swift test`). Live verification against the real API is a team task once a key exists (section F7 gives the command).
- CoreLocation heading APIs are iOS-only in practice; the CoreLocation adapter compiles only under `#if os(iOS)`. Everything else is platform-neutral Swift so it is testable on the Mac.

## F. Controller rulings — binding

### F1. Package layout (`ios/CaneKit/Sources/CaneNavigation/`, each file ≤ 200 lines)
```
Geo.swift                 # LatLng (frozen struct, degrees); haversineMeters, initialBearingDeg, crossTrackMeters, alongTrackMeters, destinationPoint — pure, unit-tested
Polyline.swift            # Google encoded polyline decode/encode at 1e5 precision (section H algorithm); encode exists so tests can build fixtures
Maneuver.swift            # enum Maneuver: String, Codable — the COMPLETE 21-value Routes API list from section H (MANEUVER_UNSPECIFIED … NAME_CHANGE), plus `.unknown` for unrecognised strings (decode must not throw on a new value); var turnDirection: TurnDirection
Route.swift               # Route { distanceMeters, durationS, points: [LatLng], legs: [RouteLeg] }; RouteLeg { steps: [RouteStep] }; RouteStep { distanceMeters, staticDurationS, points, start, end, maneuver, instruction }
RoutesAPI.swift           # Codable request/response models mirroring section H exactly (only the fields in the field mask); RoutesClient: computeWalkingRoute(origin: LatLng, destination: Destination) async throws -> Route; builds POST body, X-Goog-Api-Key + X-Goog-FieldMask headers; uses `HTTPTransport` protocol (URLSession adapter + fake)
Destination.swift         # enum Destination { latLng(LatLng), address(String) }; DestinationResolver protocol: resolve(freeText) async throws -> ResolvedPlace { name, latLng, formattedAddress }; PlacesTextSearchResolver (Places API New, section H) with GeocodingResolver as the fallback if the lookup says Places is unsuitable
Sources.swift             # LocationFix { latLng, horizontalAccuracyM, speedMps?, timestampMs }; HeadingSample { trueHeadingDeg (0..<360), accuracyDeg, timestampMs }; protocols LocationSource / HeadingSource exposing AsyncStream<...>; fakes live in the test target
RouteFollower.swift       # pure struct: FollowerState + advance(fix:, heading:) -> FollowerState. Tracks currentStepIndex, distanceToNextManeuverM (along-track), headingErrorDeg (heading − segment bearing, normalised to (−180, 180]), offRouteM (cross-track to the nearest segment of the current step ±1), isArrived. Step advance when the fix is within STEP_ADVANCE_RADIUS_M = 8 of the step end OR along-track passes the step end; never advances more than one step per fix; never regresses.
TurnCuePolicy.swift       # pure: (previous policy state, FollowerState, now) -> (new state, TurnCue?) — see F3
TurnCue.swift             # TurnCue { kind: CueKind, direction: TurnDirection, distanceM: Int?, phrase: String, priority: .navigation }; TurnDirection { left, right, slightLeft, slightRight, sharpLeft, sharpRight, straight, uTurn, arrive }; CueKind { prepare, now, continueStraight, correctHeading, offRoute, rerouting, arrived, gpsLost, gpsRestored }; static Phrasing rules (F4)
NavigationEngine.swift    # actor NavigationEngine: start(to: String) / start(to: LatLng); cues: AsyncStream<TurnCue>; stop(); state: NavigationStatus; internal 1 Hz loop (F5)
CoreLocationSources.swift # #if os(iOS): CLLocationManager-backed LocationSource + HeadingSource (delegate → AsyncStream); trueHeading < 0 ⇒ sample dropped; desiredAccuracy kCLLocationAccuracyBest, distanceFilter 1 m, headingFilter 3°; requestWhenInUseAuthorization; documents NSLocationWhenInUseUsageDescription
NavigationSecrets.swift   # apiKey(): reads env GOOGLE_MAPS_API_KEY, else Info.plist "GoogleMapsAPIKey"; throws NavigationError.missingAPIKey — checked BEFORE any network call
NavigationError.swift     # missingAPIKey, destinationNotFound(String), routesAPI(status: Int, message: String), noRoute, decoding(String), transport(Error)
```
No `Task.sleep` in pure code; time is injected as `nowMs: Int64` everywhere except the engine loop's clock, which is an injected `Clock`-like protocol so tests run instantly.

### F2. Destination resolution
Free text ("take me to the library") → `DestinationResolver`. Ruling: prefer **Places API (New) Text Search** with a `locationBias` circle of 2 km around the current fix and field mask `places.location,places.displayName,places.formattedAddress`; take the first result; if the lookup in section H marks Places as unsuitable or the key lacks Places, `GeocodingResolver` is the drop-in fallback (`status == "OK"` → first result, `ZERO_RESULTS` → `destinationNotFound`). The resolver is a protocol so tests inject a fake.

### F3. Turn-cue policy (exact values)
Evaluated once per follower update (~1 Hz). Constants in `TurnCuePolicy`:
- `PREPARE_DISTANCE_M = 30`, `NOW_DISTANCE_M = 8`, `ARRIVE_RADIUS_M = 10`, `OFF_ROUTE_M = 25`, `OFF_ROUTE_HOLD_S = 5`, `HEADING_CORRECT_DEG = 45`, `HEADING_OK_DEG = 20`, `CORRECT_COOLDOWN_S = 6`, `STRAIGHT_REMINDER_S = 20`, `MIN_WALK_SPEED_MPS = 0.5`, `GPS_LOST_AFTER_S = 10`.
- Per step, emit at most one `.prepare` (when `distanceToNextManeuverM ≤ 30` and the step's maneuver is a turn) and at most one `.now` (≤ 8 m). Straight/continue maneuvers emit no prepare/now.
- `.correctHeading(direction)` when `|headingErrorDeg| > 45` AND the last fix speed ≥ 0.5 m/s (standing still → heading is advisory; never nag at a curb), at most once per `CORRECT_COOLDOWN_S`; direction = sign of the error (positive error ⇒ turn left, define and test the sign in ONE place).
- `.continueStraight` at most every `STRAIGHT_REMINDER_S` while `|headingError| < 20` and no maneuver within 30 m.
- `.offRoute` after cross-track > 25 m for 5 consecutive seconds → engine requests a reroute (F5) and emits `.rerouting` once; on the new route, state resets.
- `.arrived` once when within 10 m of the destination; afterwards the engine stops.
- `.gpsLost` once when no fix for 10 s; **no other cue is emitted while lost** (acceptance: "losing GPS degrades navigation"); `.gpsRestored` once when fixes resume.
- **Amendment (2026-09-26, from the live FIU route Graham Center → Green Library):** `POST_MANEUVER_GRACE_S = 6` — no `.correctHeading` within 6 s after a `.now`, because the heading swings through a sharp vertex for a tick and the policy otherwise says "Right to rejoin route" one second after "Sharp left now". Persistent errors are still corrected once the grace ends.
- **Amendment (same run):** `RouteGeometry.headingTieBandM = 3` — when several segments lie within 3 m of the closest one (a vertex, or GPS noise at a corner), the heading error is measured against the segment whose bearing agrees with the walker's heading. This was the actual cause of the spurious cue; the grace above is belt-and-braces. After both, the live FIU route plays exactly prepare/now ×3 and Arrived.

### F4. Phrasing (H-12b) and haptics (H-12a)
- Direction first, ≤ 6 words, digits rounded: `"Left in 30 metres"`, `"Left now"`, `"Right in 30 metres"`, `"Slight right now"`, `"Straight on"`, `"Turn around"`, `"Off route, rerouting"`, `"Arrived"`, `"GPS lost, navigation paused"`, `"GPS back"`. A test asserts every phrase is ≤ 6 words and begins with the direction word when there is one. Distances round to the nearest 10 m.
- `TurnCue.priority` is a fixed `.navigation`, documented as **below hazard urgency 1**; the integration drops or delays navigation speech while a hazard announcement is presenting or within its speech gap. Step 12 ships the value and the doc comment, not the arbitration.
- `TurnCue.hapticHint: HapticHint { leftTurn, rightTurn, ahead, none }` is a hint only; the step-10 owner maps it under the H-12a constraints (≤ 420 ms, intensity ≤ 0.85, no continuous element > 400 ms, distinct accent from the hazard `.left/.right`). Write H-12a into a doc comment on `HapticHint`.

### F5. Engine loop
`NavigationEngine` (actor) owns: resolver, routes client, location + heading sources, clock. `start` resolves the destination, fetches the route (origin = latest fix; if no fix within 10 s → `NavigationError.noFix` and status `.failed`), then loops: on each location fix (and using the latest heading sample, which may be nil ⇒ headingError nil ⇒ no heading cues), advance the follower, run the policy, yield the cue. Reroute = fetch a new route from the current fix, at most once per 15 s. `stop()` finishes the stream. Errors never crash the actor: they become `NavigationStatus.failed(NavigationError)` plus a `.rerouting`/`.gpsLost`-style cue where a spoken outcome is appropriate. The engine never imports or references anything from CaneContracts/CaneFeedback (structural: the target has no such dependency).

### F6. Tests (`ios/CaneKit/Tests/CaneNavigationTests/`, TDD — RED then GREEN, evidence in the report)
- `PolylineTests`: Google's documented example decodes to `(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)`; encode(decode(x)) == x; negative and zero deltas; empty string → []; a truncated string throws.
- `GeoTests`: 1° of latitude ≈ 111 195 m (±50 m); bearings 0/90/180/270 for N/E/S/W; cross-track sign and magnitude for a point 10 m left of a segment; normalisation of heading error into (−180, 180].
- `RoutesAPITests` (fake transport): request JSON has `travelMode: "WALK"`, origin latLng, destination address or latLng, NO `routingPreference`; headers contain `X-Goog-Api-Key` and the exact field mask; decoding `Fixtures/routes_two_turns.json` (a two-turn walking route the implementer authors from the section H shape: ~250 m, steps DEPART → TURN_LEFT → TURN_RIGHT → arrive) yields 4 steps with the expected maneuvers and decoded points; a 403 `google.rpc.Status` body → `.routesAPI(403, message)`; a body with an unknown maneuver string decodes as `.unknown` and does not throw; missing key → `.missingAPIKey` with zero transport calls.
- `RouteFollowerTests`: simulate a walk along the fixture at 1.4 m/s, 1 Hz, exact on-route points → currentStepIndex advances exactly at each step end (never skips, never regresses); distanceToNextManeuverM strictly decreases within a step; heading aligned with the segment ⇒ |headingError| < 1°; a point 30 m off the path ⇒ offRouteM ≈ 30.
- `TurnCuePolicyTests`: the simulated two-turn walk with correct heading yields exactly `[prepare(left,30), now(left), prepare(right,30), now(right), arrived]` in order — the runbook acceptance; a 60° heading error while walking yields one `.correctHeading` then none for 6 s; standing still (speed 0.2) with 60° error yields none; no cue of any kind while GPS is lost, then `.gpsRestored`; off-route for 5 s yields `.offRoute` once.
- `NavigationEngineTests` (fake resolver, fake transport, fake sources, instant clock): `start(to: "library")` → the same cue sequence as above through the stream; sources that stop producing fixes → `.gpsLost` once and the actor stays alive; a transport error → status `.failed` and the stream finishes; `stop()` finishes the stream.
- `PhrasingTests`: every `CueKind × TurnDirection` phrase ≤ 6 words and direction-first.

### F7. Live verification (team task, documented in the report and in `ios/CaneKit/Sources/CaneNavigation/README.md`, ≤ 30 lines)
`GOOGLE_MAPS_API_KEY=… swift run nav-cli "<origin address>" "<destination>"` (executable target `nav-cli`, added by the controller) resolves both, fetches the walking route, prints the steps, and replays a simulated walk printing the cue sequence. Enable `routes.googleapis.com` and `places.googleapis.com` (or Geocoding) on the key. Field mask errors surface as HTTP 400 with the offending field name.

### F8. Out of scope
Haptic pattern design (10), voice intent (11), the map view, last-50-feet (17), any awareness-path coupling.

## G. Global constraints (verbatim)
- "CoreLocation feeds navigation ONLY and never the awareness path, where bearings stay torso-relative."
- "Acceptance: a two-turn walking route plays correct turn cues on the demo route; losing GPS degrades navigation without affecting hazard warnings"
- Commit format `<type>: <description>`; no attribution trailers. Immutable value types; small files; explicit error handling; constants not magic numbers; no hardcoded secrets.

## H. API reference (docs-lookup, 2026-09-26)

Verified 2026-09-26 against live fetches of developers.google.com and developer.apple.com (Context7 + WebFetch). Items marked UNVERIFIED could not be confirmed and must not be relied on.

### H1. Routes API v2 — `computeRoutes` (WALK)
- **POST** `https://routes.googleapis.com/directions/v2:computeRoutes`
- Headers: `Content-Type: application/json`, `X-Goog-Api-Key: <KEY>`, and **`X-Goog-FieldMask` (REQUIRED — omitting or mistyping it returns HTTP 400 INVALID_ARGUMENT)**.
- Field mask to use (one header value): `routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,routes.legs.steps.navigationInstruction,routes.legs.steps.polyline,routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration,routes.legs.steps.startLocation,routes.legs.steps.endLocation,routes.legs.steps.travelMode`
- Request body (origin latLng, destination address or latLng — both are valid Waypoint forms):
```json
{
  "origin": { "location": { "latLng": { "latitude": 25.7617, "longitude": -80.1918 } } },
  "destination": { "address": "1200 Library St, Miami, FL" },
  "travelMode": "WALK",
  "polylineQuality": "HIGH_QUALITY",
  "polylineEncoding": "ENCODED_POLYLINE",
  "languageCode": "en-US",
  "units": "METRIC",
  "computeAlternativeRoutes": false
}
```
- **Do not send for WALK:** `routingPreference`, `trafficModel`, `transitPreferences`, `arrivalTime`; `departureTime` is meaningless for WALK.
- Response (trimmed):
```json
{ "routes": [{
    "distanceMeters": 842, "duration": "631s",
    "polyline": { "encodedPolyline": "a~l~Fjk~uOwHJy@P" },
    "legs": [{ "steps": [{
        "distanceMeters": 42, "staticDuration": "30s",
        "polyline": { "encodedPolyline": "_p~iF~ps|U" },
        "startLocation": { "latLng": { "latitude": 25.7617, "longitude": -80.1918 } },
        "endLocation":   { "latLng": { "latitude": 25.7620, "longitude": -80.1915 } },
        "navigationInstruction": { "maneuver": "TURN_LEFT", "instructions": "Turn left onto Library St" },
        "travelMode": "WALK" }] }] }] }
```
- `duration` / `staticDuration` are strings like `"631s"` — strip the trailing `s` and parse seconds (NOT ISO-8601).
- **Maneuver enum, complete (21):** `MANEUVER_UNSPECIFIED, TURN_SLIGHT_LEFT, TURN_SHARP_LEFT, UTURN_LEFT, TURN_LEFT, TURN_SLIGHT_RIGHT, TURN_SHARP_RIGHT, UTURN_RIGHT, TURN_RIGHT, STRAIGHT, RAMP_LEFT, RAMP_RIGHT, MERGE, FORK_LEFT, FORK_RIGHT, FERRY, FERRY_TRAIN, ROUNDABOUT_LEFT, ROUNDABOUT_RIGHT, DEPART, NAME_CHANGE`. Decode unknown strings without throwing.
- Error shape (`google.rpc.Status`): `{ "error": { "code": 400, "message": "Request contains an invalid argument.", "status": "INVALID_ARGUMENT" } }`. Bad field mask → 400 INVALID_ARGUMENT (verified); bad/missing key → 403 PERMISSION_DENIED (standard behaviour, not re-verified on this page).

### H2. Encoded polyline decode (precision 1e5, verified against the Google utilities page)
Values are deltas from the previous point; lat and lng are two alternating streams (first point is a delta from 0). Per number:
1. For each char: `value = ord(char) − 63`; low 5 bits go into the result: `result |= (value & 0x1F) << (5 × chunkIndex)`; continue while `value & 0x20` is set.
2. When the continuation bit is clear, `result` is the zig-zag integer: if `result & 1 == 1` then `delta = ~(result >> 1)` else `delta = result >> 1`.
3. `currentLat += delta` (lat pass), then the same for lng; emit `(currentLat / 1e5, currentLng / 1e5)`; repeat.
Google's documented example `_p~iF~ps|U_ulLnnqC_mqNvxq`@` decodes to `(38.5, −120.2), (40.7, −120.95), (43.252, −126.453)`.

### H3. Destination lookup
- **Places API (New) Text Search — preferred for free text like "the library":** **POST** `https://places.googleapis.com/v1/places:searchText`, headers `X-Goog-Api-Key`, `X-Goog-FieldMask: places.location,places.displayName,places.formattedAddress` (required). Body `{ "textQuery": "library", "locationBias": { "circle": { "center": { "latitude": …, "longitude": … }, "radius": 2000.0 } } }`. Response `{ "places": [{ "location": { "latitude": 25.761, "longitude": -80.191 }, "displayName": { "text": "Main Library", "languageCode": "en" }, "formattedAddress": "…" }] }`.
- **Geocoding API (fallback for structured addresses):** **GET** `https://maps.googleapis.com/maps/api/geocode/json?address={q}&key={KEY}&region={cc}`. Response `{ "status": "OK", "results": [{ "formatted_address": "…", "geometry": { "location": { "lat": 25.7617, "lng": -80.1918 } } }] }`. `status` ∈ `OK, ZERO_RESULTS, OVER_DAILY_LIMIT, OVER_QUERY_LIMIT, REQUEST_DENIED, INVALID_REQUEST, UNKNOWN_ERROR`; `ZERO_RESULTS` arrives as HTTP 200 — check `status`, not the HTTP code. Geocoding often returns ZERO_RESULTS for POI phrases, which is why Places wins for voice destinations.

### H4. CoreLocation heading (iOS)
- `CLLocationManager.startUpdatingHeading()` after checking `CLLocationManager.headingAvailable()`; delivery is delegate-only (`locationManager(_:didUpdateHeading:)`) — iOS 17's `CLLocationUpdate.liveUpdates()` exposes **locations only, no heading**, so wrap the delegate in your own `AsyncStream`.
- `CLHeading.trueHeading`: Apple guarantees only that **a negative value means undetermined** — test `< 0`, not `== −1`. True heading needs location updates running too (declination comes from position), so call `startUpdatingLocation()` as well.
- `headingFilter` in degrees (default 1); `headingOrientation` is deprecated per Apple's doc.
- `desiredAccuracy`: `kCLLocationAccuracyBest` (or `BestForNavigation` while actively navigating; higher power) — standard guidance, UNVERIFIED against a specific doc string.
- Info.plist: `NSLocationWhenInUseUsageDescription` (foreground). `CLLocation.course`: negative = invalid; the ~0.5 m/s unreliability threshold is practical knowledge, UNVERIFIED in Apple's text — gate course use on `speed` and `courseAccuracy`.

### H5. Status 2025–2026
Routes API v2 is current/GA; the legacy Directions API carries a "Legacy" banner — do not use it. Enable `routes.googleapis.com` and `places.googleapis.com` on the key (Geocoding's console service id is UNVERIFIED; enable "Geocoding API" by display name).

### H6. Sources
developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes · …/routes (overview) · …/utilities/polylinealgorithm · …/geocoding/requests-geocoding · …/places/web-service/text-search · …/directions/get-api-key (legacy banner) · developer.apple.com/documentation/corelocation/cllocationmanager/startupdatingheading() · …/clheading/trueheading · …/cllocation/course · …/cllocationupdate.
