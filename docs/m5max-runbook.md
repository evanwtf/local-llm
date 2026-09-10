# The M5 Max runbook

**How to run this machine.** The MacBook Pro, M5 Max, 128 GB unified memory —
the hardware every number in this repo was measured on. This file holds the
operational facts that live outside git: the sysctl the big models cannot load
without, the exact server argv the published rows were taken with, where every
engine tree and weight file sits, and the client configs the harness drives.

`NEXT.md` holds only what is happening *now*. Everything here stays true from
session to session. When a fact here changes, change it here.

Updated 2026-09-04. Machine data: `hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/`.

---

## The Metal ceiling — required, not an optimization

macOS caps GPU-wired memory below what a 90 GiB model needs, and the failure
looks like the model refusing to load for no clear reason.

```sh
sudo sysctl iogpu.wired_limit_mb=114688     # 112 GiB, on a 128 GB Mac
sysctl -n iogpu.wired_limit_mb              # expect 114688
```

**Why it is required.** ds4 sets
`budget_base = ds4_gpu_recommended_working_set_size()` — Metal's
`recommendedMaxWorkingSetSize`. Its 128 GiB-host guard branch (added in `b0c31af`)
tests `base_gib >= 120.0`, but the *working set* on a 128 GiB Mac is
107.52–112.00 GiB and never reaches 120 — **so that branch cannot fire on the
hosts it names**, and the budget comes entirely from the sysctl-override path.
Stock gives a **75.5 GiB** budget against an **89.87 GiB** GLM-5.3: a refusal.
With the sysctl raised, ds4 gets 110 GiB and the model runs. The kernel default
also refuses `glm53` (100.6 GiB resident against a 107.52 GiB default).

**It is a cap, not a reservation.** With the ceiling at 112 GiB and no model
loaded, wired memory sits at ~5 GiB. Persisting it costs nothing on a normal day;
what costs is leaving a 90 GiB model resident, which
`benchmarks/agent/preflight.py` reports on every run.

