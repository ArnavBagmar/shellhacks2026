# CaneNavigation

Turn-by-turn walking navigation (runbook step 12): Routes API v2
walking route, Places Text Search (Geocoding fallback) for spoken
destinations, polyline following at ~1 Hz, and `TurnCue` values --
"Left in 30 metres", "Left now", "Arrived" -- each with a side-only
haptic hint. Step 10 renders cues; this module never builds a
`HapticScript` and never speaks. (Beside the target, not in it:
SwiftPM warns about Markdown inside a target directory.)

## The boundary rule

CoreLocation feeds navigation **only**. This target has no dependency
on `CaneContracts`/`CaneTransport`/`CaneFeedback`, so a fix or heading
cannot reach the awareness path, where bearings stay torso-relative
(`CLLocation.course` is unreliable below ~0.5 m/s -- at a curb). Keep
it dependency-free. Cues carry `priority == .navigation`, which the
feedback manager must treat as **below hazard urgency 1**.

## Live check (needs a key; the test suite never uses one)

```sh
GOOGLE_MAPS_API_KEY=... swift run nav-cli "25.76170,-80.19180" "the library"
```

Prints the route's steps, then replays a simulated 1.4 m/s walk and
prints the cues; either argument may be an address or `lat,lng`.
No key: `missingAPIKey`, exit 2, zero network calls. Enable **Routes**
and **Places (New)** APIs on the key (or Geocoding for `GeocodingResolver`); a bad field mask is an HTTP 400. On device the
app needs `NSLocationWhenInUseUsageDescription`.
