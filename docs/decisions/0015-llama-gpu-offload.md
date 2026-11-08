# 0015. GPU offload for local LLMs on a 4 GB card

Date: 2026-08-11 · Status: accepted

## Context

Two local models run through `llama-server` on Legion: Qwen3.5-4B, the always-on alert classifier (Cerberus), and Qwen3.5-9B, used on demand as the brain of the Pi coding-agent harness. Both ran on CPU only, at 4.0 tokens per second decoding for the 9B. The Debian `llama.cpp` package is a CPU-only build, so `-ngl` silently did nothing, and upstream no longer ships a CUDA binary for Ubuntu.

The container toolkit was already installed for Tdarr, the video transcoder. Instead of building from source, the official `llama.cpp:server-cuda` image was used.

**Hardware ceiling.** The GTX 1650 has 4096 MiB of VRAM. Qwen3.5-4B, fully offloaded, needs about 3 GB. Qwen3.5-9B cannot fit whatever the settings, so it is always a GPU and CPU split. Testing confirmed it: with the 4B resident, starting the 9B failed with `cudaMalloc failed: out of memory`.

## Decision 1: a sequenced VRAM swap

- The 4B runs fully offloaded with a 2048 context, down from 4096, since classification prompts are short and the smaller cache buys VRAM headroom. It sits at about 3 GB, leaving about 700 MiB for hardware video encoding.
- The 9B runs with 20 of 32 layers on the GPU and is never started automatically.
- The launcher script stops the 4B before starting the 9B and restarts it on exit.

While doing this a latent bug surfaced. The script set an `EXIT` trap for cleanup and then `exec`'d into the agent, which replaces the shell process, so the trap never fired. The 9B service had never been stopped after any earlier run. Dropping `exec` fixed it.

**Evidence.** The classifier self-test passed 10 of 10 on GPU, and a forced mid-test stop confirmed a clean fallback to "investigate" and not a drop. A real run of the swap decoded at 6.8 to 7.2 tokens per second, up from 4.0, consistent with 20 of 32 layers offloaded, and the 4B came back afterwards.

## Decision 2: free the GPU for the nightly transcode (since retired)

Update, 2026-11-03: the window-cycle timer is no longer installed, and Tdarr's own schedule now has every weekly slot enabled, so there is no overnight window. The sequenced swap in Decision 1 is unchanged. The rest of this section is kept for the investigation, which is the useful part.

Tdarr runs a nightly GPU-heavy job, and the LLMs should not compete with it for VRAM. Finding its schedule took two corrections:

1. The first belief was a fixed 01:00 to 02:30 window. That looked wrong, since there was no cron or timer and the transcoder runs as a continuous watch-folder server.
2. The second conclusion, "no fixed window exists", was also wrong. Crontab, timers and restart policy missed where the schedule actually lives: the transcoder node's own configuration, which is runtime state exposed only through its API and is invisible to `git grep` or `crontab -l`. It holds an enabled schedule allowing work only in the 01:00 to 02:00 hour. A week of real job reports confirmed jobs started at 01:00 to 01:03.

A fully reactive guard that polled every 15 seconds was built first, then removed as more machinery than the risk warranted. The final design is `tdarr-window-cycle`, triggered by a timer at 01:00:

1. Stop both LLM services immediately. This is safe on a clock trigger, because the transcoder's worker limits are mechanically zero outside the window.
2. Wait a three-minute grace period.
3. Poll every 20 seconds, and restart only the 4B classifier after two consecutive empty polls, with a three-hour hard cap so a stuck API cannot keep it off.
4. Never restart the 9B automatically.

Stopping on the clock is safe because it is provably early enough. Restarting reactively is right because a fixed 02:00 timer would be wrong if a job overran.

## Consequences

- If a transcode starts while the 9B is in use, the stop kills that session without warning. The window is one hour, so this is low-probability, but worth knowing before leaving an agent running overnight.
- Hardware video encoding uses separate fixed-function blocks from the CUDA cores, so the contention is VRAM capacity only, never compute.
- A real transcode in progress at the moment the timer fires was deliberately not forced. Queuing a job against production media just to test was not worth it.
- Backup files from the CPU-only setup were left next to the units as an easy revert.
- The checked-in files had been stale, with the 4B unit never tracked at all. All of them were synced to what is deployed.
