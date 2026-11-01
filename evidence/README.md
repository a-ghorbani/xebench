# Evidence bundles

Each directory here is one sanitized evidence bundle written by
[`scripts/export_evidence.py`](../scripts/export_evidence.py). Bundles are the
public evidence behind measured results: a verified row's contract 1.1
`evidence` names a bundle, and the experiment notes cite them.

## Layout

```
evidence/<bundleSha256>/
  manifest.json               lists every other file with its SHA-256 and size
  captures/<captureId>.json   one per capture: status, counts, installed APK hashes
  sessions/<runId>.json       one per session: configuration, build, repetitions, conditions
```

`bundleSha256` is the SHA-256 of the exact bytes of `manifest.json`, and the
directory is named by it. Every file is canonical JSON (sorted keys, ASCII only,
one trailing LF), so the same captures always export to the same bytes. The
format and the list of exported and never-exported fields are in
[docs/REFERENCE-RUNS.md](../docs/REFERENCE-RUNS.md#evidence-export).

The stable URL of a bundle is

```
https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence/<bundleSha256>/manifest.json
```

and each other file is served next to it under the path the manifest lists. This
is the `url` that `export_evidence.py export --base-url
https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence` prints for a
row's `evidence`.

## Verify a bundle

From a checkout:

```sh
sha=<bundleSha256>
sha256sum evidence/$sha/manifest.json      # must print $sha
python3 scripts/export_evidence.py verify evidence/$sha --sha256 $sha
python3 scripts/export_evidence.py verify evidence/$sha --sha256 $sha --session <sessionId>
```

`verify` checks the manifest hash, every file's checksum and size, that no file is
unlisted or a symlink, canonical form, field allowlists, the privacy screen, and
recomputes every summary from the repetitions. It exits 1 on any failure. When you
download the files instead, hash them as served; never re-serialize the JSON.

CI ([`evidence-bundles.yml`](../.github/workflows/evidence-bundles.yml)) checks,
for every directory here, that its name equals `sha256(manifest.json)` and that
`verify` passes, and refuses pull requests and pushes that modify or remove a published
bundle file.

## Rules

- **Bundles are immutable.** Never edit, re-format or re-encode a file here; any
  change breaks its hash. A new export is a new bundle under a new hash, and the
  old one stays so that existing links keep working.
- Git never normalises these files (`evidence/** -text` in
  [`.gitattributes`](../.gitattributes)), because the hash is over exact bytes.
- **Raw captures stay private by design.** Capture directories, raw session
  records, logcat, error logs, device serials, host paths and signing-certificate
  fingerprints are never exported. Only the allowlisted fields in a bundle are
  public.
- A bundle being here does not make its sessions eligible for publication on the
  board. Publication is decided per row.

## Bundles

| Bundle | Sessions | Devices | Cited by |
|---|---|---|---|
| [`5faa910117f3…`](5faa910117f3edd716afa22857469afd8197a4d528a47cc14a521ffdd02978e0/) | 6 | POCO F8 Ultra | Board rows (`cpu-reference-v3`: Q4_K_M, Q4_0, Q8_0), [#4](https://github.com/a-ghorbani/xebench/issues/4) |
| [`6c0e288a6139…`](6c0e288a61393753dcfdfec590bca6d7b651e17182c9370b9cecc0ead8b39b41/) | 6 | Galaxy S23 | Board rows (`cpu-reference-v3`: Q4_K_M, Q4_0, Q8_0), [#4](https://github.com/a-ghorbani/xebench/issues/4) |
| [`590b6fb6cf8b…`](590b6fb6cf8b6fd41c140826cfae2df2e97da5a33bcbb2f4c566bce3a1d978c3/) | 6 | Pixel 9 | Board rows (`cpu-reference-v3`: Q4_K_M, Q4_0, Q8_0), [#4](https://github.com/a-ghorbani/xebench/issues/4) |
| [`6ad4263caa8f…`](6ad4263caa8fd381a179970586ce0e307b25eee5993feb3c47ec5c017121cf65/) | 10 | POCO F8 Ultra | Board rows (`cpu-reference-v3-qwen25`: Qwen2.5 1.5B Q8_0 CPU; `cpu-reference-v3-gemma3`: Gemma 3 1B Q4_0 (QAT) CPU; `llamacpp-qwen25-opencl-v1`: Qwen2.5 1.5B Q8_0 GPU; `llamacpp-llama32-q4km-hexagon-v1`: Llama 3.2 1B Q4_K_M NPU), [#5](https://github.com/a-ghorbani/xebench/issues/5) |
| [`e94cd4e4c7b7…`](e94cd4e4c7b77550a7f654a57f3c65d8e9c58067ad109d4516f6c3fb3a6ee17e/) | 5 | Galaxy S23 | Board row (`cpu-reference-v3-gemma3`: Gemma 3 1B Q4_0 (QAT) CPU); history (`cpu-reference-v3-qwen25`: Qwen2.5 1.5B Q8_0 CPU, setup retired), [#5](https://github.com/a-ghorbani/xebench/issues/5) |
| [`5ef63e7816ae…`](5ef63e7816ae10ae6e30054b63a5fca7370426b5d1c51a83fd40d80a7dd54dd1/) | 2 | Pixel 9 | Board row (`cpu-reference-v3-qwen25`: Qwen2.5 1.5B Q8_0 CPU), [#5](https://github.com/a-ghorbani/xebench/issues/5) |
| [`019da4ad8c6d…`](019da4ad8c6dc3cb843f07d5b5898ea78a603ad156ab95bc7c545e83770860d6/) | 4 | POCO F8 Ultra | Board rows (`litertlm-qwen25-q8-cpu-v2`: Qwen2.5 1.5B Q8 CPU; `litertlm-gemma3-int4-cpu-v2`: Gemma 3 1B int4 CPU; `litertlm-gemma3-sm8850-npu-v2`: Gemma 3 1B int4 NPU); history (the NPU configuration's CPU reference run), [#9](https://github.com/a-ghorbani/xebench/issues/9) |
| [`a351d335a03b…`](a351d335a03be1c905ce659a9e183259cac6fb2784b592e9d4cfde11ec862beb/) | 2 | Galaxy S23 | Board rows (`litertlm-gemma3-int4-cpu-v2`: Gemma 3 1B int4 CPU; `litertlm-qwen25-q8-cpu-v2`: Qwen2.5 1.5B Q8 CPU), [#9](https://github.com/a-ghorbani/xebench/issues/9) |
| [`54762f3d9247…`](54762f3d924751b143a85a44a1c4dcabba625fa060175ff11f12027d75c9de6b/) | 1 | Pixel 9 | Board row (`litertlm-qwen25-q8-cpu-v2`: Qwen2.5 1.5B Q8 CPU), [#9](https://github.com/a-ghorbani/xebench/issues/9) |
| [`24e10692e008…`](24e10692e008c93619d66eabf3e6fa09d741c6873a7a83d018ca161b7b9e4229/) | 4 | POCO F8 Ultra | History (`litertlm-*-v1`: the same runs without the fixed decode length), [#9](https://github.com/a-ghorbani/xebench/issues/9) |
| [`61344100ea03…`](61344100ea038117d311aebccdd701b9d90283af7797f05ca647d1c7febbeb46/) | 2 | Galaxy S23 | History (`litertlm-*-v1`), [#9](https://github.com/a-ghorbani/xebench/issues/9) |
| [`a3ef19fae891…`](a3ef19fae8913867c1e131f53de4327bac4745896ccc85a583e9a17f97078479/) | 5 | POCO F8 Ultra | [cpu-reference-v2 vs v1](../docs/experiments/reference-config-v2.md#result-v1-vs-v2-on-three-phones) |
| [`0d77346dfff7…`](0d77346dfff7b200512e57b50ce2d569ea33a9361e8467f9a678656701969054/) | 5 | Galaxy S23 | [cpu-reference-v2 vs v1](../docs/experiments/reference-config-v2.md#result-v1-vs-v2-on-three-phones) |
| [`b5c69a2c8470…`](b5c69a2c8470ca4e9f815d05a050518017e2cfb33a48efff7a23d8fc57385d37/) | 5 | Pixel 9 | [cpu-reference-v2 vs v1](../docs/experiments/reference-config-v2.md#result-v1-vs-v2-on-three-phones) |
| [`f19f34dbf418…`](f19f34dbf4189b31be348987ffabbb7fdf35bfaa31aeee25e8bdab442c7d5c12/) | 10 | POCO F8 Ultra | [cpu-reference-v2 factor split](../docs/experiments/reference-config-v2.md#result-one-factor-split-on-the-poco-f8-ultra) |
| [`c5116d95007b…`](c5116d95007ba6e68748d46e313728d978a891f72c12652f541c12679361a972/) | 7 | POCO F8 Ultra | [LiteRT accelerators, F8 Ultra](../docs/experiments/litert-accelerators.md#poco-f8-ultra-sm8850-measured-2026-10-17) |
| [`1890e4b41626…`](1890e4b41626dbad97995990e8e1eadb838baa1a278989be7a4bbb354b18f403/) | 6 | Galaxy S23 | [LiteRT accelerators, Galaxy S23](../docs/experiments/litert-accelerators.md#galaxy-s23-sm8550-measured-2026-10-18) |
| [`7cd4368bf08f…`](7cd4368bf08f339b78e731c2eed789768de44f736f338ad38f06ee154c3ae478/) | 4 | POCO F8 Ultra | [Legacy Hexagon row check](../docs/experiments/litert-accelerators.md#legacy-hexagon-row-check-poco-f8-ultra-llama-32-1b-2026-10-17) |
| [`beb9a67ced87…`](beb9a67ced8709bf195a64d048be0d8529505f5b76e24ee612de2d8867046bd2/) | 3 | POCO F8 Ultra | [llama.rn 0.13.0-rc.7 re-check, Gemma 3 1B](../docs/experiments/litert-accelerators.md#gemma-3-1b-llamacpp-rows-f8-gemma3-llamacpp-rc7-v1-10-repetitions) |
| [`20009fec0a56…`](20009fec0a56fe4bcb2b0046a2e160e3fca5c9263de1fef8a364dc62674ca210/) | 4 | POCO F8 Ultra | [llama.rn 0.13.0-rc.7 re-check, Llama 3.2 1B](../docs/experiments/litert-accelerators.md#llama-32-1b-hexagon-check-f8-llama32-hexagon-check-v1-4-repetitions) |
| [`49a216f37945…`](49a216f37945edcc80b5d31a981206c1984e3293f16e4e47abd78adad2fe69e8/) | 2 | POCO F8 Ultra | [Model refresh spike: LiteRT-LM 0.18.1 NPU check](../docs/experiments/model-refresh.md#1-litert-lm-0181-npu-check-gemma-3-1b) (`litertlm-gemma3-sm8850-npu-v2`, history only), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`c12fb048be9b…`](c12fb048be9bfbf75361b33b57c3fd158510a84c45c369d4c35b07410c7b3880/) | 13 | POCO F8 Ultra | [Model refresh spike](../docs/experiments/model-refresh.md) (`spike-refresh-*`: llama.cpp, LiteRT-LM, ExecuTorch; history only), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`8c41c4e258ff…`](8c41c4e258ff4dd1fc149f165b8c45c7fe2cc3bb91f1db13b2db2acf078befb5/) | 4 | POCO F8 Ultra | [Model refresh spike: GENIE-X](../docs/experiments/model-refresh.md#5-genie-x-080-qwen3-17b-npu) (`spike-refresh-geniex-qwen3-v1`, history only), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`31f08310a8de…`](31f08310a8de323504572c42bc4b924ffe81f6b79c8a52a5707dc4acb0825f37/) | 6 | Galaxy S23 | [Model refresh spike: Galaxy S23 memory probe](../docs/experiments/model-refresh.md#galaxy-s23-gemma-4-e2b-memory-probe-2026-10-23) (`spike-refresh-*`, history only), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`56707acb5d2e…`](56707acb5d2eafacda4262e3c0d504d08e0e517fec177acecfabf9bd86bf5ac7/) | 1 | Galaxy S23 | Model-refresh board session, history only (`cpu-reference-v4-qwen3`: Qwen3 1.7B Q4_0, llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`d9d6990851d5…`](d9d6990851d52602fd1fe8ad46ef624fb319e9ce38204ea9437ea6a105a3d73e/) | 1 | Galaxy S23 | Model-refresh board session, history only (`cpu-reference-v4-gemma3`: Gemma 3 1B Q4_0 (QAT), llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`cddf53ba3a5b…`](cddf53ba3a5b455090c6b7219874451f65b1bf656f1d542d582e17a89b315915/) | 1 | Galaxy S23 | Model-refresh board session, history only (`litertlm-gemma4-cpu-v2`: Gemma 4 E2B mixed 2/4/8-bit, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`2fbe08f18d55…`](2fbe08f18d553e0097a46cbfa3ee969f0f76af6dae04fd65d5b16bfa5b3a8403/) | 1 | Galaxy S23 | Model-refresh board session, history only (`litertlm-qwen3-int4-cpu-v2`: Qwen3 1.7B int4, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`07f6f1ca5dea…`](07f6f1ca5dea3ef3c1a337d179426e95ced8eca0f888151a034035073665da5b/) | 1 | Galaxy S23 | Model-refresh board session, history only (`litertlm-qwen3-int4-cpu-v2` rerun under the charging rule: Qwen3 1.7B int4, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`2b94453b4d1d…`](2b94453b4d1d0b0bf22c74a0046db64978008d96e51f86bed1218b22a8fcc522/) | 1 | Galaxy S23 | Model-refresh board session, history only (`litertlm-gemma3-int4-cpu-v2`: Gemma 3 1B int4, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`2859a75aa721…`](2859a75aa721d35c567ddddc02931eaa0fbdf9bdeb0afbf404538a0b8114654e/) | 1 | Galaxy S23 | Model-refresh board session, history only (`executorch-gemma4-cpu-v1`: Gemma 4 E2B mixed 4/16-bit, ExecuTorch CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`dd9d2a9837cd…`](dd9d2a9837cd622e6077345d9553d78952b372278ad8c756bb6d8945d9e0eff4/) | 1 | Galaxy S23 | Model-refresh board session, history only (`executorch-qwen3-cpu-v1`: Qwen3 1.7B 4-bit weights, 8-bit activations, ExecuTorch CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`dc2c59056874…`](dc2c590568743747c936b436cba0debae0c8a602074a909705bf72ae0f93f9a2/) | 1 | Pixel 9 | Model-refresh board session, history only (`cpu-reference-v4-qwen3`: Qwen3 1.7B Q4_0, llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`4e6178a5e21b…`](4e6178a5e21b5689298fc830a1d91a7f48afa7c578010acbecbf428e979e982b/) | 1 | Pixel 9 | Model-refresh board session, history only (`cpu-reference-v4-gemma3`: Gemma 3 1B Q4_0 (QAT), llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`3f8f3138ef5f…`](3f8f3138ef5f063e4d53d307c0de54613e0c68ea38b00104f8640ee89f44f1d4/) | 1 | Pixel 9 | Model-refresh board session, history only (`cpu-reference-v4-gemma4`: Gemma 4 E2B Q4_0 (QAT), llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`5df4b166f5ff…`](5df4b166f5ffa11f0846ddb6b3073a93eed0b9c7ef6b5655fee9015b1d460d60/) | 1 | Pixel 9 | Model-refresh board session, history only (`litertlm-gemma4-cpu-v2`: Gemma 4 E2B mixed 2/4/8-bit, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`6b9e6a3bec98…`](6b9e6a3bec98b1f015f291daac5b832a375cf8c05a0a3a8d097f8c43147cc47d/) | 1 | Pixel 9 | Model-refresh board session, history only (`litertlm-qwen3-int4-cpu-v2`: Qwen3 1.7B int4, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`5ffecc68b9a4…`](5ffecc68b9a4a0a5eeca8bd8fe86c2d18272f93132636aa8205ddc4934fc37ac/) | 1 | Pixel 9 | Model-refresh board session, history only (`litertlm-gemma3-int4-cpu-v2`: Gemma 3 1B int4, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`8041a2063637…`](8041a206363725e7fe077fb570469f132eeabe9ddb6554266cb5a0e7fd83ffe9/) | 1 | Pixel 9 | Model-refresh board session, history only (`executorch-gemma4-cpu-v1`: Gemma 4 E2B mixed 4/16-bit, ExecuTorch CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`87faa66cd405…`](87faa66cd4055d832c69853bddd823ca1236221d8e84508ccbec377f343f2b15/) | 1 | Pixel 9 | Model-refresh board session, history only (`executorch-qwen3-cpu-v1`: Qwen3 1.7B 4-bit weights, 8-bit activations, ExecuTorch CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`4962248f3874…`](4962248f3874aa41ef01e2f4efed34f0f02c691bc72db295f594711299678a7d/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`cpu-reference-v4-qwen3`: Qwen3 1.7B Q4_0, llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`d4868653a047…`](d4868653a0473672942213ae4212aba783457b77676198235eaa8f81d639ac56/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`cpu-reference-v4-gemma4`: Gemma 4 E2B Q4_0 (QAT), llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`b8d1aaa9922e…`](b8d1aaa9922e05870eebe9ec0e8acb66e7440d0333d233f26dcb15f653d63dfe/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`cpu-reference-v4-gemma3`: Gemma 3 1B Q4_0 (QAT), llama.cpp CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`658799957eef…`](658799957eefe03b73e298dc8cc43ef25d28cee8ae6d48747d3d87b46c527359/) | 3 | POCO F8 Ultra | Model-refresh board sessions, history only (`llamacpp-gemma4-opencl-hexagon-v1`: Gemma 4 E2B Q4_0 (QAT), llama.cpp CPU (gate reference), OpenCL and Hexagon), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`57c07c4b15a3…`](57c07c4b15a376c8002c9ec22a7052c1701a513b1fb3dde7fbcc024261e353d4/) | 3 | POCO F8 Ultra | Model-refresh board sessions, history only (`llamacpp-qwen3-opencl-hexagon-v1`: Qwen3 1.7B Q4_0, llama.cpp CPU (gate reference), OpenCL and Hexagon), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`54b1832f9dde…`](54b1832f9dde54066fcd60ebd99d1bda43793b8b1e24bf2743d780f197522f90/) | 3 | POCO F8 Ultra | Model-refresh board sessions, history only (`llamacpp-gemma3-opencl-hexagon-v1`: Gemma 3 1B Q4_0 (QAT), llama.cpp CPU (gate reference), OpenCL and Hexagon), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`cc5962fa2ca2…`](cc5962fa2ca2d6d56a58f7deccd6836f1c204e504e805582f34e5111a9d7a708/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`litertlm-gemma4-cpu-v2`: Gemma 4 E2B mixed 2/4/8-bit, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`73ec19b319eb…`](73ec19b319eb0e8b354ce213c556d148a8cd4d5c238b3c6368d0257e076d99f5/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`litertlm-qwen3-int4-cpu-v2`: Qwen3 1.7B int4, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`4ce716f908ac…`](4ce716f908ac44d3aac27b4f9d219abd749f107dc27cc35014547d77e0f84417/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`litertlm-gemma3-int4-cpu-v2`: Gemma 3 1B int4, LiteRT-LM CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`9f004158ed59…`](9f004158ed598ba26cc1f190ab12e048e92cf321404ed3e96d6673a91c6037b0/) | 2 | POCO F8 Ultra | Model-refresh board sessions, history only (`litertlm-gemma3-sm8850-npu-v2`: Gemma 3 1B int4, LiteRT-LM CPU (gate reference) and NPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`27286631777c…`](27286631777c4bd7b970f2aaa6de8ca1ceb9d1d2bd62169a78b1337f668516d2/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`executorch-gemma4-cpu-v1`: Gemma 4 E2B mixed 4/16-bit, ExecuTorch CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`01a7fe322fe4…`](01a7fe322fe4532da207c191cc46b755bc9fc8bc9df022402201777dafa143e6/) | 1 | POCO F8 Ultra | Model-refresh board session, history only (`executorch-qwen3-cpu-v1`: Qwen3 1.7B 4-bit weights, 8-bit activations, ExecuTorch CPU), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`304967e2f895…`](304967e2f895d90edc289151f4934bfc721bcd4f6eac7c14685c5e6b269009a2/) | 2 | POCO F8 Ultra | Model-refresh board sessions, history only (`geniex-qwen3-sm8850-npu-v1`: Qwen3 1.7B, GENIE-X CPU (gate reference, Q4_0) and NPU (W4A16)), [#43](https://github.com/a-ghorbani/xebench/issues/43) |
| [`0f9069eb78f2…`](0f9069eb78f298168fce71410e106e7ad09e08bf55726859028c3054515ca80c/) | 8 | Galaxy S23, Pixel 9, POCO F8 Ultra | [Charging A/B experiment](../docs/experiments/charging-ab.md#what-was-run) |
| [`af7ae8ff5528…`](af7ae8ff5528e330f91d209164192b4ba99874bf311ea782c6f2ca197a48347a/) | 1 | Galaxy S23 | Reference-flavor smoke run (`cpu-reference-v3`), [#1](https://github.com/a-ghorbani/xebench/issues/1), [#3](https://github.com/a-ghorbani/xebench/issues/3) |
| [`c225b741d581…`](c225b741d58124d8ae75f56266434e6710b94efa27009971c6dba65113d42ebe/) | 1 | POCO F8 Ultra | Reference-flavor smoke run (`cpu-reference-v3`), [#1](https://github.com/a-ghorbani/xebench/issues/1), [#3](https://github.com/a-ghorbani/xebench/issues/3) |
| [`f9d74fa793a0…`](f9d74fa793a07437f2c0ebadeac1d2297aac6c97d8baba890dc00a4c5e9fd9cb/) | 1 | Galaxy S23 | Run conditions, normal capture, [#2](https://github.com/a-ghorbani/xebench/issues/2) |
| [`06eb6ca78dc9…`](06eb6ca78dc9aac855f211069b7706dd6238c96df4adb82e3f70ee4172ef3df9/) | 1 | Galaxy S23 | Run conditions, interruption capture, [#2](https://github.com/a-ghorbani/xebench/issues/2) |
| [`883fb3b07773…`](883fb3b07773fdf31c83b4060938b45e414fc71ad2a120a379969d0ad806bf77/) | 1 | OnePlus 6 | Run conditions, long run, [#2](https://github.com/a-ghorbani/xebench/issues/2) |
