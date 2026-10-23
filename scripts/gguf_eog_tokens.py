#!/usr/bin/env python3
"""The end-of-generation token ids of a GGUF, as llama.cpp decides them.

A fixed llama.cpp decode (decodeMode fixed) gives every end-of-generation token of the model a
logit bias of -infinity, so the configuration pins those ids per model (run field eogTokenIds).
llama.rn's completion loop stops at any token for which llama_vocab_is_eog() is true; that set is
built when the vocabulary loads (llama.cpp src/llama-vocab.cpp, llama_vocab::impl::load, at the
llama.cpp that llama.rn vendors: b11804, c7fc509, for llama.rn 0.13.0-rc.8):

1. the FIM pad, repo and separator tokens: the tokenizer.ggml.fim_{pad,rep,sep}_token_id metadata,
   else the first vocabulary token spelled as llama.cpp's list for each (FIM_TEXTS below);
2. every token spelled as one of EOG_TEXTS;
3. the EOS, EOT and EOM tokens (tokenizer.ggml.{eos,eot,eom}_token_id metadata; EOT and EOM are
   otherwise found by spelling, and those spellings are all in EOG_TEXTS already);
4. minus "<|end|>" when "<|call|>" or "<|calls|>" is EOG together with "<|return|>" or "<|flush|>", and
   minus "</s>" when "<|tool_response>" or "<|plamo:eos|>" is EOG or the vocabulary has the token
   '<rfl:parameter name="'.

This script reads only the GGUF's metadata (no tensors) and applies those rules. It refuses a GGUF
whose vocabulary has more than one spelling candidate for a FIM token without metadata (llama.cpp
then takes whichever its hash map yields first), the "flint" pre-tokenizer (whose extra EOG rule
is not reproduced here), and a GGUF without tokenizer.ggml.eos_token_id (llama.cpp then takes a
default EOS that depends on the tokenizer model, which this script does not reproduce). Check its output against llama.cpp itself when pinning a model: build the
vendored llama.cpp and list the ids for which llama_vocab_is_eog() is true on a vocab_only load
(docs/REFERENCE-RUNS.md).

    python3 scripts/gguf_eog_tokens.py <model.gguf>
"""
import json
import struct
import sys

# llama-vocab.cpp: token texts that are always end of generation.
EOG_TEXTS = frozenset((
    "<|eot_id|>", "<|eot|>", "<|im_end|>", "<|end|>", "<|return|>", "<|call|>", "<|flush|>", "<|calls|>",
    "<end_of_turn>", "<|endoftext|>", "</s>", "<|eom_id|>", "<EOT>", "_<EOT>", "[EOT]", "[EOS]",
    "<|end_of_text|>", "<end_of_utterance>", "<eos>", "<turn|>", "<|tool_response>",
    "<｜end▁of▁sentence｜>", "[e~[", "<|ifm|im_end|>",
))
# llama-vocab.cpp: spellings of the FIM pad, repo and separator tokens, used when the metadata has no id.
FIM_TEXTS = {
    "fim_pad": ("<|fim_pad|>", "<fim-pad>", "<fim_pad>", "<PAD>", "[PAD]"),
    "fim_rep": ("<|fim_repo|>", "<|repo_name|>", "<fim-repo>", "<REPO>", "<reponame>"),
    "fim_sep": ("<|file_sep|>", "<|plamo:file_separator|>"),
}

_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


class GgufError(ValueError):
    pass


def _read(f, fmt):
    size = struct.calcsize(fmt)
    data = f.read(size)
    if len(data) != size:
        raise GgufError("truncated GGUF")
    return struct.unpack(fmt, data)[0]


def _string(f):
    length = _read(f, "<Q")
    if length > 1 << 24:
        raise GgufError("implausible GGUF string length")
    data = f.read(length)
    if len(data) != length:
        raise GgufError("truncated GGUF")
    return data.decode("utf-8", errors="surrogateescape")


def _value(f, kind):
    if kind in _SCALARS:
        return _read(f, _SCALARS[kind])
    if kind == _STRING:
        return _string(f)
    if kind == _ARRAY:
        item, count = _read(f, "<I"), _read(f, "<Q")
        if count > 1 << 26:
            raise GgufError("implausible GGUF array length")
        return [_value(f, item) for _ in range(count)]
    raise GgufError(f"unknown GGUF value type {kind}")


def read_metadata(path):
    """The key/value metadata of a GGUF file (versions 2 and 3), without reading tensors."""
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise GgufError("not a GGUF file")
        version = _read(f, "<I")
        if version not in (2, 3):
            raise GgufError(f"unsupported GGUF version {version}")
        _read(f, "<Q")  # tensor count
        count = _read(f, "<Q")
        metadata = {}
        for _ in range(count):
            key = _string(f)
            metadata[key] = _value(f, _read(f, "<I"))
        return metadata


def eog_token_ids(metadata):
    """llama.cpp's end-of-generation set for this vocabulary, as {token id: token text}."""
    tokens = metadata.get("tokenizer.ggml.tokens")
    if not isinstance(tokens, list) or not tokens:
        raise GgufError("the GGUF has no tokenizer.ggml.tokens")
    if metadata.get("tokenizer.ggml.pre") == "flint":
        raise GgufError("the flint pre-tokenizer has an extra EOG rule this script does not reproduce")
    # llama.cpp's token_to_id map: a text spelled twice maps to its last id.
    by_text = {text: index for index, text in enumerate(tokens)}
    valid = lambda index: isinstance(index, int) and 0 <= index < len(tokens)
    eog = set()
    for name, spellings in FIM_TEXTS.items():
        index = metadata.get(f"tokenizer.ggml.{name}_token_id")
        if index is None:
            found = [by_text[text] for text in spellings if text in by_text]
            if len(found) > 1:
                raise GgufError(f"{name}: several candidate spellings and no metadata id ({found})")
            index = found[0] if found else None
        if index is not None and valid(index):
            eog.add(index)
    eog.update(by_text[text] for text in EOG_TEXTS if text in by_text)
    if not valid(metadata.get("tokenizer.ggml.eos_token_id")):
        raise GgufError("no tokenizer.ggml.eos_token_id: llama.cpp would use its tokenizer-model default EOS")
    for name in ("eos", "eot", "eom"):
        index = metadata.get(f"tokenizer.ggml.{name}_token_id")
        if index is not None and valid(index):
            eog.add(index)
    texts = {index: tokens[index] for index in eog}
    present = set(texts.values())
    # llama-vocab.cpp: has_call is "<|call|>" or "<|calls|>"; "<|end|>" leaves with return or flush.
    has_call = "<|call|>" in present or "<|calls|>" in present
    if has_call and ("<|return|>" in present or "<|flush|>" in present) and "<|end|>" in present:
        eog -= {index for index, text in texts.items() if text == "<|end|>"}
    if "<|tool_response>" in present or "<|plamo:eos|>" in present or '<rfl:parameter name="' in by_text:
        eog -= {index for index, text in texts.items() if text == "</s>"}
    return {index: tokens[index] for index in sorted(eog)}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    try:
        eog = eog_token_ids(read_metadata(args[0]))
    except (GgufError, OSError) as error:
        print(f"gguf_eog_tokens: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"eogTokenIds": list(eog), "tokens": {str(index): text for index, text in eog.items()}},
                     ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
