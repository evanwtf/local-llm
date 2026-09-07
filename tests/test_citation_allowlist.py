"""Enforce that engine-source citations carry a tree and sha.

AGENTS.md requires `ds4.c:12159 at ds4 399acbbe`, not a bare `ds4.c:12159`.
A bare line number is unverifiable: ds4.c is over 70,000 lines and moves
daily, and the same function sits at a different line in every tree.

The file set is every ds4 source file we cite: `ds4.c`, `ds4_metal.m`,
`ds4_bench.c`, `ds4.h`, and the rest, with `.c`/`.m`/`.h` extensions. A
bare `ds4_bench.c:10` is as unverifiable as a bare `ds4.c:23514`, so the
pattern covers the whole set, not just the two files the first draft named.

The allowlist (`citation_allowlist.txt`) holds the known-bare citations that
have not been retrofitted yet. It only shrinks. A bare citation outside it
fails the test, so a new bare citation cannot land without a deliberate
decision to allowlist it.

Three traps shaped the regex:

- `git grep -E` does not support `\\b`; a pattern using it returns zero
  matches and looks like a clean tree. This test uses Python's `re`, where
  `\\b` works, and never `git grep`.
- A sha near a citation is not a sha bound to it. The regex requires
  ` at <tree> <sha>` immediately after the line number, so a sha 200
  characters earlier in the paragraph does not pin the citation.
- The tree token is `ds4[A-Za-z0-9_-]*`, so `at ds4 399acbbe` and
  `at ds4-pr952 77a054e1` both pin. A lint that hardcoded the literal `ds4`
  would flag the tree-named form as bare.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWLIST_FILE = Path(__file__).resolve().parent / "citation_allowlist.txt"

# A bare citation: `ds4<name>.<c|m|h>:NNNNN` not bound to a tree and sha.
# `(?!\\d)` anchors the digits so `ds4.c:23514 at ds4-main 9ab70534` cannot
# backtrack to a bare `ds4.c:2351`. The negative lookahead accepts both the
# single-line pin (`ds4.c:23514 at ds4-main 9ab70534`) and a range pin
# (`ds4.c:1180-1182 at ds4-main 9ab70534`), because prose citations in the
# evidence `.json` files legitimately name a line range held at one sha.
BARE = re.compile(
    r"\bds4[A-Za-z0-9_]*\.(?:c|m|h):(\d+)(?!\d)"
    r"(?!(?:-\d+)? at ds4[A-Za-z0-9_-]* [0-9a-f]+)"
)

# A second, legitimate binding the git grep form uses: the `observed.stdout`
# of an evidence claim is raw command output, so its citations come back as
# `rev:file:line`, e.g. `77a054e1:ds4.c:6007:`. The sha immediately precedes
# the file token in the same token, exactly the "sha in the same citation
# token" binding the issue #182 scope endorses. A citation so bound cannot be
# edited in place (that would fabricate the recorded output), so the scan
# treats a leading bound sha as the pin. `bare_citations()` applies this
# exclusion because BARE stays the pure raw-pattern the unit tests assert on.
BOUND_LEADING_SHA = re.compile(r"(?<![0-9a-f])[0-9a-f]{7,40}:$")


def is_bound_with_leading_sha(text, start):
    """True if `start` begins a citation immediately preceded by a sha+`:`.
    The window is 41 chars: a 40-hex sha plus its `:`.
    """
    pre = text[max(0, start - 41) : start]
    return bool(BOUND_LEADING_SHA.search(pre))


def tracked_files():
    """All files git tracks, minus the lint's own machinery.

    Four exclusions, each a file that is not a document with citations:

    - `hardware/**/0731/**` — frozen historical transcripts.
    - `tests/citation_allowlist.txt` — the allowlist itself. Its lines are
      `path:file:line` entries, so the `file:line` half matches the bare
      pattern; scanning it would make the allowlist require itself.
    - `tests/test_citation_allowlist.py` — this test. Its example citations
      are fixtures that demonstrate the regex, not citations in a document.
    - `tests/test_evidence.py` — evidence-fixture test data. Its gate
      statements cite `ds4_metal.m:10000` through `ds4_metal.m:10015`, fake
      line numbers that assert on the evidence pipeline, not real source. A
      pin would fabricate a location: the line need not exist in any tree.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for line in out.splitlines():
        if "/0731/" in line:
            continue
        if line == "tests/citation_allowlist.txt":
            continue
        if line == "tests/test_citation_allowlist.py":
            continue
        if line == "tests/test_evidence.py":
            continue
        yield REPO / line


def bare_citations():
    """Yield `path:file:line` for every bare citation in a tracked file."""
    for path in tracked_files():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO)
        for m in BARE.finditer(text):
            if is_bound_with_leading_sha(text, m.start()):
                continue
            yield f"{rel}:{m.group()}"


