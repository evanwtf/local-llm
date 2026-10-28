# The M5 Max runbook

**How to run the M5 Max MacBook Pro (128 GB unified memory).** This file holds
the operational facts that live outside git: the sysctl the big models cannot
load without, the server argv behind the ds4 rows, where engine trees and
weights sit, and the client configs the harness drives.

The issue queue (`scripts/make_next.py --platform macos`) holds what is
happening now. This file holds what stays true from session to session; when a
fact changes, change it here. Machine data:
`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/` (firmware and toolchain in
its `VERSIONS.md`, the picks in its `RECOMMENDATIONS.md`).

---

## The Metal ceiling — required, not an optimization

macOS caps GPU-wired memory below what a 90 GiB model needs. The failure looks
like the model refusing to load for no clear reason.

```sh
scripts/install-metal-ceiling.sh            # one-time, needs sudo; persists across reboots
sysctl -n iogpu.wired_limit_mb              # expect 114688 (112 GiB)
```

**Why it is required.** ds4 sets
`budget_base = ds4_gpu_recommended_working_set_size()` (Metal's
`recommendedMaxWorkingSetSize`). Its 128 GiB-host branch (added in `b0c31af`)
tests `base_gib >= 120.0`, but the working set on a 128 GiB Mac is
107.52–112.00 GiB, so that branch cannot fire and the budget comes only from
the sysctl override. Stock gives a **75.5 GiB** budget against an **89.87 GiB**
GLM-5.3: a refusal. With the sysctl raised, ds4 gets 110 GiB and the model runs.
The kernel default also refuses `glm53` (100.6 GiB resident against a
107.52 GiB default).

**It is a cap, not a reservation.** With no model loaded, wired memory sits at
~5 GiB. What costs memory is a resident model, which
`benchmarks/agent/preflight.py` reports on every run.