**`sysctl` reports `0` when no override is set, and `0` means "device default",
not "no ceiling".** A 0 after a reboot means the daemon did not fire. The
authoritative figure is the Metal probe in
[#30](https://github.com/evanwtf/local-llm/issues/30).

### It persists across reboots — and this was verified by an actual reboot

```sh
scripts/install-metal-ceiling.sh            # one-time, needs sudo
sysctl -n iogpu.wired_limit_mb              # expect 114688
```

The script installs a LaunchDaemon (`scripts/wtf.local-llm.metal-ceiling.plist`)
that applies the sysctl at boot. Verified by the 2026-09-01 reboot, from
`/var/log/metal-ceiling.log`:

```
iogpu.wired_limit_mb: 114688 -> 114688     # install time, a no-op
iogpu.wired_limit_mb: 0 -> 114688          # 23:14, after the 23:13:43 boot
```

The `0 ->` line is the evidence. The kernel came up at device default and the
daemon raised it, so the daemon is what is holding the value now.

**The install trap.** `install-metal-ceiling.sh` printed
`Load failed: 5: Input/output error` on the first install and the ceiling was
correct anyway — because it had been set by hand minutes earlier. **A correct
`sysctl` reading is equally consistent with a daemon that never ran.** The
script was rewritten to use `launchctl bootstrap` (the legacy `load -w` is what
emitted the spurious error) and it now reports the job's load state and the
log's last line instead of a bare `sysctl` reading.

## Disk

Measured 2026-08-28 (`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/disk/RESULTS.md`):
sequential **9.45 GiB/s**, random 1 MiB **198 us**, random 4 KiB **61 us**. A
100-byte lookup costs one 4 KiB block — there is no smaller unit.

**Large single-file downloads need `HF_HUB_ENABLE_HF_TRANSFER=1`.** HF speed
depends on shard count, not bandwidth: a 33-shard model pulled at 5.9 GiB/min
while a single 196 GiB file managed 0.45 until hf_transfer was installed.

## The ds4 Qwen server — the current cell

The exact argv the `qwen38fnds4*` rows are taken with (fast-pack on the
`ds4-metal` fork, MTP **off**):

```sh
cd ~/git/ds4-metal && ./ds4-server --metal \
  -m ~/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf \
  --ple ~/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf \
  --ctx 100000 --warm-weights \
  --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192 \
  --host 127.0.0.1 --port 8000
```

- **Run it from inside the tree.** ds4 looks for its Metal shaders relative to
  the working directory and fails to start otherwise.
- Warms in ~5 s, settles at **74.3 GiB**. 73.57 GiB of tensors are resident;
  the 32 GB PLE n-gram table is **not** — the server reports
  `PLE=SSD-pread/Q4_1-to-BF16-double-buffer`.
- Startup log must say **`Metal 4 tensor API enabled`** and
  **`complete fast path`**. If it does not, the M5 route is not engaged and the
  numbers are not comparable.
- **`ds4 --inspect` prints every specialization as `fallback` because inspect
  never initialises Metal.** That is not a real fallback.
- The MTP arm (issue #77/#39) adds **`--mtp-model`**, `--mtp-draft 7
  --mtp-timing` and — critically — **`--kv-disk-dir ~/.ds4/server-kv-mtp`**, a
  separate directory.

  ```sh
  --mtp-model ~/models/qwen3.8-flash-next-ds4-q4/qwen3.8-flash-next-q4-mtp.gguf
  ```

  **`--mtp-model` is not optional and this file omitted it until 2026-09-07.**
  The Qwen MTP head lives entirely in that sidecar — the main gguf carries
  **zero** MTP tensors — and there is no auto-discovery of a `-mtp.gguf`
  sibling: `ds4.c:67798 at ds4-metal ba01f5d` gates the whole load on
  `opt->mtp_path && opt->mtp_path[0]`, and `mtp_ready` is set nowhere else.

  `--mtp-draft 7 --mtp-timing` are accepted without complaint regardless, so
  the argv looks like an MTP arm either way. The server is the one that says
  which it is, on its `Qwen graph allocated` line — the same build and model,
  one flag apart:

  | argv | graph line |
  |---|---|
  | without `--mtp-model` | `MTP=off verifier=off` |
  | with `--mtp-model` | `MTP=Q4_K/Q8_0/BF16 verifier=block/max16` |

  With the sidecar it also prints `MTP sidecar loaded: ... (state=ready
  draft=7)`. **Read that line before trusting an MTP row.**
  An engine flag that changes the KV format makes ds4 reject the other
  configuration's checkpoints (`Qwen checkpoint MTP state is incompatible`),
  so mixing directories makes one arm re-prefill where the other got cache
  hits, and the only symptom is that it looks slower. `~/.ds4/server-kv` is
  MTP-off, `~/.ds4/server-kv-mtp` is MTP-on. They are not interchangeable.

## The ds4 DeepSeek server

```sh
cd ~/git/ds4 && ./ds4-server -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
    --warm-weights --ctx 100000 --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192
```

**`--kv-disk-space-mb` is sized for DeepSeek.** Its entries are ~560 MiB;
GLM-5.3's are **6,012–8,061 MiB**, so the 8192 default holds one and evicts
every turn. Raise it for any non-DeepSeek model — though per
[ds4#816](https://github.com/antirez/ds4/issues/816) it will not fix `hits=0`.

## The launchers take overrides

`llamacpp-up` / `ds4-up` accept `MODEL`, `ALIAS`, `CTX`, `BACKEND`,
`EXTRA_FLAGS`, and for llama.cpp `TEMP/TOP_P/TOP_K/MIN_P`. Two traps:

- **`WARM=''` is only correct together with `--ssd-streaming`.** Alone it
  leaves weights neither resident nor streamed: RSS 3.1 GiB for an 89.9 GiB
  model, every forward pass faulting from disk, 91 s of decode inside a
  2,470 s trial.
- **`ds4-up`'s `WARM=''` is required for streaming measurement** for the same
  reason in the other direction: `--warm-weights` touches every page and
  defeats `--ssd-streaming` silently, reporting full residency.

## Engine trees

| path | commit | purpose |
|---|---|---|
| `~/git/ds4-metal` | branch `qwen3.8-flash-next` @ `2021dda`; upstream `ba01f5d` | **the runtime for the Qwen fast-pack** (ivanfioravanti's fork). `make -j8 ds4 ds4-server` builds clean, no patches. Fast-forwarding to `ba01f5d` was a no-op for the binary — both commits touch only docs, a test fixture and a repack script. |
| `~/git/ds4` | mainline | DeepSeek-V4-Flash; `download_model.sh` is the only supported layout source. See AGENTS.md's sherpa section before evaluating any model he covers. |
| `~/git/ds4-glm53` | branch `glm-5.3-flash` | **antirez force-pushes this preview branch.** Our checkout sat on `a60a2a0`; the rewritten tip carried a commit with the same message and a different SHA (`147109a`), and `git merge-base --is-ancestor` said our old HEAD was not an ancestor. **Check ancestry, not the commit count**, before assuming a rebuild is an increment. |
| `~/git/llama.cpp` | **`d7bd3bfca` (mainline master)** | qwen4exp, now merged upstream. The old pinned build is tagged **`benchmark-pr27742-2026-08-26`** — the PR was squash-merged, so its commits are NOT in mainline history and the tag is the only way back to the exact build every earlier `qwen38fnq2`/`q3` row used. |
| `~/git/llama.cpp-glm52pr` | `8a8d0bcc4` (PR #27752) | serves `glm53`. Clean, unpatched. |
| `~/git/llama.cpp-glm53` | `9370c82db` (PR #27773) | the failed attempt, **166 lines of uncommitted patches**. Two are independently upstream-worthy ([#25](https://github.com/evanwtf/local-llm/issues/25)). Do not build GLM here. |

Two `ds4` worktrees exist for the [#118](https://github.com/evanwtf/local-llm/issues/118) A/B:
`~/git/ds4-main` @ `b0a147a` (clean rebuild) and `~/git/ds4-pr964` @ `8969dbb`
(branch `pr-964` of the `~/git/ds4` hub), each built in place so each arm reads
its own `metal/*.metal`. **The ds4 Makefile tracks no header dependencies**
(`grep -MMD` finds nothing): after any checkout that changes `ds4.h` and friends,
`make clean` before building — an incremental build over an older checkout links
mixed-vintage objects silently, with no warning (#118's near-miss: 3 objects
compiled against the old header, caught before anything was measured).

**`b10729` is preserved** at `~/llamacpp-builds/b10729/bin`. It produced every
published llama.cpp number, and a `git pull` plus in-place rebuild would have
destroyed it.

## Weights on disk

> **The live listing is the census, not this table:** `uv run python
> benchmarks/agent/model_inventory.py` (preflight prints it every run). See
> [`docs/model-locations.md`](model-locations.md). The table below is a
> convenience with per-pack notes and goes stale; the census does not.

**More than two roots, and this matters.** Weights live in several trees, and a
search of the wrong one is a false negative:

- **`~/models/`** — every GGUF and ds4 pack, downloaded by hand (`hf download`,
  `download_model.sh`). This is what `--dir`/`--model` on ds4 and llama.cpp
  point at. Browse with `ls ~/models` / `du -sh ~/models/*/`.
- **`~/.mlx-serve/models/<org>/<pack>`** — every **mlx-serve** pack, managed by
  `mlx-serve pull <org/repo>`. It is **not** under `~/models`, so `ls ~/models`
  will not show it. List these with **`mlx-serve list`**, never a raw `ls` of
  `~/models`. A ds4-metal GGUF of a model and its mlx-serve pack are different
  files in different trees — e.g. Qwen3.8-Flash-Next lives as a GGUF in
  `~/models/qwen3.8-flash-next-ds4-q4k-imatrix` *and* as an MLX pack in
  `~/.mlx-serve/models/ddalcu/...`.

### mlx-serve packs — `~/.mlx-serve/models/` (via `mlx-serve list`)

| pack (org/repo) | size | notes |
|---|---|---|
| `ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit` | **100 GB** | the mlx-serve frontier pack (mixed 4/8-bit). The `stack_agent_ab.py` / `mlx-serve-vs-ds4` MLX arm. Served by directory name; `mlx-serve serve` loads it on demand. |
| `mlx-community/gemma-4-e4b-it-4bit` | 4.8 GB | small Gemma 4 test pack |

### GGUF / ds4 packs — `~/models/` and `~/git/ds4/gguf/`

| path | size | notes |
|---|---|---|
| `~/models/qwen3.8-flash-next-ds4-q4` | **113 GB** | the DS4 fast-pack (base 79 + PLE 32 + MTP 1.6 + vision 0.5), **not a llama.cpp GGUF** — standard GGUF tools will not load it. Contains a **symlink** `...Q4KExperts...gguf` → `...Q40RoutedExperts...gguf`; the manifest names the former and that is deliberate, so **keep the symlink**. Our copy of the manifest predates HF's `2026-09-02T23:07Z` update (we downloaded 19:50); weights are identical (`tensor_manifest_sha256` unchanged) — re-fetch only the manifest before quoting its recipe. |
| `~/models/qwen3.8-flash-next-ds4-q4k-imatrix` | 68 GB | **the frontier ds4 pack** — Q4_K imatrix experts + PLE sidecar (`Qwen3.8-Flash-Next-PLE-Q4_1.gguf`). The recommended-setup weights (196/196, ~95 s). |
| `~/models/Qwen3.8-Flash-Next-GGUF` | 157 GB | Q2 + Q3 (`UD-Q3_K_XL` is the recommended llama.cpp stack) |
| `~/models/Qwen3.8-Flash-Next-REAP320` | 64 GB | REAP-320 expert-pruned build |
| `~/models/deepseek-v4-flash-aproj` | 159 GB | DeepSeek-V4-Flash AProjQ4 (84 GB) + AProjQ8 (87 GB) imatrix packs (#91/#162) |
| `~/models/qwen38fn-mtplx-optimized-speed` | 107 GB | MTPLX-optimized Qwen3.8-Flash-Next |
| `~/models/GLM-5.3-Flash-MLX-2bit-lite` | 5.8 GB | small GLM MLX 2-bit test pack |
| `~/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf` | 90 GB | antirez — declares `glm5-next`. **Works**: verified 2026-08-30 on the `glm-5.3-flash` branch — loads, coherent at `--temp 0`, **35.9 t/s** decode. The old "unusable, no engine loads it" note was wrong — it was tested on the wrong engine build. |

Inventory above read 2026-09-10 (`du -sh ~/models/*/`, `mlx-serve list`); sizes
drift as packs are pulled and cleared, so re-read before quoting one. The
one-hyphen architecture-name rule (`glm5-next` vs `glm5next`) is in AGENTS.md —
check `general.architecture` with `uv run python scripts/gguf_meta.py <file>`
before debugging output.

## Clients

- **`~/.config/opencode/opencode.json`** holds the providers: `ds4` (model
  `qwen3.8-flash-next-q4` added to it) and `ds4qwenshim` pointing at the
  tool-format shim on `:8101`. A backup sits alongside the file. The shim
  itself is `ds4_qwen_tool_shim.py` at the repo root: `--port` (default 8101),
  `--upstream` the real ds4 server.
- **Codex profiles** in `~/.codex/*.config.toml` are not in git. All need
  `wire_api = "responses"`; 0.148.0 removed `"chat"`. **All llama.cpp profiles
  point at the shim (:11500/:11501), not the server** — Codex 0.150.1 sends
  both `instructions` and a `role=developer` item, which llama-server turns into
  two chat system messages and the Qwen template rejects.
- **Ollama is `/Applications/Ollama.app`** — update it from the app, not the
  CLI.
- **Nothing pins the client versions** ([#131](https://github.com/evanwtf/local-llm/issues/131)).
  Versions drift silently (OpenCode 1.18.26 → 1.18.27 arrived by itself on both
  machines; Codex 0.148 → 0.150 started a new row series). `preflight.py`
  reports current versions before every batch; do not pool rows across a client
  version bump.

## The benchmark target repo

`~/git/gmail-archive` sits on its own branch **`local-llm-benchmark`** @
`56e55cc`, deliberately behind `origin/main`. While the checkout was held back
on `main`, `origin/main` got 73 commits ahead — a `git pull` would have broken
every benchmark silently. `main` can now track upstream freely. The harness
moves the real checkout aside to `<name>-real` for the duration of a run and
restores it at exit; the restore is `run.restore_targets()` (see AGENTS.md for
why never to `pkill`).

## Check before every batch

```sh
uv run python benchmarks/agent/preflight.py
```

It names any server holding memory the run does not want, reports the Metal
ceiling and the client versions, and refuses on the machine-state problems that
ruin batches. One known false alarm while it does: selecting only a shim
backend flags the upstream ds4-server as stale
([#132](https://github.com/evanwtf/local-llm/issues/132)) — the shim on
`:8101` proxies to `:8000`, and `:8000` is named by no selected backend.

## The run lock

`run.py`, `scripts/decode_ab.py`, `scripts/decode_ab_engine.py` and both
restart-between-trials scripts claim `.run-lock.json` before loading anything
and refuse if another live process holds it ([#133](https://github.com/evanwtf/local-llm/issues/133)).

A run killed by `SIGKILL` leaves the file behind. `preflight` reports it as
stale, names what it was doing and when, and **does not take it** — remove it
deliberately, so a crashed run gets noticed rather than paved over.
`--no-lock` opts out. The lock knows nothing about downloads: 171 GB of disk
traffic under a benchmark is still yours to avoid.

## What a comparison must do to be readable

These lived in NEXT.md, which is a ranking. They belong with the machine.

**A quiet machine is not optional.** No builds, no large-file reads, no
CPU-heavy work from any session while a batch holds the lock. A build landing
in one arm and not the other is the confound that made #146's first attempt
uninterpretable, and a test suite landing in one arm and not the other is what
voided run 2 of #162 Task 3.

**Position bias is measured, not assumed** (#130, #201). Whichever arm runs
first is faster in 9 of 12 reps, median +0.9%, and **+5.9% on the first rep of
a cold session**, decaying over about an hour. Any comparison below ~1% is
inside that.

**`REPS` defaults to 4 and an odd count is refused** across all four A/B
scripts (#201). Alternation cancels the bias only on an even count, so an odd
sweep produced a complete CSV and a plausible number with nothing to say half
the design was missing. `ALLOW_ODD_REPS=1` overrides, deliberately.

**Durations are estimates.** Nothing is anchored to a clock -- each step
starts when the one before it releases the lock.

## Thermal tests: what a fan or cooling comparison must do

Established by #276 (fans auto vs max), which needed four attempts before the
protocol was sound. Every restart was a real defect, and each one is a trap
the next thermal test will hit too.

### The protocol

`scripts/fan_ab.py` implements this and `tests/test_fan_ab_protocol.py`
asserts it, so it cannot drift silently:

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

Three segments, arms `auto` then `max`, giving A,B,A,B,A,B. Step 8 runs once
at the end so the machine is handed back in the state the next run begins
from.

### Settled means a slope, never a difference of means

**Any threshold on a difference implies a rate limit nobody wrote down.**
Compute that rate before shipping the test and ask whether it is the one you
would have chosen.

The first gate compared two consecutive 30-second medians against 0.3 C. A die
cooling at a constant `r` C/hour moves `r/120` C between those windows, so it
passed **every rate below 36 C/hour** -- a degree and a half of drift across a
three-minute phase, reported as settled. Nobody would choose 36 C/hour. It was
never written down.

The office ambient watcher hit the identical failure the same evening, its own
`delta10 < 0.2 C` bar satisfied while the room fell monotonically at 1.0
C/hour. Two independent instruments, one wrong instrument shape.

Use a least-squares slope over a trailing window. A slope is zero only when
the quantity has stopped moving, and it needs no knowledge of the floor -- so
it survives the room being air-conditioned underneath it.

### Calibrate the bound against measured noise

`scripts/calibrate_settle.py` reads monitord and reports the slope
distribution over stretches the machine was genuinely idle. A settled M5 Max
die shows a trailing 120 s slope with median 0.120 C/min and p90 0.277 C/min
-- that is sensor noise fitted by least squares. The bound sits at that p90
(0.30 C/min): below it the wait rarely ends, above it the wait ends on noise.

Calibrating also corrected the predicate. At `grace=0` a day's 30,324 idle
samples fragment into 3,688 runs, of which **three** reach five minutes,
because a one-second CPU blip ends a stretch the die never noticed. Allow a
grace window, and say what you allowed -- loosening it far enough admits
stretches where the machine was working and the noise floor inflates
sevenfold.

### Give the ceiling room to actually reach idle

420 s was not enough: from 74.11 C the die reached 34.13 C and was **still
falling at 0.809 C/min**. The cost is not the wait, it is that the first phase
follows a long idle and every later phase follows a timeout, so phase 1 starts
several degrees colder than the rest and spends a pair. Use 900 s.

### Cool on max regardless of the arm that follows

The cooldown exists to make phases start **alike**, not to reach a particular
temperature. Cooling on each phase's own mode leaves the auto arm at a higher
floor than the max arm -- a temperature difference between conditions at t=0,
which is the thing the cooldown removes.

It also biases conservatively: the auto arm gets a cooler start than ordinary
auto operation would give it, so a max-fan win is not a starting-point
artefact.

### Do not "settle in" on the arm's mode before measuring

This one looked careful and was the largest threat to the comparison. Holding
the phase's own fan mode before the first rep absorbs the max->auto switch
transient by letting the **auto arm warm** toward its higher floor while the
max arm sits still: after 120 s the arms began at ~35 C and ~33 C. Both arms
must leave the same floor and start at once. The fans responding to load is
the treatment, not a transient to wait out, and under load the die passes
70 C within seconds.

### Record which way every wait ended

`plateau`, `timeout`, or `no_fit`. A phase preceded by a timeout began on a
machine still shedding heat and is not comparable to one that began from a
plateau.

`no_fit` is its own outcome because **"no answer" reads in a log exactly like
"not yet"**. If the sensor starves the window the test never evaluates, and a
wait that runs to its ceiling looks identical to a die that was still falling.
Count the evaluations that produced a number; zero of them at timeout means
the phase began on an *unknown* thermal state, which is worse than a known-hot
one because known-hot can be corrected for.

### Measure the arm, do not assert it

Join monitord's 1 Hz series to the rep boundaries (`scripts/lib/monitord.py`).
Mean fan rpm says what the fans **did**; `fancontrol` reporting `forced` says
only what was commanded. #276 measured 2,233 rpm on auto against 5,565 on max.

Carry **SoC power** in the same join. It is what makes a result interpretable
rather than merely observed: the chain a positive result needs is max fans ->
cooler die -> less throttling -> higher sustained power -> more tok/s. Without
power, a null cannot be told apart from a machine that was never thermally
limited. In #276, -8.48 C bought +0.78% power and +0.6% decode -- three
measurements that reconcile, which is what separates a mechanism from a
correlation.

### Audit the gate afterwards

`fan_ab_report.py` prints the spread of `start_die_c` across phases. That is
the cooldown's own audit -- it exists to make phases start alike, and this is
whether it did, independent of what each cooldown reported about itself. #276
came out at 5.91 C, with phase 1 the outlier because it followed an idle
rather than a load.

### Fan safety

`fancontrol set` is never called: it is the one command that can hold fans
*below* what thermal policy asks for. Only `max` and `auto`. Always `sudo -n`,
which refuses rather than waiting on an invisible password prompt. Restore
from a `finally`, an `atexit` hook **and** a SIGINT/SIGTERM handler, then
verify `mode == auto` and log an error if it is not -- fans left forced are
loud and nothing expires them but `auto` or a reboot.

Make the signal handler one-shot. A second signal during teardown raises a
second `SystemExit` from wherever the first had reached: on 2026-09-09 `uv`
forwarded a SIGTERM while a direct one was also sent, the second landed inside
`child.terminate`, and a `ds4-bench` orphan kept the GPU after the driver had
released the lock and logged a clean shutdown.

## ollama 0.33.3 and the sampler boundary

ollama on this machine is **0.33.3**, installed 2026-09-03 18:19. From that
version, model-authored GGUF sampler defaults outrank ollama's built-ins
([#84](https://github.com/evanwtf/local-llm/issues/84), ollama#16471).

Every ollama-backed row this project holds predates the change. As of
2026-09-04 the boundary has not been crossed *in the data* — the 90 rows
written since the upgrade are all `qwen38fnds4shim` or `qwen38fnds4mtp7shim`,
which do not go through ollama. **The next ollama-backed row is the first
under the new precedence and must not be pooled with earlier ollama rows.**
Rows now carry the resolved sampler, so the two regimes are distinguishable
after the fact.

## The agent clients are pinned

`client-versions.toml` holds the expected version of each client, and
`preflight.py` **refuses** when an installed client differs from its pin
([#131](https://github.com/evanwtf/local-llm/issues/131)). Every other version
difference in preflight is a warning; this one is not, because #104 measured
OpenCode 1.18.26 → 1.18.27 roughly doubling median turns with everything else
held, and the symptom read as the model writing bad patches.

`--allow-client-drift` overrides it. Rows produced that way are a new series
and must not be pooled with earlier ones.

### How OpenCode updates itself, and how to stop it

Found by inspecting the shipped binary on 2026-09-04. Recorded here because
"we turned it off" is not reproducible on a fresh install.

- **Environment:** `OPENCODE_DISABLE_AUTOUPDATE`
- **Config:** `"autoupdate": false` in `~/.config/opencode/opencode.json`
- **Deliberate upgrade:** `opencode upgrade [target]` — this is the path a pin
  move should take
- It checks `api.opencode.ai`

Either switch is enough, and `preflight` reports which one it found. **As of
2026-09-04 neither is set on this machine**, so the pin can still move under a
batch; preflight warns about that separately from the pin check itself.

Preflight only reports this. It does not edit the client config — a benchmark
harness silently changing the tool under test is the class of thing #131
exists to prevent.

### How Claude Code updates itself, and how to stop it

Found by inspection on 2026-09-04, after it self-updated **2.1.260 → 2.1.261
mid-session** and `preflight` refused on the pin ([#131](https://github.com/evanwtf/local-llm/issues/131)).

- **Environment:** `DISABLE_AUTOUPDATER`. The binary also knows
  `CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE`.
- **No settings key.** `~/.claude/settings.json` has no `autoUpdate`
  equivalent, so the environment variable is the switch.
- **Versions live side by side** in `~/.local/share/claude/versions/`, and
  `~/.local/bin/claude` is a symlink into them. On 2026-09-04 that directory
  held 2.1.258 through 2.1.261.

**So a downgrade is a symlink repoint, not a reinstall:**

```sh
ln -sfn ~/.local/share/claude/versions/2.1.260 ~/.local/bin/claude
```

That matters for a pin decision. Returning to the pinned version costs
nothing and keeps every existing Claude Code row comparable; moving the pin
forward starts a new series. The symlink's mtime is also the record of *when*
it moved — 16:13 on the day it happened, which no log announced.

`preflight` refuses on a client that has drifted from its pin, so no batch
will start until this is resolved either way. `--allow-client-drift` runs
anyway and accepts the series break.
