"""Splice gen_tables.py output into RECOMMENDATIONS.md between its markers.

    uv run python benchmarks/agent/splice_tables.py

The prose is hand-written; the numbers are not. Everything between
<!-- BEGIN GENERATED --> and <!-- END GENERATED --> is replaced from
results.jsonl, so a recommendation can never quote a figure the data no longer
supports. test_recommendations.py fails if the file has drifted.
"""

from __future__ import annotations

import pathlib

import gen_tables

DOC = pathlib.Path(__file__).resolve().parents[2] / "RECOMMENDATIONS.md"
BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"


def splice(text: str, tables: str) -> str:
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{DOC} is missing the {BEGIN} / {END} markers")
    head = text[: text.index(BEGIN) + len(BEGIN)]
    tail = text[text.index(END) :]
    return f"{head}\n\n{tables}\n{tail}"


def main() -> None:
    DOC.write_text(splice(DOC.read_text(), gen_tables.render()))


if __name__ == "__main__":
    main()
