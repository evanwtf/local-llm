"""#305, step 2: the writer of the machine header."""

from __future__ import annotations

import json
import pathlib

import machine_header as mh
import pytest
import results

AT = "2026-11-06T12:00:00-0500"
DGX = "Cortex-X925-128GB-GB10-x2"

SMI_QUERY = "580.178.04, 9A.0B.2D.00.00\n"
SMI = "| NVIDIA-SMI 580.178.04   Driver Version: 580.178.04   CUDA Version: 13.0 |\n"
SMI_Q = "    GSP Firmware Version                               : 580.178.04\n"
DGX_RELEASE = (
    'DGX_NAME="DGX Spark"\n'
    'DGX_SWBUILD_VERSION="7.2.3"\n'
    'DGX_SERIAL_NUMBER="1234567890"\n'
    "\n"
    'DGX_OTA_VERSION="7.5.0"\n'
    'DGX_OTA_VERSION="7.6.0"\n'
)
FILES = {
    "/sys/class/dmi/id/bios_version": "5.36_0ACUM018\n",
    "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor": "performance\n",
    "/sys/class/nvme/nvme0/firmware_rev": "NXHB202Q\n",
    "/etc/dgx-release": DGX_RELEASE,
}


def spark_run(cmd: list[str]) -> str | None:
    if cmd == ["nvidia-smi"]:
        return SMI
    if cmd == ["nvidia-smi", "-q"]:
        return SMI_Q
    if cmd[0] == "nvidia-smi":
        return SMI_QUERY
    return None


def test_the_spark_facts_match_its_inventory(monkeypatch):
    """The values hardware/Cortex-X925-128GB-GB10/VERSIONS.md took by hand."""
    monkeypatch.setattr(mh.platform, "release", lambda: "7.0.0-1019-nvidia")
    got = mh.linux_facts(spark_run, FILES.get)
    assert got == {
        "kernel": "7.0.0-1019-nvidia",
        "nvidia_driver": "580.178.04",
        "vbios": "9A.0B.2D.00.00",
        "cuda_driver_api": "13.0",
        "gsp_firmware": "580.178.04",
        "system_firmware": "5.36_0ACUM018",
        "cpu_governor": "performance",
        "nvme_firmware": "NXHB202Q",
        "dgx_os": "7.2.3",
        "dgx_ota": "7.6.0",
    }


def test_the_serial_number_is_never_recorded():
    got = mh.linux_facts(spark_run, FILES.get)
    assert "1234567890" not in json.dumps(got)


def test_a_linux_box_without_nvidia_records_what_it_can(monkeypatch):
    monkeypatch.setattr(mh.platform, "release", lambda: "6.8.0")
    got = mh.linux_facts(lambda cmd: None, lambda path: None)
    assert got == {"kernel": "6.8.0"}


def test_an_na_answer_is_absent_not_a_value():
    got = mh.linux_facts(
        lambda cmd: "580.1, [N/A]\n" if "--format=csv,noheader" in cmd else None,
        lambda path: None,
    )
    assert got["nvidia_driver"] == "580.1"
    assert "vbios" not in got


def test_the_mac_facts():
    answers = {
        ("sw_vers", "-productVersion"): "27.0\n",
        ("sw_vers", "-buildVersion"): "27A5300f\n",
        ("sysctl", "-n", "machdep.cpu.brand_string"): "Apple M5 Max\n",
        ("sysctl", "-n", "iogpu.wired_limit_mb"): "114688\n",
        ("system_profiler", "SPHardwareDataType"): (
            "      System Firmware Version: 13822.1.5\n"
        ),
    }
    assert mh.darwin_facts(lambda cmd: answers.get(tuple(cmd))) == {
        "macos": "27.0",
        "macos_build": "27A5300f",
        "chip": "Apple M5 Max",
        "iogpu_wired_limit_mb": 114688,
        "system_firmware": "13822.1.5",
    }


def test_a_platform_with_no_collector_has_no_facts():
    assert mh.collect("win32") == {}


