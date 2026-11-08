# Runbooks

Operator procedures for running and recovering the homelab. Each one states its safety boundaries, the checks to run, and what has and has not been tested.

## Operations and recovery

- [Daily schedule](daily-schedule.md): every recurring job, when it runs, and the gotchas
- [Backup and restore](backup-and-restore.md): backup layers, verification order, and recovery steps
- [Storage pool and parity](storage-pool-and-parity.md): the mergerfs pool, safe mounting, SnapRAID, and disk removal

## Monitoring and alerting

- [Alerting and notifications](alerting-and-notifications.md): what pages a phone, what stays in the digest, and how to roll back
- [Event pipeline](event-pipeline.md): the SQLite ledger, outbox and receiver behind it
- [Observability recovery](observability-recovery.md): failure paths for the logging and monitoring stack, and the remediation model

## Administration

- [Proxy and monitor API changes](proxy-and-monitor-api-changes.md): bulk edits through the Nginx Proxy Manager and Uptime Kuma APIs
- [Adding SSH access for a new device](add-ssh-device.md)
