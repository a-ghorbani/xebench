# xebench Methodology (v1)

Implementation status: the current [CPU reference runner](docs/REFERENCE-RUNS.md)
retains repetitions and enforces the [run-condition policy](docs/RUN-CONDITIONS.md):
cooldown, bounded thermal readiness, sampled conditions, cleanup and a seeded run
order. It does not yet enforce the full fairness contract below: backend execution,
workload compatibility and quality remain unverified, so guards never pass. Records
cannot update published data. The remaining rules are tracked in #1 and #3–#6.

The fairness rules for cross-engine on-device LLM benchmarks. This is the
document a partner (Hugging Face, an engine team, a reviewer) should read first.
The implementation status above distinguishes current behavior from the intended
contract below; file references identify the corresponding components.

**Read alongside [ENGINES.md](./ENGINES.md)** — why each engine exists, where it
shines most, and (critically) whether we currently measure it there. A fairness
rule this doc omits but ENGINES.md enforces: *run each engine on its intended
path.* Benchmarking an accelerator-first engine (LiteRT, GENIE-X) only on CPU and
publishing the number is the same unfairness as one engine's Q4_K_M vs another's
Q4_0 — moved to the backend axis. Today only llama.cpp is measured at its best;
LiteRT/GENIE-X are shown on their CPU fallback, which the board must disclose.

## Rule 0 — measure in a FOREGROUND APP, never an adb-shell binary

**Hard-won on real hardware (POCO F8 Ultra, SM8850, non-rooted, 2026-07-17).**
On modern Android, `adb shell` processes run in the **`background` cpuset
cgroup**, which on this device is confined to **CPUs 0–3** and never frequency-
boosts. Measured directly: a natively-cross-compiled `llama-bench` (built with
`dotprod+i8mm`) ran with all cores pinned at **384/883 MHz** (max is 3.6/4.6 GHz)
and reported pp512 ≈ 145 tok/s — a **background-throttled** number, ~5–10× below
device capability, and noisy (±50). The device is non-rooted, so the process
**cannot** be moved to `top-app` (all cpuset writes denied) and **`taskset`
cannot override** the cgroup's CPU confinement.

Consequence: **on that device, a pure adb-shell binary could not produce a
representative performance number.** Cpuset policy is vendor-specific and was not
measured on other devices, so xebench does not rely on shell placement anywhere.
On that device, a foreground, installed app was
placed in the **`top-app`** cpuset (CPUs 0–7 + boost) and gets real clocks — so
the harness must be an **installed app running in the foreground**, screen on.
This is why xebench is an app, not a CLI. Many casually-published on-device
numbers ignore this and are silently background-throttled; measuring in-app is a
correctness requirement, not a convenience.

(This also means the RN-vs-native-C++ question is secondary: both, as an
installed foreground app, get `top-app` clocks. The decisive axis is
foreground-app vs background-binary.)

## What v1 measures

- **Engines:** llama.cpp (via `llama.rn`) and ExecuTorch (via
  `react-native-executorch`, XNNPACK backend).
- **Backend:** CPU only. Android GPU (OpenCL/Vulkan) and NPU (QNN/Hexagon) are
  explicitly **out of scope for v1** — their maturity differs by an order of
  magnitude between engines and would make a single ranking indefensible. They
  are a v2 axis, shown as "experimental," never mixed into a CPU ranking.
- **Platform:** Android first. iOS uses the same harness (both engines support
  it); it is a follow-on, not a reimplementation.
- **Model:** one model, two engine-native quantizations of it (see §"Quant").
- **Metrics:** TTFT, prefill tok/s, decode tok/s, peak/delta RAM, model load
  time; plus an optional 5-minute sustained-load phase with per-iteration decode
  tok/s and thermal state.

**Implemented today (reference runner):** llama.cpp on CPU only; the configuration
parser rejects other engines and GPU/NPU offload, and the actual backend is not
verified (`observedBackend: null`). It measures load time, TTFT and prefill/decode
throughput. It does not measure peak or delta RAM (only endpoint PSS and sampled
RSS) or the sustained phase. The ExecuTorch, LiteRT and GENIE-X adapters, and
GPU/NPU options in the llama.cpp adapter, are experimental and outside the
reference protocol.

