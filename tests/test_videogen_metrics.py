"""scripts/videogen/metrics.py: run.json files to a Prometheus textfile (#1406)."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import pathlib
import re
import sys
import threading
import time
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import batch, metrics

H3 = "MiniMaxAI/MiniMax-H3"
NOW = 1795000000.0
H3_LABELS = (
    'arm="h3-t2v",model="MiniMaxAI/MiniMax-H3",node="head",mode="t2v",'
    'kind="scored",width="1280",height="704",fps="24"'
)


def run_json(**change: Any) -> dict[str, Any]:
    """An ok schema-2 H3 T2V run, as generate.py writes it."""
    run: dict[str, Any] = {
        "schema": 2,
        "probe": False,
        "arm": "h3-t2v",
        "mode": "t2v",
        "model": H3,
        "request": {
            "fields": {
                "width": "1280",
                "height": "704",
                "fps": "24",
                "num_inference_steps": "50",
            }
        },
        "server_config": {"served_model_name": H3},
        "node": {"role": "head"},
        "start": "2026-11-17T22:56:26-0500",
        "end": "2026-11-17T23:37:06-0500",
        "wall_s": 2440.0,
        "server_headers": {"x-inference-time-s": "2439.5"},
        "memory": {"peak_used_gib": 86.0},
        "ffprobe": {"frames": 124, "width": 1280, "height": 704, "fps": 24.0},
        "expected": {"frames": 124, "width": 1280, "height": 704, "fps": 24.0},
        "outlet": {"energy_wh": 92.5, "complete": True},
        "energy_wh": 92.5,
        "status": "ok",
        "error": None,
    }
    run.update(change)
    return run


def put(root: pathlib.Path, rel: str, run: dict[str, Any] | str) -> pathlib.Path:
    path = root / rel / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run if isinstance(run, str) else json.dumps(run))
    return path


def render(root: pathlib.Path, *, include_probes: bool = False) -> str:
    return metrics.render(
        metrics.find_runs(root, include_probes=include_probes), now=NOW
    )


# ------------------------------------------------- a minimal format check ----

NAME = r"[a-zA-Z_:][a-zA-Z0-9_:]*"
LABEL = r'[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\\n]|\\[\\"n])*"'
SAMPLE = re.compile(
    rf"^(?P<name>{NAME})(?:\{{(?P<labels>{LABEL}(?:,{LABEL})*)\}})? (?P<value>\S+)$"
)
LABEL_PAIR = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\\n]|\\[\\"n])*)"')


def unescape(value: str) -> str:
    return re.sub(r"\\([\\\"n])", lambda m: "\n" if m[1] == "n" else m[1], value)


def parse(text: str) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    """Check the text exposition format and return every sample.

    prometheus_client is not a dependency of this repo, so this checks the
    parts we write: HELP and TYPE once per family, before its samples; each
    sample's name, labels and value; no duplicate series; a final newline.
    """
    assert text.endswith("\n")
    samples: dict[tuple[str, frozenset[tuple[str, str]]], float] = {}
    helped: set[str] = set()
    typed: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("# HELP "):
            name = line.split(" ", 3)[2]
            assert name not in helped, f"two HELP lines for {name}"
            helped.add(name)
            continue
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split(" ")
            assert name in helped and name not in typed
            # The file is a snapshot whose sums can fall, so all are gauges.
            assert kind == "gauge"
            assert not name.endswith("_total")
            typed[name] = kind
            continue
        match = SAMPLE.match(line)
        assert match, f"not a sample line: {line!r}"
        name = match["name"]
        assert name in typed, f"{name} has no TYPE before it"
        labels = frozenset(
            (k, unescape(v)) for k, v in LABEL_PAIR.findall(match["labels"] or "")
        )
        key = (name, labels)
        assert key not in samples, f"duplicate series {line!r}"
        samples[key] = float(match["value"])
    return samples


def value(
    samples: dict[tuple[str, frozenset[tuple[str, str]]], float],
    name: str,
    **labels: str,
) -> list[float]:
    """The values of `name` whose labels include `labels`."""
    want = set(labels.items())
    return [v for (n, ls), v in samples.items() if n == name and want <= ls]


# ------------------------------------------------------------------ tests ----


def test_two_runs_give_this_exact_file(tmp_path: pathlib.Path) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    put(
        tmp_path,
        "out/h3-t2v/dog-a-s2",
        run_json(
            end="2026-11-18T00:17:55-0500",
            wall_s=2480.0,
            server_headers={"x-inference-time-s": "2479.5"},
            memory={"peak_used_gib": 87.0},
            energy_wh=91.5,
        ),
    )
    lab = H3_LABELS
    # s2 ended last: 124 frames / 2480 s = 0.05 frames/s, and 2480 s for
    # 124 / 24 = 5.1667 s of video is a real-time factor of 480.
    # 2026-11-18T00:17:55-0500 is 1794979075. Energy: 184 Wh x 3600 =
    # 662400 J; s2's 91.5 Wh = 329400 J. Memory: 87 GiB x 2^30 = 93415538688 B.
    expected = f"""\
