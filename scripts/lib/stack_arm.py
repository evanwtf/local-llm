"""One arm of a whole-stack A/B: engine, weights, flags, backend. #235, #138.

The first half of the `stack_agent_ab.sh` port (541 lines, the largest shell
driver and the one arbitrating the most published rows). This module is the
arm: what it is, what command line it produces, and what makes a pair of arms
invalid. The sweep loop follows separately, because a single PR for the whole
script is the unreviewable thing #235 exists to avoid.

## Why an Arm is a type

The shell carries **fourteen variables per arm** -- `NEW_TREE`, `NEW_GGUF`,
`NEW_PLE`, `NEW_KV`, `NEW_BACKEND`, `NEW_FLAGS`, `NEW_RUN_FLAGS`,
`NEW_ENGINE`, `NEW_MLX_MODEL`, `NEW_MLX_PORT`, `NEW_MLX_BIN` and their `OLD_`
twins -- and then copies all fourteen again into `first_*` and `second_*` to
alternate the order:

    first_tag=new; first_backend=$NEW_BACKEND; first_run_flags=$NEW_RUN_FLAGS
    first_tree=$NEW_TREE; first_gguf=$NEW_GGUF; first_ple=$NEW_PLE; ...

Twenty-eight assignments whose only job is to swap two things. One of them
being wrong is invisible: the run completes, the rows look fine, and one arm
was served by the other arm's weights.

## The identity line is not reimplemented here

`engine_ident()` in the shell is a **third** implementation of logic that
already lives in `benchmarks/agent/engine_identity.py`, and the shell's own
comment admits what that cost:

> That is the same defect a219ca5 fixed on the Python side, reintroduced here
> because `pwd -P` resolves the directory and not the link.

The bug: `/opt/homebrew` is itself a git checkout, so `git rev-parse` inside
the Cellar answers with **Homebrew's** HEAD. It labelled a brew arm with a
real sha of the wrong repo, while the source arm reported its own -- two arms
both labelled with a sha and no way to tell which is which, which is worse
than no label at all.

`engine_identity` already resolves that, and has tests. This renders its
answer; it does not re-derive it.
"""

from __future__ import annotations

import dataclasses
import pathlib
import shlex
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[2] / "benchmarks" / "agent")
)

import engine_identity

DS4 = "ds4"
MLX_SERVE = "mlx-serve"
ENGINES = (DS4, MLX_SERVE)

DEFAULT_CTX = 100000
DS4_PORT = 8000
DS4_KV_DISK_MB = 8192
MLX_PORT = 11234


class InvalidPair(ValueError):
    """Two arms that cannot be compared, refused before anything starts."""


class MissingAsset(ValueError):
    """An arm names a file or pack that is not on disk."""


@dataclasses.dataclass(frozen=True)
class Arm:
    """One side of a stack comparison.

    Frozen because an arm is a description, not a workspace. The shell mutated
    `SERVER_ARGV` as a global and reset it before each `case` "so a mistyped
    engine cannot leave a stale array from the previous call" -- a hazard that
    only exists because the value was shared.
    """

    name: str
    backend: str
    engine: str = DS4
    tree: pathlib.Path | None = None
    gguf: pathlib.Path | None = None
    ple: pathlib.Path | None = None
    kv: pathlib.Path | None = None
    flags: str = ""
    run_flags: str = ""
    mlx_model: pathlib.Path | None = None
    mlx_port: int = MLX_PORT
    mlx_bin: str = MLX_SERVE

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise ValueError(
                f"unknown engine {self.engine!r}; expected one of {ENGINES}"
            )

    @property
    def is_ds4(self) -> bool:
        return self.engine == DS4

    @property
    def port(self) -> int:
        return DS4_PORT if self.is_ds4 else self.mlx_port

    @property
    def served_model(self) -> str:
        """What `wait_ready.py --model` must be told.

        For mlx-serve the served id is the pack's directory name. Omitting it
        does not fail loudly -- argparse exits 2, the shell's `| tail -1`
        swallowed the status, and the harness proceeded WITHOUT waiting, so the
        first trials of a sweep hit a server still paging in 100 GiB and
        recorded 503s as failed rows.
        """
        if self.is_ds4:
            return "qwen3.8-flash-next-q4"
        if self.mlx_model is None:
            raise ValueError(f"{self.name}: an mlx-serve arm needs mlx_model")
        return self.mlx_model.name

    @property
    def draft_log_engine(self) -> str | None:
        """The log dialect the draft probe parses, or None.

        `run.py` accepts `ds4|mtplx` only. An engine that does no speculative
        decoding has no draft log to read, so the flag is OMITTED rather than
        passed with a name run.py rejects -- which is an argparse error that
        ends the sweep in one second, not a harmless unknown option.
        """
        return self.engine if self.engine in ("ds4", "mtplx") else None