## The five fairness rules

### 1. Same bytes in — identical prompt, no per-engine templating
Both engines receive the **same pre-templated raw string** (Llama 3.2 chat
template applied once, by us — not by either engine). llama.rn gets it via
`completion({ prompt })`, ExecuTorch via `forward()`. Neither applies its own
chat wrapper, so tokenizer input is byte-identical. Each engine's own prompt
token count is recorded; a mismatch is flagged, not silently averaged.
→ `src/harness/prompts.ts`, both adapters pass `cfg.prompt` unchanged.

### 2. "Engine + its own best mobile quant" — never claim bit-equivalence
There is no bit-identical INT4 across these engines. We compare each engine
running the quantization its own toolchain produces for mobile
(llama.cpp `Q4_K_M`, ExecuTorch `SpinQuant 4-bit`), and we treat the
**quantization scheme as a first-class, always-visible column** — never a
footnote. A row that hides its quant is not publishable.
→ `quant` is required on every `EngineBenchmarkRow`; surfaced in every UI cell.

**Consequence, stated plainly:** an engine can "win" decode tok/s by quantizing
more aggressively at a quality cost. v1 does not yet publish a quality-delta
column (perplexity / KL-divergence vs FP16); until it does, **cross-engine
tok/s is indicative, not a quality-adjusted verdict**, and the UI says so.
Quality parity is the top v2 item.

### 3. Throughput provenance is recorded per number
How each tok/s was obtained is a first-class field (`MeasureMethod`):
- `engine-timings` — the engine's own counters (llama.cpp exposes prefill/decode
  ms and tok/s directly; this is the gold standard).
- `ttft-derived` / `callback-derived` — ExecuTorch exposes no internal counters,
  so prefill is derived from TTFT and decode from JS-side per-token callback
  timestamps. This includes one decode step + a callback hop, which biases
  **against** ExecuTorch (conservative), and it is labeled as such on the row.

We do **not** pretend a derived number and an engine-internal number are the
same kind of measurement. TTFT itself is measured at the identical point for
both engines (request start → first token callback), so TTFT *is*
apples-to-apples even where tok/s provenance differs.
→ `src/harness/types.ts` `MeasureMethod`; both adapters set it honestly.

### 4. Repetition, cold-run definition, and reported statistic
- **n = 3 cold runs**, report the **median** (+ IQR in the raw record). Means
  are not reported — a single thermal event skews a mean badly.
- **"Cold run" is defined and disclosed:** a fresh engine+model instance
  (`load() → benchOnce() → release()`) inside a warm app process — *not* an app
  restart. This is stated on every record (`protocol.coldDefinition`), because
  "cold" is otherwise ambiguous and the difference is real.
- **30 s cooldown, then bounded thermal readiness, before every repetition**
  (the first repetition of each run cools down after model hashing). Readiness
  timeout skips the
  repetition; it is never a pass. Each record identifies the measurement as the
  first request on a fresh instance and carries a per-process ID, prior model loads
  in that process and process uptime. Warm requests and per-repetition process
  restarts are not measured. No GC hint is issued.