# HELP videogen_clips Video-gen runs with a run.json now, by status (ok, failed). A gauge: a remade run is absent while it runs.
# TYPE videogen_clips gauge
videogen_clips{{{lab},status="ok"}} 2
# HELP videogen_frames Output video frames (ffprobe) summed over the ok runs. These are frames in the MP4, not the denoised latent frames.
# TYPE videogen_frames gauge
videogen_frames{{{lab}}} 248
# HELP videogen_video_seconds Seconds of output video summed over the ok runs: frames / output fps.
# TYPE videogen_video_seconds gauge
videogen_video_seconds{{{lab}}} 10.3333333333
# HELP videogen_wall_seconds Client wall seconds summed over the ok runs.
# TYPE videogen_wall_seconds gauge
videogen_wall_seconds{{{lab}}} 4920
# HELP videogen_server_seconds Server inference seconds (x-inference-time-s) summed over the ok runs that report it; divide by videogen_server_clips.
# TYPE videogen_server_seconds gauge
videogen_server_seconds{{{lab}}} 4919
# HELP videogen_server_clips The ok runs that report the server inference time.
# TYPE videogen_server_clips gauge
videogen_server_clips{{{lab}}} 2
# HELP videogen_energy_joules Outlet energy in joules summed over the ok runs with a complete reading; a partial reading is left out. Divide by videogen_energy_clips.
# TYPE videogen_energy_joules gauge
videogen_energy_joules{{{lab}}} 662400
# HELP videogen_energy_clips The ok runs with a complete outlet energy reading.
# TYPE videogen_energy_clips gauge
videogen_energy_clips{{{lab}}} 2
# HELP videogen_steps Denoising steps requested (num_inference_steps) summed over the ok runs that record it; divide by videogen_step_clips.
# TYPE videogen_steps gauge
videogen_steps{{{lab}}} 100
# HELP videogen_step_clips The ok runs that record num_inference_steps.
# TYPE videogen_step_clips gauge
videogen_step_clips{{{lab}}} 2
# HELP videogen_last_frames_per_second The latest ok run: output frames / wall seconds. Output frames, not the denoised latent frames.
# TYPE videogen_last_frames_per_second gauge
videogen_last_frames_per_second{{{lab}}} 0.05
# HELP videogen_last_wall_seconds The latest ok run: wall seconds.
# TYPE videogen_last_wall_seconds gauge
videogen_last_wall_seconds{{{lab}}} 2480
# HELP videogen_last_realtime_factor The latest ok run: wall seconds / seconds of output video.
# TYPE videogen_last_realtime_factor gauge
videogen_last_realtime_factor{{{lab}}} 480
# HELP videogen_last_peak_memory_bytes The latest ok run: peak used memory of the whole node, bytes.
# TYPE videogen_last_peak_memory_bytes gauge
videogen_last_peak_memory_bytes{{{lab}}} 93415538688
# HELP videogen_last_energy_joules The latest ok run: outlet energy in joules, when its reading is complete.
# TYPE videogen_last_energy_joules gauge
videogen_last_energy_joules{{{lab}}} 329400
# HELP videogen_last_timestamp_seconds The latest ok run: its end time, Unix seconds.
# TYPE videogen_last_timestamp_seconds gauge
videogen_last_timestamp_seconds{{{lab}}} 1794979075
# HELP videogen_metrics_build_timestamp_seconds When metrics.py last wrote this file, Unix seconds.
# TYPE videogen_metrics_build_timestamp_seconds gauge
videogen_metrics_build_timestamp_seconds 1795000000
"""
    text = render(tmp_path)
    assert text == expected
    parse(text)


def test_the_latest_run_is_picked_by_end_time_not_by_path(
    tmp_path: pathlib.Path,
) -> None:
    # dog-c sorts last by path but ended first.
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json(end="2026-11-18T05:00:00-0500"))
    put(
        tmp_path,
        "out/h3-t2v/dog-c-s1",
        run_json(end="2026-11-18T01:00:00-0500", wall_s=9999.0),
    )
    samples = parse(render(tmp_path))
    assert value(samples, "videogen_last_wall_seconds") == [2440.0]


def test_each_arm_and_size_is_its_own_series(tmp_path: pathlib.Path) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    put(
        tmp_path,
        "out/wan-t2v/dog-a-s1",
        run_json(
            mode="t2v",
            model="Wan-AI/Wan2.2-T2V-A14B-Diffusers",
            server_config={"served_model_name": "Wan-AI/Wan2.2-T2V-A14B-Diffusers"},
            node={"role": "worker"},
            ffprobe={"frames": 81, "width": 832, "height": 480, "fps": 16.0},
            expected={"frames": 81, "width": 832, "height": 480, "fps": 16.0},
            request={"fields": {"num_inference_steps": "40"}},
            wall_s=2350.0,
        ),
    )
    samples = parse(render(tmp_path))
    wan = value(
        samples,
        "videogen_video_seconds",
        arm="wan-t2v",
        node="worker",
        width="832",
        height="480",
        fps="16",
    )
    assert wan == [81 / 16]
    assert value(samples, "videogen_frames", arm="h3-t2v") == [124.0]
    assert len(value(samples, "videogen_frames")) == 2


def test_an_incomplete_energy_run_counts_as_a_clip_but_not_as_energy(
    tmp_path: pathlib.Path,
) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    put(
        tmp_path,
        "out/h3-t2v/dog-a-s2",
        run_json(
            end="2026-11-18T00:17:55-0500",
            outlet={"energy_wh": 40.0, "complete": False, "max_gap_s": 90.0},
            energy_wh=None,
        ),
    )
    samples = parse(render(tmp_path))
    assert value(samples, "videogen_clips", status="ok") == [2.0]
    assert value(samples, "videogen_frames") == [248.0]
    assert value(samples, "videogen_energy_joules") == [92.5 * 3600]
    assert value(samples, "videogen_energy_clips") == [1.0]
    # The latest run has no complete reading, so it has no energy gauge.
    assert value(samples, "videogen_last_energy_joules") == []


def test_a_failed_run_is_counted_by_status_only(tmp_path: pathlib.Path) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    failed = run_json(status="failed", error="HTTP 500", wall_s=12.0)
    for key in ("ffprobe", "energy_wh"):
        del failed[key]
    put(tmp_path, "out/h3-t2v/dog-a-s2", failed)
    samples = parse(render(tmp_path))
    # The failed run has no ffprobe; its size comes from `expected`.
    assert value(samples, "videogen_clips", status="failed", width="1280") == [1.0]
    assert value(samples, "videogen_clips", status="ok") == [1.0]
    assert value(samples, "videogen_wall_seconds") == [2440.0]


def test_a_failed_run_with_an_empty_ffprobe_keeps_its_requested_size(
    tmp_path: pathlib.Path,
) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    failed = run_json(
        status="failed",
        error="the clip has 0 frames",
        ffprobe={"frames": 0, "width": 0, "height": 0, "fps": 0.0},
    )
    put(tmp_path, "out/h3-t2v/dog-a-s2", failed)
    samples = parse(render(tmp_path))
    # Both runs share one label set: no width="0" series appears.
    assert value(samples, "videogen_clips", width="0") == []
    assert value(samples, "videogen_clips", width="1280", height="704", fps="24") == [
        1.0,
        1.0,
    ]


def test_size_labels_never_take_a_zero(tmp_path: pathlib.Path) -> None:
    put(
        tmp_path,
        "out/h3-t2v/dog-a-s1",
        {
            "status": "failed",
            "expected": {"width": 0},
            "request": {"fields": {"width": "0"}},
            "ffprobe": {"width": 640},
        },
    )
    samples = parse(render(tmp_path))
    assert value(samples, "videogen_clips", width="640") == [1.0]


def test_a_failed_run_with_only_a_request_takes_its_size_from_it(
    tmp_path: pathlib.Path,
) -> None:
    put(
        tmp_path,
        "out/h3-t2v/dog-a-s1",
        {"status": "failed", "request": {"fields": {"width": "832", "fps": "16"}}},
    )
    samples = parse(render(tmp_path))
    assert value(
        samples,
        "videogen_clips",
        status="failed",
        width="832",
        height="unknown",
        fps="16",
        model="unknown",
        node="unknown",
        mode="unknown",
    ) == [1.0]
    # No ok run: no throughput series at all.
    assert value(samples, "videogen_frames") == []


def test_label_values_are_escaped(tmp_path: pathlib.Path) -> None:
    model = 'odd "model"\\ with\na newline'
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json(model=model, server_config={}))
    text = render(tmp_path)
    assert 'model="odd \\"model\\"\\\\ with\\na newline"' in text
    samples = parse(text)
    assert value(samples, "videogen_frames", model=model) == [124.0]


def test_a_bad_or_incomplete_file_is_skipped_with_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    bad = [
        put(tmp_path, "out/h3-t2v/dog-a-s2", "{not json"),
        put(tmp_path, "out/h3-t2v/dog-a-s3", "[1, 2]"),
        put(tmp_path, "out/h3-t2v/dog-b-s1", run_json(status=None)),
        put(tmp_path, "out/h3-t2v/dog-b-s2", run_json(ffprobe=None)),
        put(tmp_path, "out/h3-t2v/dog-b-s3", run_json(wall_s="fast")),
        put(tmp_path, "out/h3-t2v/dog-c-s1", run_json(end="yesterday")),
        # Half a frame rounds to none: not a clip.
        put(
            tmp_path,
            "out/h3-t2v/dog-c-s2",
            run_json(ffprobe={"frames": 0.5, "width": 1280, "height": 704, "fps": 24}),
        ),
    ]
    with caplog.at_level(logging.WARNING):
        samples = parse(render(tmp_path))
    assert value(samples, "videogen_clips") == [1.0]
    assert value(samples, "videogen_frames") == [124.0]
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    for path in bad:
        assert any(str(path) in m for m in warned), path


def test_probes_are_left_out_unless_asked(tmp_path: pathlib.Path) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    put(tmp_path, "probe/h3-t2v/dog-a-s1-20261117T215528", run_json(probe=True))
    scored_only = parse(render(tmp_path))
    assert value(scored_only, "videogen_frames", kind="probe") == []
    both = parse(render(tmp_path, include_probes=True))
    assert value(both, "videogen_frames", kind="probe") == [124.0]
    assert value(both, "videogen_frames", kind="scored") == [124.0]


def test_a_rerun_writes_the_same_file(tmp_path: pathlib.Path) -> None:
    root, prom = tmp_path / "vg", tmp_path / "textfile"
    prom.mkdir()
    put(root, "out/h3-t2v/dog-a-s1", run_json())
    first = metrics.write(root, prom, now=NOW).read_text()
    second = metrics.write(root, prom, now=NOW).read_text()
    assert first == second


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path: pathlib.Path) -> None:
    root, prom = tmp_path / "vg", tmp_path / "textfile"
    prom.mkdir()
    put(root, "out/h3-t2v/dog-a-s1", run_json())
    path = metrics.write(root, prom, now=NOW)
    assert path == prom / "videogen.prom"
    assert sorted(p.name for p in prom.iterdir()) == ["videogen.prom"]
    # node_exporter runs as another user and must read it.
    assert path.stat().st_mode & 0o777 == 0o644
    parse(path.read_text())


def test_a_failed_write_keeps_the_old_file_and_no_temp_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, prom = tmp_path / "vg", tmp_path / "textfile"
    prom.mkdir()
    put(root, "out/h3-t2v/dog-a-s1", run_json())
    old = metrics.write(root, prom, now=NOW).read_text()
    put(root, "out/h3-t2v/dog-a-s2", run_json())

    def fail(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk full"):
        metrics.write(root, prom, now=NOW)
    assert sorted(p.name for p in prom.iterdir()) == ["videogen.prom"]
    assert (prom / "videogen.prom").read_text() == old


def test_the_temp_name_is_not_read_by_node_exporter(tmp_path: pathlib.Path) -> None:
    seen: list[str] = []
    real = os.replace

    def spy(src: Any, dst: Any) -> None:
        seen.append(pathlib.Path(src).name)
        real(src, dst)

    target = tmp_path / "videogen.prom"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "replace", spy)
        metrics.write_atomic(target, "x 1\n")
    assert len(seen) == 1
    assert not seen[0].endswith(".prom")
    assert seen[0].startswith(".")


def test_a_missing_textfile_dir_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        metrics.write(tmp_path, tmp_path / "absent", now=NOW)


def test_main_returns_1_for_a_missing_dir(tmp_path: pathlib.Path) -> None:
    rc = metrics.main(
        ["--root", str(tmp_path), "--textfile-dir", str(tmp_path / "absent")]
    )
    assert rc == 1


# ------------------------------------------------------- the batch hook ----


def test_the_batch_hook_swallows_a_failure_and_warns_once(
    caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path
) -> None:
    calls: list[pathlib.Path] = []

    def writer(root: pathlib.Path) -> None:
        calls.append(root)
        raise PermissionError("textfile_collector")

    failed = False
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            ok = batch.refresh_metrics(writer, tmp_path, quiet=failed)
            assert ok is False
            failed = True
    assert calls == [tmp_path] * 3
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "metrics export failed" in r.getMessage()
    ]
    assert len(warnings) == 1


def test_the_batch_hook_is_a_no_op_when_off(tmp_path: pathlib.Path) -> None:
    assert batch.refresh_metrics(None, tmp_path) is True


# ------------------------------------------------- review follow-ups ----


def test_an_old_probe_without_ffprobe_logs_at_info_not_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    old = run_json(probe=True, schema=1)
    del old["ffprobe"]
    path = put(tmp_path, "probe/h3-t2v/dog-a-s1-20261117T215528", old)
    with caplog.at_level(logging.INFO):
        render(tmp_path, include_probes=True)
    mine = [r for r in caplog.records if str(path) in r.getMessage()]
    assert [r.levelno for r in mine] == [logging.INFO]


def test_the_optional_values_count_their_own_clips(tmp_path: pathlib.Path) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    put(
        tmp_path,
        "out/h3-t2v/dog-a-s2",
        run_json(server_headers={}, request={"fields": {"width": "1280"}}),
    )
    samples = parse(render(tmp_path))
    assert value(samples, "videogen_server_seconds") == [2439.5]
    assert value(samples, "videogen_server_clips") == [1.0]
    assert value(samples, "videogen_steps") == [50.0]
    assert value(samples, "videogen_step_clips") == [1.0]
    assert value(samples, "videogen_clips", status="ok") == [2.0]


def hold_lock(root: pathlib.Path) -> Any:
    """Another writer's lock: a separate open file, so flock conflicts."""
    fh = (root / metrics.LOCK_FILE).open("a")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def test_a_second_writer_times_out_while_the_lock_is_held(
    tmp_path: pathlib.Path,
) -> None:
    root, prom = tmp_path / "vg", tmp_path / "textfile"
    prom.mkdir()
    put(root, "out/h3-t2v/dog-a-s1", run_json())
    held = hold_lock(root)
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            metrics.write(root, prom, now=NOW, lock_timeout_s=0.2)
        assert time.monotonic() - t0 >= 0.2
        assert not (prom / "videogen.prom").exists()
    finally:
        held.close()


