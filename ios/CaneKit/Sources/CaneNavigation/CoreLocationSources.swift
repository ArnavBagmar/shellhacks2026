#if os(iOS)

    import CoreLocation
    import Foundation

    /// `CLLocationManager` behind the two source protocols: delegate
    /// callbacks in, `AsyncStream`s out (brief H4 -- iOS 17's
    /// `CLLocationUpdate.liveUpdates()` carries locations only, with no
    /// heading, so the delegate is still the only way to get one).
    ///
    /// **Boundary (build-plan F7).** This is the single place
    /// CoreLocation enters the app, and it lives in a target that
    /// cannot see `CaneContracts`, so GPS and magnetometer data feed
    /// navigation only. The awareness path's bearings stay
    /// torso-relative, precisely because `CLLocation.course` is
    /// unreliable below walking speed.
    ///
    /// The app must declare **`NSLocationWhenInUseUsageDescription`** in
    /// its Info.plist, and call `requestAuthorization()` before
    /// navigating; without it the streams stay empty and the engine
    /// reports `NavigationError.noFix`.
    ///
    /// **One per route.** `stop()` finishes both streams for good, so a
    /// reused instance hands the next route a dead stream: no fix ever
    /// arrives and the route fails as `.noFix` (the app did exactly that
    /// until 2026-09-27).
    ///
    /// `@unchecked Sendable`: the only non-`Sendable` stored property is
    /// `CLLocationManager`, which is created on the main actor (see
    /// `init`) and touched nowhere else -- `start`/`stop` hop to
    /// `@MainActor`, and the delegate callbacks are delivered on the
    /// queue the manager was created on. The stream continuations are
    /// `Sendable` and thread-safe by contract.
    public final class CoreLocationSources: NSObject, @unchecked Sendable {
        /// Brief H4/F1: best accuracy, a metre of movement, three
        /// degrees of turn.
        public static let distanceFilterM: CLLocationDistance = 1
        public static let headingFilterDeg: CLLocationDegrees = 3

        private let manager: CLLocationManager
        private let locationStream: AsyncStream<LocationFix>
        private let locationContinuation: AsyncStream<LocationFix>.Continuation
        private let headingStream: AsyncStream<HeadingSample>
        private let headingContinuation: AsyncStream<HeadingSample>.Continuation

        /// Must be created on the main actor: `CLLocationManager`
        /// delivers its delegate callbacks on the queue it was created
        /// on, and that queue needs a live run loop.
        @MainActor
        public override init() {
            manager = CLLocationManager()
            (locationStream, locationContinuation) = AsyncStream.makeStream(
                bufferingPolicy: .bufferingNewest(8)
            )
            (headingStream, headingContinuation) = AsyncStream.makeStream(
                bufferingPolicy: .bufferingNewest(4)
            )
            super.init()
            manager.delegate = self
            manager.desiredAccuracy = kCLLocationAccuracyBest
            manager.distanceFilter = CoreLocationSources.distanceFilterM
            manager.headingFilter = CoreLocationSources.headingFilterDeg
        }

        /// Foreground authorisation. Call before `start`.
        @MainActor
        public func requestAuthorization() {
            manager.requestWhenInUseAuthorization()
        }
    }

    extension CoreLocationSources: LocationSource {
        public func locations() -> AsyncStream<LocationFix> {
            Task { @MainActor in manager.startUpdatingLocation() }
            return locationStream
        }

        public func stop() {
            Task { @MainActor in
                manager.stopUpdatingLocation()
                manager.stopUpdatingHeading()
            }
            locationContinuation.finish()
            headingContinuation.finish()
        }
    }

    extension CoreLocationSources: HeadingSource {
        /// True heading needs position too (declination comes from
        /// where you are), so location updates start alongside.
        public func headings() -> AsyncStream<HeadingSample> {
            Task { @MainActor in
                guard CLLocationManager.headingAvailable() else { return }
                manager.startUpdatingLocation()
                manager.startUpdatingHeading()
            }
            return headingStream
        }
    }

    extension CoreLocationSources: CLLocationManagerDelegate {
        public func locationManager(
            _ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]
        ) {
            for location in locations {
                locationContinuation.yield(CoreLocationSources.fix(from: location))
            }
        }

        /// Brief H4: Apple guarantees only that a **negative**
        /// `trueHeading` means undetermined -- test `< 0`, never
        /// `== -1`. Undetermined samples are dropped, which the policy
        /// reads as "no heading" and answers with no heading cues.
        public func locationManager(
            _ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading
        ) {
            guard newHeading.trueHeading >= 0 else { return }
            headingContinuation.yield(
                HeadingSample(
                    trueHeadingDeg: newHeading.trueHeading,
                    accuracyDeg: newHeading.headingAccuracy,
                    timestampMs: CoreLocationSources.milliseconds(newHeading.timestamp)
                )
            )
        }

        /// A failure is not fatal: the streams simply go quiet and the
        /// engine's watchdog speaks "GPS lost, navigation paused".
        public func locationManager(
            _ manager: CLLocationManager, didFailWithError error: any Error
        ) {}

        static func fix(from location: CLLocation) -> LocationFix {
            LocationFix(
                latLng: LatLng(
                    latitude: location.coordinate.latitude, longitude: location.coordinate.longitude
                ),
                horizontalAccuracyM: location.horizontalAccuracy,
                speedMps: location.speed >= 0 ? location.speed : nil,
                timestampMs: milliseconds(location.timestamp)
            )
        }

        static func milliseconds(_ date: Date) -> Int64 {
            Int64(date.timeIntervalSince1970 * 1_000)
        }
    }

#endif
