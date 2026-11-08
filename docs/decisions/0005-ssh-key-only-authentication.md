# 0005. SSH password authentication disabled

Date: 2026-07-22, amended 2026-08-05 · Status: accepted

## Context

Both machines had password authentication enabled: Citadel through an explicit cloud-init drop-in, and Legion through the OpenSSH default. Key-based access already worked, which made passwords unnecessary attack surface.

## Decision

Set `PasswordAuthentication no` on both machines, in this order:

1. Confirm key authentication is effective with `sshd -T`, which shows the running configuration and not just the files.
2. Confirm `cloud-init` is disabled, so a direct edit to its drop-in will not be reverted.
3. **Before changing anything, ask what else logs in with a password.** This caught a real case: a phone using an SSH client app was authenticating with a password. Its key was added to both machines and tested. Legion was only confirmed for the phone later, on 2026-08-05.
4. For each change: edit, `sshd -t`, reload, then immediately test key login from another trusted connection.

## Amendment: the hardening regressed

The July rebuilds silently undid the change. A sweep on 2026-08-05 found password authentication back on both machines. Legion had a fresh cloud-init drop-in set to `yes`, and Citadel had no setting at all.

The files showed no `PasswordAuthentication` line, which reads as "disabled" only if you forget that the OpenSSH default is `yes`. Only `sshd -T` shows the truth. The fix was re-applied with the same discipline. On Legion the value had to be changed in the cloud-init file itself, because with OpenSSH's first-value-wins rule a later drop-in cannot override it.

## Consequences

- Any post-rebuild audit must check `sshd -T` output, not config files.
- Phones and other personal devices are real access paths. In a Tailscale-centric design they are easy to forget because they are not machine-to-machine connections.
- SSH is key-only on every machine, and each pair of machines has its own dedicated key.
