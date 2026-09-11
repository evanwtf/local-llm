"""Generate NEXT.md from the open issues, so the queue cannot drift or bloat.

NEXT.md is a ranking. It kept becoming a log: every result got a paragraph,
every correction another, and by 2026-09-08 a file that names ten items was
377 lines and had a closed issue holding a slot for two days. Nothing was
wrong with any individual addition -- that is how crust forms.

The fix is that nobody writes this file. `gh` holds the issues, the priority
labels hold the ranking, and this renders them. Three properties follow:

- **The labels ARE the order.** P0 before P1, then by issue number. There is
  no second opinion to drift from, which was the whole failure mode: a closed
  #148 sat in the prose while the label query correctly ignored it.
- **Each item's summary is the issue's own first 100 words.** Not a
  hand-written gloss that ages separately from the issue it describes.
- **The file cannot grow.** `--check` refuses a hand edit, and the render
  refuses itself if it exceeds MAX_LINES.

    uv run python scripts/make_next.py            # write NEXT.md
    uv run python scripts/make_next.py --check    # CI: is it current?

The queue-size warning is the point of the whole thing. `P0` is "blocks a
measurement" and `P1` is "next"; more than a handful of either means the
labels have stopped ranking and started describing enthusiasm.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "NEXT.md"
PLATFORM = "platform:macOS"
# The two that make the queue, and the two that do not. Both counts belong
# in the file: "5 P0" means nothing without "and 55 P2 behind them".
PRIORITIES = ("P0", "P1")
ALL_PRIORITIES = ("P0", "P1", "P2", "P3")

# Above these, the labels have stopped ranking. The operator whittles; this
# only says so, loudly, in the file and on stderr.
MAX_P0 = 5
MAX_P1 = 20

# The queue is ten items. The rest of the file is a header and a table of
# where things live, and neither should ever need more room.
VAULT = REPO_ROOT / "vault"


def vaulted_shells() -> list[str]:
    """The names of the retired drivers, which no longer live in `scripts/`."""
    return sorted(p.name for p in VAULT.rglob("*.sh"))


def unrun(text: str) -> str:
    """Strip the `scripts/` prefix from any shell that now lives in `vault/`.

    An issue about a retired driver names it by the path it used to have,
    and this file quotes issue bodies verbatim. That would hand a reader a
    path that no longer resolves -- or send them into `vault/` to find the
    archived copy and run it, which is the one thing the archive exists to
    prevent (`tests/test_vault.py`).

    The name is kept, because the summary is about that shell and removing
    it would make the line meaningless. Only the runnable path goes.
    """
    names = vaulted_shells()
    if not names:
        return text
    pattern = r"scripts/(?:lib/)?(" + "|".join(re.escape(n) for n in names) + r")"
    return re.sub(pattern, r"\1", text)


SUMMARY_WORDS = 100
MAX_LINES = 90

NOTICE = (
    "> ⚠️ **OpenCode results before 2026-08-31 21:47 EDT are INVALID** — the "
    "client was never told which directory to work in. **Other clients are "
    "unaffected.** The ledger holds none of these rows now; do not quote them "
    "from older documents either: "
    "[what happened](docs/archive/results-opencode-pre-dir.md)."
)


def fetch(limit: int = 300) -> list[dict]:
    """Every open issue for this platform, with its body."""
    out = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            PLATFORM,
            "--limit",
            str(limit),
            "--json",
            "number,title,labels,body",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return json.loads(out.stdout)


def priority(issue: dict, among: tuple[str, ...] = PRIORITIES) -> str | None:
    """The one priority label, or None. Two is a defect, not a tie to break."""
    found = sorted({lab["name"] for lab in issue["labels"]} & set(among))
    return found[0] if len(found) == 1 else None


def counts(issues: list[dict]) -> dict[str, int]:
    """How many open issues sit at each priority, including the unqueued.

    `unlabelled` is here because it is the failure this repo keeps having:
    nine issues filed across two days in September 2026 carried no priority
    at all, so they were invisible to every query the queue runs on.
    """
    out = {p: 0 for p in ALL_PRIORITIES}
    out["unlabelled"] = 0
    for issue in issues:
        p = priority(issue, ALL_PRIORITIES)
        out[p if p else "unlabelled"] += 1
    return out


def by_priority(issues: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {p: [] for p in PRIORITIES}
    for issue in issues:
        if (p := priority(issue)) is not None:
            out[p].append(issue)
    for rows in out.values():
        rows.sort(key=lambda i: i["number"])
    return out


# A sweep-sourced issue opens with the same three blocks every time: who
# verified the post, the quoted post, and a bare link. That is provenance,
# and taking it as the summary makes half the queue read "Verified
# 2026-09-05: post exists, authored by ...". The substance starts after it.
PROVENANCE = re.compile(
    r"^\s*(\*\*)?(verified|unverified|could not verify|found by source sweep"
    r"|reported by|gathered via)\b",
    re.IGNORECASE,
)


def is_provenance(block: str) -> bool:
    """A leading block that says where the claim came from, not what it is."""
    stripped = block.strip()
    if not stripped:
        return True
    if stripped.startswith(">"):  # the quoted post
        return True
    if re.fullmatch(r"\[[^\]]+\]\([^)]+\)\.?", stripped):  # a bare link line
        return True
    return bool(PROVENANCE.match(stripped))


def substance(body: str) -> str:
    """The body with its leading provenance blocks removed.

    Only leading ones. A `Verified` line further down is a correction on a
    claim, which is exactly the kind of thing a reader should be shown.
    """
    blocks = re.split(r"\n\s*\n", body or "")
    i = 0
    while i < len(blocks) and is_provenance(blocks[i]):
        i += 1
    return "\n\n".join(blocks[i:]) if i < len(blocks) else (body or "")


def summarize(body: str, words: int = SUMMARY_WORDS) -> str:
    """The issue's own opening, flattened to one line.

    Markdown structure is stripped rather than rendered: this lands inside a
    list item, and a fenced block or a heading there breaks the page. A
    truncated sentence is honest -- it says "read the issue", which is what
    the summary is for.

    Underscores survive. An earlier version stripped them as emphasis and
    turned `verify_posts.py` into `verifyposts.py` and
    `DS4_QWEN4_PLE_EVICT_TOKENS` into one word -- a summary that invents
    identifiers is worse than no summary.
    """
    text = substance(body)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # link -> its text
    text = re.sub(r"^\s*[>#|]+\s*", " ", text, flags=re.MULTILINE)
    text = re.sub(r"[*`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = unrun(text)
    parts = text.split(" ")
    if len(parts) <= words:
        return text
    return " ".join(parts[:words]).rstrip(",;:") + " …"


def render(issues: list[dict]) -> tuple[str, list[str]]:
    """The file, and any warnings. Warnings appear in both."""
    buckets = by_priority(issues)
    warnings = []
    if len(buckets["P0"]) > MAX_P0:
        warnings.append(
            f"{len(buckets['P0'])} P0 issues, over the {MAX_P0} this queue can "
            f"mean anything by. P0 is 'blocks a measurement', not 'important'."
        )
    if len(buckets["P1"]) > MAX_P1:
        warnings.append(
            f"{len(buckets['P1'])} P1 issues, over {MAX_P1}. Whittle, or the "
            f"label has stopped ranking."
        )

    lines = [
        "# What to do next, in order",
        "",
        NOTICE,
        "",
        "**Generated by `scripts/make_next.py`. Do not edit — edit the issues.**",
        "P0 before P1, then by issue number; each summary is the issue's own",
        f"first {SUMMARY_WORDS} words. The labels are the ranking, so there is",
        "nothing here to drift from.",
        "",
    ]
    for w in warnings:
        lines += [f"> ⚠️ **{w}**", ""]

    tally = counts(issues)
    unlabelled = (
        f", **{tally['unlabelled']} with no priority label**"
        if tally["unlabelled"]
        else ""
    )
    lines += [
        f"## The queue — {len(buckets['P0'])} P0, {len(buckets['P1'])} P1",
        "",
        (
            f"Open on this platform: {tally['P0']} P0, {tally['P1']} P1, "
            f"{tally['P2']} P2, {tally['P3']} P3{unlabelled}."
        ),
        "One runs at a time; the lock enforces it.",
        "",
    ]
    n = 0
    for p in PRIORITIES:
        for issue in buckets[p]:
            n += 1
            url = f"https://github.com/evanwtf/local-llm/issues/{issue['number']}"
            lines.append(f"{n}. **{p} [#{issue['number']}]({url})** {issue['title']}")
            lines.append(f"   {summarize(issue['body'])}")
    lines += [
        "",
        "## Where everything else is",
        "",
        "| | |",
        "|---|---|",
        "| machine operations, and what a comparison must do | [`docs/m5max-runbook.md`](docs/m5max-runbook.md) |",
        "| what shipped, and why | [`docs/changelog.md`](docs/changelog.md) |",
        "| results, corrections, and the reasoning for each item | the issue itself |",
        "| what was found and retracted, run by run | [`docs/history.md`](docs/history.md) |",
        "| traps that have cost us a measurement | `AGENTS.md` |",
        "| below the line, and the lead backlog | the `P2` and `P3` labels |",
        "",
    ]
    return "\n".join(lines), warnings


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if NEXT.md is not what this would write",
    )
    args = p.parse_args(argv)

    text, warnings = render(fetch())
    count = len(text.splitlines())
    if count > MAX_LINES:
        logger.error(
            "refusing: the render is %d lines, over the %d cap. This file is a "
            "ranking; if it needs more room, the queue is too long.",
            count,
            MAX_LINES,
        )
        return 2
    for w in warnings:
        logger.warning("%s", w)

    if args.check:
        if TARGET.read_text() != text:
            logger.error("NEXT.md is stale or hand-edited. Run scripts/make_next.py.")
            return 1
        logger.info("NEXT.md is current (%d lines)", count)
        return 0

    TARGET.write_text(text)
    logger.info("wrote %s (%d lines)", TARGET, count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
