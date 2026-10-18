# Model assets for xebench

## Reference model

The CPU reference configuration
([`cpu-reference-v1.json`](xebenchapp/configs/cpu-reference-v1.json)) runs exactly one
artifact. Its expected values are committed in
[`configs/models/reference-models.json`](configs/models/reference-models.json), and
[`scripts/stage_model.py`](scripts/stage_model.py) enforces them.

| | |
|---|---|
| Model | Llama 3.2 1B Instruct, Q4_K_M GGUF |
| Source (only this one) | Hugging Face [`bartowski/Llama-3.2-1B-Instruct-GGUF`](https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF) |
| Pinned revision | `067b946cf014b7c697f3654f621d577a3e3afd1c` |
| File | `Llama-3.2-1B-Instruct-Q4_K_M.gguf` |
| Size | 807694464 bytes |
| SHA-256 | `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83` |
| Base model | [`meta-llama/Llama-3.2-1B-Instruct`](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) |
| License | [Llama 3.2 Community License](https://www.llama.com/llama3_2/license/) |

Use only this source and revision. Other GGUF exports of the same model (for
example unsloth's, or a local re-quantization) are different bytes. Those are a
different artifact, even when the file name and quant label are the same.

These are the bytes of every recorded xebench run so far. All local raw records
that carry an artifact digest (12 sessions, 2026-09-05 to 2026-09-07) report this
SHA-256 and size. The Hugging Face LFS digest of the file is the same at the pinned
revision and at the commit that uploaded it (`9971ce1bbba2f8b55de026a783323a808b3eeedb`,
2024-09-25). The pinned revision was the repository's `main` when it was pinned. If
`main` changes later, the pin still names these bytes.

### License

The weights are under the Llama 3.2 Community License and Meta's Acceptable Use
Policy. Meta's upstream repository is gated on Hugging Face. bartowski's GGUF
repository is not gated, but the same license still applies. Each user must read
and accept Meta's terms themselves before downloading. xebench never rehosts or
redistributes the model: it is not in this repository, its releases or the APK,
and `stage_model.py` never downloads it.

### Download

After you accept the terms, download the pinned revision into `models/` (this
directory is git-ignored):

```bash
hf download bartowski/Llama-3.2-1B-Instruct-GGUF Llama-3.2-1B-Instruct-Q4_K_M.gguf \
  --revision 067b946cf014b7c697f3654f621d577a3e3afd1c --local-dir models
# or, without the Hugging Face CLI:
curl -L --fail --create-dirs -o models/Llama-3.2-1B-Instruct-Q4_K_M.gguf \
  https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/067b946cf014b7c697f3654f621d577a3e3afd1c/Llama-3.2-1B-Instruct-Q4_K_M.gguf
```

`python3 scripts/stage_model.py --print-download` prints the same commands from the
manifest.

### Staging on device

The app loads models by basename from its external files directory,
`/sdcard/Android/data/com.xebenchapp/files/`. Install the app first, then stage:

```bash
python3 scripts/stage_model.py --serial <serial>            # default: models/Llama-3.2-1B-Instruct-Q4_K_M.gguf
python3 scripts/stage_model.py --serial <serial> path/to/Llama-3.2-1B-Instruct-Q4_K_M.gguf
```

The script refuses to continue in these cases:

- the local file name, size or SHA-256 does not match the manifest;
- the device is unavailable (`adb -s <serial> get-state` is not `device`), or the app
  is not installed;
- after `adb -s <serial> push` to a temporary `.partial` name, the `sha256sum` of the
  pushed copy on the device does not match. The script deletes the partial copy and
  does not rename it into place. Any older copy under the final name stays as it was,
  and the error message reports it.

If the device already holds a verified copy, the script does not push again.

Each session also records the SHA-256 and size of the model file that was loaded.
The app-side check of that value against the configuration is tracked separately.

### Planned: a second, ungated reference model

A second reference model, available without gating, is planned for independent
reproduction (issue #7). It is not chosen yet.

## Non-reference models

The models below are used only by the experimental engine adapters. They are
**not** part of the reference path. They have no pinned revision or expected
digest, and `stage_model.py` does not stage them. Results from them are not
reference results.

| Engine | File | Quant | Source |
|---|---|---|---|
| ExecuTorch (rn-executorch) | `llama-3.2-1B-spinquant.pte` | SpinQuant 4-bit | HF: `software-mansion/react-native-executorch-llama-3.2` |
| ExecuTorch | `tokenizer.json`, `tokenizer_config.json` | — | from the same Llama 3.2 1B repo (HF tokenizers format) |

### LiteRT accelerator experiment (accelerator-v1)

[docs/experiments/litert-accelerators.md](docs/experiments/litert-accelerators.md) compares
LiteRT-LM with llama.cpp on Gemma 3 1B. These are not reference models either.

- **llama.cpp comparator, pinned.** `gemma-3-1b-it-q4_0.gguf` comes from the gated HF repo
  `google/gemma-3-1b-it-qat-q4_0-gguf` at revision `d1be121d36172a4b0b964657e2ee859d61138593`,
  1003541152 bytes, sha256 `95e5b8d891cd6a794f66c2a6fb59a41e9562b4660560b854274eceffb628b22a`.
  It is licensed under the Gemma Terms of Use. The pin lives in
  [`configs/models/experimental-models.json`](configs/models/experimental-models.json). The
  SHA-256 is the huggingface_hub download ETag of our copy at that revision; the gated repository
  does not show LFS hashes without authentication. Stage it the same way as the reference model:
  `python3 scripts/stage_model.py --manifest configs/models/experimental-models.json --serial <serial> <file>`.
- **Legacy-row comparator, pinned.** `Llama-3.2-1B-Instruct-Q4_0.gguf` (the Q4_0 file of the
  legacy llama.cpp Hexagon board row) comes from `bartowski/Llama-3.2-1B-Instruct-GGUF` at
  revision `067b946cf014b7c697f3654f621d577a3e3afd1c`, the same revision as the reference
  Q4_K_M: 773025920 bytes, sha256
  `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`, which equals the HF LFS
  hash at that revision. It is listed in the same manifest.
- **LiteRT-LM artifacts: bytes pinned, source revision unknown.** The accelerator
  configurations pin each file's SHA-256 as `modelSha256` (a different file is recorded
  unmeasured with `stopReason: model-hash-mismatch`). The Hugging Face source revisions these
  copies were downloaded from are not recorded:

  | File | Used for | SHA-256 |
  |---|---|---|
  | `gemma3-1b-it-int4.litertlm` | LiteRT-LM CPU/GPU | `1325ae366d31950f137c9c357b9fa89448b176d76998180c08ceaca78bba98be` |
  | `Gemma3-1B-IT_q4_ekv1280_sm8850.litertlm` | LiteRT-LM NPU, F8 Ultra | `fda5dca0e8c1c6f65ca5625c326ff79920c7eb82625a0c6515ae4f5711957b1f` |
  | `Gemma3-1B-IT_sm8550.litertlm` | LiteRT-LM NPU, S23 (bug reproduction) | `29426fd343eaeac9d54d67ca2bf72fce8c1f0cce947757e5e9c8009bcf5e72af` |

  The S23 file has the size (690143232 bytes) of `Gemma3-1B-IT_q4_ekv1280_sm8550.litertlm` in
  the `litert-community/Gemma3-1B-IT` tree; it is staged under the shorter name.
  `configs/models/experimental-models.json` lists the three files under `pinnedArtifacts`
  with `"source": "source revision unknown"`. `stage_model.py` stages only `models`
  entries, which have a pinned revision.

The experimental adapters take absolute paths in their options
(`LlamaRnAdapterOptions.modelPath`, `ExecutorchAdapterOptions.modelSource`). To stage
these files manually, push them to the app's external files directory and point the
options there:

```bash
adb -s <serial> push llama-3.2-1B-spinquant.pte tokenizer.json tokenizer_config.json \
  /sdcard/Android/data/com.xebenchapp/files/
```

Both engines use the Llama 3.2 1B tokenizer, so prompt token counts match. The
harness records each engine's own prompt-token count and flags any mismatch.

## Notes on fairness

- Q4_K_M and SpinQuant are **different quantization schemes by design**. This is
  the honest comparison of each engine with its own best mobile quant. Every
  published row carries the quant label.
- A quality-delta measurement (perplexity or KL divergence against FP16) is **not**
  part of v1. It is the top v2 addition. Until then, tok/s measures speed only, and
  the UI says so.
- v2 adds a second base model (for example Gemma 3 1B, which also has GGUF,
  LiteRT-LM and ExecuTorch exports) after the single-model loop is proven end to end.
