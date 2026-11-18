"""scripts/videogen/batch.py: fixed order, skip finished runs, stop on the first failure (#1406)."""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import batch, generate

ORDER = [(c, s) for c in ("dog-a", "dog-b", "dog-c") for s in (1, 2, 3)]


def write_good(plan: generate.Plan, out: pathlib.Path, **change: Any) -> None:
    """What a successful generate.run_one from a locked file leaves behind."""
    out.mkdir(parents=True, exist_ok=True)
    body = f"clip {plan.clip} {plan.seed}".encode()
    (out / "clip.mp4").write_bytes(body)
    run = {
        "status": "ok",
        "probe": False,
        "arms_status": "locked",
        "request": plan.request(),
        "server_config": plan.server_config(),
        "energy_wh": 41.5,
        "outlet": {"energy_wh": 41.5, "complete": True, "max_gap_s": 5.0},
        "output": {"sha256": hashlib.sha256(body).hexdigest()},
        **change,
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


# ---------------------------------------------------------- what is good ----


def dog_a_s1(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "out" / "h3-t2v" / "dog-a-s1"


def test_state_good(tmp_path: pathlib.Path) -> None:
    p = plan("dog-a", 1, tmp_path)
    write_good(p, dog_a_s1(tmp_path))
    assert batch.state(dog_a_s1(tmp_path), p) == (batch.GOOD, "done")


def test_a_run_from_a_draft_file_is_good_only_with_allow_draft(
    tmp_path: pathlib.Path,
) -> None:
    p = plan("dog-a", 1, tmp_path)
    write_good(p, dog_a_s1(tmp_path), arms_status="draft")
    assert batch.state(dog_a_s1(tmp_path), p) == (
        batch.TODO,
        "it was made from a draft arms file",
    )
    assert batch.state(dog_a_s1(tmp_path), p, allow_draft=True)[0] == batch.GOOD


def test_incomplete_energy_is_not_good_and_says_why(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    p = plan("dog-a", 1, tmp_path)
    write_good(
        p,
        dog_a_s1(tmp_path),
        energy_wh=None,
        outlet={"energy_wh": 30.1, "complete": False, "max_gap_s": 65.0, "error": None},
    )
    found, why = batch.state(dog_a_s1(tmp_path), p)
    assert found == batch.TODO
    assert "outlet energy is incomplete (max gap 65.0 s" in why
    fakes = Fakes()
    run(tmp_path, fakes)
    assert fakes.made[0] == ("dog-a", 1)
    assert "max gap 65.0 s" in caplog.text


def test_another_server_block_stops_the_batch(tmp_path: pathlib.Path) -> None:
    p = plan("dog-a", 1, tmp_path)
    other = p.server_config()
    other["argv"] = [*other["argv"], "--seed", "7"]
    write_good(p, dog_a_s1(tmp_path), server_config=other)
    assert batch.state(dog_a_s1(tmp_path), p) == (
        batch.MISMATCH,
        "it records a different server block",
    )
    fakes = Fakes()
    assert run(tmp_path, fakes) == generate.REFUSED
    assert fakes.made == []


def test_node_and_metrics_host_reach_generate(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run_one(
        plan: generate.Plan, server: str, out: pathlib.Path, **kw: Any
    ) -> int:
        seen.append({"server": server, **kw})
        return generate.FAILED

    monkeypatch.setattr(generate, "run_one", fake_run_one)
    rc = batch.run_batch(
        "h3-t2v",
        server="http://h:8040",
        out_root=tmp_path / "out",
        frames_out_root=tmp_path / "fo",
        frames_root=tmp_path / "frames",
        node="worker",
        metrics_host="spark-a",
        allow_draft=True,
    )
    assert rc == generate.FAILED
    assert seen == [
        {
            "server": "http://h:8040",
            "node": "worker",
            "metrics_host": "spark-a",
            "allow_draft": True,
            "overwrite": True,
        }
    ]


# ------------------------------------------------- gallery rebuild (#1413) ----


def run_with_gallery(
    tmp_path: pathlib.Path, fakes: Fakes, builder: batch.GalleryBuilder | None
) -> int:
    return batch.run_batch(
        "h3-t2v",
        server="http://h:8040",
        out_root=tmp_path / "out",
        frames_out_root=tmp_path / "frames-out",
        frames_root=tmp_path / "frames",
        generator=fakes.generator,
        frames_runner=fakes.frames,
        gallery_builder=builder,
    )


def test_the_gallery_rebuilds_after_each_clip_made(tmp_path: pathlib.Path) -> None:
    write_good(plan("dog-a", 1, tmp_path), tmp_path / "out" / "h3-t2v" / "dog-a-s1")
    fakes = Fakes(fail_at=("dog-b", 2))
    calls: list[tuple[pathlib.Path, int]] = []

    def builder(root: pathlib.Path) -> None:
        calls.append((root, len(fakes.made)))

    assert run_with_gallery(tmp_path, fakes, builder) == generate.FAILED
    # dog-a s1 was skipped; s2, s3, dog-b s1 and the failed dog-b s2 each rebuild,
    # each time right after that clip finished.
    assert [n for _, n in calls] == [1, 2, 3, 4]
    assert {root for root, _ in calls} == {tmp_path}


def test_a_gallery_failure_never_fails_the_batch(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    def builder(root: pathlib.Path) -> None:
        raise OSError("disk full")

    fakes = Fakes()
    with caplog.at_level(logging.WARNING):
        assert run_with_gallery(tmp_path, fakes, builder) == 0
    assert fakes.made == ORDER
    warnings = [r for r in caplog.records if "gallery rebuild failed" in r.getMessage()]
    assert len(warnings) == len(ORDER)


def test_the_default_builder_writes_the_gallery_beside_out(
    tmp_path: pathlib.Path,
) -> None:
    fakes = Fakes()
    assert run(tmp_path, fakes) == 0
    index = tmp_path / "gallery" / "index.html"
    assert index.is_file()
    assert "out/h3-t2v/dog-c-s3" in index.read_text()
    link = tmp_path / "gallery" / "media" / "out" / "h3-t2v" / "dog-a-s1.mp4"
    assert link.is_symlink()


def test_no_gallery_turns_the_rebuild_off() -> None:
    assert (
        batch.build_parser()
        .parse_args(["--arm", "h3-t2v", "--server", "http://h:8040", "--no-gallery"])
        .no_gallery
    )


# ------------------------------------------------- metrics export (#1406) ----


def run_with_metrics(
    tmp_path: pathlib.Path, fakes: Fakes, writer: batch.MetricsWriter | None
) -> int:
    return batch.run_batch(
        "h3-t2v",
        server="http://h:8040",
        out_root=tmp_path / "out",
        frames_out_root=tmp_path / "frames-out",
        frames_root=tmp_path / "frames",
        generator=fakes.generator,
        frames_runner=fakes.frames,
        gallery_builder=None,
        metrics_writer=writer,
    )


def test_the_metrics_are_written_after_each_clip_made(tmp_path: pathlib.Path) -> None:
    write_good(plan("dog-a", 1, tmp_path), tmp_path / "out" / "h3-t2v" / "dog-a-s1")
    fakes = Fakes(fail_at=("dog-b", 2))
    calls: list[tuple[pathlib.Path, int]] = []

    def writer(root: pathlib.Path) -> None:
        calls.append((root, len(fakes.made)))

    assert run_with_metrics(tmp_path, fakes, writer) == generate.FAILED
    assert [n for _, n in calls] == [1, 2, 3, 4]
    assert {root for root, _ in calls} == {tmp_path}


def test_a_metrics_failure_never_fails_the_batch_and_warns_once(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[pathlib.Path] = []

    def writer(root: pathlib.Path) -> None:
        calls.append(root)
        raise FileNotFoundError("no textfile collector directory")

    fakes = Fakes()
    with caplog.at_level(logging.WARNING):
        assert run_with_metrics(tmp_path, fakes, writer) == 0
    assert fakes.made == ORDER
    # It keeps trying (the operator may fix the directory mid-batch), but
    # warns only once.
    assert len(calls) == len(ORDER)
    warnings = [r for r in caplog.records if "metrics export failed" in r.getMessage()]
    assert len(warnings) == 1


def test_run_batch_writes_no_metrics_by_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(root: pathlib.Path) -> object:
        raise AssertionError("run_batch must not write the system directory")

    monkeypatch.setattr(batch, "write_metrics", boom)
    assert run(tmp_path, Fakes()) == 0


@pytest.mark.parametrize(("extra", "want"), [([], True), (["--no-metrics"], False)])
def test_main_wires_the_metrics_writer(
    monkeypatch: pytest.MonkeyPatch, extra: list[str], want: bool
) -> None:
    seen: dict[str, Any] = {}

    def fake_run_batch(arm: str, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(batch, "run_batch", fake_run_batch)
    assert batch.main(["--arm", "h3-t2v", "--server", "http://h:8040", *extra]) == 0
    assert (seen["metrics_writer"] is batch.write_metrics) is want
    if not want:
        assert seen["metrics_writer"] is None
