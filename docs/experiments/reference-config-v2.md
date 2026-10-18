# Experiment: cpu-reference-v2 (PocketPal's Android defaults) vs cpu-reference-v1

Part of #4. Measured 2026-10-18.

## Question

[`cpu-reference-v2`](../../xebenchapp/configs/cpu-reference-v2.json) is
[`cpu-reference-v1`](../../xebenchapp/configs/cpu-reference-v1.json) with PocketPal's
shipped Android load settings ([REFERENCE-RUNS](../REFERENCE-RUNS.md)). Two settings differ
in effect:

| llama.rn parameter | cpu-reference-v1 | cpu-reference-v2 |
| --- | --- | --- |
| `use_mmap` | `true` (memory-mapped load) | `false` (weights read into memory) |
| `flash_attn_type` | `auto` | `off` |
| `use_mlock` | `false` | `false` |
| `no_extra_bufts` | `false` (weight repacking on) | `false` (weight repacking on) |

How much do these settings change prefill, decode, TTFT and load time, and which setting
causes the change?

## Setup

- Model: Llama 3.2 1B Instruct Q4_K_M (`6f85a640…`, [MODELS.md](../../MODELS.md)), llama.cpp
  CPU through llama.rn 0.13.0-rc.7 (llama.cpp b11385, `bf9a0cc`), 4 threads, `n_ctx` 2048,
  `n_batch`/`n_ubatch` 512, f16 K/V, greedy, prompt `std-pp512-v1` (472 prompt tokens with
  this model), `n_predict` 128 (127 timed decode tokens per repetition). Every other setting is the same in both configurations.
- App: the `reference` release APK built from `0b24d8c` with a clean tree
  (SHA-256 `1fc8abbee996a07f941b8180bafeee9e36ace42efb8f26544a7028ea267a4078`, repository debug
  key). Protocol `cpu-reference-v3`, run conditions `run-conditions-v2`, charging policy
  `charging-policy-v3`.
- A v2 session runs the bundled configuration (`configurationSource` hash `6272ecc4…`). A v1
  session runs a staged byte-for-byte copy of the committed v1 file (hash `0d1b459f…`).
- Each session has 3 repetitions. Each repetition is a fresh model load after a 30 s cooldown
  and a readiness wait. Sessions are interleaved v2, v1, v2, v1, v2 on each phone. The phones
  are USB- or AC-tethered and report `FULL` throughout.
- Every session completes with condition verdict `pass` and charging `satisfied`.
  Publication policy v1 marks all 15 sessions `verified`.
- Medians and IQRs pool every repetition of a configuration on a device (v2: n = 9, v1: n = 6).
  Quartiles use linear interpolation at `(n - 1) * p`.

## Result: v1 vs v2 on three phones

| Device | Configuration | n | Prefill tok/s | Decode tok/s | TTFT ms | Load ms |
| --- | --- | --- | --- | --- | --- | --- |
| POCO F8 Ultra | cpu-reference-v1 | 6 | 207.5 (0.3) | 47.4 (0.1) | 2278 (2) | 951 (68) |
| POCO F8 Ultra | cpu-reference-v2 | 9 | 169.3 (0.4) | 49.5 (0.1) | 2792 (7) | 1106 (164) |
| Galaxy S23 | cpu-reference-v1 | 6 | 147.2 (2.2) | 28.4 (0.6) | 3208 (49) | 896 (54) |
| Galaxy S23 | cpu-reference-v2 | 9 | 106.3 (3.3) | 28.9 (1.3) | 4444 (135) | 1244 (1083) |
| Pixel 9 | cpu-reference-v1 | 6 | 156.1 (1.0) | 24.3 (0.1) | 3030 (20) | 1131 (20) |
| Pixel 9 | cpu-reference-v2 | 9 | 108.4 (1.6) | 24.8 (0.3) | 4364 (63) | 1549 (7) |

Median (IQR). Change from v1 to v2:

