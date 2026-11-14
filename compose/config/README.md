# Monitoring configuration

Config files mounted into the monitoring containers in [`../monitoring.yml`](../monitoring.yml). On the live host they sit under `data/`, which this repo ignores because the rest of `data/` is runtime state. To deploy, copy each directory to the matching path under `$HOMELAB_DIR/data/`.

| Path | Mounted into | Notes |
|---|---|---|
| `prometheus/prometheus.yml` | Prometheus | Nine scrape jobs. Netdata on each machine, the GPU exporters, VictoriaLogs, Vector, Pushgateway and the event receiver |
| `prometheus/alert-rules.yml` | Prometheus | 29 rules in groups for logging, drive health, host availability, power, containers, the wallboard, the event pipeline and the storage pool |
| `alertmanager/alertmanager.yml` | Alertmanager | One receiver. Everything goes to the event receiver, never straight to the phone |
| `ntfy/server.yml` | ntfy | Deny-all by default. The upstream relay carries a content-free wake-up for iOS push |
| `vector/vector.yaml` | Vector | Ships Docker and current-boot journald logs to VictoriaLogs, with a 4 GiB disk buffer that blocks instead of dropping, and basic redaction of bearer tokens and password-like values |
| `grafana/provisioning/datasources/` | Grafana | Prometheus, Alertmanager and VictoriaLogs |
| `grafana/provisioning/dashboards/` | Grafana | An operations dashboard, the wallboard dashboard and a log view |

## Placeholders

- `100.64.0.2` and `100.64.0.3` in `prometheus.yml` stand in for Citadel's and Nexus's Tailscale addresses. Prometheus has no environment substitution, so edit them in place.
- The ntfy `base-url` uses `example.duckdns.org`.
- The VictoriaLogs data source reads its credentials from the environment file that Grafana loads (`VICTORIALOGS_AUTH_USERNAME` and `VICTORIALOGS_AUTH_PASSWORD`).

## Validating a change

Rule edits are a real outage risk. A typo made Prometheus crash-loop and took the wallboard offline for a day ([known issues](../../docs/known-issues.md#resolved)). Check before restarting:

```bash
docker run --rm --entrypoint promtool \
  -v "$PWD/prometheus/alert-rules.yml:/r.yml:ro" prom/prometheus:latest check rules /r.yml
docker run --rm --entrypoint amtool \
  -v "$PWD/alertmanager/alertmanager.yml:/a.yml:ro" prom/alertmanager:latest check-config /a.yml
```

Prometheus does not pick up a rule file replaced by an editor's atomic rename on `SIGHUP`, so restart the container to reload ([0006](../../docs/decisions/0006-backup-verification.md)).

## Log redaction

The `normalize` transform masks bearer tokens and `password`, `token`, `secret` and `api_key` values before logs leave the host. It is a safety net and not a guarantee, because it only matches those patterns. Regexes in a VRL raw string take a single backslash, and a doubled one (`\\s`) matches a literal backslash and quietly stops redacting. This pipeline was first deployed with exactly that mistake, and a test against sample lines with `vector vrl` showed it. Test a pattern that way before trusting it.

## Dashboards

- **Operations** (`homelab-health.json`) covers per-host summary, drive and SMART lifecycle, Docker, network, reliability, battery, and the storage pool with SnapRAID state.
- **Wallboard** (`wallboard.json`) is the page shown on the kiosk display ([wallboard](../../docs/services/wallboard.md)). Every panel has a unique `id`, which Grafana's provisioned grid needs.
- **Logs** (`homelab-logs.json`) queries VictoriaLogs through a service filter and a text search.

The Prometheus data source is the default. Alerts reach the dashboard through Alertmanager and are shown on the operations dashboard's firing alerts list.
