---
name: issue-sweep
description: Re-prioritize the open issues so each machine's live queue (scripts/make_next.py --platform) says what to do next. Use when the user says "issue sweep", "reprioritize", "check the labels", "is the queue right", or after a P0 finishes.
---

# Sweeping the issues

> **Install note.** This skill lives in the repo so it is version-controlled,
> but Claude Code finds project skills relative to the session's working
> directory. A symlink makes it available everywhere while keeping the repo
> the source of truth:
>
> ```sh
> ln -sfn "$PWD/.claude/skills/issue-sweep" ~/.claude/skills/issue-sweep
> ```

**The labels are the queue.** `scripts/make_next.py --platform {macos,nvidia}`
prints each machine's queue live — P0 before P1, then by issue number. There is
no committed queue file (#463 retired `NEXT.md` on 2026-09-17).

**A priority is a judgment made on the day it was applied, and it goes stale.**
A `P1` filed during an incident stays `P1` after the incident is fixed. A `P0`
"blocks a measurement" long after the measurement ran another way. A `P2` lead
becomes urgent when upstream ships the thing it was waiting for. Nothing
mechanical notices. **This sweep re-asks, for every queued issue, whether its
priority is still true today** — and fixes the label hygiene on the way.

**The output is a proposed re-ranking with a reason per change, then — after
approval — the relabels.** Run it after a P0 finishes, after a batch of
filing, when the queue looks wrong, and at least weekly.

---

## 1. Print both queues, and write down what "next" should be first

```sh
gh repo view --json nameWithOwner -q .nameWithOwner   # confirm the repo
uv run python scripts/make_next.py --platform macos
uv run python scripts/make_next.py --platform nvidia
```

**Before reading any issue, write down what each machine should be doing
next**, from the operator's most recent direction (this conversation, the
handoff prompt, recent issue comments) and the machine's current state
(`scripts/machine_state.py`). Then compare that to the printed queue.
Otherwise the labels anchor you and you audit the queue against itself.

## 2. Pull every open issue once

One call, then work offline. Write the file outside the repo if a benchmark is
running.

```sh
gh issue list --state open --limit 300 \
  --json number,title,labels,createdAt,updatedAt,comments > "$TMPDIR/issues.json"
```

## 3. Re-judge every P0 and P1 — the check that matters

For each queued issue, read the latest comments (not just the title) and ask:

| question | if yes |
|---|---|
| **Is it already done, or answered by a closed child?** | propose closing it with the finding |
| **Is it blocked** on the operator, an upstream release, a download, or another issue? | demote to P2 and name the blocker; it re-enters when unblocked |
| **Was it raised for a reason that no longer holds?** (an incident fixed, a comparison already run, a machine state that changed) | demote, citing what changed |
| **Does a newer operator rule make it moot or smaller?** (e.g. the 200% download cap vs. an oversized model) | demote or close, citing the rule |
| **Has it gone quiet** — no comment or commit in 7+ days while labelled P0/P1? | it is either blocked or not really next; decide which |
| **Is it P0 without blocking a measurement or the machine's safety?** | P0 means that and nothing else; propose P1 |

Then look **below the line** for promotions:

| signal | consider promoting |
|---|---|
| upstream shipped what a P2 was waiting for (a release, a merged PR, weights) | to P1 |
| the operator asked for it recently, in chat or on the issue | to P1, or P0 if they said "next" |
| it protects the machine or the measurements (OOM, a harness defect voiding rows) | to P0/P1 |
| a finished P0 unblocked it | to P1 |

The per-platform caps still apply: `make_next.py` warns above 5 `P0` or 20
`P1`. A queue over the cap is a re-ranking that has not been done yet, so the
sweep proposes the demotions that bring it back under.

## 4. Re-judge platform and machine — the queue an issue lands in

The platform label decides **which machine's queue** an issue appears in, so a
wrong one is a mis-ranking, not a typo. For every open issue (all priorities,
not just P0/P1), read the body and ask **which machine actually runs this
work**:

| the work | platform | machine label |
|---|---|---|
| Metal / MLX / oMLX / ds4-metal, M5 Max thermals | `platform:macOS` | `hardware:M5-Max-128GB` |
| CUDA / vLLM / TRT-LLM / SGLang / NVFP4, GB10 ops, the DGX's OOM layers | `platform:Nvidia` | `hardware:Cortex-X925-GB10` |
| the Ryzen / RTX 3080 Ti desktop | `platform:Nvidia` | that machine's `hardware:` slug (`hardware/MACHINES.md`) |
| genuinely both (a model to measure on each, a cross-machine doc) | both platform labels | both machine labels |
| repo, CI, harness code that runs anywhere | none | none — a type label instead |

