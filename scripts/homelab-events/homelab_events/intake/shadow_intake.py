"""Offline composition of Alertmanager normalization and ledger/spool intake."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from homelab_events.intake.alertmanager_adapter import adapt_alertmanager_payload
from homelab_events.intake.canonical_intake import ingest_event
from homelab_events.store.ledger import ControlPlaneError, Ledger
from homelab_events.policy.notify_gate import PolicyConfig
from homelab_events.producers.spool import EventSpool


@dataclass(frozen=True)
class IntakeResult:
    events: int
    accepted: int
    spooled: int
    duplicates: int


def ingest_alertmanager(
    payload: Mapping[str, Any],
    ledger: Ledger,
    spool: EventSpool,
    *,
    policy: PolicyConfig | None = None,
    context: Mapping[str, Any] | None = None,
) -> IntakeResult:
    """Normalize and persist a webhook, spooling only store failures."""

    events = adapt_alertmanager_payload(payload)
    accepted = spooled = duplicates = 0
    for event in events:
        if policy is not None:
            result = ingest_event(event, ledger, spool, policy, context)
            if result.spooled:
                spooled += 1
            else:
                accepted += 1
                if result.ingest is not None and result.ingest.duplicate:
                    duplicates += 1
            continue
        try:
            result = ledger.ingest(event)
        except ControlPlaneError:
            spool.append(event)
            spooled += 1
            continue
        accepted += 1
        if result.duplicate:
            duplicates += 1
    return IntakeResult(
        events=len(events),
        accepted=accepted,
        spooled=spooled,
        duplicates=duplicates,
    )
