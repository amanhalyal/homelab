# 0011. Wallboard reliability: native service and two rendering races

Date: 2026-07-30 to 2026-09-05 · Status: accepted

The wallboard is a kiosk browser on Citadel showing a rotating set of pages, including a clock and a Grafana dashboard, on an external monitor called the Beacon. This record covers three problems with it and what each taught.

## 1. A background container starved the display

**Symptom.** Shortly after Karakeep, a bookmarking app, and its helpers were moved onto Citadel, the wallboard clock stuttered, with unevenly spaced seconds.

**First guess.** The clock page used `setInterval`, which does not correct for delay, so ticks drift off the second boundary. That was a real bug and was fixed with a self-correcting `setTimeout` loop. It did not solve the problem.

**Root cause.** Load average reached 5.9 on four threads, caused by Karakeep's Next.js server and headless Chrome during indexing and link previews. On a small machine that also renders the kiosk, a CPU burst from a container starves the browser's render thread, and a precise timer cannot help if its thread never runs.

**Decision.**
- Cap the three containers at 0.75, 1.0 and 0.5 cores. That leaves at least 1.75 of 4 threads free whatever the app is doing. The limits were checked at the cgroup level with `docker inspect`, not assumed from the Compose file.
- Move the wallboard HTTP server from Docker back to a native systemd unit. Its containerization had a thin justification, it feeds the display that contention had just disrupted, and it uses only the standard library. This partly reverses [0009](0009-docker-on-citadel.md).

The app has since moved to Legion, but the lesson stands: a display-critical process should not share a small CPU with bursty work.

**A repeat gotcha.** The health-check script judged the wallboard server by `docker inspect`. Once the container was removed it reported the service as permanently down, and a real alert fired through Alertmanager to ntfy before it was caught. The check was switched to `systemctl is-active` and run by hand. The script also exists in two places, the tracked copy and the deployed one, so both had to be updated. Removing a service means auditing everything that checks it.

## 2. The page-reveal race

**Symptoms.** A scheduled switch to the clock page appeared to do nothing, and manual page changes flashed the new page and then reverted.

**Cause.** The wallboard uses two iframes that cross-fade: the next page loads into a hidden buffer, announces itself with a `wallboard-ready` message, and is swapped to the front. Two bugs let a stale `reveal()` run a second time and bury the new page.

- The Grafana page nests its own iframe, which fires `load` on every auto-refresh, about every 30 seconds. A late message from it could arrive after a switch, when Grafana sat in the hidden buffer, and the check that the message came from the hidden buffer passed.
- The clock page posts `wallboard-ready` synchronously, before the parent's `load` handler runs. The parent then started a 2.5 second fallback timer after the reveal had already happened, and that orphaned timer fired later and reverted the display.

An earlier fix, clearing the hidden iframe's `src`, made things worse: an empty string resolves to the page's own URL in Firefox, which loaded a nested copy of the wallboard.

**Fix.** One `awaitingReveal` flag in the orchestrating page. It is set when a page is loaded into the hidden buffer and cleared as the first action of `reveal()`, so any later call returns immediately. No individual page needed changing.

## 3. The initial-load race

After a kiosk restart the Beacon could stay black while Firefox, the server, Grafana and the HDMI output were all healthy. At startup the visible buffer had no source, and the first page loaded into the hidden one, so a slow nested Grafana could lose the race with the buffer bookkeeping.

**Fix.** Load the first page directly into the already visible buffer and keep the cross-fade for later changes. A recovery step is also documented: write another page name into the wallboard's state file, wait, and write `grafana` back to force a normal transition.

## Consequences

- Race conditions in presentation state look like data or hardware failures. Check whether the services are healthy before blaming them.
- A fix that is correct but not the root cause is still worth keeping, as with the clock timer, but it should not close the investigation.
