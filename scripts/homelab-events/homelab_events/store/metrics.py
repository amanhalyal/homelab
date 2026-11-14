"""Prometheus text rendering for the local ledger prototype."""

from __future__ import annotations

import math
from collections.abc import Mapping


def render_prometheus(snapshot: Mapping[str, int | float]) -> str:
    """Render an unlabeled snapshot using Prometheus text exposition format."""

    lines: list[str] = []
    for name in sorted(snapshot):
        value = snapshot[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"metric {name!r} must be numeric")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"metric {name!r} must be finite")
        metric_type = "counter" if name.endswith("_total") else "gauge"
        lines.append(f"# TYPE {name} {metric_type}")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + ("\n" if lines else "")
