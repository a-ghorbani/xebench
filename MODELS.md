# Model assets for xebench

## Reference model

The CPU reference configurations
([`cpu-reference-v2.json`](xebenchapp/configs/cpu-reference-v2.json), the default, and
[`cpu-reference-v1.json`](xebenchapp/configs/cpu-reference-v1.json)) run exactly one
artifact, the Q4_K_M file below.
[`cpu-reference-v3.json`](xebenchapp/configs/cpu-reference-v3.json) runs it and two more
quantizations of the same model ([Q4_0 and Q8_0](#q4_0-and-q8_0-cpu-reference-v3)). The
expected values are committed in
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

### Q4_0 and Q8_0 (cpu-reference-v3)

cpu-reference-v3 also runs two other quantizations from the same repository, at the same
pinned revision `067b946cf014b7c697f3654f621d577a3e3afd1c`. The same license and download
rules apply. Each SHA-256 is the Hugging Face LFS oid at that revision.

| File | Quant | Size | SHA-256 |
|---|---|---|---|
| `Llama-3.2-1B-Instruct-Q4_0.gguf` | Q4_0 | 773025920 bytes | `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8` |
| `Llama-3.2-1B-Instruct-Q8_0.gguf` | Q8_0 | 1321083008 bytes | `432f310a77f4650a88d0fd59ecdd7cebed8d684bafea53cbff0473542964f0c3` |

The Q4_0 file is the same pin as the legacy-row comparator in
[`configs/models/experimental-models.json`](configs/models/experimental-models.json) (see
below). Download and stage each file by name:

```bash
hf download bartowski/Llama-3.2-1B-Instruct-GGUF Llama-3.2-1B-Instruct-Q8_0.gguf \
  --revision 067b946cf014b7c697f3654f621d577a3e3afd1c --local-dir models
python3 scripts/stage_model.py --serial <serial> models/Llama-3.2-1B-Instruct-Q8_0.gguf
python3 scripts/stage_model.py --model-id llama32-1b-instruct-q8_0 --print-download
```

`stage_model.py` selects the manifest entry the local file names (or `--model-id`). Without
either, it stages the Q4_K_M file.

### Qwen2.5 1.5B and Gemma 3 1B (board configurations)

[`cpu-reference-v3-qwen25.json`](xebenchapp/configs/cpu-reference-v3-qwen25.json),
[`cpu-reference-v3-gemma3.json`](xebenchapp/configs/cpu-reference-v3-gemma3.json) and the
llama.cpp accelerator configuration
[`llamacpp-qwen25-opencl-v1.json`](xebenchapp/configs/llamacpp-qwen25-opencl-v1.json) run these
files with cpu-reference-v3's settings. They are board models, not the second reference model of
issue #7:

| File | Quant label | Source | Size | SHA-256 | License |
|---|---|---|---|---|---|
| `Qwen2.5-1.5B-Instruct-Q8_0.gguf` | Q8_0 | [`bartowski/Qwen2.5-1.5B-Instruct-GGUF`](https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF) at `9eadc66189c7641e1ddd226b8267a9119b2ce2d4` | 1646573312 bytes | `7185d306cf45956c8c017cd0d3b05ecc6bc18b3ea8eb5c240dce40e87563db7f` | [Apache License 2.0](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/blob/main/LICENSE) |
| `gemma-3-1b-it-q4_0.gguf` | Q4_0 (QAT) | `google/gemma-3-1b-it-qat-q4_0-gguf` (gated), [pinned below](#litert-accelerator-experiment-accelerator-v1) | 1003541152 bytes | `95e5b8d891cd6a794f66c2a6fb59a41e9562b4660560b854274eceffb628b22a` | Gemma Terms of Use |

The pinned Qwen2.5 bytes are the lab's copy of this file (SHA-256 checked on the lab host,
`sha256sum`), which equals the Hugging Face LFS oid at the pinned revision. Its pin is in
[`configs/models/reference-models.json`](configs/models/reference-models.json); the Gemma pin
stays in [`configs/models/experimental-models.json`](configs/models/experimental-models.json).
Stage them by name:

```bash
hf download bartowski/Qwen2.5-1.5B-Instruct-GGUF Qwen2.5-1.5B-Instruct-Q8_0.gguf \
  --revision 9eadc66189c7641e1ddd226b8267a9119b2ce2d4 --local-dir models
python3 scripts/stage_model.py --serial <serial> models/Qwen2.5-1.5B-Instruct-Q8_0.gguf
python3 scripts/stage_model.py --manifest configs/models/experimental-models.json --serial <serial> \
  models/gemma-3-1b-it-q4_0.gguf
```

### License

The Llama 3.2 1B weights (Q4_K_M, Q4_0 and Q8_0) are under the Llama 3.2 Community License
and Meta's Acceptable Use Policy; the Qwen2.5 and Gemma 3 files carry the licenses in their
table above. Meta's upstream repository is gated on Hugging Face. bartowski's GGUF
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
**not** part of the reference path, and results from them are not reference results.
The ExecuTorch files in this table have no pinned revision or expected digest, and
`stage_model.py` does not stage them.

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
  hash at that revision. It is listed in the same manifest, and with the same values in
  `configs/models/reference-models.json` for cpu-reference-v3.
- **LiteRT-LM files, pinned.** See [LiteRT-LM files](#litert-lm-files) below; the same pins
  serve the LiteRT-LM board configurations.

### LiteRT-LM files

The LiteRT-LM board configurations
([`litertlm-gemma3-int4-cpu-v1.json`](xebenchapp/configs/litertlm-gemma3-int4-cpu-v1.json),
[`litertlm-qwen25-q8-cpu-v1.json`](xebenchapp/configs/litertlm-qwen25-q8-cpu-v1.json),
[`litertlm-gemma3-sm8850-npu-v1.json`](xebenchapp/configs/litertlm-gemma3-sm8850-npu-v1.json))
and the accelerator experiment run these `.litertlm` files. Each configuration pins the file's
SHA-256 as `modelSha256`; a different file is recorded unmeasured with
`stopReason: model-hash-mismatch`. `configs/models/experimental-models.json` lists them under
`pinnedArtifacts` with their source. Each SHA-256 is the Hugging Face LFS oid of the file at the
pinned revision; the Gemma repository is gated, so its oids were read with an authenticated
client. The Qwen2.5 SHA-256 also equals `sha256sum` of a copy downloaded from that revision.

| File | Source | Size | SHA-256 | Used for |
|---|---|---|---|---|
| `gemma3-1b-it-int4.litertlm` | [`litert-community/Gemma3-1B-IT`](https://huggingface.co/litert-community/Gemma3-1B-IT) (gated) at `d448835d84411a08f577e25f3afebe528402d73b` | 584417280 bytes | `1325ae366d31950f137c9c357b9fa89448b176d76998180c08ceaca78bba98be` | CPU (board), CPU/GPU (experiment) |
| `Gemma3-1B-IT_q4_ekv1280_sm8850.litertlm` | same repository and revision | 693747712 bytes | `fda5dca0e8c1c6f65ca5625c326ff79920c7eb82625a0c6515ae4f5711957b1f` | NPU on the POCO F8 Ultra (SM8850) |
| `Gemma3-1B-IT_sm8550.litertlm` | `Gemma3-1B-IT_q4_ekv1280_sm8550.litertlm`, same repository and revision, staged under the shorter name | 690143232 bytes | `29426fd343eaeac9d54d67ca2bf72fce8c1f0cce947757e5e9c8009bcf5e72af` | NPU bug reproduction on the Galaxy S23 |
| `Qwen2.5-1.5B-Instruct_multi-prefill-seq_q8_ekv4096.litertlm` | [`litert-community/Qwen2.5-1.5B-Instruct`](https://huggingface.co/litert-community/Qwen2.5-1.5B-Instruct) at `5068b3fd9788a4ed5633dc868687f778143b73f3` | 1597922856 bytes | `c4a9b7289365ab80fe599b844b77ad253eb3c53fb0e5d963d699146a6f01255e` | CPU (board) |

Each pinned revision is the repository's `main` when it was pinned; if `main` changes later, the
pin still names these bytes. The Qwen2.5 file at its pinned revision carries LiteRT-LM 0.18.0
metadata: a chat template that reads message content as a list of parts, and minimum runtime
0.18.0. The repository commit that set this metadata
(`5e6e7a58d03777898b31330a15d2e0a5a9107de3`) states that the weights are unchanged from the file
of the same name at `19edb84c69a0212f29a6ef17ba0d6f278b6a1614` (1597931520 bytes,
`faa60663b333290c1496c499828b21d3e3254a788cacd8cce917ce0f761a2dc9`). That file's chat template
reads message content as a plain string, which LiteRT-LM 0.18.0 does not pass, so xebench does not
use it. The Gemma files are under the Gemma Terms of Use; the Qwen2.5 file is under the
[Apache License 2.0](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/blob/main/LICENSE).

`stage_model.py` stages GGUF files only. Download and push a `.litertlm` file by name, then
check its SHA-256 on the phone:

```bash
hf download litert-community/Qwen2.5-1.5B-Instruct \
  Qwen2.5-1.5B-Instruct_multi-prefill-seq_q8_ekv4096.litertlm \
  --revision 5068b3fd9788a4ed5633dc868687f778143b73f3 --local-dir models
D=/sdcard/Android/data/com.xebenchapp/files
adb -s <serial> push models/Qwen2.5-1.5B-Instruct_multi-prefill-seq_q8_ekv4096.litertlm $D/
adb -s <serial> shell sha256sum $D/Qwen2.5-1.5B-Instruct_multi-prefill-seq_q8_ekv4096.litertlm
```

The ExecuTorch adapter takes absolute paths in its options
(`ExecutorchAdapterOptions.modelSource`). To stage
the ExecuTorch files manually, push them to the app's external files directory and point the
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
