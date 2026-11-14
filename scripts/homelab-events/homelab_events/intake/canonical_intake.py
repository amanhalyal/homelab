"""Canonical producer intake: gate, redact, persist, or locally spool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from homelab_events.store.ledger import ControlPlaneError, IngestResult, Ledger, redact_event, validate_event
from homelab_events.policy.notify_gate import GateDecision, PolicyConfig, evaluate_event
from homelab_events.producers.spool import EventSpool


@dataclass(frozen=True)
class CanonicalIntakeResult:
    event: dict[str, Any]
    decision: GateDecision
    ingest: IngestResult | None
    spooled: bool


def _gated_event(
    event: Mapping[str, Any],
    decision: GateDecision,
) -> dict[str, Any]:
    safe = redact_event(event)
    safe["page_policy"] = decision.action
    safe["policy_decision"] = {
        "action": decision.action,
        "reason": decision.reason,
        "topic": decision.topic,
    }
    validate_event(safe)
    return safe


def gate_event(
    event: Mapping[str, Any],
    policy: PolicyConfig,
    context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], GateDecision]:
    """Return a redacted event with its deterministic decision attached."""

    validate_event(event)
    decision = evaluate_event(event, context, policy=policy)
    return _gated_event(event, decision), decision


def ingest_event(
    event: Mapping[str, Any],
    ledger: Ledger,
    spool: EventSpool,
    policy: PolicyConfig,
    context: Mapping[str, Any] | None = None,
) -> CanonicalIntakeResult:
    """Apply policy before ledger intent creation; spool only store failures."""

    safe_event, decision = gate_event(event, policy, context)
    try:
        result = ledger.ingest(safe_event)
    except ControlPlaneError:
        spool.append(safe_event)
        return CanonicalIntakeResult(
            event=safe_event,
            decision=decision,
            ingest=None,
            spooled=True,
        )
    return CanonicalIntakeResult(
        event=safe_event,
        decision=decision,
        ingest=result,
        spooled=False,
    )