def load_allowlist():
    """The known-bare citations, one `path:file:line` per line."""
    return frozenset(
        line.strip()
        for line in ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


def test_no_bare_citation_outside_allowlist():
    """Every bare citation in the repo is allowlisted. A new bare citation
    fails here until it is either pinned or deliberately allowlisted."""
    allowlist = load_allowlist()
    outside = sorted(set(bare_citations()) - allowlist)
    assert outside == [], f"bare citations outside the allowlist: {outside}"


def test_allowlist_entries_are_still_bare():
    """Every allowlist entry must still be a bare citation. Remove an entry
    when you retrofit its citation; a stale entry means the retrofit forgot
    to update the allowlist."""
    found = set(bare_citations())
    stale = sorted(load_allowlist() - found)
    assert stale == [], f"allowlist entries no longer bare (remove them): {stale}"


def test_bare_citation_is_caught():
    """A bare `ds4.c:NNNNN` is flagged."""
    text = "the gate at ds4.c:23514 is pre-M5-only"
    assert [m.group() for m in BARE.finditer(text)] == ["ds4.c:23514"]


def test_bare_bench_citation_is_caught():
    """A bare `ds4_bench.c:NNNNN` is flagged, not invisible."""
    text = "prefill_tps measures the newest interval at ds4_bench.c:10"
    assert [m.group() for m in BARE.finditer(text)] == ["ds4_bench.c:10"]


def test_bare_header_citation_is_caught():
    """A bare `ds4.h:NNNNN` is flagged."""
    text = "the struct is declared at ds4.h:100"
    assert [m.group() for m in BARE.finditer(text)] == ["ds4.h:100"]


def test_pinned_citation_is_not_caught():
    """A citation with ` at <tree> <sha>` is not flagged."""
    text = "the gate at ds4.c:23514 at ds4-main 9ab70534 is pre-M5-only"
    assert list(BARE.finditer(text)) == []


def test_pinned_tree_named_citation_is_not_caught():
    """A citation naming a specific tree (`ds4-pr952`) is not flagged."""
    text = "the tip gate at ds4_metal.m:14410 at ds4-pr952 77a054e1"
    assert list(BARE.finditer(text)) == []


def test_pinned_citation_does_not_backtrack_to_bare():
    """`ds4.c:23514 at ds4-main 9ab70534` must not match as bare `ds4.c:2351`."""
    text = "ds4.c:23514 at ds4-main 9ab70534"
    assert list(BARE.finditer(text)) == []


def test_sha_must_be_bound_to_citation():
    """A sha elsewhere in the paragraph does not pin a citation."""
    text = "the tree is at 9ab70534. the gate at ds4.c:23514 is pre-M5-only"
    assert [m.group() for m in BARE.finditer(text)] == ["ds4.c:23514"]


def test_pinned_range_citation_is_not_caught():
    """A range pin `ds4.c:1180-1182 at <tree> <sha>` is not flagged."""
    text = "the pattern at ds4.c:1180-1182 at ds4-pr952 77a054e1"
    assert list(BARE.finditer(text)) == []


def test_bare_range_citation_is_caught():
    """A range without a pin is still flagged (as its start line)."""
    text = "the pattern at ds4.c:1180-1182"
    assert [m.group() for m in BARE.finditer(text)] == ["ds4.c:1180"]


def test_leading_sha_binding_recognized():
    """A git grep `rev:file:line` citation is bound, not bare."""
    text = "git grep 77a054e1:ds4.c:6007:static void validate_compress_ratio_metadata"
    m = next(m for m in BARE.finditer(text))
    assert m.group() == "ds4.c:6007"
    assert is_bound_with_leading_sha(text, m.start())


def test_leading_sha_elsewhere_does_not_bind():
    """A sha in the same line but not immediately before does not bind."""
    text = "at 9ab70534, the gate at ds4.c:6007 is pre-M5-only"
    m = next(m for m in BARE.finditer(text))
    assert is_bound_with_leading_sha(text, m.start()) is False


def test_pinned_with_leading_sha_is_not_in_scan():
    """A leading-sha-bound citation is excluded from the repo scan, so a
    document that only carries raw git grep output has no bare citations."""
    from pathlib import Path
    import tempfile

    body = (
        "observed: 77a054e1:ds4.c:6007:static void validate_compress_ratio_metadata(m)"
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.json"
        p.write_text(body)
        # bare_citations() reads tracked files only, so emulate its filter:
        rel = Path("x.json")
        found = []
        for m in BARE.finditer(body):
            if is_bound_with_leading_sha(body, m.start()):
                continue
            found.append(f"{rel}:{m.group()}")
        assert found == []


def test_allowlisted_citation_passes():
    """An allowlisted bare citation does not fail the repo scan."""
    allowlist = load_allowlist()
    # AGENTS.md:ds4.c:40442 is the metadiscussion example of a bare citation.
    assert "AGENTS.md:ds4.c:40442" in allowlist