| Device | Prefill | Decode | TTFT | Load |
| --- | --- | --- | --- | --- |
| POCO F8 Ultra | −18.4% | +4.4% | +22.5% | +16.4% |
| Galaxy S23 | −27.8% | +1.5% | +38.5% | +38.9% |
| Pixel 9 | −30.6% | +2.0% | +44.0% | +37.0% |

The S23's v2 load times spread: the second and third loads of its first session take 2355
and 2275 ms, and the first load of its last session takes 8523 ms. Its other six v2 loads
take 1077–1314 ms. The prefill and TTFT of the three slow-load repetitions are within 2.5%
of the S23's v2 medians, and their decode within 3.6%.

Evidence: F8 [`a3ef19fa…`](../../evidence/a3ef19fae8913867c1e131f53de4327bac4745896ccc85a583e9a17f97078479/),
S23 [`0d77346d…`](../../evidence/0d77346dfff7b200512e57b50ce2d569ea33a9361e8467f9a678656701969054/),
Pixel 9 [`b5c69a2c…`](../../evidence/b5c69a2c8470ca4e9f815d05a050518017e2cfb33a48efff7a23d8fc57385d37/).

## Result: one-factor split on the POCO F8 Ultra

Five staged experiment configurations. Each is a copy of cpu-reference-v2 with only
`useMmap` and `flashAttnType` changed, and an `experiment-v2-split-*` id, so publication
refuses them. Ten sessions of 3 repetitions each run in the order A B C D E E D C B A
(n = 6 per condition). All ten pass their run conditions with charging `satisfied`.

| Condition | `use_mmap` | `flash_attn_type` | Prefill tok/s | Decode tok/s | TTFT ms | Load ms |
| --- | --- | --- | --- | --- | --- | --- |
| A (= v2) | `false` | `off` | 169.1 (0.3) | 49.6 (0.0) | 2795 (4) | 1120 (10) |
| B | `true` | `off` | 169.4 (0.3) | 49.6 (0.1) | 2789 (4) | 923 (5) |
| C | `false` | `auto` | 207.5 (0.2) | 47.4 (0.1) | 2277 (2) | 1117 (12) |
| D (= v1) | `true` | `auto` | 207.6 (0.2) | 47.1 (0.6) | 2277 (2) | 916 (27) |
| E | `false` | `on` | 207.5 (0.1) | 47.5 (0.1) | 2277 (1) | 1110 (11) |

- Flash attention off, with the load mode held fixed (A vs C, B vs D): prefill −18.5% and
  −18.4%, decode +4.7% and +5.3%, TTFT +22.7% and +22.4%, load +0.2% and +0.7%.
- Memory-mapped load off, with flash attention held fixed (A vs B, C vs D): load +21.4% and
  +21.9% (about 200 ms). Prefill, decode and TTFT change by at most 0.6%.
- `auto` and `on` (C vs E) differ by at most 0.01% in prefill and TTFT, 0.2% in decode and
  0.7% in load. In the vendored llama.cpp (`src/llama-context.cpp`), `auto` enables flash
  attention unless a fused attention node runs on another device than its layer, which does
  not happen in a CPU-only run. The session record does not state the resolved value:
  llama.rn's native log is off, and the execution report has no flash-attention field.

Evidence: [`f19f34db…`](../../evidence/f19f34dbf4189b31be348987ffabbb7fdf35bfaa31aeee25e8bdab442c7d5c12/).

## Conclusion

On these three phones, with llama.rn 0.13.0-rc.7 and Llama 3.2 1B Q4_K_M on 4 CPU threads,
PocketPal's Android defaults (cpu-reference-v2) prefill 18–31% slower than cpu-reference-v1.
Their TTFT is 22–44% longer, their decode is 1.5–4.4% faster, and their load is 16–39%
slower. On the POCO F8 Ultra, flash attention off causes the whole prefill, TTFT and decode
change. Reading the weights into memory instead of mapping them only adds about 200 ms to
load. On the F8, `flash_attn_type: auto` measures the same as `on`, consistent with llama.cpp
enabling flash attention for a CPU-only run; the S23 and Pixel 9 were not measured with `on`.

## cpu-reference-v3

