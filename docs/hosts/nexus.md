# Nexus

A powerful workstation that is usually switched off. It is used for memory-hungry jobs and as a local LLM host. Nothing in the homelab may assume it is reachable.

## Identity

| | |
|---|---|
| Hardware | Desktop PC, AMD CPU with 12 cores, 32 GB RAM, RTX 5070 Ti with 16 GB VRAM |
| OS | CachyOS (Linux) natively since 2026-09-01. The LLM stack used to run under WSL2 on Windows |
| Availability | Powered on for specific tasks |
| Wake | Magic packet from [Citadel](citadel.md) over a dedicated cable. Reachable by an SSH alias over the LAN even when Tailscale shows it offline |
| Monitoring | One small Netdata container plus a GPU exporter, scraped by Legion ([0018](../decisions/0018-nexus-monitoring-and-sensors.md)) |

## Local LLM serving

Nexus hosts a 27B-parameter model, Qwen3.8-27B, behind `llama-swap`, with llama.cpp's CUDA backend. It exposes an OpenAI-compatible endpoint to clients on the tailnet. The setup lives under `~/locallama/` and is run by user systemd units with lingering enabled.

**Model and quantization.** The model is Unsloth's `UD-IQ4_XS` quant, 14.3 GB. It replaced a 15.6 GB `IQ4_XS` after a head-to-head: smaller, correct on every test, and 6% faster on two of three profiles purely because it leaves more VRAM free. A larger `UD-Q4_K_M` at 16.5 GB was tested first and rejected, since it was slower by 6 to 59% depending on profile. On a 16 GB card this setup is VRAM-constrained, not accuracy-constrained, so smaller won. Anything below IQ4 was ruled out on quality.

**Profiles.** Four context tiers share the same flags and differ in context size, KV-cache precision and how many layers are offloaded:

| Profile | Context | Offload | Decode speed |
|---|---|---|---|
| `fast` (default) | 48K | All layers on GPU | about 46 tokens/s |
| `64k` | 64K | All layers on GPU | about 46 tokens/s |
| `128k` | 128K | All layers on GPU | about 46 tokens/s |
| `200k` | 200K | 4 layers on CPU | about 22.6 tokens/s, for background work |

## What the tuning taught

The full log is long. These are the findings that changed decisions.

- **There is a VRAM cliff, and `nvidia-smi` hides it.** Below roughly 50 MB free, full offload falls off a ledge rather than slowing gradually: 84 MB free decoded at 45.3 tokens/s and 48 MB free at 14.6. Partial offload has its own cliff, where one more layer on the GPU dropped 22.9 tokens/s to about 12 at near-identical reported free memory. The driver also reserves 445 MB that the plain `nvidia-smi` table never shows. The fix was a smaller prefill compute buffer (`-b 1024`) plus context-shift flags, which took a failing config from 7 to 13 tokens/s back to 46.8 at the same tight margin. It was buffer sizing, not raw headroom.
- **KV-cache precision is chosen per profile.** The 48K tier spent its spare VRAM on `q8_0` K/V at no measurable speed cost. The 128K tier could not afford that. A middle option (`q5_0`) was a dead end at 29.8 tokens/s against 46.4 for `q8_0`, and `q6_0` is not a valid type at all.
- **Only every fourth layer is full attention.** Dumping the GGUF showed 17 of 65 layers use full attention, whose KV cache grows with context, and the rest are Gated-DeltaNet layers with a fixed-size recurrent state. A smarter, architecture-aware offload plan was tested and lost: it either collapsed to 6.4 tokens/s or matched speed with a worse margin. The plain `-ngl` cutoff happens to offload two expensive full-attention layers, which is close to right by accident.
- **NVFP4 was investigated and rejected.** Blackwell's native 4-bit format looked like free speed, and community numbers on a bigger card were roughly three times higher. After verifying the build really contained the Blackwell kernels, decode was at parity with the existing quant, 47.7 against 46.2 tokens/s, while prefill was 49% faster. That is expected. Single-token decode is memory-bandwidth-bound, and a 4-bit format moves about as many bytes as the current one, so compute-side hardware cannot help there.
- **Speculative decoding with MTP was net negative,** at 5 to 11 tokens/s against 46.8 without it. The setting came from a shared "benchmark guide" that also invented a nonexistent fork and cache types, so the whole document was treated as unreliable. The one claim that could be tested was wrong.
- **Native beat WSL2 by about 21%.** On Windows, the same model decoded at 35.2 tokens/s under WSL2 and 42.6 natively, and on native Linux it reached 49.9. WSL2 shares one VRAM pool with Windows, so a monitor being driven claims real VRAM, which matters near the cliff.
- **Long sessions degrade, and no one has a fix.** After about an hour of continuous use, throughput stepped down from about 30 to 15 to 5 tokens/s, with 502 errors at each step. Three independent upstream reports show the same pattern on unrelated hardware, and the maintainers have identified no root cause. The only documented remedy is restarting the process, so a watchdog does that every 30 minutes, but only when no request is in flight. A known gap remains: the check cannot see the cold-load window before a model is ready, so a restart can land during it and fail one request with a 502.
- **Parallel CUDA builds can kill a VM.** An uncapped `cmake --build -j` crashed the whole WSL2 virtual machine twice during flash-attention template compilation. Rebuilds are capped at `-j 4`.

## Other uses

Nexus builds the routing graph for OpenTripPlanner, an on-demand trip-planning service. The build needs a heap of up to 28 GB, against 12 GB to serve. The service itself runs on Legion only when needed.

For how the model comparisons for the alert classifier were run on a different, smaller GPU, see the [classifier evaluation](../services/local-llm-evaluation.md).