def test_a_second_writer_waits_for_the_lock_then_writes(
    tmp_path: pathlib.Path,
) -> None:
    root, prom = tmp_path / "vg", tmp_path / "textfile"
    prom.mkdir()
    put(root, "out/h3-t2v/dog-a-s1", run_json())
    held = hold_lock(root)
    release = threading.Timer(0.3, held.close)
    release.start()
    try:
        t0 = time.monotonic()
        metrics.write(root, prom, now=NOW, lock_timeout_s=5.0)
        waited = time.monotonic() - t0
    finally:
        release.join()
    assert waited >= 0.25
    assert (prom / "videogen.prom").is_file()


# ----------------------------------------------------------------- images ----
# #1424: images get their own families, so the clip families and every
# query over them stay as they were.

Z_IMAGE = "Tongyi-MAI/Z-Image-Turbo"


def image_json(**change: Any) -> dict[str, Any]:
    """An ok text-to-image run, as imagegen.py writes it."""
    run: dict[str, Any] = {
        "schema": 2,
        "media": "image",
        "probe": False,
        "arm": "z-image-turbo",
        "mode": "t2i",
        "model": Z_IMAGE,
        "request": {
            "fields": {"size": "1024x1024", "num_inference_steps": 9},
            "inputs": [],
        },
        "server_config": {"served_model_name": Z_IMAGE},
        "node": {"role": "head"},
        "start": "2026-11-18T20:00:00-0500",
        "end": "2026-11-18T20:00:10-0500",
        "wall_s": 10.0,
        "server_s": None,
        "memory": {"peak_used_gib": 30.0},
        "expected": {"width": 1024, "height": 1024},
        "found": {"width": 1024, "height": 1024, "format": "png"},
        "outlet": {"energy_wh": 0.5, "complete": True},
        "energy_wh": 0.5,
        "status": "ok",
        "error": None,
    }
    run.update(change)
    return run