- **Run order** is a seeded shuffle; the seed and resulting order are recorded.
→ `xebenchapp/src/harness/session.ts`, `readiness.ts`, `order.ts`; rule-by-rule
links to tests are in [RUN-CONDITIONS.md](docs/RUN-CONDITIONS.md#rule-traceability).

### 5. Run conditions are gated, sampled and flagged — no laundered numbers
The current runner enforces [`run-conditions-v2`](docs/RUN-CONDITIONS.md): a
repetition starts only after measured readiness (thermal status, battery
temperature, headroom when the API returns a value, battery, power saving, screen,
focus, lock),
then native samples are taken once per second during load and inference. Endpoint
or sampled failures and focus interruptions are flagged (`guardsPassed=false`);
flagged repetitions stay in the summary rather than being gated out.
Unknown evidence cannot pass; a condition pass does not establish backend execution,
so guards stay unverified. Charging is a separate publication-eligibility policy,
`charging-policy-v3`: USB tethering is allowed, but a repetition is eligible only if
the battery status was `FULL` or `NOT_CHARGING` (e.g. at a charge limit) at every
snapshot and sample. `current_now` is recorded only as a diagnostic. Charging is
`violated`, running on battery (`DISCHARGING`) is a known but ineligible
`on-battery` state in the tethered lab, and unknown is never eligible. Readiness
waits, within the bound, for charging to stop. The activity keeps the screen on
while visible. Historical records are not retroactively certified: legacy rows are
"charging state unknown, effect not ruled out at 3%" and are not ranked against v3
rows where differences are below about 3% plus the observed CI ([charging A/B decision](docs/experiments/charging-ab.md#decision-2026-10-17)).
→ `xebenchapp/src/harness/conditions.ts`, `readiness.ts`, `session.ts`;
`BenchConditionsModule.kt`; tests listed in
[rule traceability](docs/RUN-CONDITIONS.md#rule-traceability).

## Cold-start is not the whole story (why the sustained phase exists)
A single cold `pp512/tg128` number materially misrepresents sustained use:
published measurement shows an iPhone 16 Pro losing ~44% decode throughput
within two continuous iterations, and Android devices hitting OS-enforced GPU
frequency floors (arXiv:2603.23640). The optional sustained phase loops decode
for 5 minutes and reports first-minute vs last-minute median decode tok/s and a
degradation %, with thermal state per iteration. When present it is shown as its
own column, never blended into the cold number. The reference runner does not
implement this phase yet.

## GPU backend reality on stock Android (measured 2026-07-18, corrected 2026-10-17)

On 2026-07-18, LiteRT-LM's `Backend.GPU()` failed on **both** a Tensor G4 (Pixel 9)
and an Adreno 740 (Galaxy S23) with `Can not find OpenCL library on this device`.
We concluded that a stock, non-rooted Android app cannot load the vendor
`libOpenCL.so`. **That conclusion was wrong** for the devices we re-tested. On
the Galaxy S23 and POCO F8 Ultra, the app loaded `libOpenCL.so` once its
manifest declared it with
`<uses-native-library android:name="libOpenCL.so" android:required="false" />`.
The LiteRT-LM Kotlin guide has required declaring both `libOpenCL.so` and
`libvndksupport.so` since 0.14.0 (LiteRT-LM#1860). We declared both; on both
phones the native loader registered only `libOpenCL.so`, so that is the
declaration that mattered there. Our July app declared neither, so on those
devices the failure was our bug, not a platform limit.
In 2026-10-17 spikes, ML Drift GPU ran on the S23 and the F8 Ultra with the
declarations, and a control build without them failed again. We have not
re-tested the Pixel 9 (Tensor G4), so whether it exposes `libOpenCL.so` to apps
is still unknown.

What still holds: "GPU support" in an engine's docs depends on app-side
packaging and manifest requirements that are easy to miss. Check the engine's
integration guide before attributing a failure to the platform, and record the
exact failure per device rather than assuming GPU works. v1 remains CPU-only (see
§"What v1 measures"), and GPU is a later axis.

## Energy
Android energy is **best-effort** via `BatteryManager` and is labeled as such;
some OEMs misreport under load. iOS on-device energy is **not reliably
measurable without external hardware** and is not published as authoritative.
v1 does not gate any ranking on energy. The reference runner records battery
percentage only as a condition, not energy use.

## Reproducibility contract
- The harness is open; every published row links back to its raw JSONL record.
- Each row is stamped with **engine version** (llama.rn / rn-executorch
  version), model file, and quant — because a driver or engine update can move
  numbers release-over-release, an unstamped number is meaningless over time.
- `scripts/aggregate.mjs` is the only path from raw records to published rows;
  it invents nothing.

## Explicitly not claimed in v1
1. That the two quantizations are quality-equivalent (no quality-delta column yet).
2. Any GPU/NPU ranking (CPU only).
3. That derived tok/s (ExecuTorch) and engine-internal tok/s (llama.cpp) are the
   same class of measurement — they are labeled differently on purpose.
4. Authoritative energy numbers.

These are the honest edges. Naming them is what makes the numbers we *do*
publish trustworthy.
