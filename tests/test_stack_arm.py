"""One arm of a whole-stack A/B (#235, #138).

The first half of the `stack_agent_ab.sh` port. Every refusal here costs hours
if it is found at read-out instead of at the first second, which is why they
all run before anything starts.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import stack_arm
from source_text import code_of
from stack_arm import Arm


def ds4_arm(name: str = "new", **kw) -> Arm:
    base = {
        "name": name,
        "backend": f"backend-{name}",
        "engine": "ds4",
        "tree": pathlib.Path("/g/ds4"),
        "gguf": pathlib.Path(f"/m/{name}.gguf"),
        "ple": pathlib.Path("/m/ple.gguf"),
        "kv": pathlib.Path(f"/kv/{name}"),
    }
    return Arm(**{**base, **kw})


def mlx_arm(name: str = "new", **kw) -> Arm:
    base = {
        "name": name,
        "backend": f"backend-{name}",
        "engine": "mlx-serve",
        "mlx_model": pathlib.Path("/packs/qwen-mlx"),
    }
    return Arm(**{**base, **kw})


# --- the argv, which the run record must state exactly -----------------------


def test_the_ds4_argv_carries_the_hardcoded_flags_too() -> None:
    """The record must state the effective command line, not the FLAGS
    variable: `--ctx`, `--warm-weights`, `--kv-disk-space-mb`, `--host` and
    `--port` are hard-coded, and a record showing only flags understates what
    ran."""
    argv = stack_arm.server_argv(ds4_arm())
    for flag in (
        "--metal",
        "--ctx",
        "--warm-weights",
        "--kv-disk-dir",
        "--kv-disk-space-mb",
        "--host",
        "--port",
    ):
        assert flag in argv
    assert argv[argv.index("--port") + 1] == "8000"


def test_the_mlx_argv_serves_rather_than_chats() -> None:
    """`--serve`, not `run`: `run` is the interactive chat REPL and never
    returns. `--host 127.0.0.1` explicitly, because the documented default is
    0.0.0.0 and a benchmark server does not belong on the local network."""
    argv = stack_arm.server_argv(mlx_arm())
    assert "--serve" in argv
    assert "run" not in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--kv-quant") + 1] == "off"


def test_empty_flags_are_not_a_special_case() -> None:
    """The bash-3.2 empty-array trap, which cost `greedy_mtp_ab.sh` its
    control arm. The shell needed `${f[@]+"${f[@]}"}` here."""
    assert stack_arm.server_argv(ds4_arm(flags="")) == stack_arm.server_argv(ds4_arm())


def test_extra_flags_are_split_but_not_reparsed() -> None:
    """`shlex`, like the shell's `read -ra`: word-splitting that does not glob
    and does not re-parse metacharacters, so a flag value containing a quote
    cannot rewrite the command line."""
    argv = stack_arm.server_argv(ds4_arm(flags="--mtp-draft 7 --mtp-timing"))
    assert "--mtp-draft" in argv and "7" in argv and "--mtp-timing" in argv


def test_a_flag_value_with_a_glob_is_not_expanded() -> None:
    argv = stack_arm.server_argv(ds4_arm(flags="--name *.gguf"))
    assert "*.gguf" in argv


def test_an_arm_is_immutable() -> None:
    """The shell mutated SERVER_ARGV as a global and had to reset it before
    each branch "so a mistyped engine cannot leave a stale array from the
    previous call" -- a hazard that exists only because the value was shared."""
    with pytest.raises(dataclasses_error()):
        ds4_arm().backend = "other"  # type: ignore[misc]


def dataclasses_error():
    import dataclasses

    return dataclasses.FrozenInstanceError


def test_an_unknown_engine_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown engine"):
        Arm(name="x", backend="b", engine="llama.cpp")


# --- what a pair must not be -------------------------------------------------


def test_two_arms_may_not_share_a_backend() -> None:
    """The rows would be indistinguishable in results.jsonl."""
    with pytest.raises(stack_arm.InvalidPair, match="indistinguishable"):
        stack_arm.check_pair(
            ds4_arm("new", backend="same"), ds4_arm("old", backend="same")
        )


def test_two_ds4_arms_may_not_share_a_kv_directory() -> None:
    """ds4-server's disk cache runs cross-quant=accept, so one arm would read
    the other's checkpoints with activations computed from different weights --
    or be refused them and re-prefill, whose only symptom is looking slower."""
    kv = pathlib.Path("/kv/shared")
    with pytest.raises(stack_arm.InvalidPair, match="KV dir"):
        stack_arm.check_pair(ds4_arm("new", kv=kv), ds4_arm("old", kv=kv))


def test_the_kv_rule_does_not_apply_to_an_mlx_arm() -> None:
    """mlx-serve keeps no disk KV directory we manage, so a guard written for
    ds4 would either refuse a legitimate run or pass while checking nothing."""
    stack_arm.check_pair(mlx_arm("new"), ds4_arm("old"))


