---
name: issue-sweep
description: Reconcile NEXT.md against the open issues and their labels. Use when the user says "issue sweep", "check the labels", "is NEXT.md current", or before re-ranking the queue.
argument-hint: "[optional: --apply to make the changes, default is report only]"
allowed-tools: Bash, Read, Edit, Grep, Glob, AskUserQuestion
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

`NEXT.md` says what to do next. The labels say the same thing, made
queryable. **They are one statement in two places, so they drift**, and the
drift is silent: both halves stay readable and only their agreement is lost.
NEXT.md says so itself — *"they drift the moment this file is re-ranked
without them."*

This sweep reconciles them. It is not triage. `triage` asks whether an issue
is still worth doing; this asks whether the queue still describes reality.

**The output is a labeled queue that matches NEXT.md, or a report saying
exactly where they disagree.** Run it before re-ranking, after any batch of
filing, and whenever a `P0` finishes.

---

## The invariant

NEXT.md declares one, and it is the whole reason this skill can be mechanical:

> **`P0` + `P1` is exactly the top 10.**

So `gh issue list --label P0 --label P1` and the numbered list in NEXT.md must
name the same issues. When they disagree, **NEXT.md is the one that was
edited** — a person re-ranks prose and forgets the label.

Everything below is a way of finding a disagreement before it misleads
somebody.

---

## 1. Read the queue first

```sh
gh repo view --json nameWithOwner -q .nameWithOwner   # confirm the repo
sed -n '1,80p' NEXT.md
```

**Extract the top-10 issue numbers from the prose, in order.** They are the
`### ` and numbered items with a `github.com/.../issues/N` link. Write them
down before looking at a single label — otherwise the labels anchor you and
you audit NEXT.md against itself.

## 2. Pull every open issue once

One call, then work offline. Repeated `gh issue view` calls are slow and
rate-limited.

```sh
gh issue list --state open --limit 300 \
  --json number,title,labels,createdAt,updatedAt > /tmp/issues.json
```

## 3. The four label checks, cheapest first

Priority labels are `P0` `P1` `P2` `P3`. Platform labels are `macOS` and
`Nvidia`.

```python
# uv run python - <<'PY'   (or inline; keep it out of the repo if a run is live)
import json, pathlib
d = json.loads(pathlib.Path("/tmp/issues.json").read_text())
PRIO = {"P0", "P1", "P2", "P3"}
PLAT = {"macOS", "Nvidia"}
for i in d:
    L = {l["name"] for l in i["labels"]}
    p, pl = L & PRIO, L & PLAT
    if not p:        print(f"#{i['number']} NO priority   {i['title'][:60]}")
    elif len(p) > 1: print(f"#{i['number']} MULTI {sorted(p)} {i['title'][:60]}")
    if len(pl) > 1:  print(f"#{i['number']} MULTI platform {sorted(pl)}")
PY
```

- **No priority label.** The issue is invisible to every query the queue runs
  on. This is the common failure: nine issues filed across two days in
  September 2026 carried none until a sweep found them.
- **More than one priority label.** The queue cannot rank it.
- **No platform label** — but see the exemption below, which is real and is
  not a lapse.
- **More than one platform label.** Split the issue or pick the machine that
  will actually run it.

### The platform exemption, which is not an oversight

**A platform label is required for anything that consumes machine time.** It
is the filter that separates this Mac's queue from the Linux/RTX tier.

**Repo hygiene, CI, and harness defects are exempt**, because they belong to
no machine. NEXT.md sets the precedent explicitly: *"The one exception is
#154, which is CI rather than a machine and is labeled `bug`."*

So an unlabeled issue is a finding **only** when it would consume machine
time. Do not mass-apply `macOS` to close a gap in a report — that is how the
filter stops meaning anything. An exempt issue must instead carry a type
label (`bug`, `documentation`, `enhancement`) so it is not simply naked.

## 4. Reconcile against NEXT.md — the check that matters

```sh
gh issue list --state open --label P0 --json number -q '[.[].number]|sort|@json'
gh issue list --state open --label P1 --json number -q '[.[].number]|sort|@json'
```

Compare with the numbers from step 1. Report **three sets**, never a count
alone — a count that matches can still be two errors cancelling:

| set | meaning | usual cause |
|---|---|---|
| labelled, not in NEXT.md | work promoted without re-ranking | new issues filed at P1 and never queued |
| in NEXT.md, not labelled | the file was re-ranked, labels forgotten | a demotion that stopped halfway |
| in NEXT.md but **CLOSED** | the file is stale | the item finished and nobody struck it |
| NEXT.md's text does not match the issue's title | the file cites the wrong number | the work moved to a new issue and the prose kept the old link |

**Check state, not just labels — and note WHY this one hides.** A closed
issue keeps its labels forever. The documented queries all pass
`--state open`, so they correctly drop it, and the label audit in step 3 also
walks only open issues. Nothing in the mechanical half of this sweep will
ever mention it. Meanwhile NEXT.md's prose still names it as live work, with
a *Done when:* clause and a slot in the top 10.

