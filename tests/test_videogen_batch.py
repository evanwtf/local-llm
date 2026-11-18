"""scripts/videogen/batch.py: fixed order, skip finished runs, stop on the first failure (#1406)."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import batch, generate

ORDER = [(c, s) for c in ("dog-a", "dog-b", "dog-c") for s in (1, 2, 3)]


def write_good(plan: generate.Plan, out: pathlib.Path) -> None:
    """What a successful generate.run_one leaves behind."""
    out.mkdir(parents=True, exist_ok=True)
    body = f"clip {plan.clip} {plan.seed}".encode()
    (out / "clip.mp4").write_bytes(body)
    run = {
        "status": "ok",
        "probe": False,
        "request": plan.request(),
        "output": {"sha256": hashlib.sha256(body).hexdigest()},
    }
    (out / "run.json").write_text(json.dumps(run))


class Fakes:
    def __init__(self, fail_at: tuple[str, int] | None = None) -> None:
        self.fail_at = fail_at
        self.made: list[tuple[str, int]] = []
        self.framed: list[tuple[pathlib.Path, pathlib.Path, bool]] = []

    def generator(self, plan: generate.Plan, out: pathlib.Path) -> int:
        self.made.append((plan.clip, plan.seed))
        if (plan.clip, plan.seed) == self.fail_at:
            out.mkdir(parents=True, exist_ok=True)
            (out / "run.json").write_text('{"status": "failed"}')
            return generate.FAILED
        write_good(plan, out)
        return 0

    def frames(self, clip: pathlib.Path, out: pathlib.Path, overwrite: bool) -> int:
        self.framed.append((clip, out, overwrite))
        return 0


def run(tmp_path: pathlib.Path, fakes: Fakes) -> int:
    return batch.run_batch(
        "h3-t2v",
        server="http://h:8040",
        out_root=tmp_path / "out",
        frames_out_root=tmp_path / "frames-out",
        frames_root=tmp_path / "frames",
        generator=fakes.generator,
        frames_runner=fakes.frames,
    )


def plan(clip: str, seed: int, tmp_path: pathlib.Path) -> generate.Plan:
    return generate.make_plan("h3-t2v", clip, seed, frames_root=tmp_path / "frames")


def test_every_clip_and_seed_runs_in_a_fixed_order(tmp_path: pathlib.Path) -> None:
    fakes = Fakes()
    assert run(tmp_path, fakes) == 0
    assert fakes.made == ORDER
    assert [f[1] for f in fakes.framed] == [
        tmp_path / "frames-out" / "h3-t2v" / f"{c}-s{s}" for c, s in ORDER
    ]
    assert all(f[0].name == "clip.mp4" for f in fakes.framed)


def test_a_finished_run_is_skipped(tmp_path: pathlib.Path) -> None:
    write_good(plan("dog-a", 1, tmp_path), tmp_path / "out" / "h3-t2v" / "dog-a-s1")
    fakes = Fakes()
    assert run(tmp_path, fakes) == 0
    assert fakes.made == ORDER[1:]


def test_a_failed_run_is_made_again(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "out" / "h3-t2v" / "dog-a-s1"
    out.mkdir(parents=True)
    (out / "run.json").write_text('{"status": "failed", "error": "HTTP 500"}')
    fakes = Fakes()
    assert run(tmp_path, fakes) == 0
    assert fakes.made == ORDER


def test_a_clip_whose_bytes_changed_is_made_again(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "out" / "h3-t2v" / "dog-a-s1"
    write_good(plan("dog-a", 1, tmp_path), out)
    (out / "clip.mp4").write_bytes(b"truncated")
    fakes = Fakes()
    run(tmp_path, fakes)
    assert fakes.made[0] == ("dog-a", 1)


def test_the_first_failure_stops_the_batch(tmp_path: pathlib.Path) -> None:
    fakes = Fakes(fail_at=("dog-a", 3))
    assert run(tmp_path, fakes) == generate.FAILED
    assert fakes.made == ORDER[:3]
    # The two good clips still get their frames.
    assert [f[1].name for f in fakes.framed] == ["dog-a-s1", "dog-a-s2"]


def test_a_good_run_of_another_request_stops_the_batch(tmp_path: pathlib.Path) -> None:
    other = plan("dog-a", 1, tmp_path)
    other.fields["num_inference_steps"] = "10"
    write_good(other, tmp_path / "out" / "h3-t2v" / "dog-a-s1")
    fakes = Fakes()
    assert run(tmp_path, fakes) == generate.REFUSED
    assert fakes.made == []


def test_frames_already_made_from_this_clip_are_skipped(tmp_path: pathlib.Path) -> None:
    fakes = Fakes()
    run(tmp_path, fakes)
    clip = tmp_path / "out" / "h3-t2v" / "dog-b-s2" / "clip.mp4"
    stale = tmp_path / "frames-out" / "h3-t2v" / "dog-c-s3"
    for meta_dir, sha in (
        (tmp_path / "frames-out" / "h3-t2v" / "dog-b-s2", generate.sha256(clip)),
        (stale, "0" * 64),
    ):
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "meta.json").write_text(json.dumps({"clip_sha256": sha}))
    again = Fakes()
    assert run(tmp_path, again) == 0
    assert again.made == []
    framed = {f[1].name: f[2] for f in again.framed}
    assert "dog-b-s2" not in framed
    assert framed["dog-c-s3"] is True  # frames of another clip: overwrite
    assert framed["dog-a-s1"] is False


@pytest.mark.parametrize("which", ["out_root", "frames_out_root"])
def test_an_output_root_inside_a_repo_is_refused(
    tmp_path: pathlib.Path, which: str
) -> None:
    roots = {"out_root": tmp_path / "out", "frames_out_root": tmp_path / "fo"}
    roots[which] = ROOT / "videogen-test-output-must-not-exist"
    fakes = Fakes()
    rc = batch.run_batch(
        "h3-t2v",
        server="http://h:8040",
        frames_root=tmp_path / "frames",
        generator=fakes.generator,
        frames_runner=fakes.frames,
        out_root=roots["out_root"],
        frames_out_root=roots["frames_out_root"],
    )
    assert rc == generate.REFUSED
    assert fakes.made == []
    assert not roots[which].exists()