def test_a_valid_pair_is_not_refused() -> None:
    """The negative case: without it the guard could refuse everything and the
    harness would be unreachable until an overnight run produced no rows."""
    stack_arm.check_pair(ds4_arm("new"), ds4_arm("old"))


# --- assets, checked before the machine is committed -------------------------


def test_a_missing_gguf_is_refused(tmp_path) -> None:
    with pytest.raises(stack_arm.MissingAsset, match="missing"):
        stack_arm.check_assets(ds4_arm(gguf=tmp_path / "absent.gguf"))


def test_a_half_pulled_mlx_pack_is_refused(tmp_path) -> None:
    """A pack directory with no config.json is void condition 11."""
    pack = tmp_path / "pack"
    pack.mkdir()
    with pytest.raises(stack_arm.MissingAsset, match="config.json"):
        stack_arm.check_assets(mlx_arm(mlx_model=pack))


def test_a_complete_mlx_pack_passes(tmp_path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "config.json").write_text("{}")
    stack_arm.check_assets(mlx_arm(mlx_model=pack))


def test_an_mlx_arm_with_no_pack_is_refused() -> None:
    with pytest.raises(stack_arm.MissingAsset, match="no mlx_model"):
        stack_arm.check_assets(Arm(name="x", backend="b", engine="mlx-serve"))


# --- what run.py must be told ------------------------------------------------


def test_the_served_model_for_mlx_is_the_pack_directory_name() -> None:
    """`wait_ready.py --model` is REQUIRED and the served id is the pack's
    directory name. Omitting it did not fail loudly in the shell: argparse
    exits 2, `| tail -1` swallowed the status, and the harness proceeded
    WITHOUT waiting -- so the first trials hit a server still paging in 100 GiB
    and recorded 503s as failed rows."""
    assert mlx_arm(mlx_model=pathlib.Path("/packs/qwen-mlx")).served_model == "qwen-mlx"


def test_a_non_speculative_engine_gets_no_draft_log_flag() -> None:
    """`run.py` accepts ds4|mtplx only. Passing a name it rejects is an
    argparse error that ends the sweep in one second, not a harmless unknown
    option. It was hard-coded `ds4` before this script had a second engine."""
    assert ds4_arm().draft_log_engine == "ds4"
    assert mlx_arm().draft_log_engine is None


def test_each_engine_uses_its_own_port() -> None:
    assert ds4_arm().port == 8000
    assert mlx_arm(mlx_port=11234).port == 11234


# --- the run record, which is the only place the stack is named --------------


def test_the_record_names_both_arms_and_their_command_lines() -> None:
    """The rows do NOT record the engine binary or the gguf -- `env.servers`
    carries served_model_id and context_length only, and both arms answer to
    the same served_model_id. Without this the cell silently mixes two engines
    exactly the way it silently mixed two clients (#137)."""
    text = stack_arm.describe(ds4_arm("new"), ds4_arm("old"), sweeps=2)
    assert "NEW backend=backend-new" in text
    assert "OLD backend=backend-old" in text
    assert "NEW server: ./ds4-server" in text
    assert "OLD server: ./ds4-server" in text


def test_the_record_states_the_screen_and_its_resolution() -> None:
    """Pre-registered as a SCREEN, not a superiority test. A record that does
    not say so invites the run to be read as one."""
    text = stack_arm.describe(ds4_arm("new"), ds4_arm("old"), sweeps=2)
    assert "SCREEN" in text
    assert "n=30/arm" in text


def test_two_engines_cannot_attribute_either_alone() -> None:
    assert "DIFFERENT ENGINES" in stack_arm.attribution(mlx_arm("new"), ds4_arm("old"))


def test_same_tree_and_gguf_means_the_flags_are_the_variable() -> None:
    """#210/#151 need MTP on against MTP off, same tree, same gguf, same PLE."""
    gguf = pathlib.Path("/m/same.gguf")
    said = stack_arm.attribution(
        ds4_arm("new", gguf=gguf, flags="--mtp-model x"), ds4_arm("old", gguf=gguf)
    )
    assert "flags are the only variable" in said


def test_two_mlx_arms_with_different_packs_are_not_the_same_stack() -> None:
    """The bug @deepseek found. `gguf` and `tree` are ds4 concepts and are
    None on an mlx arm, so comparing them said "same" for EVERY pair of mlx
    arms -- including two serving different packs, whose run record then
    claimed the flags were the only variable. The record is the only place the
    stack is named, so a false line there is worse than no line."""
    said = stack_arm.attribution(
        mlx_arm("new", mlx_model=pathlib.Path("/packs/pack-A")),
        mlx_arm("old", mlx_model=pathlib.Path("/packs/pack-B")),
    )
    assert "only variable" not in said
    assert "move together" in said


def test_two_mlx_arms_with_the_same_pack_differ_only_in_flags() -> None:
    """The other half: same pack, same binary, so the flags really are it."""
    pack = pathlib.Path("/packs/same")
    said = stack_arm.attribution(
        mlx_arm("new", mlx_model=pack, flags="--kv-quant q8"),
        mlx_arm("old", mlx_model=pack),
    )
    assert "flags are the only variable" in said


