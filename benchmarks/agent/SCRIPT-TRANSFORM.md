# `script-transform` — spec

A second script task, harder than [`script-reverse`](PROMPTS.md) by design.
`script-reverse` now passes 3/3 on every backend and client measured, so it
distinguishes only speed. This one asks for argument parsing, three operations
in one file, and **composition** — where an agent can be subtly wrong rather
than simply absent.

Still greenfield: the agent starts in an empty directory. No repository, no
excision, no fixture, nothing to leak.

---

## The prompt

Copy this verbatim. It is the whole task.

```text
Create a Python script called transform.py that transforms a string from the
command line.

It takes --input STRING, plus any combination of these operation flags:

  --reverse   reverse the characters of the value
  --sort      sort the characters of the value ascending by Unicode code point
  --sha256    replace the value with the lowercase hex SHA-256 of its UTF-8 bytes

Operations apply in this fixed order regardless of the order the flags are
given on the command line: reverse first, then sort, then sha256.

With no operation flags, print the input unchanged.

Print the final result on one line.

For example, `python3 transform.py --input hello --reverse` prints `olleh`.
```

## Why it is worded that way

Every ambiguity is closed on purpose. `script-reverse`'s predecessor, `fib`,
was under-specified — it named the Fibonacci sequence without stating the
recurrence, so it tested *recall* rather than coding, and one model spent over
900 s on it. The rules that came out of that:

| decision | why |
|---|---|
| **Fixed order, not command-line order** | argparse does not preserve flag order without a custom action or scanning `sys.argv`. Command-line order would partly test argparse trivia; fixed order tests composition cleanly. Stated explicitly so neither reading is a guess. |
| **"characters", "ascending by Unicode code point"** | "sort a string" could mean words, could mean case-insensitive. Now it cannot. |
| **"lowercase hex", "UTF-8 bytes"** | Both are choices. An agent should not have to guess which we check. |
| **"With no operation flags, print the input unchanged"** | Otherwise the empty case is undefined and a correct-looking script may error. |
| **"on one line"** | Matches the oracle, which compares stripped stdout. |
| **The example uses `hello`** | And **no check uses `hello`**, so a script that hardcodes the demonstrated case fails. |

## The oracle

`transform.py` is run once per row; stdout is compared after stripping.

| argv | expected stdout |
|---|---|
| `--input banana --sort` | `aaabnn` |
| `--input abc --sha256` | `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad` |
| `--input abc --reverse --sha256` | `6d970874d0db767a7058798973f22cf6589601edab57996312f2ef7b56e5584d` |
| `--input "hello world" --sort --sha256` | `b990b0a2fb0a571f541a0cd35bd81271a2ef8e06a10484526e8308651e1d0562` |
| `--input Benchmarking --reverse --sort --sha256` | `4e5c331b4e299d20e4d5cce3c91febf10914e469461b55530a44ce5edde634b5` |
| `--input plain` | `plain` |

Each operation is exercised alone, in a pair, in all three, and not at all.

**A deliberate omission: no check has a whitespace-edged result.**
`--input "hello world" --sort` is correctly `" dehllloorw"` — a **leading
space**, because space is code point 32 and sorts first. The oracle strips
stdout, so that row would fail a correct implementation. Spaces still appear in
the inputs, but only where the result is a hash. If the oracle ever stops
stripping, that row becomes worth adding, since it is a real edge case.

## Verifying the expected values

They are computed, not asserted:

```sh
python3 - <<'PY'
import hashlib
def apply(text, ops):
    v = text
    if "reverse" in ops: v = v[::-1]
    if "sort" in ops:    v = "".join(sorted(v))
    if "sha256" in ops:  v = hashlib.sha256(v.encode()).hexdigest()
    return v

for text, ops in [("banana",["sort"]), ("abc",["sha256"]),
                  ("abc",["reverse","sha256"]),
                  ("hello world",["sort","sha256"]),
                  ("Benchmarking",["reverse","sort","sha256"]),
                  ("plain",[])]:
    print("%-14s %-28s %s" % (text, " ".join(ops) or "(none)", apply(text, ops)))
PY
```

## What this is expected to show

**Prediction, recorded before running:** most backends will still pass. This is
more typing, not more thinking, and every model measured so far cleared
`script-reverse`. If that holds, it is evidence for [#4](https://github.com/evanwtf/local-llm/issues/4) — the ceiling is not
about task size — and the task that actually discriminates will be one where a
*wrong* answer is plausible, not merely a longer one.

The failure modes worth watching for, none of which `script-reverse` can produce:

- applying operations in **flag order** rather than the stated fixed order
- ignoring `--sort` when combined with `--sha256` (chaining dropped)
- hashing the **original** input rather than the transformed value
- uppercase hex
- erroring instead of passing through when no operation flag is given

---

## Running it by hand against a local model

The harness is not required. To drive it yourself with OpenCode against
**Qwen3.8-Flash-Next `UD-Q3_K_XL`** on llama.cpp:

```sh
# 1. Free the GPU. Only one large model fits at a time.
pkill -f ds4-server; pkill -f llama-server; sleep 4

# 2. Start llama.cpp on the Q3 weights. The launcher defaults to Q2, so name
#    the model explicitly. Samplers are already Unsloth's thinking-mode preset:
#    --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0
MODEL=~/models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL/Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf \
ALIAS=qwen3.8-flash-next-q3 \
  benchmarks/llamacpp/llamacpp-up start

# 3. Wait for a real completion. /health answers "ok" while the model is still
#    loading, and curl exits 0 on a 503 -- do not trust either.
uv run python benchmarks/agent/wait_ready.py \
    --base-url http://127.0.0.1:8020 --model qwen3.8-flash-next-q3

# 4. Work in a scratch directory, never the repo.
mkdir -p /tmp/transform && cd /tmp/transform

# 5. --dir is REQUIRED. `opencode run` attaches to a persistent server that
#    holds its own working directory and ignores your shell's cwd. Without it
#    the file lands wherever that server started -- this cost us four backends
#    of results (#67).
opencode run --dir /tmp/transform --model llamacpp/qwen3.8-flash-next-q3 --auto \
  "$(sed -n '/^```text$/,/^```$/p' ~/git/local-llm/benchmarks/agent/SCRIPT-TRANSFORM.md | sed '1d;$d')"

# 6. Check it.
python3 transform.py --input banana --sort                       # aaabnn
python3 transform.py --input abc --reverse --sha256              # 6d970874...
python3 transform.py --input Benchmarking --reverse --sort --sha256   # 4e5c331b...
python3 transform.py --input plain                               # plain
```

Stop the server with `benchmarks/llamacpp/llamacpp-up stop` when you are done —
it holds ~60 GiB whether or not anyone is using it.
