"""The transcribed shell side must actually be in the shell.

`scripts/equiv_five_report.py` diffs each port's LIVE argv builders against a
hand transcription of the corresponding `.sh`, and reports IDENTICAL. The port
half cannot drift -- it calls the real functions. The shell half is a list
somebody typed, with a `# shell, <file>:<lines>` citation next to it, and
nothing read those lines.

That is the same shape as the bug found this morning in
`benchmarks/agent/test_ds4_route.py`, which asserted for months against a line
ds4 has never printed and passed the whole time because the matcher was a
substring of both. A transcription is a typed string. If it is wrong, the diff
comes back empty for the wrong reason -- and it would be wrong in the most
plausible direction, because whoever transcribes the shell is reading the port
at the same time.

So: every flag in a transcription must appear in the `.sh` lines its own
comment cites. This does not re-implement bash and does not check values
(paths are built from constants on both sides). It checks the thing #264 was
about -- which flags a driver emits -- against the file, not against memory.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT = ROOT / "scripts" / "equiv_five_report.py"

#: `# shell, greedy_mtp_ab.sh:114-120 (run_arm)` or `...sh:94`
CITATION = re.compile(r"#\s*shell,\s*(\S+?\.sh):(\d+)(?:-(\d+))?")

#: A flag inside a transcribed list: "--backend", "-m", "--metal".
FLAG = re.compile(r'"(-{1,2}[A-Za-z][\w-]*)"')


def transcriptions() -> list[tuple[str, int, int, list[str], int]]:
    """(sh path, first line, last line, flags, the citation's own line no)."""
    lines = REPORT.read_text().splitlines()
    found = []
    for i, line in enumerate(lines):
        hit = CITATION.search(line)
        if not hit:
            continue
        name, start, end = (
            hit.group(1),
            int(hit.group(2)),
            int(hit.group(3) or hit.group(2)),
        )
        # The list literal that follows, until its brackets balance.
        depth, body = 0, []
        for text in lines[i + 1 :]:
            body.append(text)
            depth += text.count("[") - text.count("]")
            if depth <= 0 and body:
                break
        flags = FLAG.findall("\n".join(body))
        found.append((name, start, end, flags, i + 1))
    return found


def test_the_report_transcribes_at_least_the_five_drivers() -> None:
    """If the citations vanish, this file silently checks nothing."""
    got = transcriptions()
    assert got, "no `# shell, <file>:<lines>` citations found in the report"
    named = {name for name, *_ in got}
    # All five, because an uncited transcription is the one this file cannot
    # check -- and decode_ab and metal_knob_ab were exactly that until their
    # citations were added.
    assert named >= {
        "greedy_mtp_ab.sh",
        "strip_toggle_ab.sh",
        "targets_ab.sh",
        "decode_ab.sh",
        "metal_knob_ab.sh",
    }, f"only {sorted(named)} are cited"


def test_every_cited_shell_file_exists() -> None:
    for name, _, _, _, at in transcriptions():
        assert (ROOT / "scripts" / name).exists(), (
            f"{REPORT.name}:{at} cites scripts/{name}, which does not exist"
        )


def test_every_transcribed_flag_is_in_the_lines_it_cites() -> None:
    """The assertion this file exists for.

    A flag in the transcription that is not in the cited range means the
    report is diffing the port against something nobody wrote in the shell --
    and an empty diff then says the two agree when one of them is fiction.
    """
    problems = []
    for name, start, end, flags, at in transcriptions():
        sh = (ROOT / "scripts" / name).read_text().splitlines()
        # A generous window: the citation points at a function, and a line
        # number drifts by an edit or two without the claim becoming false.
        window = "\n".join(sh[max(0, start - 4) : min(len(sh), end + 4)])
        for flag in flags:
            if flag not in window:
                problems.append(
                    f"{REPORT.name}:{at} says scripts/{name}:{start}-{end} "
                    f"emits {flag}, and those lines do not"
                )
    assert not problems, "\n".join(problems)


def test_a_flag_absent_from_the_shell_would_be_caught() -> None:
    """Prove the check can fail, so a green result means something.

    `--skip-tensor-gate` is the #264 flag: it IS in route_agent_ab.sh and is
    in none of the five files this report transcribes. If the check above
    cannot tell the difference, it is not a check.
    """
    cited = {name for name, *_ in transcriptions()}
    for name in cited:
        text = (ROOT / "scripts" / name).read_text()
        assert "--skip-tensor-gate" not in text, (
            f"scripts/{name} carries the #264 flag; this test's premise is stale"
        )
    route = (ROOT / "scripts" / "route_agent_ab.sh").read_text()
    assert "--skip-tensor-gate" in route, (
        "route_agent_ab.sh no longer carries --skip-tensor-gate -- good, but "
        "this test needs a new example of a flag that is really absent"
    )