# --- when a header is written ------------------------------------------------


def test_a_new_ledger_gets_a_header(tmp_path):
    path = tmp_path / "results.jsonl"
    got = mh.ensure(path, DGX, {"nvidia_driver": "580.178.04"}, AT)
    assert got == {
        "record": "machine-header",
        "v": 1,
        "at": AT,
        "machine": DGX,
        "facts": {"nvidia_driver": "580.178.04"},
    }
    assert results.header(path) == got
    assert results.load(path) == []


def test_unchanged_facts_write_nothing(tmp_path):
    path = tmp_path / "results.jsonl"
    mh.ensure(path, DGX, {"nvidia_driver": "580.178.04"}, AT)
    before = path.read_text()
    assert mh.ensure(path, DGX, {"nvidia_driver": "580.178.04"}, AT) is None
    assert path.read_text() == before


def test_a_driver_update_starts_a_segment(tmp_path):
    """Rows before the update keep the old header; rows after get the new."""
    path = tmp_path / "results.jsonl"
    mh.ensure(path, DGX, {"nvidia_driver": "580.173.02"}, AT)
    with path.open("a") as fh:
        fh.write(json.dumps({"task": "t", "backend": "b", "client": "opencode"}))
        fh.write("\n")
    mh.ensure(path, DGX, {"nvidia_driver": "595.10"}, AT)
    lines = path.read_text().splitlines()
    assert len(lines) == 3
    first, last = results.header(path, 0), results.header(path)
    assert first is not None and first["facts"] == {"nvidia_driver": "580.173.02"}
    assert last is not None and last["facts"] == {"nvidia_driver": "595.10"}


def test_a_failed_probe_is_not_a_boundary(tmp_path):
    """An nvidia-smi hiccup drops a key; that is not a firmware change."""
    path = tmp_path / "results.jsonl"
    mh.ensure(path, DGX, {"kernel": "7.0", "nvidia_driver": "580"}, AT)
    assert mh.ensure(path, DGX, {"kernel": "7.0"}, AT) is None
    assert len(path.read_text().splitlines()) == 1


def test_a_newly_answered_probe_is_recorded(tmp_path):
    path = tmp_path / "results.jsonl"
    mh.ensure(path, DGX, {"kernel": "7.0"}, AT)
    assert mh.ensure(path, DGX, {"kernel": "7.0", "vbios": "9A"}, AT) is not None


def test_no_facts_write_no_header(tmp_path):
    path = tmp_path / "results.jsonl"
    assert mh.ensure(path, DGX, {}, AT) is None
    assert not path.exists()


def test_a_header_for_another_machine_refuses(tmp_path):
    path = tmp_path / "results.jsonl"
    mh.ensure(path, DGX, {"kernel": "7.0"}, AT)
    with pytest.raises(SystemExit, match="One file, one machine"):
        mh.ensure(path, "MacBook-Pro-M5-Max", {"kernel": "7.0"}, AT)


def test_a_header_a_reader_rejects_is_never_written(tmp_path):
    path = tmp_path / "results.jsonl"
    with pytest.raises(SystemExit, match="not a scalar"):
        mh.ensure(path, DGX, {"nested": {"a": 1}}, AT)
    assert not path.exists()


def test_the_fingerprint_does_not_count_the_header(tmp_path):
    import provenance

    path = tmp_path / "results.jsonl"
    mh.ensure(path, DGX, {"kernel": "7.0"}, AT)
    assert provenance.fingerprint(pathlib.Path(path)).startswith("0 rows")


def test_a_deliberate_second_machine_starts_its_own_segment(tmp_path):
    """--allow-foreign-hardware: the mixing was decided, so say where it starts."""
    path = tmp_path / "results.jsonl"
    mh.ensure(path, DGX, {"kernel": "7.0"}, AT)
    got = mh.ensure(path, "Mac", {"kernel": "7.0"}, AT, allow_other_machine=True)
    assert got is not None and got["machine"] == "Mac"
    last = results.header(path)
    assert last is not None and last["machine"] == "Mac"
