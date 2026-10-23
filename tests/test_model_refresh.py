"""Model refresh (#43): llama.cpp fixed decoding, the end-of-generation ids and the board configurations."""
import copy
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


raw_session = load("raw_session")
eog = load("gguf_eog_tokens")
refresh = load("model_refresh_configs")
ACCEL = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted-accelerator.json").read_text())["records"]
LLAMA_FIXED = "board-llama-fixed-gemma4-e2b-q40-qat-cpu"


def codes(record):
    return {item["code"] for item in raw_session.validate(record)}


def gguf(path, tokens, metadata=None):
    """A GGUF with only key/value metadata (no tensors): the tokenizer fields the EOG rules read."""
    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data
    entries = [("tokenizer.ggml.tokens", struct.pack("<IIQ", 9, 8, len(tokens)) + b"".join(map(string, tokens)))]
    for key, value in (metadata or {}).items():
        entries.append((key, struct.pack("<II", 4, value) if isinstance(value, int) else
                        struct.pack("<I", 8) + string(value)))
    body = b"".join(string(key) + value for key, value in entries)
    Path(path).write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, len(entries)) + body)


class LlamaFixedDecodeRecordTests(unittest.TestCase):
    """raw_session.py: a fixed-decode llama.cpp record biases every configured EOG token, with the cache off."""

    def test_app_emitted_fixed_llama_records_are_valid(self):
        for name in (LLAMA_FIXED, "board-llama-fixed-gemma4-e2b-q40-qat-opencl",
                     "board-llama-fixed-gemma4-e2b-q40-qat-hexagon"):
            with self.subTest(name):
                record = ACCEL[name]
                self.assertEqual(record["protocol"]["decodeMode"], "fixed")
                self.assertEqual(record["effectiveSettings"]["completion"]["logit_bias"],
                                 [[1, False], [50, False], [106, False]])
                self.assertEqual(raw_session.validate(record), [])

    def test_the_bias_must_be_exactly_the_configured_ids(self):
        for change in (lambda s: s["completion"].pop("logit_bias"),
                       lambda s: s["completion"]["logit_bias"].pop(),
                       lambda s: s["completion"]["logit_bias"].__setitem__(0, [2, False]),
                       lambda s: s["init"].pop("state_cache_budget_mb"),
                       # A numeric bias of 0 is not -infinity, although 0 == False in Python.
                       lambda s: s["completion"]["logit_bias"].__setitem__(0, [1, 0])):
            record = copy.deepcopy(ACCEL[LLAMA_FIXED])
            change(record["effectiveSettings"])
            self.assertIn("settings.protocol-mismatch", codes(record))
        # The semantic rule is type-strict on its own (the schema also refuses a float id).
        for pair in ([1, 0], [1.0, False], [True, False]):
            with self.subTest(pair):
                record = copy.deepcopy(ACCEL[LLAMA_FIXED])
                record["effectiveSettings"]["completion"]["logit_bias"][0] = pair
                run = record["configuration"]["runs"][0]
                self.assertIn("settings.protocol-mismatch",
                              {item["code"] for item in raw_session._llama_fixed_decode(record, run)})
        # A record without decodeMode carries no bias.
        plain = copy.deepcopy(ACCEL["qwen25-qwen25-1.5b-q80-cpu"])
        plain["effectiveSettings"]["completion"]["logit_bias"] = [[151645, False]]
        self.assertIn("settings.protocol-mismatch", codes(plain))

    def test_decode_rate_is_over_the_decode_steps_after_the_first_token(self):
        record = copy.deepcopy(ACCEL[LLAMA_FIXED])
        attempt = next(item for item in record["coldRuns"] if item["status"] == "complete")
        # A rate over all 128 tokens is refused: predicted_ms covers the 127 decode steps after the first.
        attempt["decodeTps"] = attempt["engineInternal"]["predictedPerSecond"] = \
            128 * 1000 / attempt["engineInternal"]["predictedMs"]
        self.assertIn("execution.inconsistent", codes(record))
        stripped = copy.deepcopy(ACCEL[LLAMA_FIXED])
        next(item for item in stripped["coldRuns"] if item["status"] == "complete").pop("engineInternal")
        self.assertIn("execution.inconsistent", codes(stripped))
        short = copy.deepcopy(ACCEL[LLAMA_FIXED])
        next(item for item in short["coldRuns"] if item["status"] == "complete")["decodeTokens"] = 127
        self.assertIn("protocol.decode-length", codes(short))

    def test_publication_policy_rechecks_the_fixed_decode(self):
        policy = load("publication_policy")
        self.assertEqual(policy.recheck(copy.deepcopy(ACCEL[LLAMA_FIXED])), [])
        record = copy.deepcopy(ACCEL[LLAMA_FIXED])
        record["effectiveSettings"]["completion"].pop("logit_bias")
        self.assertIn("settings.protocol-mismatch /effectiveSettings/completion/logit_bias", policy.recheck(record))

    def test_eog_ids_belong_to_fixed_llama_runs(self):
        config = copy.deepcopy(ACCEL[LLAMA_FIXED]["configuration"])
        self.assertEqual(raw_session._configuration_accelerator(config), [])
        for change in (lambda c: c.pop("decodeMode"), lambda c: c["runs"][0].pop("eogTokenIds"),
                       lambda c: c["runs"][0].__setitem__("eogTokenIds", [106, 1])):
            broken = copy.deepcopy(config)
            change(broken)
            self.assertIn("configuration.run-invalid",
                          {item["code"] for item in raw_session._configuration_accelerator(broken)})
        litert = json.loads((ROOT / "xebenchapp/configs/litertlm-gemma4-cpu-v2.json").read_text())
        litert["runs"][0]["eogTokenIds"] = [1]
        self.assertIn("configuration.run-invalid",
                      {item["code"] for item in raw_session._configuration_accelerator(litert)})