def test_two_mlx_arms_with_different_binaries_are_not_the_same_stack() -> None:
    """#225 runs two mlx-serve BUILDS against each other on the same pack. A
    brew binary and a source build are not one stack."""
    pack = pathlib.Path("/packs/same")
    said = stack_arm.attribution(
        mlx_arm("new", mlx_model=pack, mlx_bin="/g/mlx-serve/zig-out/bin/mlx-serve"),
        mlx_arm("old", mlx_model=pack, mlx_bin="mlx-serve"),
    )
    assert "only variable" not in said


def test_different_ggufs_move_engine_and_quant_together() -> None:
    said = stack_arm.attribution(ds4_arm("new"), ds4_arm("old"))
    assert "move together" in said


# --- the identity line, which is not reimplemented here ----------------------


def test_the_identity_line_delegates_rather_than_re_deriving(monkeypatch) -> None:
    """`engine_ident()` in the shell is a THIRD implementation of logic that
    already lives in engine_identity.py, and the shell's own comment admits
    what that cost: "the same defect a219ca5 fixed on the Python side,
    reintroduced here". /opt/homebrew is itself a git checkout, so `git
    rev-parse` inside the Cellar answers with HOMEBREW's HEAD -- it labelled a
    brew arm with a real sha of the wrong repo."""
    monkeypatch.setattr(
        stack_arm.engine_identity,
        "identity",
        lambda engine, tree=None: {
            "engine_tree": "/g/ds4",
            "engine_version": "abc1234",
        },
    )
    assert stack_arm.identity_line(ds4_arm()) == "/g/ds4 @ abc1234"


def test_a_dirty_engine_tree_is_marked() -> None:
    """A dirty tree's sha does not name its binary."""
    import stack_arm as sa

    original = sa.engine_identity.identity
    sa.engine_identity.identity = lambda e, tree=None: {
        "engine_tree": "/g/ds4",
        "engine_version": "abc1234",
        "engine_dirty": True,
    }
    try:
        assert "-dirty" in sa.identity_line(ds4_arm())
    finally:
        sa.engine_identity.identity = original


def test_an_unresolvable_engine_says_so_rather_than_printing_a_question_mark(
    monkeypatch,
) -> None:
    """Printing `?` for a brew binary would read as a failed lookup of a sha,
    which is how a run record stops being evidence."""
    monkeypatch.setattr(stack_arm.engine_identity, "identity", lambda e, tree=None: {})
    assert "unknown kind" in stack_arm.identity_line(ds4_arm())


def test_this_module_does_not_reimplement_the_cellar_check() -> None:
    """The whole point: one implementation, already tested, already fixed."""
    # code_of(), not a hand-rolled docstring strip. Splitting on the first
    # two triple-quotes removes only the MODULE docstring, so every function
    # docstring below it still matches -- the fourth variant of this trap
    # today, and the shared helper exists precisely to end it.
    code = code_of(ROOT / "scripts" / "lib" / "stack_arm.py")
    assert "Cellar" not in code, "delegate to engine_identity; do not re-derive"
    assert "readlink" not in code


def test_a_missing_mlx_binary_is_refused(tmp_path) -> None:
    """`command -v mlx-serve` in the shell (stack_agent_ab.sh:226).

    It matters more here than it did there. #225 needs a DIFFERENT binary per
    arm, so `mlx_bin` is per-arm, and a typo in one arm's MLX_BIN is a typo
    nothing else looks at. Without this the run reaches the first sweep with
    the machine lock held and an 85 GiB pack resident before anything says the
    binary does not exist.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "config.json").write_text("{}")
    with pytest.raises(stack_arm.MissingAsset, match="does not resolve on PATH"):
        stack_arm.check_assets(
            mlx_arm(mlx_model=pack, mlx_bin="mlx-serve-that-does-not-exist")
        )


def test_the_binary_check_is_per_arm(tmp_path) -> None:
    """One arm's good binary must not vouch for the other's.

    #225's whole point is two binaries; a check that passed as long as SOME
    mlx-serve existed would be satisfied by the arm that is fine.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "config.json").write_text("{}")
    # The real one on this machine resolves; the invented one must not.
    stack_arm.check_assets(mlx_arm("new", mlx_model=pack, mlx_bin="mlx-serve"))
    with pytest.raises(stack_arm.MissingAsset):
        stack_arm.check_assets(mlx_arm("old", mlx_model=pack, mlx_bin="mlx-serve-old"))


def test_an_absolute_path_to_a_binary_is_accepted_when_it_exists(tmp_path) -> None:
    """#225 passes a PATH to the second binary, not just a name."""
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "config.json").write_text("{}")
    binary = tmp_path / "mlx-serve-git"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    stack_arm.check_assets(mlx_arm(mlx_model=pack, mlx_bin=str(binary)))
