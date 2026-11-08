# Evaluating small local models for alert triage

How three small models were compared for the alert classifier (Cerberus) on a 4 GB GPU, and what that showed. The decision that followed is [0016](../decisions/0016-alert-triage-classifier.md).

**Date:** 2026-10-28. These are small synthetic tests. They identify candidates for shadow evaluation on real alerts. They are not broad quality rankings or evidence of readiness for autonomous operation.

## Setup

- **Hardware:** Legion, i5-9300H, 22 GiB RAM, GTX 1650 with 4096 MiB VRAM.
- **Models, as practically deployable candidates:**

  | Model | Precision | File |
  |---|---|---|
  | Qwen3.5-4B (the deployed baseline) | Q4_K_M | about 2.6 GB |
  | LFM3-1.7B | Q8_0 | 1.85 GB |
  | Nemotron 3.5 Bolt E4B (official checkpoint) | Q4_K_M, mixed precision | 6.18 GB |

  Precision differs on purpose. This compares deployable choices, not architectures at equal precision.
- **Runtime:** llama.cpp built from one pinned commit with the CUDA backend, the same for all models. Full GPU offload where it fits, one server slot, four CPU threads, batch 512, microbatch 128, flash attention, a Q8 K/V cache, thinking off, temperature 0 and a fixed seed.
- **Isolation:** each model ran on a separate port with synthetic alerts and tool results only. Tools were inspected as generated output and never executed. No notification, ledger or production setting changed, and GPU memory returned to its 1 MiB idle baseline afterward.
- Weights were checksum-verified, and GPU memory and process RSS were sampled separately, since the file size or a "4GB" name does not say what fits.

## Results

| Check | Qwen3.5-4B | LFM3-1.7B | Nemotron Bolt E4B |
|---|---:|---:|---:|
| Original classification prompt, 26 cases | 25/26 | 25/26 | **26/26** |
| Valid bounded brief structures | 8/8 | 8/8 | 8/8 |
| Tool call, exact tool-result follow-up, runbook | 3/3 | 2/3 | 3/3 |
| Warm classification median | 0.94 s | 1.32 s | 1.45 s |
| Median classification decode rate | 34.2 tokens/s | 52.0 tokens/s | 28.8 tokens/s |
| Peak VRAM at 8K | 2932 MiB | 1918 MiB | 3384 MiB |

With a revised prompt, LFM3 and Nemotron both scored 26/26 on the original cases and 12/12 on twelve unseen ones. Qwen scored 25/26 and 12/12, and its remaining miss was a conservative escalation of a self-heal notice. The prompt was written before Nemotron was tested, with no examples tailored to it.

Nemotron handled both cases the others missed: instructions injected into an unhealthy service's log, and a service still unhealthy 20 minutes after an update. It also returned the exact requested value after a synthetic tool result, which LFM3 failed.

### Longer context

A synthetic probe hides facts at three depths in a long, varied telemetry history, requires every field to match exactly, and then adds a newer recovery event that has to update status without losing the history.

| Profile | Input | Retrieval | Fresh request | Follow-up | Peak VRAM |
|---|---:|---|---:|---:|---:|
| Qwen 32K | 24,936 tokens | pass | 181.7 s | 4.8 s | 3442 MiB |
| LFM3 32K | 24,012 tokens | pass | 71.4 s | 71.9 s | 2122 MiB |
| Nemotron 32K | 24,912 tokens | pass | 196.0 s | 6.6 s | 3692 MiB |
| LFM3 64K, full cache | 49,150 tokens | pass | 250.2 s | 3.1 s | 2750 MiB |

- Nemotron's follow-up reused 24,946 cached tokens and processed only 171 new ones, with no workaround needed. LFM3's default sliding-window cache reuses history poorly, and `--swa-full` fixes that.
- **32K is the largest Nemotron profile that worked, not a hard limit.** Its advertised 64K context failed to initialize on a 292 MiB compute-buffer allocation. Smaller batches, a lower-precision cache or partial offload might change that, and they were not benchmarked.

## Findings worth keeping

- **A reasoning flag was not enough.** With `--reasoning off` alone, Nemotron often emitted a thought channel, used up the 80-token classification budget and returned no JSON. Adding `--reasoning-budget 0` fixed it. Both are needed. A restart that overlapped with an old process was excluded from all scores, and the launcher now refuses to start if the port is taken.
- **Briefs are scored on structure, not truth.** Nemotron still invented a container name in a suggested `docker stats` check. A passing brief shows valid, bounded output and does not certify diagnostic accuracy.
- **Fit is measured.** Peak process RSS was about 4.5 GiB for Nemotron, so it does not fit entirely on a 4 GB GPU despite the name.
- **The timeouts are too short for long histories.** The existing 30-second inference timeout and 90-second lease are shorter than a fresh long-history read for all three models. Expanding context needs its own background budget, not just a bigger `-c`. Critical routing stays deterministic and independent of inference.

## Choice

- **A 64K incident-history role on this card:** LFM3 with `--swa-full` fits best, with more headroom and faster history reuse.
- **Short triage, 8K to 32K:** Nemotron gave the cleanest quality. Its costs are more VRAM and host memory and slower requests.
- **Qwen:** a workable baseline that uses less GPU memory than Nemotron, with one false ignore on the original prompt.

Nemotron was deployed for short triage on 2026-10-28, preferring quality over LFM3's headroom. LFM3 stays downloaded as a tested alternative.
