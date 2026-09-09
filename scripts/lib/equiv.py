"""The argv/env equivalence harness for the #235 port. #235, #264, #149

The port's claim is that a Python driver and the shell driver it ports,
under identical inputs, hand the same argv to `run.py` and the same
environment to the `ds4-server` child. If the port drifts, the numbers
change and the equivalence is false.

This module is the instrument that measures that claim. It holds:

- the `Invocation` model -- exactly what a fake measurement binary recorded,
- the PATH-shim writer -- fake `ds4-server`, `opencode` and
  `benchmarks/agent/run.py` that append their argv and full environment to a
  JSONL file and exit 0,
- the AST walk that reads `run.py`'s declared flags, so a #264-style check
  cannot drift from what `run.py` actually accepts,
- the four assertions the port's acceptance criteria depend on.

The recordings it produces are fixtures (see scripts/gen_equiv_fixtures.py),
so the assertions run offline, against what the drivers actually emit, with no
driver loop, server, or GPU present.

## The four assertions

The peer review of the port (#235) pre-registered four properties and asked
that each be expressed as a test, not a prose note.

1. **#264** -- every `--flag` a driver passes `run.py` is a flag `run.py`
   declares. The shell once passed `--skip-tensor-gate` after the port removed
   it; argparse exited 2 under a `|| echo returned non-zero` and the batch
   wrote zero rows with a clean exit code.

2. **#149 env difference** -- the *difference* between the arms' server
   environments matches. The tensor arm sets `DS4_METAL_ENABLE_TENSOR=1`; the
   reference arm *removes* it. The removal is the half a dict cannot express,
   and it matters: `env -u` on the shell means the key is ABSENT in the child,
   not empty, and it must be absent even when the operator exported it in their
   own shell.

3. **argv order** -- order matters between a flag and its own value, not
   between two independent flags (argparse is order-insensitive across
   options). The comparison therefore attaches each flag to its value and
   compares the pair-multiset.

4. **the tag** -- the tag a driver emits parses under the report module that
   consumes that driver's tags, back to the arm. A tag neither consumer can
   read does not degrade; it raises `KeyError` halfway through a read-out.
"""

from __future__ import annotations

import ast
import json
import logging
import pathlib
import sys
import textwrap
from collections.abc import Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
RUN_PY = REPO / "benchmarks" / "agent" / "run.py"

# The key #149's reference arm must REMOVE from the server child's env. The
# shell wrote `env -u DS4_METAL_ENABLE_TENSOR` for exactly this reason; the
# port must do the same, and the removal must reach the child, not vanish in
# a dict merge that leaves the key inherited.
TENSOR_ENV = "DS4_METAL_ENABLE_TENSOR"

# Value the tensor arm must set TENSOR_ENV to. It is "1", never "0" and never
# empty: the route is on or it is not.
TENSOR_ENV_ON = "1"

# Every #149 driver tags its tensor arm with one of these and its reference
# arm with the other, though which is which is per-driver (route_agent_ab uses
# t/r, stack_agent_ab uses new/old as BACKEND names). These are the report
# consumers' own arm tokens, so the tag assertion stays driver-agnostic.
TENSOR_ARMS = ("t", "new")
REF_ARMS = ("r", "old")
ARMS = TENSOR_ARMS + REF_ARMS


class Invocation:
    """What one fake measurement binary recorded: program, arm, argv, env.

    Frozen tuple fields so an invocation is comparable and hashable, and so a
    test can assert on it without worrying that a later edit to the fixture
    mutated it.
    """

    __slots__ = ("argv", "arm", "env", "program")

    def __init__(
        self,
        program: str,
        arm: str,
        argv: Iterable[str],
        env: dict[str, str],
    ) -> None:
        self.program = program
        self.arm = arm
        self.argv = tuple(argv)
        self.env = dict(env)

    def as_json(self) -> dict[str, object]:
        return {
            "program": self.program,
            "arm": self.arm,
            "argv": list(self.argv),
            "env": self.env,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Invocation):
            return NotImplemented
        return (
            self.program,
            self.arm,
            self.argv,
            self.env,
        ) == (
            other.program,
            other.arm,
            other.argv,
            other.env,
        )

    def __hash__(self) -> int:
        return hash((self.program, self.arm, self.argv, frozenset(self.env.items())))

    def __repr__(self) -> str:  # pragmatic: a one-line traceable form
        return (
            f"Invocation(program={self.program!r}, arm={self.arm!r}, "
            f"argv={self.argv!r})"
        )


