# 0008. Cron timing: set the system timezone, not `CRON_TZ`

Date: 2026-07-28 · Status: accepted

## Context

The nightly sync, database backup and daily digest notifications were arriving about six hours later than expected. Legion's clock was UTC, and both crontabs began with a `CRON_TZ=Asia/Kolkata` line meant to make the schedule numbers read as IST.

## What was wrong

`CRON_TZ` was never supported on this machine. Ubuntu's `cron` package does not mention it in `man 5 crontab` or in its changelog, so the line was accepted silently and ignored. The syslog proved it: a job scheduled for `0 0 * * *` fired at 00:00:01 UTC, which is 05:30 IST and not the intended midnight IST. Every job ran about 5.5 hours late.

## Options

1. Convert every schedule to its UTC equivalent and keep the system on UTC.
2. Set the system timezone to `Asia/Kolkata` and leave the crontab numbers alone.

**Chosen: option 2**, after checking the blast radius, which is smaller than it sounds:

- Every application container already sets its own `TZ`, so none inherit host time.
- Prometheus, Grafana and Netdata store Unix timestamps, which have no timezone.
- Only native maintenance jobs shift (`logrotate`, `fstrim`, `apt-daily`, and similar), and none are time-of-day sensitive here.
- The hardware clock stays in UTC, so there is no dual-boot issue on a Linux-only server.
- Log lines and backup filenames written by the home-grown scripts now read in local time.

## Change

`timedatectl set-timezone Asia/Kolkata`, restart `cron`, and delete the dead `CRON_TZ` lines. Leaving them would mislead a future reader into thinking they were what made the schedule work.

## Lesson

Do not assume a crontab directive works. Check the daemon's documentation, then confirm against a real syslog timestamp. A related trap: containers with no `TZ` run in UTC, so a job set for 04:00 inside one runs at 09:30 IST.
