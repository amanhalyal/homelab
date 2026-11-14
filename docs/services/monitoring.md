# Monitoring stack

What watches the homelab, how the pieces connect, and the reasoning behind the shape. Delivery behavior is in [alerting and notifications](../runbooks/alerting-and-notifications.md), and the ledger behind it is in the [event pipeline](../runbooks/event-pipeline.md).

Configuration for the components below is in [compose/config](../../compose/config/README.md).

## Why it exists

Before this stack, alerting was a handful of one-off scripts. A Telegram bot watched SMART data, a mirror script reused the same bot token for its own warnings, a heartbeat container pinged an outside service, and Netdata ran standalone with no export. Each had its own notification code, none shared routing, and none kept history. One stack replaced them with a single place to define rules, one place to see dashboards, and one delivery mechanism.

## Components

| Component | Where | Role |
|---|---|---|
| **Netdata** | Legion, Citadel, Nexus | Per-host metrics agent: system, per-container cgroups, hardware sensors, SMART. Exposes a Prometheus endpoint |
| **Prometheus** | Legion | The one time-series database. Scrapes every Netdata, the GPU exporters and Pushgateway |
| **Pushgateway** | Legion | Receives results from cron-style jobs that are not long-running services |
| **Alertmanager** | Legion | Grouping, inhibition, silencing and routing |
| **Grafana** | Legion | Dashboards, including the wallboard's Grafana page |
| **Uptime Kuma** | Legion | Service and endpoint checks |
| **Scrutiny** | Legion hub, collectors on each host | SMART history and trends for every drive |
| **Event receiver and ledger** | Legion | The canonical intake that records events and decides what reaches ntfy |
| **ntfy** | Legion | Terminal delivery to the phone |
| **VictoriaLogs and Vector** | Legion | Raw log storage and shipping, separate from the alert path |
| **Exporters** | Legion, Nexus | A GPU exporter on each NVIDIA host, and a Kuma exporter that turns monitor status into metrics |
| **External heartbeat** | Citadel | A pinger to an outside service, so a dead host is noticed by something that does not depend on it |

There is one Prometheus, on Legion. Nexus and Citadel run only a Netdata agent, so a rarely-on machine does not carry a second monitoring stack ([0018](../decisions/0018-nexus-monitoring-and-sensors.md)).

## How data flows

- Netdata agents are scraped every 10 seconds, and alert rules are evaluated every 30 seconds so brief blips do not churn alerts.
- Cron jobs push outcome metrics, such as success and last-run time, to Pushgateway, and rules fire on `success == 0` or on a run that is too old. The second rule catches a job that silently stopped running, which a script-side alert cannot do.
- Alertmanager and Uptime Kuma post to the canonical receiver, which records the event and applies policy before anything can reach the phone.
- Container health is a pair of Prometheus rules on a metric Netdata already exports ([0014](../decisions/0014-prometheus-replaces-crash-loop-watch.md)). Host availability has its own rule ([0022](../decisions/0022-citadel-unreachable-alert.md)).

## Lessons

- **Netdata's Docker module is expensive on a busy host.** It polls the Docker API for every container each cycle, with a stock interval of 1 to 3 seconds. With 9 containers on Citadel and 42 on Legion, `dockerd` and `containerd` used about half a core combined. Setting `update_every: 10` in the module's config dropped that to 0 to 1%, and all the container state charts still worked. The module is `docker`, not `docker_engine`. The override lives under gitignored runtime data, so it is documented here as its restore source:

  ```yaml
  update_every: 10
  autodetection_retry: 0
  jobs:
    - name: local
  ```

  On Legion that config directory is owned by `nobody`, so write through the container: `printf '<conf>' | docker exec -i netdata tee /etc/netdata/go.d/docker.conf`.
- **A Prometheus label is not a fact.** A `host` label is whatever the scrape config says. After a migration, a container-name target resolved to whichever machine ran the stack, so dashboards showed plausible but mislabeled data instead of failing.
- **Check which job a scrape runs from.** A monitor's data file readable only by root, read from a normal user's crontab, made an exporter silently dead for about 51 days ([0022](../decisions/0022-citadel-unreachable-alert.md)).
- **Do not add new channels that post straight to ntfy.** Everything goes through the canonical receiver so grouping, deduplication, redaction and policy always apply.

## Uptime Kuma

Kuma's single notification is a webhook to the receiver, not a direct ntfy channel. A normal monitor warning is therefore digest-only unless a critical condition or an escalation threshold is met. Monitors are created and edited through its Socket.IO API ([admin notes](../runbooks/proxy-and-monitor-api-changes.md#uptime-kuma)).
