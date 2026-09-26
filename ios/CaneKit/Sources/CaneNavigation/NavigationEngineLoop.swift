import Foundation

/// `NavigationEngine`'s per-fix loop: ingest, follow, evaluate, speak,
/// and the 1 Hz watchdog that notices GPS going quiet. Split from the
/// actor's lifecycle half (`NavigationEngine.swift`) to keep both files
/// small; mirrors `CaneTransport`'s `EventLinkClientRunLoop.swift`.
extension NavigationEngine {
    // MARK: - The loop

    func ingest(_ fix: LocationFix) async {
        latestFix = fix
        if let waiter = firstFixWaiter {
            firstFixWaiter = nil
            waiter.resume(returning: fix)
        }
        guard isNavigating else { return pendingFixes.append(fix) }
        await handle(fix)
    }

    func ingest(_ heading: HeadingSample) {
        latestHeading = heading
    }

    /// Replays fixes that arrived while the route was being fetched,
    /// in order, then lets later ones through directly.
    func drainPendingFixes() async {
        while !pendingFixes.isEmpty {
            let batch = pendingFixes
            pendingFixes = []
            for fix in batch { await handle(fix) }
        }
        isNavigating = true
    }

    func handle(_ fix: LocationFix) async {
        guard let follower else { return }
        let state = follower.advance(
            from: followerState ?? follower.initialState, fix: fix, heading: latestHeading
        )
        followerState = state
        let (next, cue) = TurnCuePolicy.evaluate(
            state: policyState, follower: state, nowMs: fix.timestampMs
        )
        policyState = next
        await deliver(cue, at: fix)
    }

    /// The watchdog: the only thing that runs without a fix, and the
    /// only reason the engine needs a clock at all.
    func startWatchdog() {
        guard watchdogTask == nil else { return }
        let clock = self.clock
        watchdogTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await clock.sleep(forMilliseconds: NavigationEngine.tickIntervalMs)
                guard !Task.isCancelled, let self else { return }
                await self.tick()
            }
        }
    }

    func tick() async {
        guard case .navigating = status, let state = followerState else { return }
        let now = await clock.nowMs()
        let (next, cue) = TurnCuePolicy.evaluate(state: policyState, follower: state, nowMs: now)
        policyState = next
        await deliver(cue, at: latestFix)
    }

    func deliver(_ cue: TurnCue?, at fix: LocationFix?) async {
        guard let cue else { return }
        cueContinuation.yield(cue)
        switch cue.kind {
        case .arrived:
            status = .arrived
            shutdown()
        case .offRoute:
            if let fix { await reroute(from: fix) }
        default:
            break
        }
    }

    // MARK: - Rerouting

    func reroute(from fix: LocationFix) async {
        guard let destination else { return }
        if let last = lastRerouteMs,
            Double(fix.timestampMs - last) / 1_000 < NavigationEngine.rerouteMinIntervalS
        {
            return
        }
        lastRerouteMs = fix.timestampMs

        do {
            let route = try await routes.computeWalkingRoute(
                origin: fix.latLng, destination: .latLng(destination)
            )
            adopt(route)
            policyState = TurnCuePolicy.State()
            cueContinuation.yield(TurnCue(kind: .rerouting, direction: .straight, distanceM: nil))
        } catch {
            // A failed reroute must not silence navigation: keep the
            // old route and let the walker be told again after another
            // off-route hold.
            policyState.offRouteAnnounced = false
            policyState.offRouteSinceMs = nil
        }
    }
}
