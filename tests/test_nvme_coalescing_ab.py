from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import nvme_coalescing_ab as ab

# The shape `fio --output-format=json` writes (fio 3.36), cut to the fields read.
FIO_JSON = {
    "fio version": "fio-3.36",
    "jobs": [
        {
            "jobname": "qd1-randread-4k",
            "read": {
                "iops": 17543.2,
                "bw": 70172,
                "clat_ns": {
                    "mean": 56123.4,
                    "percentile": {"50.000000": 55552, "99.000000": 71168},
                },
            },
        }
    ],
}


def test_summarize_reads_iops_and_latency_in_microseconds() -> None:
    s = ab.summarize(FIO_JSON)
    # Known absolutes, not only relationships: 56123.4 ns is 56.1 us.
    assert s == {
        "iops": 17543.2,
        "bw_kib_s": 70172,
        "clat_mean_us": 56.1,
        "clat_p50_us": 55.6,
        "clat_p99_us": 71.2,
    }


def test_summarize_refuses_a_run_with_no_jobs() -> None:
    with pytest.raises(ValueError, match="no jobs"):
        ab.summarize({"jobs": []})


PROC_INTERRUPTS = """\
           CPU0       CPU1       CPU2
  98:          5          0          0  ITS-MSI 0 Edge      nvme0q0
  99:       1200        300          0  ITS-MSI 1 Edge      nvme0q1
 100:          0         40          2  ITS-MSI 2 Edge      nvme0q2
 101:        777          0          0  ITS-MSI 3 Edge      mlx5_comp0
"""


def test_nvme_interrupts_sums_io_queues_only() -> None:
    # nvme0q0 is the admin queue: coalescing never applies to it, so it is out.
    assert ab.nvme_interrupts(PROC_INTERRUPTS, "nvme0") == 1200 + 300 + 40 + 2


def test_nvme_interrupts_ignores_other_controllers() -> None:
    text = PROC_INTERRUPTS + " 102:  9  9  9  ITS-MSI 4 Edge  nvme10q1\n"
    assert ab.nvme_interrupts(text, "nvme0") == 1542


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("get-feature:0x08 (Interrupt Coalescing), Current value:0x00000107", 0x107),
        ("get-feature:0x08 (Interrupt Coalescing), Current value:00000000", 0),
        ("get-feature:0x08 (Interrupt Coalescing), Current value:0000000", 0),
    ],
)
def test_parse_feature_value(line: str, value: int) -> None:
    assert ab.parse_feature_value(line) == value


def test_parse_feature_value_refuses_garbage() -> None:
    with pytest.raises(ValueError):
        ab.parse_feature_value("NVMe status: INVALID_FIELD")


def test_new_files_names_only_what_appeared(tmp_path: pathlib.Path) -> None:
    (tmp_path / "keep.bin").write_bytes(b"x")
    before = ab.listing([tmp_path])
    (tmp_path / "qd1-randread-4k.0.0").write_bytes(b"fio")
    after = ab.listing([tmp_path])
    assert ab.new_files(before, after) == [tmp_path / "qd1-randread-4k.0.0"]


def test_arm_states_alternate_and_end_on() -> None:
    # on/off three times: three datapoints per state; then the script's own restore leaves the drive at NVIDIA's value.
    assert ab.ARMS == ("on", "off", "on", "off", "on", "off")
    assert ab.STATE_VALUE == {"on": 0x107, "off": 0}