def server_argv(arm: Arm) -> list[str]:
    """The server command line for this arm, as it will actually run.

    The run record must state *this*, not the flags variable: the ds4 branch
    hard-codes `--ctx`, `--warm-weights`, `--kv-disk-space-mb`, `--host` and
    `--port`, and a record showing only `FLAGS` understates what ran. One
    function serves both the record and the launcher, so the two cannot drift.

    Extra flags are split with `shlex`, which is what the shell's `read -ra`
    approximated: word-splitting that does not glob and does not re-parse
    metacharacters, so a flag value containing a quote cannot rewrite the
    command line.
    """
    extra = shlex.split(arm.flags) if arm.flags else []
    if arm.is_ds4:
        for field in ("gguf", "ple", "kv"):
            if getattr(arm, field) is None:
                raise ValueError(f"{arm.name}: a ds4 arm needs {field}")
        return [
            "./ds4-server",
            "--metal",
            "-m",
            str(arm.gguf),
            "--ple",
            str(arm.ple),
            "--ctx",
            str(DEFAULT_CTX),
            "--warm-weights",
            *extra,
            "--kv-disk-dir",
            str(arm.kv),
            "--kv-disk-space-mb",
            str(DS4_KV_DISK_MB),
            "--host",
            "127.0.0.1",
            "--port",
            str(DS4_PORT),
        ]
    if arm.mlx_model is None:
        raise ValueError(f"{arm.name}: an mlx-serve arm needs mlx_model")
    # `--serve`, not `run`: `run` is the interactive chat REPL and never
    # returns. `--host 127.0.0.1` explicitly, because the documented default
    # is 0.0.0.0 and a benchmark server does not belong on the local network.
    return [
        arm.mlx_bin,
        "--model",
        str(arm.mlx_model),
        "--serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(arm.mlx_port),
        "--ctx-size",
        str(DEFAULT_CTX),
        "--kv-quant",
        "off",
        *extra,
    ]


def identity_line(arm: Arm) -> str:
    """One line naming what built this arm's engine.

    Delegated to `engine_identity`, which already resolves the Cellar-vs-git
    question the shell got wrong twice. A source build reports its sha; a brew
    binary reports its version. Printing `?` for the second kind would read as
    a failed lookup of the first, which is how a run record stops being
    evidence.
    """
    got = engine_identity.identity(arm.engine, str(arm.tree) if arm.tree else None)
    if not got:
        return f"{arm.engine} (unknown kind)"
    version = got.get("engine_version") or "?"
    tree = got.get("engine_tree")
    dirty = " -dirty" if got.get("engine_dirty") else ""
    return f"{tree} @ {version}{dirty}" if tree else f"{arm.engine} @ {version}"


def check_pair(new: Arm, old: Arm) -> None:
    """Refuse two arms that cannot be compared. Raises InvalidPair.

    Before anything starts, because each of these costs hours if it is found
    at read-out instead.
    """
    if new.backend == old.backend:
        raise InvalidPair(
            f"both arms name backend {new.backend} -- the rows would be "
            "indistinguishable in results.jsonl"
        )
    # A ds4-only concern. mlx-serve keeps no disk KV directory we manage, so a
    # guard written for ds4 would either refuse a legitimate run or pass while
    # checking nothing.
    if new.is_ds4 and old.is_ds4 and new.kv == old.kv:
        raise InvalidPair(
            f"both arms share KV dir {new.kv} -- ds4-server's disk cache runs "
            "cross-quant=accept, so one arm would read the other's checkpoints "
            "with activations computed from different weights, or be refused "
            "them and re-prefill. The only symptom is that it looks slower."
        )


def check_assets(arm: Arm) -> None:
    """Refuse an arm whose files are not on disk. Raises MissingAsset.

    A half-pulled mlx pack is void condition 11, and a missing gguf costs the
    whole run at the first sweep rather than at the first second.
    """
    if arm.is_ds4:
        for field in ("gguf", "ple"):
            path = getattr(arm, field)
            if path is None:
                raise MissingAsset(f"{arm.name}: a ds4 arm needs {field}")
            if not path.exists():
                raise MissingAsset(f"{arm.name}: missing {path}")
        return
    if arm.mlx_model is None:
        raise MissingAsset(f"{arm.name}: an mlx-serve arm has no mlx_model set")
    if not arm.mlx_model.is_dir():
        raise MissingAsset(f"{arm.name}: missing mlx pack dir {arm.mlx_model}")
    if not (arm.mlx_model / "config.json").exists():
        raise MissingAsset(f"{arm.name}: {arm.mlx_model} has no config.json")


def attribution(new: Arm, old: Arm) -> str:
    """What this pair can and cannot attribute, for the run record.

    The rows do NOT record the engine binary or the gguf -- `env.servers`
    carries `served_model_id` and `context_length` only, and both arms answer
    to the same `served_model_id`. Without this written down, the cell
    silently mixes two engines exactly the way it silently mixed two clients
    (#137).
    """
    if new.engine != old.engine:
        return (
            "DIFFERENT ENGINES and different weight formats. Engine and quant "
            "move together; neither can be attributed alone (#138, #191). This "
            "is a stack comparison, and must be reported as one."
        )
    if new.gguf == old.gguf and new.tree == old.tree:
        return "Same tree and same gguf in both arms: the flags are the only variable."
    return (
        "engine and quant move together in both arms; neither can be "
        "attributed alone (#138)."
    )


def describe(new: Arm, old: Arm, *, sweeps: int) -> str:
    """The run record. Written before the first sweep, kept with the results."""
    screen = (
        f"# SCREEN, not a superiority test: n={sweeps * 15}/arm resolves "
        "~18-27 pp pass, ~17-26% paired wall."
    )
    lines = ["# stack agent A/B", screen]
    for arm in (new, old):
        label = arm.name.upper()
        lines.append(
            f"{label} backend={arm.backend} engine={arm.engine} {identity_line(arm)}"
        )
        if arm.is_ds4:
            lines.append(
                f"{label} gguf={arm.gguf.name if arm.gguf else '?'}  kv={arm.kv}"
            )
        else:
            lines.append(f"{label} pack={arm.mlx_model}  port={arm.mlx_port}")
        lines.append(
            f"{label} flags={arm.flags or '<none>'}  run.py={arm.run_flags or '<none>'}"
        )
        lines.append(f"{label} server: {' '.join(server_argv(arm))}")
    lines.append(f"# {attribution(new, old)}")
    return "\n".join(lines) + "\n"