def test_a_video_only_root_writes_no_image_family(tmp_path: pathlib.Path) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    assert "videogen_image" not in render(tmp_path)


def test_images_have_their_own_families_and_leave_the_clips_alone(
    tmp_path: pathlib.Path,
) -> None:
    put(tmp_path, "out/h3-t2v/dog-a-s1", run_json())
    put(tmp_path, "out/z-image-turbo/meme-text-s1", image_json())
    put(
        tmp_path,
        "out/z-image-turbo/meme-text-s2",
        image_json(end="2026-11-18T20:01:00-0500", wall_s=12.0, energy_wh=None),
    )
    put(
        tmp_path,
        "out/z-image-turbo/cartoon-s1",
        image_json(status="failed", error="HTTP 500", found=None),
    )
    samples = parse(render(tmp_path))
    labels = {
        "arm": "z-image-turbo",
        "model": Z_IMAGE,
        "node": "head",
        "mode": "t2i",
        "kind": "scored",
        "width": "1024",
        "height": "1024",
    }
    want = frozenset(labels.items())
    assert samples[("videogen_images", want | {("status", "ok")})] == 2
    assert samples[("videogen_images", want | {("status", "failed")})] == 1
    assert samples[("videogen_image_wall_seconds", want)] == 22.0
    assert samples[("videogen_image_steps", want)] == 18
    assert samples[("videogen_image_step_images", want)] == 2
    assert samples[("videogen_image_last_wall_seconds", want)] == 12.0
    assert samples[("videogen_image_last_peak_memory_bytes", want)] == 30.0 * 2**30
    # No block.json yet: no energy family, and no per-request energy one.
    assert not [n for n, _ in samples if "energy" in n and "image" in n]
    # No fps label on an image, and no image in a clip family.
    image_keys = [ls for n, ls in samples if n.startswith("videogen_image")]
    assert image_keys and all("fps" not in dict(ls) for ls in image_keys)
    assert value(samples, "videogen_wall_seconds") == [2440.0]
    assert value(samples, "videogen_clips") == [1.0]
    assert not value(samples, "videogen_frames", arm="z-image-turbo")


