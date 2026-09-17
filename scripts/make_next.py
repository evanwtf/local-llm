"""Print what to do next, live from the open issues' labels.

The queue used to be a committed file, NEXT.md. It kept becoming a log (377
lines naming ten items by 2026-09-08), and once #231 generated it from the
labels it could only go stale: it covered one platform, and an issue
relabelled on the web left it wrong until someone regenerated it. #463
retired the file. Nothing is written now; this reads `gh` and prints.

- **The labels ARE the order.** P0 before P1, then by issue number. There is
  no second opinion to drift from, which was the whole failure mode: a closed
  #148 sat in the prose while the label query correctly ignored it.
- **Each item's summary is the issue's own first 100 words.** Not a
  hand-written gloss that ages separately from the issue it describes.
- **One queue per platform.** `--platform macos` is the M5 Max's queue,
  `--platform nvidia` the DGX Spark's; the default follows the host OS.

    uv run python scripts/make_next.py                     # this host's queue
    uv run python scripts/make_next.py --platform nvidia   # the DGX Spark's

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
PLATFORMS = {"macos": "platform:macOS", "nvidia": "platform:Nvidia"}
RUNBOOKS = {"macos": "docs/m5max-runbook.md", "nvidia": "docs/dgx-spark-runbook.md"}
# The two that make the queue, and the two that do not. Both counts belong
# in the output: "5 P0" means nothing without "and 55 P2 behind them".
PRIORITIES = ("P0", "P1")
ALL_PRIORITIES = ("P0", "P1", "P2", "P3")

# Above these, the labels have stopped ranking. The operator whittles; this
# only says so, loudly, in the output and on stderr.
MAX_P0 = 5
MAX_P1 = 20

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


def default_platform() -> str:
    """The M5 Max is the only macOS host; every Linux host is an Nvidia one."""
    return "macos" if sys.platform == "darwin" else "nvidia"


def fetch(platform: str, limit: int = 300) -> list[dict]:
    """Every open issue for one platform, with its body."""
    out = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            PLATFORMS[platform],
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


def render(issues: list[dict], platform: str = "macos") -> tuple[str, list[str]]:
    """The queue as markdown, and any warnings. Warnings appear in both."""
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
        f"# What to do next on {PLATFORMS[platform]}, in order",
        "",
        "Read live from the issue labels: P0 before P1, then by issue number;",
        f"each summary is the issue's own first {SUMMARY_WORDS} words.",
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
            f"Open on {PLATFORMS[platform]}: {tally['P0']} P0, {tally['P1']} P1, "
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
        f"| machine operations, and what a comparison must do | `{RUNBOOKS[platform]}` |",
        "| what shipped, and why | `docs/changelog.md` |",
        "| results, corrections, and the reasoning for each item | the issue itself |",
        "| what was found and retracted, run by run | `docs/history.md` |",
        "| traps that have cost us a measurement | `AGENTS.md` |",
        "| below the line, and the lead backlog | the `P2` and `P3` labels |",
        "",
    ]
    return "\n".join(lines), warnings


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--platform",
        choices=sorted(PLATFORMS),
        default=default_platform(),
        help="whose queue to print (default: this host's, from the OS)",
    )
    args = p.parse_args(argv)

    text, warnings = render(fetch(args.platform), args.platform)
    for w in warnings:
        logger.warning("%s", w)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