That is the trap: the closed item is invisible to every query and fully
visible to every reader. **The only thing that finds it is asking the state
of the numbers NEXT.md itself names** — which is why step 1 extracts them
from the prose before any label is read.

```sh
# every issue NEXT.md names: real state, real labels, real TITLE
for n in <numbers from step 1>; do
  gh issue view "$n" --json number,state,labels,title \
    -q '"#\(.number) [\(.state)] \([.labels[].name]|join(","))  \(.title[0:60])"'
done
```

**Read the titles against what NEXT.md says each item is.** A number can be
live, correctly labelled, and still wrong. In September 2026 item 2 read
*"#148 Prove the MTP draft head drafts, per row"* — but #148 was an oMLX
6-bit recipe issue, closed, and the work described was #210, open and already
`P1`. Every mechanical check passed on that line. Only comparing the prose to
the title caught it, and it had made one top-10 slot point at nothing while
the real work sat unqueued.

### Closed means closed

**A closed ticket is out of scope for this sweep.** Do not query closed
issues, do not audit their labels, do not mine them for leftover work. The
state filter is the whole policy:

```sh
gh issue list --state open ...      # every query in this skill
```

A closed issue keeps whatever labels it had. That is harmless and is not a
finding — `--state open` drops it from every query that matters, so a stale
`P0` on a closed ticket costs nothing and is not worth an edit.

**A closed number appearing in NEXT.md is a NEXT.md defect, and the fix is to
remove the line.** Nothing else. Do not carve a successor ticket, do not
reopen and rescope, do not ask what work might remain. If work remains,
somebody opens a ticket — and that is a decision made by a person looking at
the problem, not a bookkeeping step inferred during a sweep.

The principle: **if we have to act on it, it should be open.** A backlog that
requires reading closed tickets to know what to do is not a backlog.

## 5. Report before changing anything

Print the reconciliation as a table and **stop**. Say what you would change
and why, then ask once for the whole batch — not per issue.

    P0+P1 count ....... 21   (invariant: 10)
    closed, still queued  #148 (item 2), #143 (item 6)
    labelled P1, unqueued #208 #209 #210 #211 #213 #217 #218 #223 #224 #227
    no platform label ... #221 #222 #223 #224 #225 #227
      of those, machine time: #224 #225      -> need macOS
      exempt (CI/harness):    #222 #223 #227 -> need a type label

**Never relabel to make the invariant pass.** The invariant is a smoke
detector. Silencing it by demoting ten issues to `P2` produces a green report
and a queue that describes nothing. If ten things really are P1, then either
NEXT.md's top 10 is wrong or the priority bar has slipped — and that is a
decision for the operator, not a label edit.

## 6. Apply, with the reason on each change

Only after approval. Label changes are cheap to reverse; a wrong one is
cheap to make and expensive to notice.

```sh
gh issue edit <n> --add-label P2 --remove-label P1
gh issue edit <n> --add-label macOS
```

Then bring NEXT.md into line **in the same pass**. Editing labels and leaving
the file is how the drift started.

- Remove a closed item from the numbered list. NEXT.md keeps a *"Recently
  done, listed so the next reader does not re-open them"* section; a one-line
  entry there is enough. Do not leave a closed number holding a slot, and do
  not open a successor ticket as part of the sweep.
- Fill the vacated slot from below the line, and **say why it was promoted**.
  A slot filled silently is a ranking nobody can argue with.
- Update the counts in the "Priority labels" section. They are quoted as
  facts and go stale first.
- Re-date the file.

## 7. Record it

Append one line to the sweep record, or a short entry to `docs/changelog.md`
when the queue itself changed:

    issue sweep YYYY-MM-DD: P0+P1 21->10, struck #148 #143 (closed),
    promoted #212 #225, platform label on 2, type label on 3.

**Record the numbers a later sweep can diff**: the P0/P1/P2/P3 counts, the
open-issue total, and the invariant's state. Prose alone cannot show change.

---

## Rules that survive every sweep

**NEXT.md is the ranking; labels are its index.** When they disagree, fix the
index to match the ranking — unless the ranking names something closed, in
which case the ranking is what is stale.

**A closed issue still queued in NEXT.md is the failure mode to hunt.** The
`--state open` queries drop it, so no count is ever wrong and no query ever
returns it. It survives precisely because the mechanical checks cannot see
it. Only reading the state of the numbers in the prose finds it — and the
fix is to delete the line, not to chase what it used to mean.

**Do not re-rank during the sweep.** Reconciling and re-prioritising in one
pass means no one can tell which change was bookkeeping and which was a
decision. Report the drift; let the operator re-rank as a separate act.

**A count is not a reconciliation.** Report the sets. Two errors in opposite
directions produce a correct total and a wrong queue.

**If a live benchmark holds the machine, do not write into the repo.** An
untracked file sets `harness_dirty` on every row from that moment and voids
the run at read-out, hours later. `gh` calls are safe — they touch no git
state. Editing NEXT.md is not. Stage the edit outside the repo and apply it
when the run reports.
