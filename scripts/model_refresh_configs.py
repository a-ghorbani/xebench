#!/usr/bin/env python3
"""The model-refresh board configurations (issue #43), written from one table.

Every configuration the refresh sessions run is declared here and written to
xebenchapp/configs/<id>.json. The pins are not typed twice: modelSha256 and tokenizerSha256 come
from the model manifests (configs/models/*-models.json), the quant labels from QUANT_LABELS and the
llama.cpp end-of-generation ids from EOG_TOKEN_IDS. The two Gemma 3 LiteRT-LM configurations the
refresh reuses (litertlm-gemma3-int4-cpu-v2, litertlm-gemma3-sm8850-npu-v2) are committed files
of the earlier set and are not written here.

    python3 scripts/model_refresh_configs.py --write   # (re)write the files
    python3 scripts/model_refresh_configs.py --check   # exit 1 when a committed file differs

A configuration is fixed once a session has run it: change this table only before the sessions.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "xebenchapp/configs"
MANIFESTS = ROOT / "configs/models"

# The quant label each artifact's runs carry: the row's quant, in the board vocabulary
# (docs/STYLE.md, scripts/check_public_text.mjs).
QUANT_LABELS = {
    "gemma-4-E2B_q4_0-it.gguf": "Q4_0 (QAT)",
    "Qwen3-1.7B-Q4_0.gguf": "Q4_0",
    "gemma-3-1b-it-q4_0.gguf": "Q4_0 (QAT)",
    "gemma-4-E2B-it.litertlm": "mixed 2/4/8-bit",
    "Qwen3-1.7B_dynamic_wi4b32_afp32.litertlm": "int4",
    "gemma_4_e2b_xnnpack_8da4w.pte": "mixed 4/16-bit",
    "qwen_3_1_7b_xnnpack_8da4w.pte": "4-bit weights, 8-bit activations",
    "qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy": "W4A16",
}

# llama.cpp's end-of-generation ids of each pinned GGUF: scripts/gguf_eog_tokens.py, checked against
# llama_vocab_is_eog() of the llama.cpp llama.rn 0.13.0-rc.8 vendors (b11804).
EOG_TOKEN_IDS = {
    # <eos>, <|tool_response>, <turn|>
    "gemma-4-E2B_q4_0-it.gguf": [1, 50, 106],
    # </s>, <|endoftext|>, <|im_end|>, <|fim_pad|>, <|repo_name|>, <|file_sep|>
    "Qwen3-1.7B-Q4_0.gguf": [128247, 151643, 151645, 151662, 151663, 151664],
    # <eos>, <end_of_turn>, </s>
    "gemma-3-1b-it-q4_0.gguf": [1, 106, 212],
}

# The ExecuTorch tokenizer staged next to each .pte.
TOKENIZERS = {
    "gemma_4_e2b_xnnpack_8da4w.pte": "gemma_4_e2b_tokenizer.json",
    "qwen_3_1_7b_xnnpack_8da4w.pte": "qwen_3_tokenizer.json",
}

# cpu-reference-v3's timing and load settings; std-pp512-v1 with a 128-token decode budget.
TIMING = {"nColdRuns": 3, "cooldownSec": 120, "readinessTimeoutSec": 900, "maxBatteryTempRiseC": 2,
          "maxDecodeTokens": 128}
LLAMA_LOAD = {"useMmap": False, "useMlock": False, "noExtraBufts": False, "flashAttnType": "auto"}

MODELS = {
    # name: (board model label, chat template, GGUF, run id stem)
    "gemma4": ("Gemma 4 E2B", "gemma4", "gemma-4-E2B_q4_0-it.gguf", "gemma4-e2b-q40-qat"),
    "qwen3": ("Qwen3 1.7B", "qwen3", "Qwen3-1.7B-Q4_0.gguf", "qwen3-1.7b-q40"),
    "gemma3": ("Gemma 3 1B", "gemma3", "gemma-3-1b-it-q4_0.gguf", "gemma3-1b-q40-qat"),
}


def pins():
    """modelFile -> sha256 from every manifest's models and pinnedArtifacts."""
    table = {}
    for path in sorted(MANIFESTS.glob("*-models.json")):
        manifest = json.loads(path.read_text())
        for entry in manifest.get("models", []) + manifest.get("pinnedArtifacts", []):
            if table.setdefault(entry["file"], entry["sha256"]) != entry["sha256"]:
                raise SystemExit(f"{entry['file']}: the manifests pin different hashes")
    return table


def llama_run(model, backend, reference, sha):
    label, _, gguf, stem = MODELS[model]
    suffix = {"cpu": "cpu", "gpu": "opencl", "npu": "hexagon"}[backend]
    run = {"id": f"{stem}-{suffix}", "engine": "llama.cpp", "backend": backend, "purpose": "measurement",
           "model": label, "modelFile": gguf, "quant": QUANT_LABELS[gguf], "nThreads": 4, "nCtx": 2048,
           "nGpuLayers": 0 if backend == "cpu" else 99}
    if backend == "npu":
        run["devices"] = ["HTP0"]
    run.update(LLAMA_LOAD)
    run.update({"referenceRunId": reference, "eogTokenIds": EOG_TOKEN_IDS[gguf], "modelSha256": sha[gguf]})
    return run