**`sysctl` reports `0` when no override is set, and `0` means "device
default".** A 0 after a reboot means the daemon did not fire. The authoritative
figure is the Metal probe in
[#30](https://github.com/evanwtf/local-llm/issues/30).

The script installs a LaunchDaemon (`scripts/wtf.local-llm.metal-ceiling.plist`)
that applies the sysctl at boot and logs each change to
`/var/log/metal-ceiling.log`. A line `iogpu.wired_limit_mb: 0 -> 114688` after
a boot is the proof that the daemon holds the value. **Do not trust a correct
`sysctl` reading alone**: a value set by hand looks the same as one the daemon
set. The script reports the job's load state and the log's last line for that
reason.

## Disk

Measured 2026-08-28
(`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/disk/RESULTS.md`):
sequential **9.45 GiB/s**, random 1 MiB **198 us**, random 4 KiB **61 us**.

**Large single-file downloads need `HF_HUB_ENABLE_HF_TRANSFER=1`.** HF speed
depends on shard count: a 33-shard model pulled at 5.9 GiB/min, a single
196 GiB file at 0.45 GiB/min until hf_transfer was installed.

## The ds4-metal Qwen server (the `qwen38fnds4*` rows)

The argv behind the ds4-metal fast-pack rows (MTP **off**). Each backend's
exact tree is its `engine_tree` in `benchmarks/agent/tasks.toml`.

```sh
cd ~/git/ds4-metal && ./ds4-server --metal \
  -m ~/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf \
  --ple ~/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf \
  --ctx 100000 --warm-weights \
  --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192 \
  --host 127.0.0.1 --port 8000
```

- **Run it from inside the tree.** ds4 finds its Metal shaders relative to the
  working directory and fails to start otherwise.
- It warms in ~5 s and settles at **74.3 GiB**. The 32 GB PLE n-gram table is
  not resident: the server reports `PLE=SSD-pread/Q4_1-to-BF16-double-buffer`.
- The startup log must say **`Metal 4 tensor API enabled`** and
  **`complete fast path`**. If not, the M5 route is not engaged and the numbers
  are not comparable.
- `ds4 --inspect` prints every specialization as `fallback` because inspect
  never initializes Metal. That is not a real fallback.

**The MTP arm** ([#39](https://github.com/evanwtf/local-llm/issues/39)) adds
three things:

```sh
--mtp-model ~/models/qwen3.8-flash-next-ds4-q4/qwen3.8-flash-next-q4-mtp.gguf \
--mtp-draft 7 --mtp-timing --kv-disk-dir ~/.ds4/server-kv-mtp
```

- **`--mtp-model` is required.** The main GGUF carries no MTP tensors and ds4
  does not discover a `-mtp.gguf` sibling (`ds4.c:67798 at ds4-metal ba01f5d`
  gates the load on `opt->mtp_path`). Without it, `--mtp-draft 7` is accepted
  silently and the arm runs without MTP.
- **Read the `Qwen graph allocated` line before you trust an MTP row.** It says
  `MTP=off verifier=off` without the sidecar and
  `MTP=Q4_K/Q8_0/BF16 verifier=block/max16` with it, followed by
  `MTP sidecar loaded: ... (state=ready draft=7)`.
- **Keep the two KV directories apart.** ds4 rejects the other configuration's
  checkpoints (`Qwen checkpoint MTP state is incompatible`), so a shared
  directory makes one arm re-prefill, and the only symptom is a slower arm.
  `~/.ds4/server-kv` is MTP-off; `~/.ds4/server-kv-mtp` is MTP-on.

## The ds4 DeepSeek server

```sh
cd ~/git/ds4 && ./ds4-server -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
    --warm-weights --ctx 100000 --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192
```

**`--kv-disk-space-mb 8192` is sized for DeepSeek**, whose entries are
~560 MiB. GLM-5.3's are 6,012–8,061 MiB, so 8192 holds one and evicts every
turn. Raise it for any other model; per
[ds4#816](https://github.com/antirez/ds4/issues/816) that does not fix `hits=0`.

## The launchers take overrides

`llamacpp-up` (`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/llamacpp/`)
and `ds4-up` (`…/benchmarks/ds4/0731/agent/`) accept `MODEL`, `ALIAS`, `CTX`,
`BACKEND`, `EXTRA_FLAGS`, and for llama.cpp `TEMP/TOP_P/TOP_K/MIN_P`. Two traps:

- **`WARM=''` is correct only together with `--ssd-streaming`.** Alone it
  leaves weights neither resident nor streamed: RSS 3.1 GiB for an 89.9 GiB
  model, and every forward pass faults from disk.
- **A streaming measurement needs `WARM=''`.** `--warm-weights` touches every
  page, silently defeats `--ssd-streaming`, and reports full residency.

## Engine trees

Each backend names its tree in `tasks.toml` (`engine_tree`), and every row
stamps that tree's commit. Read the tree from there, not from a list here. The
traps that apply across trees:

- **antirez force-pushes the `glm-5.3-flash` preview branch** (`~/git/ds4-glm53`).
  A rewritten tip can carry a commit with the same message and a new SHA.
  **Check ancestry** (`git merge-base --is-ancestor`), not the commit count,
  before you treat a rebuild as an increment.
- **The ds4 Makefile tracks no header dependencies.** After a checkout that
  changes `ds4.h` or another header, run `make clean` before you build. An
  incremental build links objects from two vintages with no warning (#118
  caught three such objects before anything was measured).
- **`~/git/ds4`** (mainline DeepSeek-V4-Flash): `download_model.sh` is the only
  supported layout source. Read the "antirez is the sherpa" section of
  [`docs/harness-operations.md`](harness-operations.md#antirez-is-the-sherpa--check-his-path-before-designing-your-own)
  before you evaluate a model he covers.
- **`~/git/llama.cpp-glm53`** holds 166 lines of uncommitted patches from a
  failed GLM build ([#25](https://github.com/evanwtf/local-llm/issues/25)). Do
  not build GLM there; `~/git/llama.cpp-glm52pr` serves `glm53` clean.
- **Two llama.cpp builds are preserved on purpose.** The tag
  `benchmark-pr27742-2026-08-26` is the only way back to the build behind the
  early `qwen38fnq2`/`q3` rows (the PR was squash-merged, so its commits are not
  in mainline). `~/llamacpp-builds/b10729/bin` produced the earliest published
  llama.cpp numbers; a pull and in-place rebuild would destroy it.

## Weights on disk

**The census is the live listing:** `uv run python
benchmarks/agent/model_inventory.py` (preflight prints it on every run). The
roots and how to list each are in [`docs/model-locations.md`](model-locations.md).
A search of the wrong root is a false negative: mlx-serve packs live under
`~/.mlx-serve/models/`, not `~/models`, so list them with `mlx-serve list`.

Per-pack notes that the census cannot carry:

- **`~/models/qwen3.8-flash-next-ds4-q4`** is a ds4 fast-pack, not a llama.cpp
  GGUF; standard GGUF tools do not load it. It contains a symlink
  `...Q4KExperts...gguf` → `...Q40RoutedExperts...gguf`. The manifest names the
  symlink, so **keep the symlink**. The local manifest predates HF's
  `2026-09-02T23:07Z` update (weights identical, `tensor_manifest_sha256`
  unchanged); re-fetch the manifest before you quote its recipe.
- **`~/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf`** (antirez) declares `glm5-next` and
  works on the `glm-5.3-flash` branch: it loads, is coherent at `--temp 0`, and
  decodes at 35.9 t/s (2026-08-30). One hyphen in `general.architecture`
  decides which engine loads a file; read it with
  `uv run python scripts/gguf_meta.py <file>` before you debug output.

### Weights live on the `Models` volume (#755)

Every model directory on the M5 Max lives on a separate APFS volume,
`/Volumes/Models`, in the internal container. The default paths are symlinks
into it, so no engine or config changed:

| path | on the volume |
|---|---|
| `~/models` | `models` |
| `~/.ollama/models` | `ollama` |
| `~/.ds4` | `ds4-state` |
| `~/.mlx-serve/models` | `mlx-serve` |
| `~/git/ds4/gguf` | `ds4-gguf` |
| `~/.mtplx` | `mtplx` |
| `~/.sushi` | `sushi` |
| `~/.cache/huggingface` | `huggingface` |
| `~/git/mlxfast-gemma4-26b-a4b/weights` | `mlxfast-weights` |
| `~/.lmstudio` | `lmstudio` |

**Why a volume, not only exclusions:** a local Time Machine snapshot covers the
whole Data volume and ignores exclusions. On 2026-09-25 a 1,004 GiB delete freed
almost nothing until `sudo tmutil deletelocalsnapshots /` ran. Time Machine
excludes the `Models` volume as a whole and takes no local snapshots of it, so a
delete there frees its space at once.

**A new engine with a new model directory:** move the directory onto the volume
and leave a symlink. Copy, verify, and only then delete:

```sh
SRC=~/.newengine; DST=/Volumes/Models/newengine
/opt/homebrew/bin/rsync -aHX "$SRC/" "$DST/"
test -z "$(/opt/homebrew/bin/rsync -aHXn --checksum --itemize-changes "$SRC/" "$DST/")" \
  && rm -rf "$SRC" && ln -s "$DST" "$SRC"
```

**Then check that Time Machine backs up no model file** (#440: six unexcluded
directories, 773 GB, once filled the network backup share to 94%):

```sh
uv run python scripts/tm_model_guard.py      # exit 1 names every included model file
```

If it names a file that must stay off the volume, exclude its directory with
the fixed-path form, `sudo tmutil addexclusion -p <directory>`. A sticky
exclusion (no `-p`) is lost when the directory is deleted and made again.
`tmutil addexclusion -v` and `tmutil disable` need Full Disk Access for the
terminal; set a volume exclusion in System Settings > Time Machine > Options
instead.

## Clients

- **`~/.config/opencode/opencode.json`** holds the providers, among them `ds4`
  and `ds4qwenshim`, which points at the tool-format shim on `:8101`. The shim
  is `ds4_qwen_tool_shim.py` at the repo root: `--port` (default 8101),
  `--upstream` the real ds4 server.
- **Codex profiles** in `~/.codex/*.config.toml` are not in git. All need
  `wire_api = "responses"`; Codex 0.148.0 removed `"chat"`. **The llama.cpp
  profiles point at the shim (:11500/:11501), not the server**: Codex sends
  both `instructions` and a `role=developer` item, llama-server turns them into
  two system messages, and the Qwen template rejects that.
- **Ollama is `/Applications/Ollama.app`.** Update it from the app, not the CLI.
  From 0.33.3, sampler defaults in a model's GGUF outrank Ollama's built-ins
  ([#84](https://github.com/evanwtf/local-llm/issues/84)). Do not pool ollama
  rows across that boundary; rows carry the resolved sampler, so the two
  regimes stay separable.

### Client versions: recorded, and kept current

Rows record each client's version (`client_version`), and nothing pins it
([#131](https://github.com/evanwtf/local-llm/issues/131); `client-versions.toml`
explains why). Since 2026-09-28, `benchmarks/agent/currency.py` refuses a batch
while a component it checks is behind its latest release (#726). Update every
client **before** a batch, never during one: OpenCode 1.18.26 → 1.18.27 once
arrived mid-session and about doubled median turns with everything else held
(#104). Do not pool rows across a client version.

`preflight.py` reports whether each client's auto-update is off. The switches:

- **OpenCode:** `OPENCODE_DISABLE_AUTOUPDATE` in the environment, or
  `"autoupdate": false` in `~/.config/opencode/opencode.json`. Upgrade on
  purpose with `opencode upgrade [target]`.
- **Claude Code:** `DISABLE_AUTOUPDATER` in the environment; there is no
  settings key. Versions sit side by side in `~/.local/share/claude/versions/`,
  and `~/.local/bin/claude` is a symlink into them, so its mtime records when
  the version moved. Upgrade with `claude update`.
- **Codex:** `auto_update_enabled = false` in `~/.codex/config.toml`. Upgrade
  with `codex update`.

Preflight only reports these. It does not edit a client config: a harness that
changes the tool under test is the problem #131 exists to prevent.

## The benchmark target repo

`~/git/gmail-archive` stays on its own branch **`local-llm-benchmark`** at the
pinned `base_commit` in `tasks.toml`, so its `main` can track upstream freely.
Under the default `--targets legacy`, the harness moves the real checkout aside
to `<name>-real` for the run and restores it at exit with
`run.restore_targets()`; never `pkill` a run. `--targets sandbox` uses the
harness's own clones instead (#146).

## Check before every batch

```sh
uv run python benchmarks/agent/preflight.py
```

It names any server holding memory the run does not want, reports the Metal
ceiling and client versions, and refuses on the machine-state problems that
ruin batches.

## The run lock

`run.py` and the A/B scripts claim `.run-lock.json` before they load anything,
and refuse if another live process holds it
([#133](https://github.com/evanwtf/local-llm/issues/133)).

A run killed by `SIGKILL` leaves the file behind. `preflight` reports it as
stale, names what it was doing and when, and **does not take it**. Remove it
yourself, so a crashed run gets noticed rather than paved over. `--no-lock`
opts out. The lock knows nothing about downloads: do not run a large download
under a benchmark.

## What a comparison must do to be readable

**Keep the machine quiet.** No builds, large-file reads, or CPU-heavy work from
any session while a batch holds the lock. A build in one arm voided #146's
first attempt; a test suite in one arm voided run 2 of #162 Task 3.

**Position bias is measured, not assumed** (#130, #201). The arm that runs
first is faster in 9 of 12 reps, median +0.9%, and +5.9% on the first rep of a
cold session, decaying over about an hour. A difference below ~1% is inside
that.

**`REPS` defaults to 4, and the A/B scripts refuse an odd count** (#201).
Alternation cancels the bias only on an even count; an odd sweep once produced
a complete CSV and a plausible number with half the design missing.
`ALLOW_ODD_REPS=1` overrides it.

**Durations are estimates.** Each step starts when the one before it releases
the lock, not at a clock time.

## Thermal tests: what a fan or cooling comparison must do

These rules come from #276 (fans auto vs max), which needed four attempts
before the protocol was sound. Each rule is a defect that a thermal test hit.

### The protocol

`scripts/fan_ab.py` implements it and `tests/test_fan_ab_protocol.py` asserts
it:

```
1. max fans until the die plateaus (idle GPU)
2. set fans to the arm's mode
3. begin test
4. end test
5. max fans until the die plateaus
6. begin test
7. end test
8. max fans until the die plateaus   <- once, after the last cycle
9. fans auto
```

Three segments, arms `auto` then `max`, give A,B,A,B,A,B. Step 8 runs once at
the end, so the next run starts from the same state.

### Settled means a slope, never a difference of means

**A threshold on a difference implies a rate limit.** Compute that rate before
you ship the test, and ask whether you would choose it. The first gate compared
two consecutive 30-second medians against 0.3 C. A die cooling at `r` C/hour
moves `r/120` C between those windows, so the gate passed **every rate below
36 C/hour**. The office ambient watcher had the same defect (`delta10 < 0.2 C`
while the room fell at 1.0 C/hour).

Use a least-squares slope over a trailing window. A slope is zero only when the
quantity stops moving, and it needs no knowledge of the floor.

### Calibrate the bound against measured noise

`scripts/calibrate_settle.py` reads monitord and reports the slope distribution
over idle stretches. A settled M5 Max die shows a trailing 120 s slope with
median 0.120 C/min and p90 0.277 C/min: sensor noise. The bound sits at that
p90 (0.30 C/min). Below it the wait rarely ends; above it the wait ends on
noise.

Allow a grace window for one-second CPU blips, and record it. At `grace=0` a
day's 30,324 idle samples split into 3,688 runs and only three reach five
minutes. A grace window that is too loose admits busy stretches and inflates
the noise floor sevenfold.

### Give the ceiling room to reach idle: 900 s

At 420 s the die went from 74.11 C to 34.13 C and was still falling at
0.809 C/min. Phase 1 follows a long idle and later phases follow a timeout, so
a short ceiling makes phase 1 start several degrees colder.

### Cool on max, whatever arm follows

The cooldown makes phases start **alike**. Cooling on each arm's own mode leaves
the auto arm on a higher floor at t=0. Cooling on max also gives the auto arm a
cooler start than normal use, so a max-fan win is not a starting-point
artifact.

### Do not "settle in" on the arm's mode

Holding the arm's fan mode before the first rep lets the auto arm warm toward
its higher floor while the max arm stays put: after 120 s the arms began at
~35 C and ~33 C. Both arms leave the same floor and start at once. The fans'
response to load is the treatment; under load the die passes 70 C in seconds.

### Record how every wait ended

`plateau`, `timeout`, or `no_fit`. A phase after a timeout began on a machine
still shedding heat and does not compare with one after a plateau. `no_fit` is
separate because "no answer" reads in a log like "not yet": if the sensor
starves the window, zero evaluations at timeout means the phase began on an
unknown thermal state.

### Measure the arm, do not assert it

Join monitord's 1 Hz series to the rep boundaries (`scripts/lib/monitord.py`).
Mean fan rpm says what the fans did; `fancontrol` reporting `forced` says only
what was commanded. #276 measured 2,233 rpm on auto against 5,565 on max.

Carry **SoC power** in the same join. A positive result needs the chain max
fans → cooler die → less throttling → higher sustained power → more tok/s.
Without power, a null cannot be told apart from a machine that was never
thermally limited. In #276, -8.48 C bought +0.78% power and +0.6% decode.

### Audit the gate afterward

`fan_ab_report.py` prints the spread of `start_die_c` across phases: whether
the cooldown made phases start alike, independent of what each cooldown
reported. #276 came out at 5.91 C, with phase 1 the outlier.

### Fan safety

- Never call `fancontrol set`: it can hold fans below what thermal policy asks
  for. Use only `max` and `auto`.
- Always use `sudo -n`, which refuses rather than waits on a hidden password
  prompt.
- Restore from a `finally`, an `atexit` hook **and** a SIGINT/SIGTERM handler,
  then verify `mode == auto` and log an error if not. Forced fans stay loud
  until `auto` or a reboot.
- Make the signal handler one-shot. A second signal during teardown raises a
  second `SystemExit` mid-cleanup: on 2026-09-09 one landed inside
  `child.terminate`, and a `ds4-bench` orphan kept the GPU after the driver
  logged a clean shutdown.
