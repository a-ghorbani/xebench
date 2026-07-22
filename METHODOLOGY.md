# xebench Methodology (v1)

The fairness rules for cross-engine on-device LLM benchmarks. This is the
document a partner (Hugging Face, an engine team, a reviewer) should read first.
Every rule here is enforced by the harness code, not just asserted — file
references are given so claims are checkable.

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

Consequence: **a pure adb-shell binary cannot produce a representative
performance number on a non-rooted phone.** A foreground, installed app is
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
- **30 s cooldown + GC hint between runs**; process-reuse is recorded
  (`processReused`) because it contaminates lifetime VmHWM (PSS-delta stays
  valid).
→ `src/harness/protocol.ts`.

### 5. Run conditions are gated and recorded — no laundered numbers
Before a session the harness records SoC, RAM, OS, engine version, thermal
status/headroom, battery %, power-save, charging state. A run that starts
below 50% battery or in power-save mode is **flagged** (`guardsPassed=false`,
surfaced in `asStated`), not silently published. Screen is kept on to avoid
doze. Every published row is traceable to a raw JSONL record that carries these
conditions.
→ `src/harness/types.ts` `ThermalSnapshot`/guards; `src/native/BenchProbeModule.kt`.

## Cold-start is not the whole story (why the sustained phase exists)
A single cold `pp512/tg128` number materially misrepresents sustained use:
published measurement shows an iPhone 16 Pro losing ~44% decode throughput
within two continuous iterations, and Android devices hitting OS-enforced GPU
frequency floors (arXiv:2603.23640). The optional sustained phase loops decode
for 5 minutes and reports first-minute vs last-minute median decode tok/s and a
degradation %, with thermal state per iteration. When present it is shown as its
own column, never blended into the cold number.

## GPU backend reality on stock Android (measured 2026-07-18)

LiteRT-LM's `Backend.GPU()` failed on **both** a Tensor G4 (Pixel 9) and an Adreno
740 (Galaxy S23) with `Can not find OpenCL library on this device`. On stock,
non-rooted Android an app generally cannot `dlopen` the vendor `libOpenCL.so`
(linker-namespace restriction) — so an engine's OpenCL GPU path won't initialize
in a normal app without bundling an OpenCL loader or being on a device that
exposes it. This is why v1 is CPU-only and GPU is a later, device-specific axis:
"GPU support" in an engine's docs does not mean it runs from an installed app on a
given device. Document the exact failure per device rather than assuming GPU works.

## Energy
Android energy is **best-effort** via `BatteryManager` and is labeled as such;
some OEMs misreport under load. iOS on-device energy is **not reliably
measurable without external hardware** and is not published as authoritative.
v1 does not gate any ranking on energy.

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
