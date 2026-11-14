"""Deterministic notification policy decisions without transport side effects."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast


@dataclass(frozen=True)
class PolicyConfig:
    critical_allowlist: frozenset[str]
    critical_topic: str
    digest_topic: str
    backup_known_good_age_hours: float
    backup_consecutive_failures: int
    core_services: frozenset[str]
    core_probe_grace_minutes: float
    core_probe_failures: int
    never_page: frozenset[str]
    production_enabled: bool


@dataclass(frozen=True)
class GateDecision:
    action: str
    topic: str | None
    reason: str


_BACKUP_TERMS = ("backup", "mirror", "checksum", "integrity")
_ACTIVE_STATUSES = {"firing", "failure"}
_APPROVED_POLICY_STATUSES = frozenset(
    {"approved_for_shadow_design", "approved_for_production_canary"}
)


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _yaml_subset(text: str) -> dict[str, Any]:
    """Parse the small maps/lists/scalars used by the checked-in policy.

    This intentionally is not a general YAML implementation. It supports the
    policy's indentation-based maps, scalar lists, and one-key map list items,
    keeping the runtime independent of PyYAML.
    """

    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    raw_lines = text.splitlines()
    meaningful: list[tuple[int, str]] = []
    for raw in raw_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        meaningful.append((indent, content))

    for index, (indent, content) in enumerate(meaningful):
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("invalid policy indentation")
        parent = stack[-1][1]

        if content.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError("policy list item has no list parent")
            item = content[2:].strip()
            if ":" in item:
                key, value = item.split(":", 1)
                item_map: dict[str, Any] = {key.strip(): _scalar(value)}
                parent.append(item_map)
                stack.append((indent, item_map))
            else:
                parent.append(_scalar(item))
            continue

        if ":" not in content or not isinstance(parent, dict):
            raise ValueError("invalid policy mapping")
        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            parent[key] = _scalar(value)
            continue

        next_item = None
        for next_indent, next_content in meaningful[index + 1:]:
            if next_indent <= indent:
                break
            next_item = next_content
            break
        nested: Any = [] if next_item and next_item.startswith("- ") else {}
        parent[key] = nested
        stack.append((indent, nested))
    return root


def _duration(value: Any, suffix: str) -> float:
    text = str(value).strip().lower()
    if not text.endswith(suffix):
        raise ValueError(f"expected duration ending in {suffix}: {value!r}")
    return float(text[: -len(suffix)])


def _page_when_value(page_when: Any, key: str, default: Any) -> Any:
    if not isinstance(page_when, list):
        return default
    for item in page_when:
        if isinstance(item, Mapping) and key in item:
            return item[key]
    return default


def load_policy(path: str | Path) -> PolicyConfig:
    """Load and validate the approved policy without importing third parties."""

    data = _yaml_subset(Path(path).read_text(encoding="utf-8"))
    critical = data.get("critical")
    escalation = data.get("escalation")
    backup = escalation.get("backup_and_mirror") if isinstance(escalation, Mapping) else None
    core = (
        escalation.get("designated_core_services")
        if isinstance(escalation, Mapping)
        else None
    )
    warning = data.get("warning_and_routine")
    digest = data.get("digest")
    if not isinstance(critical, Mapping) or not isinstance(escalation, Mapping):
        raise ValueError("policy missing critical/escalation sections")
    if not isinstance(backup, Mapping) or not isinstance(core, Mapping):
        raise ValueError("policy missing escalation sections")
    if not isinstance(warning, Mapping) or not isinstance(digest, Mapping):
        raise ValueError("policy missing digest sections")

    allowlist = critical.get("allowlist")
    never_page = data.get("never_page_by_default")
    services = core.get("services")
    if not all(isinstance(value, list) for value in (allowlist, never_page, services)):
        raise ValueError("policy list sections are malformed")
    allowlist_values = cast(list[Any], allowlist)
    never_page_values = cast(list[Any], never_page)
    service_values = cast(list[Any], services)
    backup_page_when = backup.get("page_when")
    if str(data.get("status", "")) not in _APPROVED_POLICY_STATUSES:
        raise ValueError("unexpected policy status")
    known_good_age = _page_when_value(backup_page_when, "known_good_age_exceeds", "26h")
    return PolicyConfig(
        critical_allowlist=frozenset(str(value) for value in allowlist_values),
        critical_topic=str(critical.get("topic", "")),
        digest_topic=str(digest.get("topic") or warning.get("topic") or ""),
        backup_known_good_age_hours=(
            float(known_good_age)
            if isinstance(known_good_age, (int, float))
            else _duration(known_good_age, "h")
        ),
        backup_consecutive_failures=int(
            _page_when_value(backup_page_when, "consecutive_failed_runs_at_least", 3)
        ),
        core_services=frozenset(str(value) for value in service_values),
        core_probe_grace_minutes=_duration(core.get("health_probe_grace", "15m"), "m"),
        core_probe_failures=int(core.get("consecutive_failed_probes_at_least", 3)),
        never_page=frozenset(str(value) for value in never_page_values),
        production_enabled=bool(data.get("production_enabled", False)),
    )


def _number(context: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(context.get(key, default))
    except (TypeError, ValueError):
        return default


def _is_backup_event(event: Mapping[str, Any]) -> bool:
    material = " ".join(
        str(event.get(key, "")).lower()
        for key in ("event", "service", "resource", "dedupe_key")
    )
    return any(term in material for term in _BACKUP_TERMS)


def _is_core_event(event: Mapping[str, Any], policy: PolicyConfig) -> bool:
    return str(event.get("service", "")).lower() in policy.core_services


def evaluate_event(
    event: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    *,
    policy: PolicyConfig,
) -> GateDecision:
    """Return a deterministic routing decision; perform no I/O."""

    if not isinstance(event, Mapping):
        raise ValueError("event must be an object")
    context = context or {}
    event_name = str(event.get("event", ""))
    status = str(event.get("status", "")).lower()
    severity = str(event.get("severity", "info")).lower()

    if status == "resolved":
        return GateDecision("digest", policy.digest_topic, "resolved_no_standalone_page")
    if status == "success" or event_name in policy.never_page:
        return GateDecision("log", None, "never_page_by_default")
    if status not in _ACTIVE_STATUSES:
        return GateDecision("log", None, "non_notification_status")

    if event_name in policy.critical_allowlist:
        return GateDecision("critical", policy.critical_topic, "critical_allowlist")

    if _is_backup_event(event):
        evidence = (
            bool(context.get("known_good_copy_missing", False))
            or _number(context, "known_good_age_hours") >= policy.backup_known_good_age_hours
            or _number(context, "consecutive_failed_runs") >= policy.backup_consecutive_failures
        )
        if evidence:
            return GateDecision("critical", policy.critical_topic, "backup_deterministic_evidence")

    if _is_core_event(event, policy):
        grace_met = (
            _number(context, "consecutive_failed_probes") >= policy.core_probe_failures
            and _number(context, "probe_window_minutes") >= policy.core_probe_grace_minutes
        )
        if grace_met:
            return GateDecision("critical", policy.critical_topic, "core_service_grace_exceeded")

    if severity in {"warning", "critical"}:
        reason = "critical_not_allowlisted" if severity == "critical" else "warning_digest"
        return GateDecision("digest", policy.digest_topic, reason)
    return GateDecision("log", None, "informational_event")
