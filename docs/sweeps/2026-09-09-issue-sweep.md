# Issue sweep 2026-09-09

**Repo half.** 3 scratchpad worktrees removed; 20 stale branches deleted
(6 merged, 14 `port/*`, all gone from origin); 3 branches remain — `main`,
`Ryzen9-7900X-32GB-RTX3080Ti-12GB`, `worktree-equiv-other-ports`. Tree
clean, nothing unpushed, CI green.

**Queue half.**

| | before | after |
|---|---|---|
| open | 100 | 99 |
| P0 / P1 / P2 / P3 | 8 / 14 / 65 / 13 | 7 / 14 / 65 / 13 |
| macOS P0 / P1 | 7 / 9 | 6 / 9 |
| NEXT.md said | 5 P0, 3 P1 | matches the labels |

NEXT.md is generated, so it cannot drift from the labels — but it had not
been regenerated since 05:40, and five issues had been filed and one closed
since. The file named eight queue items and hid eight more. Regenerating is
the whole fix; nothing was relabelled.

The binding invariant is now the generator's own: **P0 is over the 5 this
queue can mean anything by.** That is a ranking decision for the operator,
not a label edit, and it is deliberately left standing.

**Label hygiene:** 3 findings in 100 issues — #92, #83, #16 each carry both
`macOS` and `Nvidia`. All three exist to compare the two machines. Not
defects; recorded so the next sweep does not re-raise them.

## Stale-commit pass

38 of 100 open issues mention a hex sha. Most are our own commits recording
where a defect was found, which is what an issue is for. **11 pin an
upstream head**; each was resolved against `~/git/ds4` and `~/git/ds4-metal`,
and every upstream ticket they block on was checked for state.

| upstream | state on 2026-09-09 |
|---|---|
| ds4 #885 #886 #569 #952 #990 #991 #1014 | all OPEN |
| llama.cpp #27773 #27836 | both OPEN |

Nothing closed on "the blocker merged", because nothing merged.

**Closed:** #267 — both Q4 MPP knobs re-tested at `b8507c9f`, decode 0.989
and prefill 0.995, inside a 3.1 pp repeat spread. A null. The head then
moved to `4a87d9cc`, which touches `Makefile` and CUDA/ROCm sources only —
a Metal number cannot move across a commit that does not touch Metal.

**Retitled, not closed:** #162. Its title pinned `77a054e1`, four heads
stale (`20d5dff6` -> `ff749b84` -> `b8507c9f` -> `4a87d9cc`), and it read as
dead work. It is the opposite: a live thread with an upstream reviewer, last
exchanged 2026-09-09. Now "the standing M5 Max re-test lane, currently at
head `4a87d9cc`" — the sha is a dated fact, not a claim the title makes
forever.

**The lesson, and it cost two issues for one PR:** a title that pins a sha
rots within a day on an active PR, and a rotted title reads exactly like
finished work. Re-tests at a new head belong on the standing lane. Do not
file an issue per sha.

**Looked stale, is not:** #93 says llama.cpp#27773 is "not maintained". That
PR was updated 2026-09-09. The premise expired toward more relevant.

**Not acted on:** #65's most recent comment is a product plug, not a reply.
No issue; recorded so the next sweep does not re-read it.

**Numbers to diff next time:** open 99; P0 6 on macOS; ds4#952 head
`4a87d9cc`; `~/git/ds4` at `399acbbe` (2026-08-27, behind); `~/git/ds4-metal`
at `ba01f5d`.