# ---------------------------------------------------------------- recording


def record(path: pathlib.Path, inv: Invocation) -> None:
    """One JSON line per invocation, appending."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(inv.as_json(), separators=(",", ":")) + "\n")


def load(path: pathlib.Path) -> list[Invocation]:
    """The invocations a recording file holds, in written order.

    Blank lines and lines beginning with `#` are ignored, so a fixture can
    carry a `# source: ...` provenance marker alongside the JSONL rows.
    """
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        return []
    out: list[Invocation] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        blob = json.loads(line)
        out.append(Invocation(blob["program"], blob["arm"], blob["argv"], blob["env"]))
    return out


def by_program(invs: Iterable[Invocation], program: str) -> list[Invocation]:
    """The invocations one program recorded, in order."""
    return [i for i in invs if i.program == program]


# ------------------------------------------------------------------ the shim


def write_shim(directory: pathlib.Path, out: pathlib.Path) -> pathlib.Path:
    """A directory of PATH fakes that record, then exit 0.

    Creates `ds4-server`, `opencode`, and `benchmarks/agent/run.py` under
    `directory`. Each appends its argv (the tokens after its own name) and its
    full environment to the JSONL at `out`, tagged with `EQUIV_PROGRAM` and
    `EQUIV_ARM`, then exits 0. The caller sets those on the child's env, which
    is how the recording line knows which program and arm it was.

    Returns `directory` so a caller can chain the path construction.
    """
    (directory / "benchmarks" / "agent").mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(
        """\
        #!/usr/bin/env python3
        import json, os, sys
        out = os.environ["EQUIV_OUT"]
        line = {
            "program": os.environ["EQUIV_PROGRAM"],
            "arm": os.environ["EQUIV_ARM"],
            "argv": sys.argv[1:],
            "env": {k: v for k, v in os.environ.items()},
        }
        with open(out, "a") as h:
            h.write(json.dumps(line, separators=(",", ":")) + "\\n")
        sys.exit(0)
        """
    )
    run_py = directory / "benchmarks" / "agent" / "run.py"
    run_py.write_text(body)
    for name in ("ds4-server", "opencode"):
        exe = directory / name
        exe.write_text(body)
        exe.chmod(0o755)
    return directory


def run_fake(
    directory: pathlib.Path,
    program: str,
    arm: str,
    argv: Sequence[str],
    env: dict[str, str],
    unset: Sequence[str],
    output: pathlib.Path,
    log: pathlib.Path,
    *,
    via: str = "",
) -> None:
    """Spawn one fake exactly as a driver would, recording it.

    This routes through `child.run` -- the port's own spawn mechanism -- so
    the env merge and the `unset` removal are the production path, not a copy
    of it. The fake therefore records what the port's child really sees: the
    tensor arm's key set to "1", the reference arm's key ABSENT even when the
    operator exported it. If `child.run` ever stopped honouring `unset`, the
    recording would say so and assertion 2 would fail on the fixture.

    `via` names the program `argv` was built for: `"run.py"` goes through a
    python interpreter, anything else is the bare fake name. The interpreter is
    the current one because `argv` are the tokens AFTER `run.py`'s own name, so
    the fake records the driver's flags either way; the real driver's
    `uv run python` prefix is not carried into a fake's argv. The tagging keys
    live in `env`, inherited by the child, so the recorded line carries the arm
    and program.
    """
    if via == "run.py":
        cmd = [
            sys.executable,
            str(directory / "benchmarks" / "agent" / "run.py"),
            *argv,
        ]
    else:
        cmd = [str(directory / program), *argv]
    import child as childlib

    child_env = dict(env)
    child_env["EQUIV_OUT"] = str(output)
    child_env["EQUIV_PROGRAM"] = program
    child_env["EQUIV_ARM"] = arm
    rc = childlib.run(cmd, cwd=directory, log=log, env=child_env, unset=unset)
    if rc != 0:
        raise AssertionError(f"fake {program} did not exit 0: rc={rc} see {log}")


# ---------------------------------------------------------- run.py's flags


def declared_run_flags(run_py: pathlib.Path = RUN_PY) -> frozenset[str]:
    """The option strings `run.py` declares, from its source. Drift-proof.

    Reads `run.py`'s own `add_argument` calls rather than a frozen copy of
    them, so when a flag is removed from `run.py` this set changes with it and
    a driver still passing it fails immediately -- the #264 failure mode, in
    reverse. A cached copy of the declared flags would be exactly the stale
    answer the gate exists to refuse.
    """
    tree = ast.parse(pathlib.Path(run_py).read_text())
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
            continue
        for arg in node.args:
            value = arg.value
            if (
                isinstance(arg, ast.Constant)
                and isinstance(value, str)
                and value.startswith("-")
                and value != "-"
            ):
                flags.add(value)
    # argparse adds the help option to every parser.
    flags.add("-h")
    flags.add("--help")
    return frozenset(flags)


def non_literal_flag_tests(run_py: pathlib.Path = RUN_PY) -> list[str]:
    """`add_argument` calls whose flag is not a literal string, one line each.

    `declared_run_flags` reads only `ast.Constant` string args. A flag declared
    from a variable, a splat (`parser.add_argument(*SPEC, ...)`), or an
    f-string would leave the set silently, and the #264 gate would then reject
    a driver that is correct -- a loud refusal that reads like the driver is
    broken when the drift is in run.py. The gate's whole authority is that it
    reflects the live source, so it must refuse to be fooled here. Returns a
    source line per offending call; a test asserts none, and a unit test proves
    the guard fires on a splat.
    """
    tree = ast.parse(pathlib.Path(run_py).read_text())
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
            continue
        first = node.args[0] if node.args else None
        if first is not None and not (
            isinstance(first, ast.Constant) and isinstance(first.value, str)
        ):
            out.append(f"line {first.lineno}: {ast.unparse(node)}")
    return out


def assert_flags_declared(argv: Sequence[str], declared: frozenset[str]) -> None:
    """Assertion 1 (#264): every flag in `argv` is one `run.py` declares.

    Only tokens that look like flags are checked, so a value such as a path or
    a number is not mistaken for an option. Raises AssertionError naming the
    first unknown flag, with the same silence this caught: a driver passing an
    unknown flag once wrote zero rows and exited clean.
    """
    unknown = [t for t in argv if t.startswith("-") and t != "-" and t not in declared]
    if unknown:
        raise AssertionError(
            "driver passes flags run.py does not declare (#264): "
            + ", ".join(repr(u) for u in unknown)
        )


# --------------------------------------------------- the four assertions (2-4)


def env_key_state(env: dict[str, str], key: str) -> str:
    """How `key` sits in a child's env: "absent", "empty", or "set:VALUE".

    The absent/empty split is the point of `env -u`: a key the driver removed
    is absent; a key it left blank is empty, and neither is the other. #149
    required the reference arm's key to be *absent*, and this is what turns
    "the driver said it unset it" into "the child actually lacks it".
    """
    if key not in env:
        return "absent"
    if env[key] == "":
        return "empty"
    return f"set:{env[key]}"


def assert_arm_env_delta(
    invs: Iterable[Invocation], *, key: str, program: str = "ds4-server"
) -> None:
    """Assertion 2 (#149): each arm's server child shows the driver's intent.

    Only the server child is checked: the experiment's variable is the route
    the *server* takes, and the variable lives (or is removed) on that child's
    env. The `run.py` client legitimately keeps whatever the operator exported,
    so asserting on it would fail for no reason. For `program`'s invocations,
    the tensor arm's `key` must be `set:1` and the reference arm's must be
    `absent`. This asserts the DIFFERENCE between the arms, not each arm's full
    env, matching how #149 defines the treatment: one binary, one command line,
    one variable whose removal is the whole point.
    """
    for inv in invs:
        if inv.program != program:
            continue
        state = env_key_state(inv.env, key)
        if inv.arm in TENSOR_ARMS:
            expect = f"set:{TENSOR_ENV_ON}"
        elif inv.arm in REF_ARMS:
            expect = "absent"
        else:
            raise AssertionError(f"{inv.program} arm {inv.arm!r} is not a known arm")
        assert state == expect, (
            f"{inv.program} {inv.arm}: {key} is {state!r}, want {expect!r} "
            f"(this is #149's env difference reaching the child)"
        )


def canonical(
    argv: Sequence[str], declared: frozenset[str]
) -> tuple[tuple[str, ...], ...]:
    """Assertion 3's comparison form: flag-to-value pairs, flag order-free.

    Argparse is order-insensitive across options, so two drivers are
    equivalent when each flag carries the same value (or values), in any flag
    order. A flag's own value, though, must stay attached to it: flipping a
    flag's values is a real change even when the flag set is identical.

    A token that starts with `-` opens a pair, whether or not `run.py` declares
    it. That is deliberate: an *undeclared* flag is still syntactically a flag,
    and treating it as a value would glue it onto the preceding flag and hide
    the very thing #264 is about (a driver passing a flag run.py dropped). The
    following non-flag tokens are its values. Returns a sorted tuple of pairs,
    so order between pairs compares by equality while order inside a pair holds.
    """
    pairs: list[list[str]] = []
    cur: list[str] | None = None
    for tok in argv:
        if tok.startswith("-"):
            cur = [tok]
            pairs.append(cur)
        elif cur is not None:
            cur.append(tok)
    return tuple(sorted(tuple(p) for p in pairs))


def argv_difference(
    shell_argv: Sequence[str], py_argv: Sequence[str], declared: frozenset[str]
) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    """The canonical pairs only one side emits: (shell-only, port-only).

    `canonical` already dropped flag order and kept flag-to-value binding, so
    this is the set difference of two order-free summaries. The test asserts
    this difference is exactly the sanctioned fixes for the driver -- the port
    is not blankly equal to the shell, it is equal except where a bug is being
    fixed, and the fix is what the assertion pins.
    """
    c_shell = set(canonical(shell_argv, declared))
    c_py = set(canonical(py_argv, declared))
    return c_shell - c_py, c_py - c_shell


def assert_tag_roundtrips(
    tag: str, arm: str, consumer: Callable[[str], str | None]
) -> None:
    """Assertion 4: the driver's tag round-trips through its report to the arm.

    `consumer` is the driver's own report's tag parser: it takes a tag and
    returns the arm, or None for a tag it cannot read. The driver's tag must
    resolve back to the arm it was stamped for. Wiring the real consumer (see
    the tests) is the whole point -- a tag is an interface, and asserting with
    a typed string would only prove the string can be typed.
    """
    got = consumer(tag)
    assert got == arm, (
        f"tag {tag!r} read as {got!r}, want {arm!r}; the sweep would be mis-stamped"
    )


def default_tags() -> list[str]:
    """The bare ab_driver default tag shape, which MUST not parse (#235).

    Both report consumers reject it: route_ab_report needs a `t-sweepN` or
    `r-sweepN` prefix, stack_agent_report needs an arm name (new/old) before
    `-sweepN`. `r1-t` matches neither -- route_ab_report's regex wants the
    `-sweep` index, stack_agent_report wants `-sweep` to split on and then an
    arm it knows. This is why every driver passes a `tag_for` of its own
    instead of letting ab_driver default to `f"r{n}-{arm}"`.
    """
    return ["r1-t", "r1-r"]
