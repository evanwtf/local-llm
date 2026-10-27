"""Every RECOMMENDATIONS.md stays at or under 2,000 words.

The pages answer one question: what should I run on this machine, and how.
On 2026-10-27 the two-Spark page held 10,194 words and the single-Spark page
3,550. The two-Spark page had been trimmed once, on 2026-10-08 (5,475 to 4,454
words), and then grew back past 10,000 in 19 days. Every addition was
reasonable on its own: a new run, an audit note, one more table. That is #232's
failure on the M5 Max page, which reached 838 lines, and the same cause. #232's
cap checked only the M5 Max page, so the DGX pages grew with nothing to stop
them.

So the budget is a test, and it finds the pages by pattern: a new machine's
page is covered the day it is written.

How a page stays under it (the M5 Max page is the model):

* The page holds the pick and how to run it, one table of rows with a reason
  each, a short "why this order", one line per stack measured and not picked,
  and a "where the rest went" table.
* A new result changes a row, or adds one line. Its evidence (per-run tables,
  audits, failure lists) goes in its issue and in `results.jsonl`, never on
  the page.
* When the page has no room, freeze it word for word as
  `recommendations-archive-<YYYY-MM-DD>.md` beside it, link the archive from
  "where the rest went", and rewrite the page short. An archive is a record and
  is never edited; it is not a RECOMMENDATIONS.md, so the budget skips it.

The count is `wc -w`'s: whitespace-separated tokens over the whole file, code
blocks and link definitions included. A reader scrolls through all of it.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The ceiling for every recommendations page (operator, 2026-10-27).
MAX_WORDS = 2000


def recommendation_pages() -> list[pathlib.Path]:
    """The root page and every machine's page."""
    pages = [ROOT / "RECOMMENDATIONS.md"]
    pages += sorted(ROOT.glob("hardware/*/RECOMMENDATIONS.md"))
    return [p for p in pages if p.is_file()]


def count_words(text: str) -> int:
    """Words as `wc -w` counts them."""
    return len(text.split())


def test_the_word_count_matches_wc() -> None:
    # `printf 'a b\n\n```sh\nx  y\n```\n[l]: https://e.x\n' | wc -w` prints 8.
    assert count_words("a b\n\n```sh\nx  y\n```\n[l]: https://e.x\n") == 8
    assert count_words("") == 0


def test_every_machine_page_is_found() -> None:
    """A glob that matched nothing would pass the budget vacuously."""
    found = {p.relative_to(ROOT).as_posix() for p in recommendation_pages()}
    assert {
        "RECOMMENDATIONS.md",
        "hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RECOMMENDATIONS.md",
        "hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md",
        "hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md",
    } <= found


def test_an_archive_is_not_a_page() -> None:
    names = {p.name for p in recommendation_pages()}
    assert names == {"RECOMMENDATIONS.md"}


def test_every_page_is_within_the_budget() -> None:
    over = [
        f"{p.relative_to(ROOT)}: {n} words"
        for p in recommendation_pages()
        if (n := count_words(p.read_text())) > MAX_WORDS
    ]
    assert not over, (
        f"over the {MAX_WORDS}-word budget: {'; '.join(over)}. Move the evidence "
        "to its issue, or freeze the page as recommendations-archive-<date>.md "
        "and rewrite it short (see this test's docstring)."
    )
