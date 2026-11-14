# Legion Compose stack

Legion runs one Compose project, `nidavellir`, split into category files. This directory is a sanitized copy of the running configuration. Paths, addresses and hostnames are variables, and secrets are never in the repo.

```bash
cp .env.example .env     # then fill it in
docker compose config -q # validate before every change
docker compose up -d
```

`COMPOSE_FILE` in `.env` loads all categories into one project, so cross-file references such as shared networks and `depends_on` resolve. Do not run a category on its own.

| File | Contents |
|---|---|
| `base.yml` | Project name, shared networks, and named volumes pinned to existing names |
| `network.yml` | Nginx Proxy Manager and Pi-hole |
| `ops.yml` | Homepage, Portainer, Uptime Kuma, Netdata, Scrutiny, GPU exporter, Watchtower |
| `media.yml` | Jellyfin and its automation (Prowlarr, Sonarr, Radarr, Bazarr, a download client, a VPN gateway), Tdarr, and Immich with its machine-learning, database and cache services |
| `apps.yml` | Home Assistant, Vaultwarden, Karakeep, Actual Budget, Minecraft with lazymc, kan.bn, dynamic DNS |
| `storage.yml` | Nextcloud, its database and cache. `nextcloud/Dockerfile` adds ffmpeg |
| `monitoring.yml` | Prometheus, Grafana, Pushgateway, Alertmanager, ntfy, the event receiver, VictoriaLogs, Vector, a host dashboard |
| `backups.yml` | Server and game-server archive workers |

## Conventions worth knowing

- **Every service has a memory limit and capped JSON logs** (10 MB, 3 files). Limits were set from measurements, and one that was too low corrupted backups, see [known issues](../docs/known-issues.md).
- **Services that must not listen on the LAN bind to `TAILSCALE_IP`** or to `127.0.0.1`. Nothing is port-forwarded.
- **Bind mounts that matter are `create_host_path: false`.** The Arcadia pool and the SnapRAID lock fail loudly when absent instead of creating an empty directory ([storage and parity](../docs/runbooks/storage-pool-and-parity.md)).
- **Watchtower is off for Home Assistant** through a label, because its updates are manual.
- **Images are pinned where an upgrade has bitten before** (Nextcloud, Immich, kan.bn, Scrutiny, Meilisearch, and the logging services by digest). The rest follow `latest` or `stable` and are updated by Watchtower.
- **Single-file bind mounts go stale.** See the note at the top of `backups.yml`.
- **Containers without `TZ` run in UTC,** which is why Watchtower's 04:00 job runs at 09:30 IST ([daily schedule](../docs/runbooks/daily-schedule.md)).

## What is not here

- Runtime state under `data/`, and the separate env files listed at the bottom of `.env.example`.
- A few small personal applications and their databases, and a photo-import job tied to a personal cloud account.
- Config files that live under `data/` and are mounted into containers, such as Vector's pipeline. Prometheus, Alertmanager, ntfy and Grafana provisioning are tracked in `data/` on the live host, and will be published under `compose/config/` in the next step.

## Review notes

- Jellyfin, Tdarr and the media automation services mount the whole repo checkout at `/data` so that library paths resolve unchanged after the move to Arcadia. That is broader than they need, because the checkout includes `compose/.env`. Narrowing it to the media directories is a worthwhile cleanup.
- `homelab-monitor` is a third-party image with a wide privilege footprint, accepted deliberately and noted in the file.
