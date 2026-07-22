# Model assets for xebench v1

v1 benchmarks **one model** in each engine's mobile-native quantization. The
whole comparison hinges on both being the *same base model* so tokenizer input
and capability match; only the quant scheme differs (that's the point).

## Chosen model: Llama 3.2 1B Instruct

Picked because it exists in clean, published exports for **both** engines with no
conversion work — avoiding the unsupported-op quagmire for a first run.

| Engine | File | Quant | Source |
|---|---|---|---|
| llama.cpp (llama.rn) | `Llama-3.2-1B-Instruct-Q4_K_M.gguf` | Q4_K_M | HF: `bartowski/Llama-3.2-1B-Instruct-GGUF` (or unsloth) |
| ExecuTorch (rn-executorch) | `llama-3.2-1B-spinquant.pte` | SpinQuant 4-bit | HF: `software-mansion/react-native-executorch-llama-3.2` |
| both | `tokenizer.json`, `tokenizer_config.json` | — | from the same Llama 3.2 1B repo (HF tokenizers format) |

**Tokenizer must be the same** for both engines (it is — both are Llama 3.2 1B),
so prompt token counts match and per-token math is comparable. The harness
records each engine's own prompt-token count and flags any mismatch.

## Staging on device

```bash
adb shell mkdir -p /data/local/tmp/xebench
adb push Llama-3.2-1B-Instruct-Q4_K_M.gguf /data/local/tmp/xebench/
adb push llama-3.2-1B-spinquant.pte        /data/local/tmp/xebench/
adb push tokenizer.json                     /data/local/tmp/xebench/
adb push tokenizer_config.json              /data/local/tmp/xebench/
```

Paths are wired in the adapter options (`LlamaRnAdapterOptions.modelPath`,
`ExecutorchAdapterOptions.modelSource`). For ExecuTorch, `react-native-executorch`
can also fetch from an HF URL and cache — but for a controlled benchmark, stage
the exact file locally so the measured `modelFileSizeMb` is pinned.

## Notes on fairness

- These are **different quantization schemes by design** (Q4_K_M vs SpinQuant) —
  that's the honest "engine + its own best mobile quant" comparison. The quant
  label rides every published row.
- A quality-delta measurement (perplexity / KL-div vs FP16) is **not** part of
  v1 and is the top v2 addition; until then tok/s is speed-only, and the UI says
  so.
- v2 adds a second base model (e.g. Gemma 3 1B, which also has GGUF + LiteRT-LM
  + ExecuTorch exports) once the single-model loop is proven end to end.