class EndOfGenerationTests(unittest.TestCase):
    """scripts/gguf_eog_tokens.py follows llama.cpp b11804's llama_vocab EOG rules."""

    def eog(self, tokens, metadata=None):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "m.gguf"
            gguf(path, tokens, metadata)
            return eog.eog_token_ids(eog.read_metadata(path))

    def test_gemma4_shape(self):
        # <eos> and the end of turn are EOG; </s> is a normal token once <|tool_response> is EOG.
        tokens = ["<pad>", "<eos>", "<bos>", "</s>", "<|tool_response>", "<|turn>", "<turn|>", "hello"]
        self.assertEqual(self.eog(tokens, {"tokenizer.ggml.eos_token_id": 1}),
                         {1: "<eos>", 4: "<|tool_response>", 6: "<turn|>"})

    def test_gemma3_and_qwen3_shapes(self):
        tokens = ["<pad>", "<eos>", "<bos>", "<end_of_turn>", "</s>", "x"]
        self.assertEqual(list(self.eog(tokens, {"tokenizer.ggml.eos_token_id": 1})), [1, 3, 4])
        qwen = ["a", "</s>", "<|endoftext|>", "<|im_start|>", "<|im_end|>", "<|fim_pad|>", "<|repo_name|>",
                "<|file_sep|>", "<|fim_prefix|>"]
        self.assertEqual(list(self.eog(qwen, {"tokenizer.ggml.eos_token_id": 4})), [1, 2, 4, 5, 6, 7])

    def test_metadata_ids_and_the_end_rule(self):
        # EOS/EOT from the metadata are EOG whatever their spelling; a FIM id from the metadata wins.
        self.assertEqual(list(self.eog(["a", "b", "c", "<PAD>"], {"tokenizer.ggml.eos_token_id": 1,
                                                                  "tokenizer.ggml.eot_token_id": 2,
                                                                  "tokenizer.ggml.fim_pad_token_id": 0})),
                         [0, 1, 2])
        # <|end|> leaves the set when <|call|> or <|calls|> is EOG with <|return|> or <|flush|>
        # (o200k_harmony, solar-open; llama.cpp's has_call takes either spelling).
        eos = {"tokenizer.ggml.eos_token_id": 0}
        for tokens in (["<|return|>", "<|call|>", "<|end|>"], ["<|return|>", "<|calls|>", "<|end|>"],
                       ["<|flush|>", "<|calls|>", "<|end|>"], ["<|flush|>", "<|call|>", "<|end|>"]):
            with self.subTest(tokens):
                self.assertEqual(list(self.eog(tokens, eos)), [0, 1])
        self.assertEqual(list(self.eog(["<|end|>", "x"], eos)), [0])
        self.assertEqual(list(self.eog(["x", "<|return|>", "<|end|>"], eos)), [0, 1, 2])
        # Without an EOS id llama.cpp takes a tokenizer-model default, which the script refuses to guess.
        with self.assertRaises(eog.GgufError):
            self.eog(["<|end|>", "x"])

    def test_refusals(self):
        with self.assertRaises(eog.GgufError):
            self.eog(["<PAD>", "[PAD]"])  # two FIM pad spellings and no metadata id
        with self.assertRaises(eog.GgufError):
            self.eog(["a"], {"tokenizer.ggml.pre": "flint"})
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "x.gguf"
            path.write_bytes(b"GGML")
            with self.assertRaises(eog.GgufError):
                eog.read_metadata(path)


class RefreshConfigurationTests(unittest.TestCase):
    def test_committed_files_are_what_the_table_writes(self):
        self.assertEqual(refresh.main(["--check"]), 0)

    def test_pins_and_ids_come_from_the_manifests_and_the_eog_table(self):
        pins = refresh.pins()
        configs = refresh.configurations()
        self.assertEqual(len(configs), 11)
        for config in configs:
            for run in config["runs"]:
                with self.subTest(f"{config['id']} {run['id']}"):
                    self.assertEqual(run["modelSha256"], pins[run["modelFile"]])
                    if "tokenizerFile" in run:
                        self.assertEqual(run["tokenizerSha256"], pins[run["tokenizerFile"]])
                    if run["engine"] == "llama.cpp":
                        self.assertEqual(run["eogTokenIds"], refresh.EOG_TOKEN_IDS[run["modelFile"]])
                    self.assertEqual(run["quant"], refresh.QUANT_LABELS[run["modelFile"]])
                    self.assertEqual(raw_session._configuration_accelerator(config), [])
        # Every pinned GGUF of the board's three models has its EOG ids.
        self.assertEqual(sorted(refresh.EOG_TOKEN_IDS), sorted(model[2] for model in refresh.MODELS.values()))

    def test_every_configured_model_file_agrees_with_the_manifest_models(self):
        """models[] (downloadable GGUFs) as well as pinnedArtifacts: a configuration that names a pinned file
        pins its hash."""
        pins = refresh.pins()
        for path in sorted((ROOT / "xebenchapp/configs").glob("*.json")):
            for run in json.loads(path.read_text())["runs"]:
                if run["modelFile"] in pins and "modelSha256" in run:
                    with self.subTest(f"{path.name} {run['id']}"):
                        self.assertEqual(run["modelSha256"], pins[run["modelFile"]])


if __name__ == "__main__":
    unittest.main()
