# 0014. Prometheus rules replace a crash-loop watcher

Date: 2026-08-11 · Status: accepted

## Context

A third-party tool, homebutler, ran a `watch` daemon and a notification shim on Legion to detect container crash loops. Looking into its backup-drill feature showed that its `backup`, `doctor` and `install` commands were never wired into anything. No cron job or timer used them, and its nine archives came from one manual test session. The only part in real use was `watch`, so the question narrowed from "replace a management suite" to "replace one crash-loop daemon".

The tool is small, young and single-maintainer. A real upstream bug had already been found and fixed by hand: `watch start` never loaded its config, which silently disabled all notifications.

## Decision

Detect container problems with two Prometheus alert rules in the `docker-containers` group, built on a metric that was already being collected:

- **`DockerContainerDown`:** a container has been in a non-running state for 5 or more minutes. Containers that are intentionally stopped for days, such as the game server, are excluded. It originally matched only `exited`, and was later widened to `exited|created|dead` after containers that failed to be created went unnoticed. See [0022](0022-citadel-unreachable-alert.md).
- **`DockerContainerFlapping`:** the running state changed four or more times in 15 minutes, roughly two restart cycles. This is the crash-loop signal.

Both use `netdata_docker_container_state_state_average`, which Netdata's tuned Docker module already exported and Prometheus already scraped from both machines. The alerts reach the phone through the existing Alertmanager and ntfy path. Net new containers: zero. Net removed: one. The tool itself was then removed entirely.

Third-party backup tools were evaluated and found more mature, but none does crash-loop detection. The stack already had everything needed, and two rules in an existing file add less surface than any new binary, container or maintainer to trust.

## Trade-off, stated plainly

`watch` was event-driven through `docker events` and could not miss a restart. The Prometheus rules are bound to the 30 second scrape interval, so a container that dies and recovers faster than one scrape could be missed. This was judged acceptable. The live test's restarts were caught cleanly, and the old tool's own incident log tagged many of its events as low-confidence clean restarts, so it was not a precise signal to begin with.

## Evidence

A container was stopped and started twice to create a real flap. `DockerContainerFlapping` went from pending to firing, Alertmanager routed it to the ntfy receiver, and ntfy's stats showed one new message in the same second. It was a live restart, a live alert and a live notification, not a read of the rule file.

## Consequences

- Prefer extending a monitoring stack that is already proven to adding a parallel tool.
- A claim in the old known-issues list called the tool "my own". That was wrong, and the open item was closed instead of left to mislead.