Propose a fix when:
- the platform names the wrong machine (a vLLM issue labelled `platform:macOS`),
- the `hardware:` label disagrees with the platform (`hardware:M5-Max-128GB` on
  a `platform:Nvidia` issue),
- a machine-time issue has no platform or no `hardware:` label,
- an issue carries both platforms but only one machine will ever run it,
- a harness/CI issue carries a platform label and so clutters a machine queue.

These are judgment calls too, with a one-line reason each, like the priority
changes.

## 5. The mechanical checks, on the way

```python
import json, os, pathlib

d = json.loads(pathlib.Path(os.environ["TMPDIR"], "issues.json").read_text())
PRIO = {"P0", "P1", "P2", "P3"}
PLAT = {"platform:macOS", "platform:Nvidia"}
TYPES = {"bug", "documentation", "enhancement", "question"}
for i in d:
    L = {l["name"] for l in i["labels"]}
    p, pl = L & PRIO, L & PLAT
    hw = {x for x in L if x.startswith("hardware:")}
    if not p:
        print(f"#{i['number']} NO priority   {i['title'][:60]}")
    elif len(p) > 1:
        print(f"#{i['number']} MULTI {sorted(p)} {i['title'][:60]}")
    if len(pl) > 1:
        print(f"#{i['number']} MULTI platform {sorted(pl)}")
    elif not pl and not (L & TYPES):
        print(f"#{i['number']} NO platform and NO type  {i['title'][:40]}")
    if pl and not hw:
        print(f"#{i['number']} platform but NO hardware label  {i['title'][:40]}")
    mac_hw = any("M5-Max" in x for x in hw)
    nv_hw = any("M5-Max" not in x for x in hw)
    if (mac_hw and "platform:macOS" not in pl) or (
        nv_hw and "platform:Nvidia" not in pl
    ):
        print(
            f"#{i['number']} hardware {sorted(hw)} disagrees with platform {sorted(pl)}"
        )
```

- **No priority** — in no queue at all; give it one as part of this sweep.
- **Two priorities** — `make_next.py` drops it rather than guess.
- **No platform label** — a finding only when it consumes machine time. Repo,
  CI, and harness defects are exempt but must carry a type label.
- **Two platform labels** — shows in both queues; fine for genuinely
  cross-machine work, otherwise pick the machine.
- **Platform but no hardware label** — the class label names a vendor, not the
  box.

## 6. Propose, then stop

Print one table per platform and ask **once** for the whole batch (the rows
below show the shape; they are illustrative, not a standing verdict):

    nvidia (P0 0, P1 5 -> P0 0, P1 4)
    #370 P1 -> P2   SSD streaming: 200% download cap limits it to 122-243 GiB models; nothing queued fits
    #406 P2 -> P1   operator asked for the MTP arm today; run in progress
    #459 +hardware:Cortex-X925-GB10   (hygiene)
    #92  -platform:macOS -hardware:M5-Max-128GB   harness runs on the Linux runner only (platform)

Every change carries its reason in one line — a re-ranking nobody can argue
with is worse than none. Mark which rows are **priority**, which are **platform**
(which queue an issue belongs in), and which are **hygiene** (missing labels); the operator may approve one set and not the
other.

**Never relabel just to silence the cap.** If more than 20 things really are
P1, say that the bar has slipped and ask which to drop.

## 7. Apply, and leave the reason on the issue

Only after approval.

```sh
gh issue edit <n> --add-label P2 --remove-label P1
gh issue comment <n> -F - <<'EOF'
Re-labelled in the YYYY-MM-DD issue sweep (P1 -> P2 / platform:X -> platform:Y): <the one-line reason>.

--opus
EOF
```

A priority change without a comment is a ranking nobody can trace. Then
re-print both queues with `make_next.py` and confirm they read the way the
operator intends.

## 8. Record it

When the queue changed, add a short entry to `docs/changelog.md`:

    issue sweep YYYY-MM-DD: nvidia P0/P1 0/5 -> 0/4 (#370 down: download cap),
    macos 5/2 unchanged; hardware label on #459.

**Record the per-platform P0/P1/P2/P3 counts and the open-issue total** so a
later sweep can diff them.

---

## Rules that survive every sweep

**Closed means closed.** Every query passes `--state open`. Do not reopen or
mine closed issues for leftover work; if work remains, somebody opens a ticket.

**The operator's latest word outranks the label.** A priority applied a week
ago loses to what the operator said this morning. When the two disagree, the
label is what is stale.

**A count is not a sweep.** Report the changes, each with its reason.

**If a live benchmark holds the machine, do not write into the repo.** `gh`
calls — relabels and comments included — touch no git state and are safe.
