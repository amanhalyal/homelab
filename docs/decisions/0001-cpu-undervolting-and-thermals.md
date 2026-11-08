# 0001. CPU undervolting and thermal tuning

Date: 2026-07-22 · Status: accepted

## Context

Both laptops run 24/7 as servers. Citadel (i3-8145U, thin chassis) hit 91 to 96°C under sustained load, and a single busy `gzip` was enough to do it. Legion (i5-9300H, gaming-laptop cooling) had no thermal problem, sitting at 52°C idle, so tuning it was proactive.

## Decision

**Undervolt both machines in small, tested steps, and stop at a measured middle value.**

- **Citadel: -90 mV on core and cache** through `intel-undervolt`. Steps of -50, -75 and -90 mV were each checked with repeated `stress-ng` runs, with no failures and no machine-check errors. Secure Boot had to be disabled first, because kernel lockdown blocks raw MSR access when it is on. A value of -150 mV found online was tested and rejected: it came with no evidence for this chip, and thermal margin was already thin.
- **Legion: -125 mV core, -120 mV cache.** Steps of -50, -75, -100 and -125 mV were each validated with a 60-second check and a 5-minute verified soak. The final values match a community profile for this exact CPU, and the sweep stopped there because the peak had reached 92°C against a 100°C throttle point.
- **One owner for frequency control.** `thermald`, TLP and an `auto-cpufreq` snap were silently overriding each other's governor and turbo settings. This showed up as `no_turbo` flipping back within seconds of being set. Only TLP is left running.
- **Citadel turbo cap, then removed.** A 3.4 GHz cap brought sustained load to about 83°C. After the physical cleaning below, the cap was removed because the thermal problem it managed no longer existed.

## Physical maintenance

Cleaning was the single biggest improvement. All figures are from 5-minute `stress-ng` runs.

| Machine | Work done | Before | After |
|---|---|---|---|
| Citadel | Repaste and fan clean | 95 to 96°C | 77°C, uncapped at 3.9 GHz |
| Legion | Fan clean only | 92°C | 59°C |

Legion's repaste was skipped because the screws were too tight to open safely. The Legion "before" figure comes from the undervolt validation earlier the same day, so it is directionally reliable but not a tight before and after bracket. The result suggests dust, not dried paste, was the main factor there.

## Consequences

- Disassembly reset Citadel's BIOS, which re-enabled Secure Boot and silently broke the undervolt service. After any teardown, check Secure Boot and the undervolt service.
- The cap was removed once the cause was fixed in hardware, which is the same "measured middle value" principle applied to a changed constraint.
- A hardware temperature limit for Legion was added later; see [0013](0013-tcc-thermal-offset.md).
