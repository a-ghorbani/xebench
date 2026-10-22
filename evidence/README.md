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
| [`0f9069eb78f2…`](0f9069eb78f298168fce71410e106e7ad09e08bf55726859028c3054515ca80c/) | 8 | Galaxy S23, Pixel 9, POCO F8 Ultra | [Charging A/B experiment](../docs/experiments/charging-ab.md#what-was-run) |
| [`af7ae8ff5528…`](af7ae8ff5528e330f91d209164192b4ba99874bf311ea782c6f2ca197a48347a/) | 1 | Galaxy S23 | Reference-flavor smoke run (`cpu-reference-v3`), [#1](https://github.com/a-ghorbani/xebench/issues/1), [#3](https://github.com/a-ghorbani/xebench/issues/3) |
| [`c225b741d581…`](c225b741d58124d8ae75f56266434e6710b94efa27009971c6dba65113d42ebe/) | 1 | POCO F8 Ultra | Reference-flavor smoke run (`cpu-reference-v3`), [#1](https://github.com/a-ghorbani/xebench/issues/1), [#3](https://github.com/a-ghorbani/xebench/issues/3) |
| [`f9d74fa793a0…`](f9d74fa793a07437f2c0ebadeac1d2297aac6c97d8baba890dc00a4c5e9fd9cb/) | 1 | Galaxy S23 | Run conditions, normal capture, [#2](https://github.com/a-ghorbani/xebench/issues/2) |
| [`06eb6ca78dc9…`](06eb6ca78dc9aac855f211069b7706dd6238c96df4adb82e3f70ee4172ef3df9/) | 1 | Galaxy S23 | Run conditions, interruption capture, [#2](https://github.com/a-ghorbani/xebench/issues/2) |
| [`883fb3b07773…`](883fb3b07773fdf31c83b4060938b45e414fc71ad2a120a379969d0ad806bf77/) | 1 | OnePlus 6 | Run conditions, long run, [#2](https://github.com/a-ghorbani/xebench/issues/2) |