[`cpu-reference-v3`](../../xebenchapp/configs/cpu-reference-v3.json) is the configuration
chosen for the board's llama.cpp CPU rows. It keeps cpu-reference-v2's load settings
(`use_mmap: false`, `use_mlock: false`, repacking on), PocketPal's Android defaults, and sets
`flash_attn_type` to `auto`. On the F8, reading the weights into memory costs only load
time, about 200 ms, while flash attention off accounts for the whole 18% prefill loss of v2,
and `auto` measures the same as `on`. On the S23 and Pixel 9, v2 prefills 28–31% slower than
v1; there only the two settings together were measured. cpu-reference-v3 runs Llama 3.2 1B
Instruct Q4_K_M, Q4_0 and Q8_0 from the same pinned revision ([MODELS.md](../../MODELS.md))
with the same settings.

## Sessions

| Time (UTC) | Device | Session | Configuration | Prefill | Decode | TTFT ms | Load ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15:58:22 | Galaxy S23 | `1792339102447-0-zq4fagwot9e` | cpu-reference-v2 | 108.9 | 29.9 | 4336 | 2275 |
| 16:00:28 | Galaxy S23 | `1792339228362-0-9h2j6dbq1it` | cpu-reference-v1 | 147.6 | 28.8 | 3200 | 920 |
| 16:02:27 | Galaxy S23 | `1792339347822-0-st2qtd5ndf8` | cpu-reference-v2 | 106.3 | 28.9 | 4444 | 1232 |
| 16:04:32 | Galaxy S23 | `1792339472083-0-sayeyenlon` | cpu-reference-v1 | 144.8 | 28.1 | 3262 | 872 |
| 16:06:31 | Galaxy S23 | `1792339591736-0-dk1d1ljdaud` | cpu-reference-v2 | 105.0 | 28.2 | 4498 | 1314 |
| 16:02:47 | POCO F8 Ultra | `1792339367210-0-85zb7xkvswo` | cpu-reference-v2 | 169.5 | 49.5 | 2789 | 1288 |
| 16:04:40 | POCO F8 Ultra | `1792339480081-0-4dkuq6grhdk` | cpu-reference-v1 | 207.7 | 47.4 | 2278 | 979 |
| 16:06:30 | POCO F8 Ultra | `1792339590863-0-dgr63vkzg` | cpu-reference-v2 | 169.3 | 49.6 | 2792 | 1083 |
| 16:08:23 | POCO F8 Ultra | `1792339703733-0-muwhxz3gsm` | cpu-reference-v1 | 207.3 | 47.4 | 2280 | 907 |
| 16:10:14 | POCO F8 Ultra | `1792339814973-0-ishxv1estr` | cpu-reference-v2 | 169.1 | 49.5 | 2795 | 1106 |
| 16:03:03 | Pixel 9 | `1792339383644-0-0b6accgp90kl` | cpu-reference-v2 | 110.5 | 24.8 | 4280 | 1549 |
| 16:05:11 | Pixel 9 | `1792339511183-0-iliz0qvdxlj` | cpu-reference-v1 | 156.5 | 24.3 | 3023 | 1115 |
| 16:07:14 | Pixel 9 | `1792339634201-0-udz3yds65j` | cpu-reference-v2 | 108.3 | 24.8 | 4369 | 1547 |
| 16:09:21 | Pixel 9 | `1792339761587-0-j4o89ditrr` | cpu-reference-v1 | 156.0 | 24.3 | 3031 | 1138 |
| 16:11:24 | Pixel 9 | `1792339884211-0-kxcmzm520fg` | cpu-reference-v2 | 108.4 | 24.5 | 4364 | 1552 |

Session medians of 3 repetitions; speeds in tok/s. The factor-split sessions are listed in
their bundle.

## Notes

- The POCO F8 Ultra was locked (swipe, no PIN) when its first capture launched the app at
  15:56:55. The app started its session only after the lock was dismissed with
  `wm dismiss-keyguard`, at 16:02:47. Its recorded readiness is a single ready poll with no
  keyguard lock, and the session completed normally.
- Hashing the model before measurement warms the OS file cache, so the load times do not
  include cold storage reads.
