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

## The Metal ceiling — required, not an optimisation

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
- The MTP arm (issue #77/#39) adds `--mtp-draft 7 --mtp-timing` and —
  critically — **`--kv-disk-dir ~/.ds4/server-kv-mtp`**, a separate directory.
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

| path | size | notes |
|---|---|---|
| `~/models/qwen3.8-flash-next-ds4-q4` | **113 GB** | the DS4 fast-pack (base 79 + PLE 32 + MTP 1.6 + vision 0.5), **not a llama.cpp GGUF** — standard GGUF tools will not load it. Contains a **symlink** `...Q4KExperts...gguf` → `...Q40RoutedExperts...gguf`; the manifest names the former and that is deliberate, so **keep the symlink**. Our copy of the manifest predates HF's `2026-09-02T23:07Z` update (we downloaded 19:50); weights are identical (`tensor_manifest_sha256` unchanged) — re-fetch only the manifest before quoting its recipe. |
| `~/models/Qwen3.8-Flash-Next-GGUF` | 157 GB | Q2 + Q3 (`UD-Q3_K_XL` is the recommended llama.cpp stack) |
| `~/models/GLM-5.3-Flash-GGUF` | 101 GB | Unsloth Q2 — declares `glm5next` |
| `~/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf` | 90 GB | antirez — declares `glm5-next`. **Works**: verified 2026-08-30 on the `glm-5.3-flash` branch — loads, coherent at `--temp 0`, **35.9 t/s** decode. The old "unusable, no engine loads it" note was wrong — it was tested on the wrong engine build. |
| `~/models/GLM-5.2-GGUF` | 196.6 GiB | IQ2_XXS — streams into 30.8 GiB but is 14x too slow to use |
| `~/models/AtomicChat-Qwen3.8-Flash-Next` | 88 GiB | 4-bit `-M64` — tested and rejected, +28% slower than 3-bit |

The last two are keepable-or-deletable; neither is in the recommended set. The
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