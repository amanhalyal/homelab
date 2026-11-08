# Services

The service catalog. Service pages cover how things are wired. Repeatable procedures are in the [runbooks](../runbooks/README.md), and the reasoning behind choices is in the [decision records](../decisions/README.md).

| Page | Covers |
|---|---|
| [Monitoring stack](monitoring.md) | Metrics, dashboards, alerting components and the lessons from running them |
| [Wallboard](wallboard.md) | The kiosk display, its control API and its Home Assistant integration |
| [Home automation](home-automation.md) | Home Assistant, lighting, the dashboard and storage controls |
| [Minecraft server](minecraft.md) | Paper server with a wake-on-join proxy |
| [Local model evaluation](local-llm-evaluation.md) | How small models were compared for alert triage |

## What runs where

Legion runs one Compose project, `nidavellir`, split into category files. Citadel runs a separate project, `valhalla`, with its own Docker daemon. Container names resolve only on their own host, so cross-machine calls use the Tailscale address ([architecture](../architecture.md#network-and-trust)).

### Legion

| Service | Category | Purpose |
|---|---|---|
| Nginx Proxy Manager | network | Reverse proxy for tailnet-gated hostnames |
| Pi-hole | network | DNS, ad blocking and the tailnet's resolver |
| Immich with its ML, database and cache | media | Photo management |
| Nextcloud with its database and cache | storage | File storage and sync |
| Jellyfin, Tdarr and media automation | media | Media server, transcoding and management |
| Karakeep with Chrome and Meilisearch | apps | Bookmarks with full-text search |
| Actual Budget | apps | Budgeting |
| Home Assistant | apps | Home automation ([page](home-automation.md)) |
| kan.bn with its database | apps | Task board and API |
| Vaultwarden | apps | Password manager |
| Minecraft and lazymc | apps, backups | A server that sleeps when idle ([page](minecraft.md)) |
| Self-built apps | native | Two small personal services |
| Homepage, Portainer, Uptime Kuma | ops | Dashboard, container management, uptime checks |
| Watchtower | ops | Image update checks |
| Netdata, Prometheus, Pushgateway, Grafana, Alertmanager | monitoring | Metrics, dashboards and alert grouping ([page](monitoring.md)) |
| ntfy and the event receiver | monitoring | Delivery and the canonical event ledger |
| VictoriaLogs and Vector | monitoring | Raw log storage |
| Backup jobs | backups | Server and game-server archives, database dumps |
| Local LLM worker | container | On-demand alert classifier ([0016](../decisions/0016-alert-triage-classifier.md)) |

### Citadel

Netdata, a Scrutiny collector, Watchtower and a heartbeat pinger in Docker, plus the native wallboard. See [Citadel](../hosts/citadel.md).

### Nexus

A Netdata agent and a GPU exporter, plus the local model server. It is usually off. See [Nexus](../hosts/nexus.md).

## Notes on specific services

### Uptime Kuma alerting

Kuma's one notification is a webhook to the canonical receiver, not a direct ntfy channel. Events enter the same ledger and policy as Alertmanager and scheduled jobs, so a normal monitor warning is digest-only. Only an approved critical condition or an escalation threshold reaches the critical topic. Add new monitors through the webhook, not through a new direct channel ([alerting](../runbooks/alerting-and-notifications.md)).

### OpenTripPlanner

Trip planning runs on demand from a standalone Compose file outside the main project, and is started only when routing is needed. Its graph was trimmed from 2.97 GB (built nationwide on Nexus) to 544 MB with the heap capped at 6 GB, which brought steady state from about 12.6 GB resident to about 3.8 GB. Starting it still pushed swap from 2.7 GB to 5.7 GB and pushed about 1.7 GB of the Minecraft heap out to swap, so it is best avoided while someone is playing.

Its host port was remapped twice. The first port collided with an always-on native service, and the second with the classifier that is permanently bound to its own port. Starting it on the old port would have failed to bind silently. Check the port map before adding anything.

### Self-built apps

Two small personal services run natively under systemd and not in containers. They are not described further here.

### Deferred

AdGuard Home is not deployed. Running it beside Pi-hole on the same machine would not provide real redundancy.
