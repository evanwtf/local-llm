"""Which prompt a decode/prefill A/B was measured on (#140).

A prefill figure is not well-posed without naming the prompt. @adamlawi
measured the same Q4-vs-Q8 question on one box with two prompts and got
+2.5% with a 135 kB input and parity with a 405 kB one -- 2.4 pp apart, same
binaries, same machine. Our own four-run recheck read parity on a 1298 kB
prompt, which fits his result and was published without naming the file.

`decode_ab.sh` takes `PROMPT` as an environment variable with a default, and
until this module nothing wrote it down. The measurement therefore could not
say what it was a measurement OF. Two places record it now:

* **the rows** -- `stamp()` appends `prompt_file` and `prompt_bytes` to every
  row of a ds4-bench CSV, so a CSV carries its own provenance even when it is
  copied out of its directory. This is the same reasoning that put
  `client_version` and `run_position` on agent rows.
* **a sidecar** -- `run-meta.json`, the only thing available for runs measured
  before the stamp existed. A sidecar may be marked `inferred`, and anything
  read from one prints as inferred for as long as it is quoted.

`agree()` is the guard against pooling: several runs may be summarised
together only when they used the same prompt.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import logging
import pathlib

logger = logging.getLogger(__name__)

#: The sidecar's name inside a run directory.
SIDECAR = "run-meta.json"

#: The two columns a stamped CSV carries.
COLUMNS = ("prompt_file", "prompt_bytes")


@dataclasses.dataclass(frozen=True)
class PromptRef:
    """The prompt a run used: its name, its size, and how we know."""

    name: str
    size: int
    sha256: str | None
    #: True when this came from a sidecar written after the fact rather than
    #: from the run itself. An inferred prompt is evidence, not a record.
    inferred: bool


def describe(ref: PromptRef | None) -> str:
    """The prompt as it should appear beside a figure.

    KiB, because 1,329,139 bytes has been quoted in this project as
    "1298 kB" and that number is bytes/1024. The unit was wrong, not the
    figure. Naming it KiB keeps every past quote readable and still compares
    against @adamlawi's 135 kB and 405 kB -- a 2.4% unit difference cannot
    confuse a ten-fold size comparison.
    """
    if ref is None:
        return "prompt NOT RECORDED -- a prefill figure without one is under-specified"
    tail = " -- inferred, not recorded by the run" if ref.inferred else ""
    return f"{ref.name} ({ref.size / 1024:.0f} KiB){tail}"


def digest(path: pathlib.Path) -> str:
    """SHA-256 of the prompt file. Two files can share a name and a size."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stamp(csv_path: pathlib.Path, prompt: pathlib.Path) -> None:
    """Append the prompt columns to every row of one ds4-bench CSV.

    Idempotent, because the caller is a shell loop that may be re-run over a
    directory it already stamped. Re-stamping with a *different* prompt is
    refused rather than overwritten: that means one run directory holds two
    regimes, which is the exact confusion #140 is about.
    """
    with csv_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        logger.warning("%s: no rows to stamp", csv_path)
        return
    fields = list(rows[0])
    name, size = prompt.name, prompt.stat().st_size
    if COLUMNS[0] in fields:
        seen = {(r.get("prompt_file"), r.get("prompt_bytes")) for r in rows}
        if seen != {(name, str(size))}:
            raise ValueError(
                f"{csv_path.name} is already stamped with {sorted(seen)}, "
                f"refusing to restamp it as {name} ({size} bytes)"
            )
        return
    for row in rows:
        row["prompt_file"] = name
        row["prompt_bytes"] = str(size)
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields + list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def write_sidecar(
    outdir: pathlib.Path,
    *,
    name: str,
    size: int,
    sha256: str | None,
    inferred: bool,
    why: str,
) -> pathlib.Path:
    """Record the prompt beside a run directory.

    `why` is required and is not decoration: a sidecar is the only record for
    runs that predate the stamp, and one that does not say how the prompt was
    established cannot be told apart from a guess.
    """
    path = outdir / SIDECAR
    path.write_text(
        json.dumps(
            {
                "prompt_file": name,
                "prompt_bytes": size,
                "prompt_sha256": sha256,
                "inferred": inferred,
                "why": why,
            },
            indent=2,
        )
        + "\n"
    )
    return path


def _from_rows(outdir: pathlib.Path) -> PromptRef | None:
    """The stamp, read off the run's own CSVs."""
    seen: set[tuple[str, int]] = set()
    for path in sorted(outdir.glob("*-rep*.csv")):
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                got = row.get("prompt_file")
                if got:
                    seen.add((got, int(row["prompt_bytes"])))
    if not seen:
        return None
    if len(seen) > 1:
        raise ValueError(
            f"{outdir.name} holds two prompts: {sorted(seen)}. "
            "The arms are not comparable; do not summarise them together."
        )
    name, size = seen.pop()
    return PromptRef(name, size, None, False)


def _from_sidecar(outdir: pathlib.Path) -> PromptRef | None:
    path = outdir / SIDECAR
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text())
    except ValueError as exc:
        logger.warning("%s: %s", path, exc)
        return None
    return PromptRef(
        str(body["prompt_file"]),
        int(body["prompt_bytes"]),
        body.get("prompt_sha256"),
        bool(body.get("inferred", True)),
    )


def for_run(outdir: pathlib.Path) -> PromptRef | None:
    """The prompt one run used, or None if nothing recorded it.

    The rows win over the sidecar. The rows were written by the run; a
    sidecar can be written by anyone afterwards, and where they disagree the
    later hand is the one to distrust.
    """
    return _from_rows(outdir) or _from_sidecar(outdir)


def agree(refs: list[PromptRef | None]) -> PromptRef | None:
    """The single prompt every run shares, or None.

    None is the answer both when the runs used different prompts and when any
    one of them did not record it -- an unrecorded prompt cannot be shown to
    match, and "probably the default" is the assumption that produced #140.
    """
    if not refs or any(r is None for r in refs):
        return None
    first = refs[0]
    assert first is not None
    for ref in refs[1:]:
        assert ref is not None
        if (ref.name, ref.size) != (first.name, first.size):
            return None
    return first


def main(argv: list[str] | None = None) -> int:
    """Stamp CSVs and write the sidecar, for the shell harnesses to call.

    A CLI rather than a shell-side `awk`: the stamp's idempotence and its
    refusal to restamp a different prompt are the parts worth having, and
    neither survives being reimplemented in each harness.
    """
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--prompt", required=True, type=pathlib.Path)
    p.add_argument("--stamp", nargs="*", type=pathlib.Path, default=[])
    p.add_argument(
        "--sidecar",
        type=pathlib.Path,
        help="run directory to write run-meta.json into",
    )
    p.add_argument("--show", action="store_true", help="print the prompt description")
    args = p.parse_args(argv)

    if not args.prompt.is_file():
        logger.error("prompt not found: %s", args.prompt)
        return 1
    for csv_path in args.stamp:
        stamp(csv_path, args.prompt)
    if args.sidecar:
        write_sidecar(
            args.sidecar,
            name=args.prompt.name,
            size=args.prompt.stat().st_size,
            sha256=digest(args.prompt),
            inferred=False,
            why=f"recorded by the harness at run time from {args.prompt}",
        )
    if args.show:
        logger.info(
            "%s",
            describe(
                PromptRef(args.prompt.name, args.prompt.stat().st_size, None, False)
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
