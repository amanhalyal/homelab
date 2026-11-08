# 0002. HDD head-parking and read-ahead tuning

Date: 2026-07-22 · Status: accepted

## Context

Both data drives (Atlas and Aegir) are laptop HDDs with high `Load_Cycle_Count` values. The cause turned out to be different for each, so they were given different fixes instead of being made to match.

## Atlas: an active problem

At the default APM level of 128, the drive parked its heads on every few-second I/O gap. The counter rose from 29,298 to 29,306 in about 90 minutes of normal use, roughly 2.75 cycles per hour. This is pure wear: the drive is never spun down, so parking buys nothing.

**Fix:** APM 192, the middle of the band that avoids spin-down. Level 254 would disable parking entirely, but a middle value was chosen over the extreme.

**Verified a week later,** after the drive had also been moved into Legion's chassis: 160 cycles in about 168 hours, or 0.95 per hour, a drop of about 65%. The exact start of the fix was not recorded, so this is an approximation, but a sustained 65% drop over a week is well outside what that error could explain. The udev rule matches the drive by serial number rather than mount location, so the setting re-applied by itself after the move.

## Aegir: a historical number

Its count of 322,353 dates from its earlier life in another system. A 90-second idle test showed zero increments, and APM was already at 254. **Left unchanged.** Lowering it to match Atlas would introduce parking behavior for no benefit.

## Read-ahead

Atlas was at the stock 128 KB, and Aegir had an unexplained 8192 KB that no rule or script accounted for. Both were set to **4096 KB**. At the time the main workload was a nightly full-tree rsync mirror, which is sequential and benefits from larger read-ahead, with diminishing returns well before 8 MB on a single mechanical drive.

## Consequences

- Check `Load_Cycle_Count` periodically to confirm the rate stays low.
- "Make the two drives consistent" was the wrong instinct. Same symptom, different causes.