def configuration(config_id, template, runs, decode_fixed=True):
    return {"schemaVersion": 2, "id": config_id, **TIMING, "chatTemplate": template,
            "measuredPrompt": "std-pp512-v1", **({"decodeMode": "fixed"} if decode_fixed else {}), "runs": runs}


def configurations():
    sha = pins()
    out = []
    for model, (_, template, _, _) in MODELS.items():
        # The board's llama.cpp CPU rows: every phone, except Gemma 4 E2B on the Galaxy S23, where the
        # fully loaded GGUF does not fit (docs/REFERENCE-RUNS.md, Model-refresh board configurations).
        out.append(configuration(f"cpu-reference-v4-{model}", template, [llama_run(model, "cpu", None, sha)]))
        # POCO F8 Ultra: OpenCL and Hexagon, gated against the CPU run of the same file.
        cpu = llama_run(model, "cpu", None, sha)
        out.append(configuration(f"llamacpp-{model}-opencl-hexagon-v1", template, [
            cpu, llama_run(model, "gpu", cpu["id"], sha), llama_run(model, "npu", cpu["id"], sha)]))
    for config_id, model, template, file, run_id in (
            ("litertlm-gemma4-cpu-v2", "Gemma 4 E2B", "gemma4", "gemma-4-E2B-it.litertlm", "gemma4-e2b-litertlm-cpu"),
            ("litertlm-qwen3-int4-cpu-v2", "Qwen3 1.7B", "qwen3", "Qwen3-1.7B_dynamic_wi4b32_afp32.litertlm",
             "qwen3-1.7b-int4-litertlm-cpu")):
        out.append(configuration(config_id, template, [{
            "id": run_id, "engine": "litert-lm", "backend": "cpu", "purpose": "measurement", "model": model,
            "modelFile": file, "quant": QUANT_LABELS[file], "nThreads": 4, "nCtx": None, "referenceRunId": None,
            "modelSha256": sha[file]}]))
    for config_id, model, template, file, run_id in (
            ("executorch-gemma4-cpu-v1", "Gemma 4 E2B", "gemma4", "gemma_4_e2b_xnnpack_8da4w.pte",
             "gemma4-e2b-8da4w-executorch-cpu"),
            ("executorch-qwen3-cpu-v1", "Qwen3 1.7B", "qwen3", "qwen_3_1_7b_xnnpack_8da4w.pte",
             "qwen3-1.7b-8da4w-executorch-cpu")):
        tokenizer = TOKENIZERS[file]
        out.append(configuration(config_id, template, [{
            "id": run_id, "engine": "executorch", "backend": "cpu", "purpose": "measurement", "model": model,
            "modelFile": file, "quant": QUANT_LABELS[file], "nThreads": None, "nCtx": None, "referenceRunId": None,
            "modelSha256": sha[file], "tokenizerFile": tokenizer, "tokenizerSha256": sha[tokenizer]}]))
    # GENIE-X: the SM8850 QAIRT bundle on the NPU, gated against GENIE-X's own llama_cpp CPU run of the
    # Qwen3 GGUF. GENIE-X has no fixed decode: its rows state the decode length they reached.
    gguf = MODELS["qwen3"][2]
    bundle = "qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy"
    out.append(configuration("geniex-qwen3-sm8850-npu-v1", "qwen3", [
        {"id": "qwen3-1.7b-q40-geniex-cpu", "engine": "genie-x", "backend": "cpu", "purpose": "measurement",
         "model": "Qwen3 1.7B", "modelFile": gguf, "quant": QUANT_LABELS[gguf], "nThreads": 4, "nCtx": 4096,
         "referenceRunId": None, "modelSha256": sha[gguf]},
        {"id": "qwen3-1.7b-w4a16-geniex-npu", "engine": "genie-x", "backend": "npu", "purpose": "measurement",
         "model": "Qwen3 1.7B", "modelFile": bundle, "quant": QUANT_LABELS[bundle], "nThreads": None,
         "nCtx": None, "referenceRunId": "qwen3-1.7b-q40-geniex-cpu", "modelSha256": sha[bundle]}],
        decode_fixed=False))
    return out


def text(config):
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args not in (["--write"], ["--check"]):
        print("usage: model_refresh_configs.py --write | --check", file=sys.stderr)
        return 2
    stale = []
    for config in configurations():
        path = CONFIGS / f"{config['id']}.json"
        if args == ["--write"]:
            path.write_text(text(config))
        elif not path.exists() or path.read_text() != text(config):
            stale.append(path.name)
    if stale:
        print("model_refresh_configs: out of date (run --write): " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