def test_an_edit_takes_its_size_from_the_request_when_it_failed(
    tmp_path: pathlib.Path,
) -> None:
    put(
        tmp_path,
        "out/qwen-image-edit/face-swap-s1",
        image_json(
            arm="qwen-image-edit",
            mode="edit",
            expected=None,
            found=None,
            status="failed",
            request={"fields": {"size": "1024x576"}},
        ),
    )
    samples = parse(render(tmp_path))
    [(_, labels)] = [k for k in samples if k[0] == "videogen_images"]
    assert dict(labels)["width"] == "1024" and dict(labels)["height"] == "576"
    assert dict(labels)["mode"] == "edit"


def test_an_ok_image_without_wall_time_is_skipped_with_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    put(tmp_path, "out/z-image-turbo/meme-text-s1", image_json(wall_s=None))
    assert "videogen_images" not in render(tmp_path)
    assert "it says ok but has no wall_s" in caplog.text


BLOCK = {
    "arm": "z-image-turbo",
    "start": "2026-11-18T20:00:00-0500",
    "end": "2026-11-18T20:00:36-0500",
    "duration_s": 36.0,
    "requests": 12,
    "images": 12,
    "energy_wh": 1.8,
    "wh_per_image": 0.15,
}


def test_image_energy_is_the_block_wh_per_image(tmp_path: pathlib.Path) -> None:
    """#1424 review: a 3 s image has no energy of its own; the arm's block
    (block.json) gives the energy per image."""
    put(tmp_path, "out/z-image-turbo/meme-text-s1", image_json(energy_wh=None))
    put(tmp_path, "out/z-image-turbo/meme-text-s2", image_json(energy_wh=None))
    (tmp_path / "out/z-image-turbo/block.json").write_text(json.dumps(BLOCK))
    samples = parse(render(tmp_path))
    per = value(samples, "videogen_image_block_energy_joules_per_image")
    assert per == [pytest.approx(0.15 * 3600)]
    assert value(samples, "videogen_image_block_energy_joules") == [
        pytest.approx(1.8 * 3600)
    ]
    assert value(samples, "videogen_image_block_images") == [12.0]
    assert value(samples, "videogen_image_block_seconds") == [36.0]


def test_an_incomplete_block_has_no_energy_but_its_image_count(
    tmp_path: pathlib.Path,
) -> None:
    put(tmp_path, "out/z-image-turbo/meme-text-s1", image_json(energy_wh=None))
    block = {**BLOCK, "energy_wh": None, "wh_per_image": None}
    (tmp_path / "out/z-image-turbo/block.json").write_text(json.dumps(block))
    samples = parse(render(tmp_path))
    assert not value(samples, "videogen_image_block_energy_joules_per_image")
    assert not value(samples, "videogen_image_block_energy_joules")
    assert value(samples, "videogen_image_block_images") == [12.0]


def test_a_bad_block_file_is_skipped_with_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    put(tmp_path, "out/z-image-turbo/meme-text-s1", image_json())
    (tmp_path / "out/z-image-turbo/block.json").write_text("{not json")
    samples = parse(render(tmp_path))
    assert value(samples, "videogen_images", status="ok") == [1.0]
    assert not value(samples, "videogen_image_block_images")
    assert "block.json: unreadable" in caplog.text
