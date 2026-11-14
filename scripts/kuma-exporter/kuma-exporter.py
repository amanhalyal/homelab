#!/usr/bin/env python3
"""kuma-exporter — push Uptime Kuma monitor status to the pushgateway.

Reads Kuma's SQLite DB directly (read-only; no REST API exists in Kuma 1.x)
and pushes `probe_success{monitor_name=..., host="legion"} 1|0` for every
monitor. Prometheus already scrapes the pushgateway, so the wallboard's
"Kuma" panels query probe_success{job="kuma-exporter"}.

Run every 2 minutes via cron. Silent on success; logs on error.
"""

import os
import sqlite3
import urllib.request

KUMA_DB = "/opt/homelab/data/uptime-kuma/kuma.db"
PUSHGW = "http://localhost:9091/metrics/job/kuma-exporter"


def fetch_monitors():
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % KUMA_DB, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT m.name, m.active, "
            "COALESCE((SELECT h.status FROM heartbeat h "
            "WHERE h.monitor_id=m.id ORDER BY h.time DESC LIMIT 1), 0) "
            "FROM monitor m"
        )
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as exc:
        print("kuma-exporter: db read failed: %s" % exc, flush=True)
        return None


def push(metrics_text):
    req = urllib.request.Request(
        PUSHGW,
        data=metrics_text.encode(),
        headers={"Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status != 200:
                print("kuma-exporter: push status %s" % r.status, flush=True)
    except Exception as exc:
        print("kuma-exporter: push failed: %s" % exc, flush=True)


def main():
    monitors = fetch_monitors()
    if monitors is None:
        return
    lines = []
    for name, active, status in monitors:
        clean = str(name).replace('"', "")
        up = 1 if (active and status == 1) else 0
        lines.append(
            'probe_success{monitor_name="%s",host="legion"} %d' % (clean, up)
        )
    if lines:
        push("# TYPE probe_success gauge\n" + "\n".join(lines) + "\n")
    print("kuma-exporter: %d monitors pushed" % len(lines), flush=True)


if __name__ == "__main__":
    main()
