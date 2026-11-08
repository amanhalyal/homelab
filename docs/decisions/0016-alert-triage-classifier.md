# 0016. Alert triage with a local classifier: debounce, pre-digest, on demand

Date: 2026-08-11 to 2026-10-28 · Status: accepted

## Context

Alerts pass through a tiered filter. Tier 1 is regex rules for known noise and known criticals. Tier 2 is a small local LLM classifier, called Cerberus and initially backed by Qwen3.5-4B, which decides whether an ambiguous alert needs attention. Alerts that pass are forwarded for investigation.

A proposed design wanted two additions: buffer problem alerts for 5 to 10 minutes and cancel the pair if a resolution arrived, and have the classifier write a short brief before forwarding.

## Review of the proposal

Checking the proposal against the real pipeline found three problems.

1. **Debounce was already done one layer up.** `DockerContainerDown` has `for: 5m`, with a note that a Watchtower update should resolve within a minute or two. Stacking an 8 minute debounce would delay a real container-down from 5 to 13 minutes for no extra noise reduction. Uptime Kuma alerts have no equivalent grace period, so that is where a debounce helps.
2. **The cancelling half is on the wrong branch.** The "Up" and "RESOLVED" messages are tier-1 drop patterns that classify as ignore. And the proposal's own example, a Kuma "Down", is a tier-1 keep, which skips the classifier. As written, the design would have missed its own motivating case.
3. **The brief should not depend on debounce.** Attaching it only on escalation would leave Alertmanager alerts, which are never debounced, without one, losing the main benefit.

## Decision

- **Debounce for Kuma only,** keyed by monitor name. Alertmanager alerts forward immediately, since their `for:` durations are tuned per alert. A Kuma "Up" is intercepted before normal ignore handling and cancels the pending "Down". A duplicate "Down" does not reset the timer, so a flapping monitor that is really down still escalates. Pending state persists to a file so a restart does not drop a buffered alert. The default is 8 minutes, overridable by an environment variable.
- **Pre-digest on every forwarded alert,** whether or not it was debounced. The classifier turns raw text and, when it can confidently match a container, the last 15 log lines into a brief of about 200 tokens. A false positive was caught in testing before shipping: the string `[Uptime-Kuma]` appears in every Kuma alert, so monitoring containers are denylisted from log matching.
- **Dry-run first, and it must actually run.** The first version updated the pending buffer only in live mode, so dry-run could never exercise it, which defeated the point. The buffer is now always updated and logged, and only the "skip forwarding" behavior is gated, so a dry run shows what would happen without changing anything.
- **Fail open.** If the model is slow, down or times out, the raw alert is forwarded and never dropped.

## Running the model on demand

The log showed about 71 classifier requests in seven days, around 10 a day, while the model held about 3 GiB of a 4 GiB GPU. A measured cold start took about 32 seconds, which the path tolerates, since Kuma retries, Prometheus `for:` windows and fail-open already delay escalation.

So the model is no longer resident. The alert shim starts the service only when a classification or brief needs it, waits up to 45 seconds for its health endpoint, and stops it after 15 minutes idle. The service is disabled at boot, and the shim is the only thing that starts it. While it existed, the nightly job from [0015](0015-llama-gpu-offload.md) stopped it and did not restart it.

## Model change

In October Qwen3.5-4B was replaced by the official Nemotron 3.5 Bolt E4B Q4_K_M checkpoint after a local comparison. Nemotron passed 26 of 26 classification cases and 3 of 3 tool and runbook checks. These are small synthetic tests and not a broad quality guarantee. LFM3, which offers a larger usable context and lower memory use, was kept downloaded as a tested alternative but not selected. Quality was preferred over those advantages. Notes:

- It runs with a 32K context, one slot, a quantized key/value cache, and reasoning disabled with both flags set to keep short JSON replies inside the token budgets. All consumers use a stable API alias, so a model swap does not change callers.
- The container image is pinned by digest. A named Docker volume preserves the CUDA compilation cache. Without it, first start took about 62 seconds. With it, an on-demand restart was ready in about 3 seconds and the first brief took 7.9 seconds.
- Larger context does not enable long histories automatically. They need an appropriate timeout first.

## Consequences

- Real end-to-end validation of the debounce against a live Kuma down and up was still pending when this was written. Check the dry-run log before enabling it.
- Review a design against the actual system before building it. The most useful work here was finding that half of the proposal was already solved.
